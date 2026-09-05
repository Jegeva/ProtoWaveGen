"""USB HID (Human Interface Device), stacked on `UsbBus`.

Scope is deliberately narrow (mirrors `spiflash.py`/`rtc8564.py`'s
precedent of a real-but-narrow device subset, not full HID spec
coverage): a fixed 3-byte relative-mouse report (buttons bitmap, signed X,
signed Y), plus `GET_DESCRIPTOR` for the HID and REPORT descriptor types
via `UsbBus.control_transfer`. No report-descriptor *parsing*, no multiple
report IDs, no OUT reports / SET_IDLE / SET_PROTOCOL / SET_REPORT, no boot
protocol negotiation.

No mainline sigrok decoder exists for USB HID (confirmed: only
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` ship
under `/usr/share/libsigrokdecode/decoders/`) -- validated instead by a
self-authored decoder, single-oracle tier (see
`tests/custom_decoders/usb_hid/pd.py` and CLAUDE.md's oracle-tier table).

`send_report`'s DATA0/DATA1 toggle is tracked per instance -- `UsbBus`
itself has no notion of toggle state, the same division of responsibility
`I2CDevice`/`OneWireDevice` already use for their own addressing state on
top of a shared transport. Starts at DATA0, flips on every `send_report`
call (real USB alternates per successful transaction; this generates the
single-attempt happy path, matching `UsbBus.control_transfer`'s own
documented convention of not modeling NAK/retry).
"""

from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .usb import UsbBus

_HID_DESCRIPTOR_TYPE = 0x21
_REPORT_DESCRIPTOR_TYPE = 0x22
_GET_DESCRIPTOR = 0x06

# A short, fixed HID report descriptor for a 3-byte relative mouse report
# (buttons bitmap + signed X + signed Y). Not meant to be exhaustive -- just
# realistic enough bytes to round-trip through GET_DESCRIPTOR(REPORT), the
# same "real but narrow" precedent `spiflash.py`/`rtc8564.py` set.
REPORT_DESCRIPTOR = bytes([
    0x05, 0x01,  # Usage Page (Generic Desktop)
    0x09, 0x02,  # Usage (Mouse)
    0xA1, 0x01,  # Collection (Application)
    0x09, 0x01,  #   Usage (Pointer)
    0xA1, 0x00,  #   Collection (Physical)
    0x05, 0x09,  #     Usage Page (Button)
    0x19, 0x01,  #     Usage Minimum (Button 1)
    0x29, 0x03,  #     Usage Maximum (Button 3)
    0x15, 0x00,  #     Logical Minimum (0)
    0x25, 0x01,  #     Logical Maximum (1)
    0x95, 0x03,  #     Report Count (3)
    0x75, 0x01,  #     Report Size (1)
    0x81, 0x02,  #     Input (Data,Var,Abs)
    0x95, 0x01,  #     Report Count (1)
    0x75, 0x05,  #     Report Size (5)
    0x81, 0x01,  #     Input (Const,Array,Abs) -- padding
    0x05, 0x01,  #     Usage Page (Generic Desktop)
    0x09, 0x30,  #     Usage (X)
    0x09, 0x31,  #     Usage (Y)
    0x15, 0x81,  #     Logical Minimum (-127)
    0x25, 0x7F,  #     Logical Maximum (127)
    0x75, 0x08,  #     Report Size (8)
    0x95, 0x02,  #     Report Count (2)
    0x81, 0x06,  #     Input (Data,Var,Rel)
    0xC0,        #   End Collection
    0xC0,        # End Collection
])


