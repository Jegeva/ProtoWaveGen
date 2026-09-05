from __future__ import annotations

import struct

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import DriverTracker, TransportProtocol, bind_clock_samples, format_byte, register_protocol
from .checksums import crc16_x25
from .payload import decode_payload_with_floating, group_floating_by_byte

# --- SIR (Serial InfraRed) physical layer -----------------------------------
#
# SIR is literally "UART timing, with each bit slot's level-hold replaced by
# a pulse-or-no-pulse": a logic 0 is a brief infrared light pulse (nominally
# 3/16 of the bit period wide) at the *start* of the bit cell, a logic 1 is
# no pulse (light off) for the whole bit cell. Confirmed against IrDA's own
# physical-layer spec summaries (e.g. ARM's PL011 UART "IrDA SIR operation"
# doc, TI's SLAA044 IrDA SIR encoder/decoder app note): "a zero logic level
# is transmitted as a pulse of 3/16th duration of the selected baud rate bit
# period ... logic one levels are transmitted as no pulse". That 0-is-pulsed
# convention is exactly UART's own polarity (start bit = 0, idle/stop = 1),
# so byte framing is unchanged from UART: 1 start bit (0, always pulsed),
# 8 data bits LSB-first, 1 stop bit (1, never pulsed) — no parity, since SIR
# has none in practice. `_send_byte` below is structurally the same loop as
# `UartTransport._send_byte` (see `uart.py`), just swapping "hold level for
# the whole bit period" for "pulse-or-not within the bit period".
#
# The `ir` envelope line reuses the IR remote-control family's active-low
# convention (`_ir_pulse.py`'s module docstring: idle/space = 1, mark/pulse
# = 0) for consistency across every IR-ish signal in this codebase — but
# `_ir_pulse.py`'s own helpers (`mark_space`, `biphase_bit`) aren't reused
# here: they're pulse-*distance* and Manchester primitives built around a
# mark width that itself carries no bit information (NEC) or a mark/space
# transition at a bit's midpoint (RC-5/RC-6), whereas SIR is a fixed-length
# bit cell whose *presence or absence* of a pulse (of fixed width, at a
# fixed position) is the only thing that varies — a genuinely different
# shape needing its own small primitive (`_send_bit` below), not a forced
# fit onto either existing helper.

_XID_FORMAT = 0x01
"""XID Information field's Format Identifier byte. Confirmed empirically:
a frame built with this value decodes cleanly via a real, independently
implemented dissector (`tshark`'s `irlap`, see the Wireshark cross-
validation test in `tests/test_sigrok_roundtrip.py`) into the expected
`irlap.xid.fi`/`saddr`/`daddr`/`flags`/`slotnr`/`version` fields."""

_XID_CMD = 0x2F
"""IrLAP XID command's U-frame control-byte value (`bit0=1,bit1=1` marks a
U-frame; `0x2F`'s modifier bits select "Exchange Station Identification"),
confirmed against a real IrLAP implementation's own header
(`include/net/irda/irlap_frame.h` in the (now-removed) Linux IrDA stack,
`net/irda`) and cross-checked empirically: `tshark`'s `irlap` dissector
labels a frame using this exact byte "Command: Exchange Station
Identification" and correctly recurses into its XID-specific fields. Does
*not* include the P/F bit (bit4) — `send_frame`'s `final` param ORs that in
separately, matching the real stack's own `XID_CMD | PF_BIT` construction."""

_PF_BIT = 0x10
"""Poll/Final bit (control byte bit 4) — Poll when the frame is a command
(`command=True`), Final when it's a response."""

_INTER_FRAME_GAP_BITS = 16
"""Minimum guaranteed idle (bit periods, no pulse) between two `send_frame`
calls. IrLAP's own framing has no explicit end-of-frame delimiter at the
SIR byte-stream level — frame boundaries are inferred purely from silence,
the same idle-timeout shape this codebase already uses for LIN/Modbus RTU
(see `uart.py`'s docstring). This is safe because a SIR byte's start bit is
*always* a pulse (logic 0 by definition), so the longest possible silent
run *within* one frame is bounded: at most 8 data bits + 1 stop bit = 9
consecutive no-pulse bit cells between one byte's start-bit pulse and the
next byte's, regardless of frame length or content (proven by the fact a
start-bit pulse recurs at least once every 10 bit cells). 16 bit periods
comfortably clears that 9-cell bound while staying cheap; the custom sigrok
decoder in `tests/custom_decoders/irda/pd.py` uses a matching (smaller than
16, larger than 9) threshold to tell "next byte, same frame" from "frame
ended" — see its own module docstring."""


