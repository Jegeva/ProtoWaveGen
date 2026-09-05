"""USB DFU (Device Firmware Upgrade, USB DFU 1.1) class requests, stacked on
`UsbBus`. Scope is deliberately narrow, mirroring `spiflash.py`/
`jedec_cfi.py`'s own "don't build more than the scoped mode" precedent: no
vendor DFU extensions (ST's DfuSe, etc.), no runtime-vs-DFU-mode descriptor
switching modeled (this tool synthesizes a diagram of the DFU control
transfers themselves, not a full enumeration state machine), and every
transfer is the single-attempt, everything-ACKs happy path
`UsbBus.control_transfer` already provides (same convention `CanBus.send`
uses for not modeling bus contention).

DFU is entirely control-transfer-based -- no bulk/interrupt endpoints at
all, the simplest of this project's USB stacked protocols in that respect
-- so every operation here goes through `UsbBus.control_transfer` exclusively,
never the raw `token`/`data_packet`/`handshake` primitives directly.

Implements the four class requests needed to drive a real DFU download/
status-poll sequence: `DFU_DNLOAD` (bRequest=1), `DFU_UPLOAD` (bRequest=2),
`DFU_GETSTATUS` (bRequest=3), `DFU_ABORT` (bRequest=6). Not implemented:
DFU_DETACH, DFU_CLRSTATUS, DFU_GETSTATE (all rare in a synthesized-timing-
diagram use case; trivial to add later following the same pattern here).

bmRequestType per USB DFU 1.1 spec section 3: `0x21` (host-to-device,
class, interface recipient) for the OUT-direction requests (DNLOAD, ABORT),
`0xA1` (device-to-host, class, interface) for the IN-direction ones
(UPLOAD, GETSTATUS). wValue/wIndex/wLength are little-endian 16-bit fields
in the 8-byte SETUP packet, same convention `usb.py`'s own
`control_transfer` docstring shows.

Example flow (a real-ish DFU download, matching the demo `operations` list
in `examples/usb_dfu_basic.json`):

    dnload(block_num=0, data=<firmware chunk>)      # host sends a block
    get_status(status=0, state=DFU_DNBUSY)          # device busy programming
    get_status(status=0, state=DFU_DNLOAD_IDLE)     # device ready for more
    dnload(block_num=0, data=[])                    # empty DNLOAD: download complete
    get_status(status=0, state=DFU_MANIFEST)        # device manifesting new firmware
    get_status(status=0, state=DFU_IDLE)            # device back to idle

The empty `dnload(block_num=0, data=[])` call is the real DFU wire signal
for "no more data" (USB DFU 1.1 section 6.1.3) -- it MUST still produce a
genuine zero-length OUT DATA1 packet, not a skipped Data stage. This is why
`dnload` always passes `out_data=values` (a real, possibly-empty list) to
`control_transfer`, never `out_data=None`: `control_transfer`'s own
`if out_data is not None:` branch (see `usb.py`) is what actually emits
that OUT token + zero-length DATA1 + ACK; passing `None` instead would fall
into the *different* zero-length-data-stage-request shape (no Data stage at
all, just an IN-direction Status stage) that real DFU hardware does not use
for this call.
"""

from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .payload import decode_payload
from .usb import UsbBus

# -- DFU 1.1 state machine (bState, USB DFU 1.1 spec table A.1) -------------
# Python identifiers can't have hyphens -- real states with a hyphen in
# their name use underscores instead (e.g. dfuDNLOAD-SYNC -> DFU_DNLOAD_SYNC).
DFU_APP_IDLE = 0
DFU_APP_DETACH = 1
DFU_IDLE = 2
DFU_DNLOAD_SYNC = 3
DFU_DNBUSY = 4
DFU_DNLOAD_IDLE = 5
DFU_MANIFEST_SYNC = 6
DFU_MANIFEST = 7
DFU_MANIFEST_WAIT_RESET = 8
DFU_UPLOAD_IDLE = 9
DFU_ERROR = 10

_STATE_NAMES = {
    DFU_APP_IDLE: "appIDLE",
    DFU_APP_DETACH: "appDETACH",
    DFU_IDLE: "dfuIDLE",
    DFU_DNLOAD_SYNC: "dfuDNLOAD-SYNC",
    DFU_DNBUSY: "dfuDNBUSY",
    DFU_DNLOAD_IDLE: "dfuDNLOAD-IDLE",
    DFU_MANIFEST_SYNC: "dfuMANIFEST-SYNC",
    DFU_MANIFEST: "dfuMANIFEST",
    DFU_MANIFEST_WAIT_RESET: "dfuMANIFEST-WAIT-RESET",
    DFU_UPLOAD_IDLE: "dfuUPLOAD-IDLE",
    DFU_ERROR: "dfuERROR",
}

