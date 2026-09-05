"""USB Full-Speed (12 Mbit/s) transport: raw electrical/packet layer only.

Scope is deliberately narrow (v1, control transfers): SYNC field, PID
(+complement), NRZI encoding, 6-consecutive-1s bit-stuffing, CRC5 (token
packets)/CRC16 (data packets), EOP, and enough packet-level plumbing
(`token`/`data_packet`/`handshake`/`control_transfer`) to synthesize a
full SETUP/DATA/STATUS control transfer. No application-layer device
modeling: HID/CDC/Mass-Storage/DFU are a separate, later `StackedProtocol`
built on top of this the way `LinBus` stacks on `UartTransport` -- this
class's job is to be a usable, independently-testable transport for that
future work, the way `I2CBus.write_then_read()` already is for its own
stacked devices. Out of scope entirely: isochronous/interrupt-specific PID
nuances beyond what's already exposed here, SPLIT packets (a hub sitting
between a full/high-speed host and a low-speed device), and High-Speed
signaling (chirp, 480 Mbit/s) -- Full-Speed only.

Electrical facts below were cross-checked three ways this session: the
USB 2.0 spec's own description (chapters 7-8), a live web search run
during implementation (bit-stuffing-before-NRZI ordering, and the exact
CRC5 bit-order/residual convention), and -- the strongest check, since
it's what actually has to decode this project's output -- reading sigrok's
own independently-implemented decoder stack in full:
`/usr/share/libsigrokdecode/decoders/usb_signalling/pd.py`,
`usb_packet/pd.py`, `usb_request/pd.py`.

- **NRZI + bit-stuffing** (`_usb_nrzi.py`): stuff first, NRZI-encode the
  stuffed result second. Confirmed by web search this session, matching
  `usb_signalling/pd.py`'s own docstring ("Data transmitted on the USB is
  encoded with NRZI... If 6 ones are transmitted consecutively, a zero is
  inserted"). Threshold is 6 consecutive *1* bits specifically (not 5,
  not either polarity like CAN's own bit-stuffing in `can.py`).
- **J/K polarity is Full-Speed-specific and does NOT transfer to
  Low-Speed** -- confirmed directly from `usb_signalling/pd.py`'s
  `symbols` table: Full-Speed has `(dp=1,dm=0)=J`, `(dp=0,dm=1)=K`;
  Low-Speed has the *opposite* mapping (`(dp=1,dm=0)=K`,
  `(dp=0,dm=1)=J`). This module hardcodes the Full-Speed convention only
  (see `_STATE_TO_DPDM`) -- a future Low-Speed mode must not reuse it
  as-is.
- **SYNC field**: fixed byte `0x80`, sent LSB-first -- logical bits
  `0,0,0,0,0,0,0,1` -- which NRZI-encodes to the well-known "KJKJKJKK"
  line pattern real receivers lock onto. Verified against
  `usb_packet/pd.py`'s own `handle_packet()`, which literally asserts
  `sync == '00000001'` on the destuffed bit string it decodes.
- **PID byte**: 4-bit type nibble in the byte's low nibble, its bitwise
  complement in the high nibble, whole byte sent LSB-first (`_pid_byte`).
  The four type-nibble constants this module needs (`_TOKEN_PIDS`/
  `_DATA_PIDS`/`_HANDSHAKE_PIDS`) are the conventional MSB-first values
  from USB 2.0 Table 8-1 (e.g. SETUP = 0b1101) -- verified by hand against
  `usb_packet/pd.py`'s `pids` dict, whose keys are the full 8-bit
  *wire-order* (LSB-first) string: encoding SETUP's type nibble 0b1101
  this way produces exactly `pids`' `'10110100': ['SETUP', ...]` entry.
- **CRC5** (token packets, covering the 11-bit ADDR+EP field): polynomial
  x^5+x^2+1 (0x05, seed 0x1F, all-ones). Confirmed by web search this
  session (USB-IF's own CRC application note): "the CRC is the ones
  complement of the remainder after division of the 11-bit address/
  endpoint field, LSB first, by the generating polynomial... the CRC is
  sent MSb to LSb." That is: feed the 11 field bits in their own wire
  order (LSB-of-each-field-first, i.e. address then endpoint, each
  transmitted LSB-first) into a standard bit-serial CRC register seeded
  to all-ones, complement the final register, then transmit *that*
  register's bits MSB-first. `_usb_crc()` below is a direct
  transliteration of `usb_packet/pd.py`'s own `calc_crc5`/`calc_crc16`
  (same seed/complement/compare-top-bit shape); working through that
  decoder's own bit-reversal-then-parse-as-int comparison algebraically
  shows the two reversals cancel, landing on exactly the "transmit the
  raw pre-reversal register MSB-first" rule stated above and confirmed
  independently by the web search.
- **CRC16** (data packets, covering the payload bytes): polynomial
  x^16+x^15+x^2+1 (0x8005, seed 0xFFFF, all-ones), same shape as CRC5
  (bit-serial, LSB-first input per byte, final register complemented and
  sent MSB-first) -- `_usb_crc()` is shared between both widths.
- **EOP**: SE0 (`dp=dm=0`) held for 2 bit times, then J for 1 bit time,
  per `usb_signalling/pd.py`'s own docstring ("SE0 for >= 1 bittime
  (usually 2 bittimes), then J").
- Handshake packets (ACK/NAK/STALL) carry only SYNC+PID+EOP -- no CRC --
  confirmed by `usb_packet/pd.py`'s `handle_packet()`, whose ACK/NAK/
  STALL/NYET branch is a bare `pass` ("Nothing to do, these only have
  SYNC+PID+EOP fields").

A mandatory minimum inter-packet idle gap is built into `_send_packet`
itself (not left to callers) after every packet's EOP -- the same lesson
`SpiBus.transfer`/`MicrowireBus.transfer` already learned the hard way
(see CLAUDE.md): two packets emitted back-to-back with zero gap would
leave sigrok's edge-based `usb_signalling` decoder unable to distinguish
one packet's final EOP edge from the next packet's SOP-triggering edge.
"""

