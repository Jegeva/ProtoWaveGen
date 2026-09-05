"""USB CDC/ACM (virtual serial port class), stacked on `UsbBus`.

Scope is deliberately narrow (real-but-narrow subset, same precedent as
`spiflash.py`/`rtc8564.py`, not full CDC spec coverage): the two class
requests a host uses to configure a virtual serial port
(`SET_LINE_CODING`, `SET_CONTROL_LINE_STATE`) plus outbound bulk data on
the data interface. Out of scope: the notification endpoint (interrupt IN
`SERIAL_STATE` from device to host), `GET_LINE_CODING`, `SEND_BREAK`, and
any multi-interface modeling — CDC's usual two-interface split (one
control, one data) is collapsed here to a single `interface` number
reused for both, since this tool has no notion of a USB configuration
descriptor to assign real interface numbers from.

No mainline sigrok decoder exists for USB CDC (confirmed: only
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` ship), so
this is validated by a custom, self-authored decoder
(`tests/custom_decoders/usb_cdc/pd.py`) — single-oracle tier, per
CLAUDE.md's oracle-tier writeup (a second, independent oracle for a narrow
USB application-layer protocol like this one was already researched and
ruled out as not worth it).

**Deviation from the "wrap in `builder.frame()` for a summary annotation"
pattern used elsewhere (LIN's BREAK, DALI's ADDR/CMD, IR RC-5/NEC/RC-6's
whole-frame label):** those all add their summary on the `"field"` track
because the bits they just emitted carry no annotations of their own.
Here, `UsbBus._send_packet`/`_annotate_role` already densely annotates
`"field"` for every sub-packet (PID name, ADDR/EP, each data byte via
`format_byte()`) inside any operation this class performs — adding
another `"field"` annotation spanning the *whole* multi-packet operation
would be a same-track, overlapping-range annotation on top of those,
exactly the failure mode CLAUDE.md warns about ("two annotations on the
same track covering the same range paint over each other"), just with a
superset range instead of an identical one. Since `UsbBus.data_packet`
also has no `labels=` override hook (unlike `UartTransport.send`/
`SpiBus.transfer`/`I2CBus.write`), there's no way to fold a CDC-level
description into an existing annotation either. Resolution: put the
human-readable operation-level summary on a new `"cdc"` track instead —
exactly what `Annotation.track` is for per this project's own
architecture ("There's no per-concern subclassing; every requirement...
is just another track"). `SVGWriter` renders it as its own lane, with no
risk of colliding with `UsbBus`'s own `"field"`/`"unit"`/`"driver"` rows.
"""

from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .payload import decode_payload
from .usb import UsbBus

# CDC class-specific requests (USB CDC PSTN subclass spec).
_SET_LINE_CODING = 0x20
_SET_CONTROL_LINE_STATE = 0x22
# bmRequestType: host-to-device, class, interface recipient.
_BM_REQUEST_TYPE_HOST_TO_DEVICE_CLASS_INTERFACE = 0x21


def _setup_bytes(bm_request_type: int, b_request: int, w_value: int, w_index: int, w_length: int) -> list[int]:
    """The 8-byte SETUP packet payload, little-endian `wValue`/`wIndex`/
    `wLength` per USB 2.0 spec 9.3."""

    return [
        bm_request_type,
        b_request,
        w_value & 0xFF,
        (w_value >> 8) & 0xFF,
        w_index & 0xFF,
        (w_index >> 8) & 0xFF,
        w_length & 0xFF,
        (w_length >> 8) & 0xFF,
    ]