# -- DFU class requests (USB DFU 1.1 spec table 3.2) -------------------------
_DFU_DNLOAD = 1
_DFU_UPLOAD = 2
_DFU_GETSTATUS = 3
_DFU_ABORT = 6

# bmRequestType: class request, interface recipient, direction bit (0x80).
_BM_HOST_TO_DEVICE = 0x21
_BM_DEVICE_TO_HOST = 0xA1


def _setup_packet(bm_request_type: int, b_request: int, w_value: int, w_index: int, w_length: int) -> list[int]:
    """The 8-byte SETUP stage payload `control_transfer` requires: bmRequestType,
    bRequest, wValue (LE), wIndex (LE), wLength (LE)."""

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


@register_protocol("usb_dfu")
class UsbDfu(StackedProtocol):
    """USB DFU 1.1 class requests, stacked on `UsbBus` (`transport`). Every
    operation issues exactly one control transfer via
    `UsbBus.control_transfer` and wraps it in `builder.frame()` for a
    summary `field` annotation naming the request and its key fields."""

    def __init__(self, node_id: str, transport: UsbBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    def _dp_dm(self) -> tuple[str, str]:
        return self.transport.sig("dp"), self.transport.sig("dm")

    def dnload(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        interface: int = 0,
        block_num: int,
        data,
        datatype: str = "bytes",
    ) -> FrameHandle:
        """`DFU_DNLOAD` (bRequest=1): host sends one firmware block.
        `block_num=0, data=[]` is the real end-of-download signal (see
        module docstring) -- `data=[]` (an empty list, not omitted) still
        produces a genuine zero-length OUT DATA1 packet."""

        values = decode_payload(data, datatype)
        setup = _setup_packet(_BM_HOST_TO_DEVICE, _DFU_DNLOAD, block_num, interface, len(values))

        with builder.frame() as fh:
            # `out_data=values` -- a real list, even when empty -- not
            # `None`: see module docstring for why this distinction matters.
            self.transport.control_transfer(builder, address=address, endpoint=0, setup_data=setup, out_data=values)
        dp, dm = self._dp_dm()
        builder.annotate(
            "field", f"DFU DNLOAD block={block_num} len={len(values)}", start=fh.start, end=fh.end, signals=(dp, dm)
        )
        return fh

    def upload(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        interface: int = 0,
        block_num: int,
        data,
        datatype: str = "bytes",
    ) -> FrameHandle:
        """`DFU_UPLOAD` (bRequest=2): device sends one firmware block back
        (read-back). `data` is the synthesized block content -- this tool
        generates diagrams, it doesn't sense real device firmware."""

        values = decode_payload(data, datatype)
        setup = _setup_packet(_BM_DEVICE_TO_HOST, _DFU_UPLOAD, block_num, interface, len(values))

        with builder.frame() as fh:
            self.transport.control_transfer(builder, address=address, endpoint=0, setup_data=setup, in_data=values)
        dp, dm = self._dp_dm()
        builder.annotate(
            "field", f"DFU UPLOAD block={block_num} len={len(values)}", start=fh.start, end=fh.end, signals=(dp, dm)
        )
        return fh

    def get_status(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        interface: int = 0,
        status: int,
        poll_timeout_ms: int = 0,
        state: int,
    ) -> FrameHandle:
        """`DFU_GETSTATUS` (bRequest=3): device reports its 6-byte status
        block -- bStatus, bwPollTimeout (3 bytes LE), bState, iString (always
        0, no string descriptor modeled)."""

        status_bytes = [
            status & 0xFF,
            poll_timeout_ms & 0xFF,
            (poll_timeout_ms >> 8) & 0xFF,
            (poll_timeout_ms >> 16) & 0xFF,
            state & 0xFF,
            0,  # iString
        ]
        setup = _setup_packet(_BM_DEVICE_TO_HOST, _DFU_GETSTATUS, 0, interface, len(status_bytes))

        with builder.frame() as fh:
            self.transport.control_transfer(
                builder, address=address, endpoint=0, setup_data=setup, in_data=status_bytes
            )
        state_name = _STATE_NAMES.get(state, f"unknown({state})")
        dp, dm = self._dp_dm()
        builder.annotate(
            "field", f"DFU GETSTATUS status={status} state={state_name}", start=fh.start, end=fh.end, signals=(dp, dm)
        )
        return fh

    def abort(self, builder: CaptureBuilder, *, address: int, interface: int = 0) -> FrameHandle:
        """`DFU_ABORT` (bRequest=6): zero-length-data-stage request (neither
        `in_data` nor `out_data`) -- returns the device to dfuIDLE."""

        setup = _setup_packet(_BM_HOST_TO_DEVICE, _DFU_ABORT, 0, interface, 0)

        with builder.frame() as fh:
            self.transport.control_transfer(builder, address=address, endpoint=0, setup_data=setup)
        dp, dm = self._dp_dm()
        builder.annotate("field", "DFU ABORT", start=fh.start, end=fh.end, signals=(dp, dm))
        return fh