@register_protocol("irda")
class IrdaBus(TransportProtocol):
    """IrDA SIR (Serial InfraRed) physical layer carrying IrLAP frames: one
    demodulated IR envelope line (`sig("ir")`, active-low — idle/space is
    logic 1, a light pulse is logic 0, matching `_ir_pulse.py`'s convention
    for the rest of the IR family even though the encoding shape itself is
    unrelated). NOT the consumer-remote protocols in `ir_rc5.py`/
    `ir_nec.py`/`ir_rc6.py` — IrDA is a genuine serial data link (used by
    old phones/PDAs/laptops/printers for OBEX file transfer, IrCOMM, etc.),
    standardized by the Infrared Data Association, layered as SIR (physical)
    under IrLAP (link access protocol, this module) under IrLMP/IrOBEX/etc.
    (out of scope here).

    v1 scope: SIR only (up to 115.2kbit/s) — MIR/FIR (0.576/4+ Mbit/s) use
    fundamentally different RZI/4PPM encodings, not implemented. IrLAP
    I-frames (`send_i_frame`, data-carrying, with N(S)/N(R) sequence
    numbers) and one U-frame type, XID (`send_xid`, eXchange station
    IDentification — the frame IrLAP devices broadcast to discover each
    other, chosen over SNRM/UA connection setup because its Information
    field has fully public, simple, fixed-width sub-fields, making its
    real wire shape straightforward to get exactly right and to verify
    against `tshark`'s independent dissector).

    IrLAP frame shape (Address, Control, Information, FCS — no explicit
    start/end delimiter at the SIR layer, see `_INTER_FRAME_GAP_BITS`):
    - **Address** (1 byte): bit 0 is C/R (Command=1/Response=0, `command=`
      param), bits 1-7 are a 7-bit connection address (0-127; 0x7F is the
      broadcast address used for discovery). Confirmed against the Linux
      kernel's own (now-removed) IrDA stack (`net/irda/irlap_frame.c`:
      `command = skb->data[0] & CMD_FRAME` with `CMD_FRAME = 0x01`) and
      against `tshark`'s `irlap` dissector's own field masks
      (`irlap.a.cr` = 0x01, `irlap.a.address` = the remaining 7 bits).
    - **Control** (1 byte): bit 0 = 0 selects an I-frame (bits 1-3 = N(S),
      bit 4 = P/F, bits 5-7 = N(R)); bits 0-1 = `11` selects a U-frame
      (bit 4 = P/F, the other 5 bits select the command/response type,
      `_XID_CMD` for XID). Confirmed the same two ways as `_XID_CMD` above.
    - **Information** (0+ bytes): opaque payload (an IrLMP PDU in real
      traffic; arbitrary bytes here) for I-frames, or the XID discovery
      fields (`send_xid`) for XID.
    - **FCS** (2 bytes, LSB first): CRC-16/X-25 (`crc16_x25`, `checksums.
      py`) over Address+Control+Information — verified against the Linux
      kernel's own magic-residue self-check value (`GOOD_FCS = 0xf0b8`)
      and against a real IrLAP frame's own reconstructed FCS. **Not**
      included in the Wireshark cross-validation pcap frames (see
      `tests/test_sigrok_roundtrip.py`): a real Linux IrDA capture point
      only sees a frame *after* the receiving hardware/driver has already
      validated and stripped its trailing FCS (the same reason a captured
      Ethernet frame usually has no trailing FCS either) — confirmed
      empirically: feeding `tshark` an Address+Control+Info+FCS frame
      makes its `irlmp`/discovery sub-dissectors misparse the 2 real FCS
      bytes as extra payload content, while Address+Control+Info alone
      (no FCS) decodes cleanly. Our own SIR waveform (and the custom
      sigrok decoder validating it) still carries a real FCS on the wire,
      since a real transmitter always sends one — only the capture-format
      comparison point differs.
    """

    def __init__(self, node_id: str, *, baudrate: int = 115200, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        if baudrate <= 0:
            raise ValueError(f"baudrate must be positive, got {baudrate}")
        self.baudrate = baudrate
        self._samples_per_bit: int | None = None
        self._pulse_samples: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        spb = bind_clock_samples(samplerate, self.baudrate, hz_label="baudrate", divisor=1)
        pulse = max(round(spb * 3 / 16), 1)
        if pulse >= spb:
            raise ValueError(
                f"samplerate {samplerate} too low to represent a SIR pulse distinct from a full bit "
                f"period at baudrate {self.baudrate} (need at least {16 * self.baudrate} Hz)"
            )
        self._samples_per_bit = spb
        self._pulse_samples = pulse

    @property
    def bit_period_samples(self) -> int | None:
        """Samples per bit, once bound (after the first `send_frame()`). Lets
        the app translate a `unit_bits` config override into raw samples
        without knowing this protocol's internals — same role as
        `UartTransport.bit_period_samples`."""

        return self._samples_per_bit

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("ir"), initial_level=1)]

    def _send_bit(self, builder: CaptureBuilder, line: str, bit: int) -> None:
        spb = self._samples_per_bit
        if bit:
            builder.set_level(line, 1)
            builder.advance(spb)
        else:
            pulse = self._pulse_samples
            builder.set_level(line, 0)
            builder.advance(pulse)
            builder.set_level(line, 1)
            builder.advance(spb - pulse)

    def _send_byte(
        self, builder: CaptureBuilder, line: str, byte: int, tracker: DriverTracker, owner: str,
        floating_bits: frozenset[int] = frozenset(),
    ) -> None:
        if not (0 <= byte <= 0xFF):
            raise ValueError(f"byte {byte} does not fit in 8 bits")
        tracker.set(owner)
        self._send_bit(builder, line, 0)  # start bit, always pulsed
        for i in range(8):
            bit = (byte >> i) & 1  # LSB first
            # `i` is the shift amount; FloatingSpan's bit_index is MSB-first (0=MSB).
            tracker.set("floating" if (7 - i) in floating_bits else owner)
            self._send_bit(builder, line, bit)
        tracker.set(owner)
        self._send_bit(builder, line, 1)  # stop bit, never pulsed

    def _inter_frame_gap(self, builder: CaptureBuilder) -> None:
        builder.advance(_INTER_FRAME_GAP_BITS * self._samples_per_bit)

    def send_frame(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        control: int,
        info=None,
        datatype: str = "bytes",
        command: bool = True,
        final: bool = True,
        driver: str | None = None,
    ) -> FrameHandle:
        """One generic IrLAP frame: Address, Control, optional Information,
        FCS — every byte SIR-encoded (see the class docstring). `control`
        must not set bit 4 (0x10, the P/F bit) directly; pass `final=` for
        that instead, matching how a real IrLAP stack ORs `PF_BIT` in
        separately from the frame-type constant (`SNRM_CMD | PF_BIT`, etc.
        — see `_XID_CMD`'s docstring). `info`/`datatype` follow this
        codebase's usual floating-marker convention
        (`decode_payload_with_floating`, `tristate=False` since IrDA is a
        single-transmitter-at-a-time link with no protocol-defined pull,
        same reasoning as `dali.py`/the IR family). `send_i_frame`/
        `send_xid` below build the right `control`/`info` for their frame
        types and call this."""

        if not (0 <= address <= 0x7F):
            raise ValueError(f"address {address} does not fit in 7 bits (0-127)")
        if not (0 <= control <= 0xFF):
            raise ValueError(f"control {control} does not fit in 8 bits")
        if control & _PF_BIT:
            raise ValueError(
                f"control 0x{control:02X} must not set bit 4 (0x{_PF_BIT:02X}, the P/F bit) directly "
                f"— pass final=True/False instead"
            )

        if info is None:
            payload_values: list[int] = []
            floating_by_byte: dict[int, frozenset[int]] = {}
        else:
            payload = decode_payload_with_floating(info, datatype, tristate=False)
            payload_values = payload.values
            floating_by_byte = group_floating_by_byte(payload.floating)

        if self._samples_per_bit is None:
            self.bind_samplerate(builder.samplerate)
        line = self.sig("ir")
        owner = driver or "sender"

        addr_byte = (address << 1) | (1 if command else 0)
        control_byte = control | (_PF_BIT if final else 0)
        frame_bytes = [addr_byte, control_byte, *payload_values]
        fcs = crc16_x25(frame_bytes)
        fcs_bytes = [fcs & 0xFF, (fcs >> 8) & 0xFF]
        all_bytes = frame_bytes + fcs_bytes

        labels = [f"ADDR {format_byte(addr_byte)}", f"CTRL {format_byte(control_byte)}"]
        labels += [f"INFO[{i}] {format_byte(b)}" for i, b in enumerate(payload_values)]
        labels += [f"FCS-LO {format_byte(fcs_bytes[0])}", f"FCS-HI {format_byte(fcs_bytes[1])}"]

        self._inter_frame_gap(builder)
        tracker = DriverTracker(builder, line)
        with builder.frame() as fh:
            for i, byte in enumerate(all_bytes):
                info_index = i - 2
                floating_bits = (
                    floating_by_byte.get(info_index, frozenset())
                    if 0 <= info_index < len(payload_values)
                    else frozenset()
                )
                with builder.frame() as byte_fh:
                    self._send_byte(builder, line, byte, tracker, owner, floating_bits)
                builder.annotate("unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=(line,))
                builder.annotate(
                    "field", labels[i], start=byte_fh.start, end=byte_fh.end, signals=(line,), value=byte,
                )
        tracker.close()

        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(line,))
        return fh

    def send_i_frame(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        ns: int,
        nr: int,
        info,
        datatype: str = "bytes",
        command: bool = True,
        final: bool = True,
        driver: str | None = None,
    ) -> FrameHandle:
        """One IrLAP I-frame (Information transfer, the data-carrying frame
        type): `info` (an IrLMP PDU in real traffic) framed with N(S)/N(R)
        sequence numbers, control byte bit0=0 implicit. Control layout
        (bits1-3=N(S), bit4=P/F, bits5-7=N(R)) confirmed against the Linux
        IrDA stack's own frame-building code
        (`skb->data[1] = I_FRAME | (self->vs << 1); ... |= (self->vr << 5)`)
        and against `tshark`'s `irlap` dissector's own `irlap.c.n_s`/
        `irlap.c.n_r` field masks on a synthesized frame (see the
        Wireshark cross-validation test in `tests/test_sigrok_roundtrip.
        py`)."""

        if not (0 <= ns <= 7):
            raise ValueError(f"ns {ns} does not fit in 3 bits (0-7)")
        if not (0 <= nr <= 7):
            raise ValueError(f"nr {nr} does not fit in 3 bits (0-7)")
        control = (ns << 1) | (nr << 5)
        return self.send_frame(
            builder, address=address, control=control, info=info, datatype=datatype,
            command=command, final=final, driver=driver,
        )

    def send_xid(
        self,
        builder: CaptureBuilder,
        *,
        address: int = 0x7F,
        source_address: int,
        dest_address: int = 0xFFFFFFFF,
        discovery_flags: int = 0,
        slot: int = 0xFF,
        version: int = 0x00,
        command: bool = True,
        final: bool = True,
        driver: str | None = None,
    ) -> FrameHandle:
        """One IrLAP XID (eXchange station IDentification) command frame —
        broadcast (`address` defaults to 0x7F, the broadcast connection
        address) during discovery so every station in range replies with
        its own device address/service hints. Information field: Format
        Identifier (1 byte, `_XID_FORMAT`), Source/Destination Device
        Address (4 bytes each, little-endian — `dest_address` stays the
        broadcast `0xFFFFFFFF` for a discovery command), Discovery Flags
        (1 byte), Slot Number (1 byte, `0xFF` = final slot), Version
        Number (1 byte). Field order/sizes/endianness all confirmed
        empirically against `tshark`'s real `irlap` dissector (see the
        Wireshark cross-validation test in `tests/test_sigrok_roundtrip.
        py`): a frame built exactly this way decodes into
        `irlap.xid.fi`/`saddr`/`daddr`/`flags`/`slotnr`/`version` with
        precisely the values passed in here."""

        for name, value in (
            ("source_address", source_address), ("dest_address", dest_address),
        ):
            if not (0 <= value <= 0xFFFFFFFF):
                raise ValueError(f"{name} {value} does not fit in 32 bits")
        for name, value in (
            ("discovery_flags", discovery_flags), ("slot", slot), ("version", version),
        ):
            if not (0 <= value <= 0xFF):
                raise ValueError(f"{name} {value} does not fit in 8 bits")

        info = list(struct.pack("<BIIBBB", _XID_FORMAT, source_address, dest_address, discovery_flags, slot, version))
        return self.send_frame(
            builder, address=address, control=_XID_CMD, info=info, datatype="bytes",
            command=command, final=final, driver=driver,
        )