from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from ._usb_nrzi import nrzi_encode, stuff_bits
from .base import TransportProtocol, bind_clock_samples, bits_of_byte, format_byte, register_protocol
from .payload import decode_payload_with_floating, group_floating_by_byte

_BITRATE_HZ = 12_000_000  # USB Full-Speed. Not configurable in this v1 -- Low-/High-Speed are out of scope.
_SYNC_BYTE = 0x80
_EOP_SE0_BITS = 2
_EOP_J_BITS = 1
_MIN_INTERPACKET_IDLE_BITS = 2

# PID type nibbles: conventional MSB-first 4-bit values from USB 2.0 Table
# 8-1 (see module docstring for how these map onto sigrok's own wire-order
# PID table). SOF/PRE/SPLIT/PING are out of v1's control-transfer-only
# scope; the dicts below only carry what control_transfer() needs.
_TOKEN_PIDS = {"OUT": 0b0001, "IN": 0b1001, "SETUP": 0b1101}
_DATA_PIDS = {"DATA0": 0b0011, "DATA1": 0b1011}
_HANDSHAKE_PIDS = {"ACK": 0b0010, "NAK": 0b1010, "STALL": 0b1110}

# Full-Speed line-state mapping (USB 2.0 spec 7.1.7.1 / sigrok
# usb_signalling's own `symbols['full-speed']` table): 1 = J (idle), 0 = K.
# Low-Speed's J/K assignment is the OPPOSITE of this -- see module
# docstring -- so this table must never be reused verbatim for a future
# Low-Speed mode.
_STATE_TO_DPDM = {1: (1, 0), 0: (0, 1)}


def _bits_lsb_first(value: int, width: int) -> list[int]:
    """`value`'s bits, LSB first -- the wire order for USB's ADDR/EP/
    frame-number fields (distinct from `base.py`'s `bits_of_byte`, which
    is fixed at 8 bits; USB's token fields are 7 and 4 bits wide)."""

    return [(value >> i) & 1 for i in range(width)]


def _pid_byte(pid_type: int) -> int:
    """4-bit type nibble in the low nibble, its bitwise complement in the
    high nibble -- see module docstring."""

    return (pid_type & 0xF) | ((~pid_type & 0xF) << 4)


def _usb_crc(bits: list[int], poly: int, width: int) -> list[int]:
    """Bit-serial CRC shared by CRC5 (`width=5, poly=0x05`) and CRC16
    (`width=16, poly=0x8005`) -- see module docstring for the derivation
    and the sigrok source this was checked against. `bits` must already be
    in wire order (LSB-first per field/byte, fields/bytes concatenated in
    transmission order). Returns the final register's bits **MSB-first**
    -- that is already wire/transmission order for the CRC field itself,
    ready to append directly onto the logical bitstream."""

    mask = (1 << width) - 1
    reg = mask  # seed: all-ones
    for bit in bits:
        reg <<= 1
        top = (reg >> width) & 1
        if bit != top:
            reg ^= poly
        reg &= mask
    reg ^= mask  # final complement
    return [(reg >> i) & 1 for i in reversed(range(width))]