@register_protocol("usb_hid")
class UsbHid(StackedProtocol):
    """Minimal USB HID device, stacked on `UsbBus`. See module docstring
    for scope."""

    def __init__(self, node_id: str, transport: UsbBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        self._next_data_toggle = "DATA0"

    @staticmethod
    def _setup_packet(
        *, bm_request_type: int, b_request: int, w_value: int, w_index: int, w_length: int
    ) -> list[int]:
        """8-byte SETUP payload, little-endian wValue/wIndex/wLength (USB
        2.0 spec 9.3, Table 9-2)."""

        return [
            bm_request_type & 0xFF,
            b_request & 0xFF,
            w_value & 0xFF, (w_value >> 8) & 0xFF,
            w_index & 0xFF, (w_index >> 8) & 0xFF,
            w_length & 0xFF, (w_length >> 8) & 0xFF,
        ]

    def get_hid_descriptor(self, builder: CaptureBuilder, *, address: int, endpoint: int = 0) -> FrameHandle:
        """GET_DESCRIPTOR(HID): bmRequestType=0x81 (device-to-host,
        standard, interface recipient), bRequest=0x06, wValue=(0x21<<8),
        wIndex=<interface>, wLength=9 (the fixed HID descriptor size)."""

        report_desc_len = len(REPORT_DESCRIPTOR)
        hid_descriptor = [
            9, _HID_DESCRIPTOR_TYPE,
            0x10, 0x01,  # bcdHID = 1.10, LE
            0x00,        # bCountryCode
            0x01,        # bNumDescriptors
            _REPORT_DESCRIPTOR_TYPE,
            report_desc_len & 0xFF, (report_desc_len >> 8) & 0xFF,
        ]
        setup = self._setup_packet(
            bm_request_type=0x81, b_request=_GET_DESCRIPTOR,
            w_value=(_HID_DESCRIPTOR_TYPE << 8), w_index=0, w_length=len(hid_descriptor),
        )
        fh = self.transport.control_transfer(
            builder, address=address, endpoint=endpoint, setup_data=setup, in_data=hid_descriptor,
        )
        self._annotate_summary(builder, fh, "GET_DESCRIPTOR(HID)")
        return fh

    def get_report_descriptor(self, builder: CaptureBuilder, *, address: int, endpoint: int = 0) -> FrameHandle:
        """GET_DESCRIPTOR(REPORT): same shape, descriptor type 0x22,
        returning `REPORT_DESCRIPTOR`'s bytes as the Data-stage payload."""

        setup = self._setup_packet(
            bm_request_type=0x81, b_request=_GET_DESCRIPTOR,
            w_value=(_REPORT_DESCRIPTOR_TYPE << 8), w_index=0, w_length=len(REPORT_DESCRIPTOR),
        )
        fh = self.transport.control_transfer(
            builder, address=address, endpoint=endpoint,
            setup_data=setup, in_data=list(REPORT_DESCRIPTOR),
        )
        self._annotate_summary(builder, fh, "GET_DESCRIPTOR(REPORT)")
        return fh

    def send_report(
        self, builder: CaptureBuilder, *, buttons: int, x: int, y: int, address: int, endpoint: int = 1
    ) -> FrameHandle:
        """Raw interrupt-IN transaction (not a control transfer): IN token
        + DATA0/DATA1 (this instance's own toggle, flipped every call) +
        ACK. `x`/`y` two's-complement-wrap into a byte the same way real
        HID descriptors with a signed 8-bit logical range do."""

        report = [buttons & 0xFF, x & 0xFF, y & 0xFF]
        pid = self._next_data_toggle
        self._next_data_toggle = "DATA1" if pid == "DATA0" else "DATA0"

        with builder.frame() as fh:
            self.transport.token(builder, pid="IN", address=address, endpoint=endpoint)
            self.transport.data_packet(builder, pid=pid, data=report, driver="device")
            self.transport.handshake(builder, pid="ACK", driver="host")
        self._annotate_summary(builder, fh, f"HID report buttons=0x{buttons & 0xFF:02X} x={x} y={y}")
        return fh

    def _annotate_summary(self, builder: CaptureBuilder, fh: FrameHandle, label: str) -> None:
        builder.annotate(
            "field", label, start=fh.start, end=fh.end,
            signals=(self.transport.sig("dp"), self.transport.sig("dm")),
        )
