from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .payload import Payload, decode_payload_with_floating, render_as_bin
from .spi import SpiBus

_JEDEC_ID_OPCODE = 0x9F
_READ_OPCODE = 0x03


@register_protocol("jedec_cfi")
class JedecCfi(StackedProtocol):
    """JEDEC manufacturer-ID query and standard SPI-NOR reads, stacked on
    `SpiBus` (`transport`, must be `width=1` classic SPI — the command phase
    is always single-line even on QSPI/OctoSPI parts). Uses `SpiBus
    .transfer`'s `labels` param throughout so command/address/data bytes
    show what they mean instead of a duplicate overlapping annotation.

    Only the classic single-line JEDEC ID opcode (0x9F) and a plain 0x03
    READ are implemented — not the full CFI/SFDP parameter table walk (that
    would mean decoding dozens of well-known offsets, out of scope here).
    """

    def __init__(self, node_id: str, transport: SpiBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        if transport.width != 1:
            raise ValueError("JedecCfi requires a width=1 (classic SPI) transport for its command phase")

    def read_jedec_id(
        self, builder: CaptureBuilder, *, manufacturer_id: int, memory_type: int, capacity: int
    ) -> FrameHandle:
        """Issue 0x9F, then clock 3 dummy bytes on MOSI while the flash
        shifts manufacturer ID / memory type / capacity back on MISO."""

        mosi = [_JEDEC_ID_OPCODE, 0x00, 0x00, 0x00]
        miso = [0x00, manufacturer_id, memory_type, capacity]
        labels = [
            f"CMD=0x{_JEDEC_ID_OPCODE:02X}",
            f"MFR={format_byte(manufacturer_id)}",
            f"TYPE={format_byte(memory_type)}",
            f"CAP={format_byte(capacity)}",
        ]
        return self.transport.transfer(builder, mosi=mosi, miso=miso, labels=labels)

    def read(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """Standard SPI-NOR READ (0x03): opcode + 3-byte address, then the
        flash shifts back `len(data)` bytes (synthesized, since this tool
        generates rather than senses real flash contents).

        `data` supports floating markers (`decode_payload_with_floating`).
        Since it's the MISO payload folded in after 4 fixed prefix bytes
        (opcode + 3-byte address) and `SpiBus.transfer()` decodes MOSI/MISO
        each under one shared `datatype`, both get rendered into flat
        `bin`-datatype strings via `render_as_bin` (MOSI has no floating
        positions of its own, so it renders as plain concrete digits) and
        passed with `datatype="bin"` — resolution happens downstream in
        `transfer()`, same as any other floating-marker payload."""

        payload = decode_payload_with_floating(data, datatype, tristate=False)
        data = payload.values
        if not (0 <= address < (1 << 24)):
            raise ValueError(f"address {address} does not fit in 3 bytes")
        addr_bytes = [(address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF]

        mosi_bytes = [_READ_OPCODE, *addr_bytes, *([0x00] * len(data))]
        labels = [
            f"CMD=0x{_READ_OPCODE:02X}",
            f"ADDR[23:16]={format_byte(addr_bytes[0])}",
            f"ADDR[15:8]={format_byte(addr_bytes[1])}",
            f"ADDR[7:0]={format_byte(addr_bytes[2])}",
            *(format_byte(byte) for byte in data),
        ]
        mosi = render_as_bin(Payload(values=mosi_bytes))
        miso = render_as_bin(payload, prefix_bytes=[0x00, 0x00, 0x00, 0x00])
        return self.transport.transfer(builder, mosi=mosi, miso=miso, datatype="bin", labels=labels)