@register_protocol("usb_cdc")
class UsbCdcAcm(StackedProtocol):
    """USB CDC/ACM stacked on `UsbBus`. `set_line_coding`/
    `set_control_line_state` are control transfers (via
    `UsbBus.control_transfer`); `send_data` is a raw bulk OUT transaction
    (`UsbBus.token`/`data_packet`/`handshake` called directly, since a
    bulk transfer has no SETUP/Status stages) — `UsbBus` itself has no
    notion of DATA0/DATA1 toggle state, so this class tracks its own
    per-instance toggle, flipping it on every `send_data` call, starting
    at DATA0.
    """

    def __init__(self, node_id: str, transport: UsbBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        self._data_toggle = 0

    def set_line_coding(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint: int = 0,
        interface: int = 0,
        baud: int,
        data_bits: int = 8,
        stop_bits: int = 0,
        parity: int = 0,
    ) -> FrameHandle:
        """CDC `SET_LINE_CODING` (bRequest=0x20): a 7-byte Line Coding
        structure (`dwDTERate` LE32, `bCharFormat`, `bParityType`,
        `bDataBits`) as the control transfer's OUT data stage.
        `stop_bits`/`parity` are passed through as their raw CDC codes
        (stop: 0=1, 1=1.5, 2=2; parity: 0=none, 1=odd, 2=even, 3=mark,
        4=space), not validated against that enum here — same "don't
        behaviorally simulate, just carry the bits" precedent `rtc8564.py`
        uses for its VL/century flag bits.
        """

        setup = _setup_bytes(_BM_REQUEST_TYPE_HOST_TO_DEVICE_CLASS_INTERFACE, _SET_LINE_CODING, 0, interface, 7)
        line_coding = [
            baud & 0xFF,
            (baud >> 8) & 0xFF,
            (baud >> 16) & 0xFF,
            (baud >> 24) & 0xFF,
            stop_bits,
            parity,
            data_bits,
        ]
        dp, dm = self.transport.sig("dp"), self.transport.sig("dm")
        with builder.frame() as fh:
            self.transport.control_transfer(
                builder, address=address, endpoint=endpoint, setup_data=setup, out_data=line_coding,
            )
        builder.annotate(
            "cdc",
            f"SET_LINE_CODING baud={baud} bits={data_bits} stopbits={stop_bits} parity={parity}",
            start=fh.start, end=fh.end, signals=(dp, dm),
        )
        return fh

    def set_control_line_state(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint: int = 0,
        interface: int = 0,
        dtr: bool = True,
        rts: bool = True,
    ) -> FrameHandle:
        """CDC `SET_CONTROL_LINE_STATE` (bRequest=0x22): `wValue` bit 0 =
        DTR, bit 1 = RTS, zero-length data stage — matches
        `UsbBus.control_transfer`'s own "neither `in_data` nor `out_data`"
        zero-length-data-stage case (its docstring's SET_ADDRESS example).
        """

        w_value = (1 if dtr else 0) | ((1 if rts else 0) << 1)
        setup = _setup_bytes(
            _BM_REQUEST_TYPE_HOST_TO_DEVICE_CLASS_INTERFACE, _SET_CONTROL_LINE_STATE, w_value, interface, 0,
        )
        dp, dm = self.transport.sig("dp"), self.transport.sig("dm")
        with builder.frame() as fh:
            self.transport.control_transfer(builder, address=address, endpoint=endpoint, setup_data=setup)
        builder.annotate(
            "cdc", f"SET_CONTROL_LINE_STATE DTR={int(dtr)} RTS={int(rts)}",
            start=fh.start, end=fh.end, signals=(dp, dm),
        )
        return fh

    def send_data(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint: int = 2,
        data,
        datatype: str = "bytes",
    ) -> FrameHandle:
        """Bulk OUT data transfer on the data interface's OUT endpoint —
        not a control transfer: a bare OUT token + one DATA packet + ACK,
        via `UsbBus`'s raw packet primitives directly."""

        pid = "DATA0" if self._data_toggle == 0 else "DATA1"
        self._data_toggle ^= 1
        dp, dm = self.transport.sig("dp"), self.transport.sig("dm")
        with builder.frame() as fh:
            self.transport.token(builder, pid="OUT", address=address, endpoint=endpoint)
            self.transport.data_packet(builder, pid=pid, data=data, datatype=datatype, driver="host")
            self.transport.handshake(builder, pid="ACK", driver="device")
        values = decode_payload(data, datatype)
        builder.annotate(
            "cdc", "TX " + " ".join(format_byte(b) for b in values),
            start=fh.start, end=fh.end, signals=(dp, dm),
        )
        return fh
