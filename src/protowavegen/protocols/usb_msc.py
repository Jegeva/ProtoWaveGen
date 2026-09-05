"""USB Mass Storage: Bulk-Only Transport (BOT) + a narrow SCSI command
subset, stacked on `UsbBus`.

Scope deliberately narrow (mirrors `spiflash.py`/`rtc8564.py` precedent):
the BOT CBW -> [data] -> CSW transaction shape and five SCSI-3 commands
(INQUIRY, READ_CAPACITY(10), READ(10), WRITE(10), TEST_UNIT_READY). No
Bulk-Only Mass Storage Reset / Get-Max-LUN control requests, no vendor
commands, no multi-LUN, single fixed 512-byte logical block size (not
configurable) used only to compute READ(10)/WRITE(10)'s "transfer length
in blocks" CDB field. All of this rides on raw bulk `token`/`data_packet`/
`handshake` calls, never `control_transfer()` -- the BOT data path uses
zero control transfers.

Byte-order note (the easiest place in this file to introduce a silent
bug, called out explicitly since it's opposite of the CBW/CSW convention):
CBW/CSW wrapper fields (`dCBWSignature`, `dCBWTag`,
`dCBWDataTransferLength`, `dCSWSignature`, `dCSWTag`, `dCSWDataResidue`)
are little-endian; SCSI CDB fields inside the CBW's `CBWCB` (LBA, transfer
length), and the READ_CAPACITY(10) response, are big-endian. `_le32` is
never used on a CDB/response field; `_be32`/`_be16` are never used on a
wrapper field.

DATA0/DATA1 toggle state is owned entirely by this class -- `UsbBus` has
no notion of it, the same way `I2CDevice`/`OneWireDevice` own their own
addressing state -- tracked per `(address, endpoint)` pair in
`self._toggle`, persisting for the instance's lifetime since Bulk-Only
Mass Storage Reset (which would clear it on real hardware) is out of
scope here.
"""

from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .payload import decode_payload_with_floating
from .usb import UsbBus

_CBW_SIGNATURE = 0x43425355  # "USBC"
_CSW_SIGNATURE = 0x53425355  # "USBS"
_BLOCK_SIZE = 512


def _le32(value: int) -> list[int]:
    return [(value >> (8 * i)) & 0xFF for i in range(4)]


def _be32(value: int) -> list[int]:
    return [(value >> (8 * i)) & 0xFF for i in reversed(range(4))]


def _be16(value: int) -> list[int]:
    return [(value >> 8) & 0xFF, value & 0xFF]