def _crc5(bits11: list[int]) -> list[int]:
    return _usb_crc(bits11, poly=0x05, width=5)


def _crc16(bits: list[int]) -> list[int]:
    return _usb_crc(bits, poly=0x8005, width=16)


@register_protocol("usb")
class UsbBus(TransportProtocol):
    """USB Full-Speed control-transfer transport. Single pair of plain
    `DIGITAL` signals, `dp`/`dm` -- no `TRISTATE`/open-drain modeling
    needed (this is a push-pull differential pair, not a shared open-drain
    bus like I2C/1-Wire), so `decode_payload_with_floating(...,
    tristate=False)` is used for every payload field: `z`/`Z` floating
    markers have no defined pull here and always need `l`/`h` used
    explicitly, matching CAN's own `data` field.

    `token`/`data_packet`/`handshake` are the reusable per-packet
    primitives (each a `TransportProtocol` operation in its own right, so
    a future stacked protocol needing bulk/interrupt transactions --
    outside this class's own v1 scope -- can call them directly instead of
    going through `control_transfer`, the same way `I2CBus.write()`/
    `.read()` stay independently callable alongside its own
    `write_then_read()` convenience method). `control_transfer` composes
    all three into a full 3-stage (Setup/Data/Status) control transfer.
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self._bit_samples: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        self._bit_samples = bind_clock_samples(samplerate, _BITRATE_HZ, hz_label="bitrate", divisor=1)

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._bit_samples is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return self._bit_samples

    def get_signals(self) -> list[Signal]:
        # Full-Speed idle is J: dp=1, dm=0 (see module docstring -- do NOT
        # reuse for a future Low-Speed mode, whose idle J is the opposite
        # dp/dm pairing).
        return [Signal(self.sig("dp"), initial_level=1), Signal(self.sig("dm"), initial_level=0)]

    # -- packet primitives ---------------------------------------------

    def token(
        self, builder: CaptureBuilder, *, pid: str, address: int, endpoint: int, driver: str = "host"
    ) -> FrameHandle:
        """OUT/IN/SETUP token: SYNC + PID + 7-bit ADDR + 4-bit EP + CRC5 +
        EOP. Tokens are always host-originated in real USB (there's no
        other party on the bus allowed to initiate one), hence the
        `driver="host"` default -- overridable only for exotic test
        scenarios."""

        if pid not in _TOKEN_PIDS:
            raise ValueError(f"token pid must be one of {sorted(_TOKEN_PIDS)}, got {pid!r}")
        if not (0 <= address <= 127):
            raise ValueError(f"USB device address must be 0-127, got {address}")
        if not (0 <= endpoint <= 15):
            raise ValueError(f"USB endpoint must be 0-15, got {endpoint}")
        self._ensure_bound(builder)

        field_bits = _bits_lsb_first(address, 7) + _bits_lsb_first(endpoint, 4)
        return self._send_packet(
            builder,
            pid_type=_TOKEN_PIDS[pid],
            pid_name=pid,
            field_bits=field_bits,
            field_roles=["addrep"] * 11,
            field_floating=[False] * 11,
            crc_width=5,
            driver=driver,
            summary_label=f"{pid} ADDR={address} EP={endpoint}",
            summary_data={"address": address, "endpoint": endpoint},
        )

    def data_packet(
        self,
        builder: CaptureBuilder,
        *,
        pid: str,
        data=None,
        datatype: str = "bytes",
        driver: str,
    ) -> FrameHandle:
        """DATA0/DATA1: SYNC + PID + 0-1024 payload bytes + CRC16 + EOP.
        `driver` has no sensible default (device drives an IN transfer's
        data, host drives an OUT transfer's or SETUP's) -- callers must
        say which."""

        if pid not in _DATA_PIDS:
            raise ValueError(f"data_packet pid must be one of {sorted(_DATA_PIDS)}, got {pid!r}")
        self._ensure_bound(builder)

        payload = decode_payload_with_floating(data if data is not None else [], datatype, tristate=False)
        values = payload.values
        if len(values) > 1024:
            raise ValueError(f"USB data payload is at most 1024 bytes, got {len(values)}")
        floating_by_byte = group_floating_by_byte(payload.floating)

        field_bits: list[int] = []
        field_roles: list[str] = []
        field_floating: list[bool] = []
        for byte_index, byte in enumerate(values):
            byte_floating = floating_by_byte.get(byte_index, frozenset())
            for bit_index in range(8):
                field_bits.append((byte >> bit_index) & 1)  # LSB first
                field_roles.append(f"data{byte_index}")
                # `bit_index` is the shift amount; FloatingSpan.bit_index is
                # MSB-first (0=MSB) -- same 7-i conversion base.py's
                # bits_of_byte docstring calls out for LSB-order callers.
                field_floating.append((7 - bit_index) in byte_floating)

        return self._send_packet(
            builder,
            pid_type=_DATA_PIDS[pid],
            pid_name=pid,
            field_bits=field_bits,
            field_roles=field_roles,
            field_floating=field_floating,
            crc_width=16,
            driver=driver,
            data_bytes=values,
        )

    def handshake(self, builder: CaptureBuilder, *, pid: str, driver: str) -> FrameHandle:
        """ACK/NAK/STALL: SYNC + PID + EOP only -- no CRC field at all
        (confirmed from sigrok's `usb_packet` decoder source, see module
        docstring)."""

        if pid not in _HANDSHAKE_PIDS:
            raise ValueError(f"handshake pid must be one of {sorted(_HANDSHAKE_PIDS)}, got {pid!r}")
        self._ensure_bound(builder)

        return self._send_packet(
            builder,
            pid_type=_HANDSHAKE_PIDS[pid],
            pid_name=pid,
            field_bits=[],
            field_roles=[],
            field_floating=[],
            crc_width=None,
            driver=driver,
        )

    # -- control transfer -------------------------------------------------

    def control_transfer(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint: int,
        setup_data,
        setup_data_datatype: str = "bytes",
        in_data=None,
        in_data_datatype: str = "bytes",
        out_data=None,
        out_data_datatype: str = "bytes",
    ) -> FrameHandle:
        """Full 3-stage control transfer (USB 2.0 spec 8.5.3): Setup stage
        (SETUP token + DATA0 + ACK), an optional Data stage (IN or OUT
        token + DATA1 + handshake -- at most one of `in_data`/`out_data`
        may be given; neither means a zero-length-data-stage request like
        SET_ADDRESS/SET_CONFIGURATION), then the Status stage (a
        zero-length DATA1 in the *opposite* direction from the Data
        stage -- or IN when there was no Data stage at all, matching every
        real zero-length-data control request -- plus its handshake).

        Real control transfers can NAK/retry the Data or Status stage
        indefinitely; this generates the single-attempt, everything-ACKs
        happy path (matching this codebase's existing convention -- e.g.
        `CanBus.send` doesn't model bus arbitration contention either).
        Bulk/interrupt transactions (no Setup/Status stages, no data
        toggle bookkeeping across multiple transfers) are out of this
        method's scope but reachable by calling `token`/`data_packet`/
        `handshake` directly.
        """

        if in_data is not None and out_data is not None:
            raise ValueError("control_transfer: pass in_data or out_data (or neither), not both")
        self._ensure_bound(builder)

        setup_payload = decode_payload_with_floating(setup_data, setup_data_datatype, tristate=False)
        if len(setup_payload.values) != 8:
            raise ValueError(
                f"SETUP stage payload must be exactly 8 bytes (bmRequestType, bRequest, wValue, "
                f"wIndex, wLength), got {len(setup_payload.values)}"
            )

        start = builder.cursor
        self.token(builder, pid="SETUP", address=address, endpoint=endpoint)
        self.data_packet(builder, pid="DATA0", data=setup_payload.values, driver="host")
        self.handshake(builder, pid="ACK", driver="device")

        if out_data is not None:
            self.token(builder, pid="OUT", address=address, endpoint=endpoint)
            self.data_packet(builder, pid="DATA1", data=out_data, datatype=out_data_datatype, driver="host")
            self.handshake(builder, pid="ACK", driver="device")
            status_direction = "IN"
        elif in_data is not None:
            self.token(builder, pid="IN", address=address, endpoint=endpoint)
            self.data_packet(builder, pid="DATA1", data=in_data, datatype=in_data_datatype, driver="device")
            self.handshake(builder, pid="ACK", driver="host")
            status_direction = "OUT"
        else:
            status_direction = "IN"  # zero-length-data-stage request (e.g. SET_ADDRESS)

        if status_direction == "IN":
            self.token(builder, pid="IN", address=address, endpoint=endpoint)
            self.data_packet(builder, pid="DATA1", data=[], driver="device")
            self.handshake(builder, pid="ACK", driver="host")
        else:
            self.token(builder, pid="OUT", address=address, endpoint=endpoint)
            self.data_packet(builder, pid="DATA1", data=[], driver="host")
            self.handshake(builder, pid="ACK", driver="device")
        end = builder.cursor

        return FrameHandle(start=start, end=end)

    # -- shared packet assembly -------------------------------------------

    def _send_packet(
        self,
        builder: CaptureBuilder,
        *,
        pid_type: int,
        pid_name: str,
        field_bits: list[int],
        field_roles: list[str],
        field_floating: list[bool],
        crc_width: int | None,
        driver: str,
        summary_label: str | None = None,
        summary_data: dict | None = None,
        data_bytes: list[int] | None = None,
    ) -> FrameHandle:
        dp, dm = self.sig("dp"), self.sig("dm")

        sync_bits = bits_of_byte(_SYNC_BYTE, order="lsb")
        pid_bits = bits_of_byte(_pid_byte(pid_type), order="lsb")

        logical_bits = sync_bits + pid_bits + field_bits
        logical_roles = ["sync"] * 8 + ["pid"] * 8 + field_roles
        logical_floating = [False] * 16 + field_floating

        if crc_width == 5:
            crc_bits = _crc5(field_bits)
            logical_bits, logical_roles, logical_floating = (
                logical_bits + crc_bits,
                logical_roles + ["crc5"] * 5,
                logical_floating + [False] * 5,
            )
        elif crc_width == 16:
            crc_bits = _crc16(field_bits)
            logical_bits, logical_roles, logical_floating = (
                logical_bits + crc_bits,
                logical_roles + ["crc16"] * 16,
                logical_floating + [False] * 16,
            )

        stuffed_bits, stuffed_roles, _stuffed_floating = stuff_bits(logical_bits, logical_roles, logical_floating)
        states = nrzi_encode(stuffed_bits, initial_state=1)  # 1 = J, matches idle

        start = builder.cursor
        current_role: str | None = None
        role_start = start
        for state, role in zip(states, stuffed_roles):
            if role != current_role:
                self._annotate_role(
                    builder, dp, dm, current_role, role_start, builder.cursor,
                    pid_name, summary_label, summary_data, data_bytes,
                )
                current_role, role_start = role, builder.cursor
            level_dp, level_dm = _STATE_TO_DPDM[state]
            builder.set_level(dp, level_dp)
            builder.set_level(dm, level_dm)
            builder.advance(self._bit_samples)
        self._annotate_role(
            builder, dp, dm, current_role, role_start, builder.cursor,
            pid_name, summary_label, summary_data, data_bytes,
        )

        # EOP: SE0 for 2 bit times, then J for 1 bit time (see module docstring).
        builder.set_level(dp, 0)
        builder.set_level(dm, 0)
        builder.advance(self._bit_samples * _EOP_SE0_BITS)
        builder.set_level(dp, 1)
        builder.set_level(dm, 0)
        builder.advance(self._bit_samples * _EOP_J_BITS)
        end = builder.cursor

        builder.annotate("driver", driver, start=start, end=end, signals=(dp, dm))
        builder.annotate("bitorder", "lsb", start=start, end=end, signals=(dp, dm))

        # Mandatory minimum inter-packet idle gap, built into every packet
        # rather than left to callers -- see module docstring's closing
        # paragraph (same lesson as SpiBus.transfer's CS-gap fix).
        builder.advance(self._bit_samples * _MIN_INTERPACKET_IDLE_BITS)

        return FrameHandle(start=start, end=end)

    def _annotate_role(
        self,
        builder: CaptureBuilder,
        dp: str,
        dm: str,
        role: str | None,
        start: int,
        end: int,
        pid_name: str,
        summary_label: str | None,
        summary_data: dict | None,
        data_bytes: list[int] | None,
    ) -> None:
        """Bracket a `field` annotation around one logical role's span --
        mirrors `CanBus._annotate_role`. `pid`/`addrep`/`dataN` spans never
        overlap each other, so (per the pattern `format_byte()`'s own
        docstring documents) there's no risk of one annotation painting
        over another."""

        if role is None or start == end:
            return
        if role == "pid":
            builder.annotate("field", pid_name, start=start, end=end, signals=(dp, dm))
        elif role == "addrep":
            builder.annotate(
                "field", summary_label, start=start, end=end, signals=(dp, dm), **(summary_data or {})
            )
        elif role.startswith("data"):
            byte = data_bytes[int(role[len("data") :])]
            builder.annotate("unit", "byte", start=start, end=end, signals=(dp, dm))
            builder.annotate("field", format_byte(byte), start=start, end=end, signals=(dp, dm), value=byte)