@register_protocol("usb_msc")
class UsbMassStorage(StackedProtocol):
    """USB Mass Storage, Bulk-Only Transport, stacked on `UsbBus`. See
    module docstring for scope."""

    def __init__(self, node_id: str, transport: UsbBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        self._tag = 0
        self._toggle: dict[tuple[int, int], str] = {}

    def _next_toggle(self, address: int, endpoint: int) -> str:
        """Per-`(address, endpoint)` DATA0/DATA1 alternation -- the first
        packet ever sent on a given endpoint is DATA0 (BOT spec), and every
        subsequent one on that same endpoint flips, independently of any
        other endpoint's own toggle state."""

        key = (address, endpoint)
        previous = self._toggle.get(key, "DATA1")  # so the first packet is DATA0
        current = "DATA0" if previous == "DATA1" else "DATA1"
        self._toggle[key] = current
        return current

    def _bulk_out(
        self, builder: CaptureBuilder, *, address: int, endpoint: int, data, datatype: str = "bytes"
    ) -> None:
        self.transport.token(builder, pid="OUT", address=address, endpoint=endpoint)
        self.transport.data_packet(
            builder, pid=self._next_toggle(address, endpoint), data=data, datatype=datatype, driver="host",
        )
        self.transport.handshake(builder, pid="ACK", driver="device")

    def _bulk_in(self, builder: CaptureBuilder, *, address: int, endpoint: int, data) -> None:
        self.transport.token(builder, pid="IN", address=address, endpoint=endpoint)
        self.transport.data_packet(
            builder, pid=self._next_toggle(address, endpoint), data=data, driver="device",
        )
        self.transport.handshake(builder, pid="ACK", driver="host")

    def _bot_command(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint_out: int,
        endpoint_in: int,
        cdb: list[int],
        direction: str,
        data_length: int,
        in_data=None,
        out_data=None,
        label: str,
    ) -> FrameHandle:
        """One full CBW -> [data] -> CSW Bulk-Only transaction, wrapped in
        a single `builder.frame()` for a summary annotation over the whole
        logical command (see `rtc8564.py`/`i3c.py` for the same pattern)."""

        self._tag += 1
        tag = self._tag
        flags = 0x80 if direction == "IN" else 0x00
        cbw = _le32(_CBW_SIGNATURE) + _le32(tag) + _le32(data_length) + [flags, 0x00, len(cdb) & 0x1F]
        cbw += list(cdb) + [0] * (16 - len(cdb))
        assert len(cbw) == 31

        dp, dm = self.transport.sig("dp"), self.transport.sig("dm")
        with builder.frame() as fh:
            self._bulk_out(builder, address=address, endpoint=endpoint_out, data=cbw)
            if data_length:
                if direction == "IN":
                    self._bulk_in(builder, address=address, endpoint=endpoint_in, data=in_data)
                else:
                    self._bulk_out(builder, address=address, endpoint=endpoint_out, data=out_data)
            csw = _le32(_CSW_SIGNATURE) + _le32(tag) + _le32(0) + [0x00]
            assert len(csw) == 13
            self._bulk_in(builder, address=address, endpoint=endpoint_in, data=csw)
        builder.annotate("field", label, start=fh.start, end=fh.end, signals=(dp, dm))
        return fh

    # -- SCSI operations ---------------------------------------------------

    def scsi_inquiry(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint_out: int = 1,
        endpoint_in: int = 2,
        vendor: str,
        product: str,
    ) -> FrameHandle:
        """INQUIRY (0x12): synthesizes a 36-byte SCSI INQUIRY response.
        Byte 0 = 0x00 (direct-access block device); byte 1 = 0x80 (RMB bit
        set -- removable, an arbitrary but explicit choice for this
        synthetic device); `vendor` padded/truncated to 8 ASCII bytes at
        offset 8, `product` padded/truncated to 16 ASCII bytes at offset
        16; the rest zero-filled."""

        vendor_bytes = vendor.encode("ascii")[:8].ljust(8, b" ")
        product_bytes = product.encode("ascii")[:16].ljust(16, b" ")
        response = [0x00, 0x80] + [0] * 6 + list(vendor_bytes) + list(product_bytes) + [0] * 4
        assert len(response) == 36
        cdb = [0x12, 0x00, 0x00, 0x00, 36, 0x00]
        return self._bot_command(
            builder, address=address, endpoint_out=endpoint_out, endpoint_in=endpoint_in,
            cdb=cdb, direction="IN", data_length=36, in_data=response,
            label=f"INQUIRY vendor={vendor!r} product={product!r}",
        )

    def scsi_read_capacity10(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint_out: int = 1,
        endpoint_in: int = 2,
        last_lba: int,
        block_size: int = 512,
    ) -> FrameHandle:
        """READ CAPACITY(10) (0x25): 8-byte response, `last_lba` (4 bytes
        BE) + `block_size` (4 bytes BE)."""

        response = _be32(last_lba) + _be32(block_size)
        cdb = [0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        return self._bot_command(
            builder, address=address, endpoint_out=endpoint_out, endpoint_in=endpoint_in,
            cdb=cdb, direction="IN", data_length=8, in_data=response,
            label=f"READ CAPACITY(10) last_lba={last_lba} block_size={block_size}",
        )

    def scsi_test_unit_ready(
        self, builder: CaptureBuilder, *, address: int, endpoint_out: int = 1, endpoint_in: int = 2,
    ) -> FrameHandle:
        """TEST UNIT READY (0x00): no data stage at all
        (`dCBWDataTransferLength = 0`)."""

        cdb = [0x00, 0, 0, 0, 0, 0]
        return self._bot_command(
            builder, address=address, endpoint_out=endpoint_out, endpoint_in=endpoint_in,
            cdb=cdb, direction="IN", data_length=0, label="TEST UNIT READY",
        )

    def scsi_read10(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint_out: int = 1,
        endpoint_in: int = 2,
        lba: int,
        data,
        datatype: str = "bytes",
    ) -> FrameHandle:
        """READ(10) (0x28): `data` is the response payload (IN direction),
        decoded via the project's normal datatype convention. Fixed
        512-byte logical block size -- `data`'s length must be a positive
        multiple of it, used to compute the CDB's transfer-length-in-blocks
        field (bytes 7-8, big-endian)."""

        values = decode_payload_with_floating(data, datatype, tristate=False).values
        if len(values) == 0 or len(values) % _BLOCK_SIZE != 0:
            raise ValueError(
                f"scsi_read10: data length must be a positive multiple of {_BLOCK_SIZE}, got {len(values)}"
            )
        blocks = len(values) // _BLOCK_SIZE
        cdb = [0x28, 0, *_be32(lba), 0, *_be16(blocks), 0]
        return self._bot_command(
            builder, address=address, endpoint_out=endpoint_out, endpoint_in=endpoint_in,
            cdb=cdb, direction="IN", data_length=len(values), in_data=values,
            label=f"READ(10) lba={lba} blocks={blocks}",
        )

    def scsi_write10(
        self,
        builder: CaptureBuilder,
        *,
        address: int,
        endpoint_out: int = 1,
        endpoint_in: int = 2,
        lba: int,
        data,
        datatype: str = "bytes",
    ) -> FrameHandle:
        """WRITE(10) (0x2A): `data` is the OUT-direction payload host
        writes. Same 512-byte block-alignment rule as `scsi_read10`."""

        values = decode_payload_with_floating(data, datatype, tristate=False).values
        if len(values) == 0 or len(values) % _BLOCK_SIZE != 0:
            raise ValueError(
                f"scsi_write10: data length must be a positive multiple of {_BLOCK_SIZE}, got {len(values)}"
            )
        blocks = len(values) // _BLOCK_SIZE
        cdb = [0x2A, 0, *_be32(lba), 0, *_be16(blocks), 0]
        return self._bot_command(
            builder, address=address, endpoint_out=endpoint_out, endpoint_in=endpoint_in,
            cdb=cdb, direction="OUT", data_length=len(values), out_data=values,
            label=f"WRITE(10) lba={lba} blocks={blocks}",
        )
