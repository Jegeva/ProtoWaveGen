from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .payload import decode_payload
from .i2c import I2CBus


@register_protocol("eeprom_24xx")
class Eeprom24xx(StackedProtocol):
    """Generic 24xx-series I2C EEPROM (24C01..24C512+), stacked on `I2CBus`.

    `addr_width` picks 1-byte word addressing (small parts, <=16Kbit) or
    2-byte (larger parts). `page_size` is only used for a sanity-check
    `ValueError` on an oversized page write, not for behavioral timing — the
    post-write cycle time (tWR, a few ms in reality) isn't modeled.

    Reads use `I2CBus.write_then_read()` (write the word address, repeated
    START, read) — the standard "random read" idiom real EEPROM drivers use.
    """

    def __init__(
        self, node_id: str, transport: I2CBus, *, address: int = 0x50, addr_width: int = 1,
        page_size: int = 16, operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        if addr_width not in (1, 2):
            raise ValueError(f"addr_width must be 1 or 2, got {addr_width}")
        self.address = address
        self.addr_width = addr_width
        self.page_size = page_size

    def _addr_bytes(self, word_addr: int) -> list[int]:
        limit = 1 << (8 * self.addr_width)
        if not (0 <= word_addr < limit):
            raise ValueError(f"word address {word_addr} does not fit in {self.addr_width} byte(s)")
        return [(word_addr >> (8 * i)) & 0xFF for i in reversed(range(self.addr_width))]

    def _addr_labels(self, word_addr: int) -> list[str]:
        label = f"ADDR=0x{word_addr:0{self.addr_width * 2}X}"
        return [label] * self.addr_width

    def write_page(self, builder: CaptureBuilder, *, word_addr: int, values: list[int]) -> FrameHandle:
        if len(values) > self.page_size:
            raise ValueError(f"page write of {len(values)} bytes exceeds page_size={self.page_size}")
        addr_bytes = self._addr_bytes(word_addr)
        labels = self._addr_labels(word_addr) + [format_byte(v) for v in values]
        return self.transport.write(builder, address=self.address, data=[*addr_bytes, *values], labels=labels)

    def write_byte(self, builder: CaptureBuilder, *, word_addr: int, value: int) -> FrameHandle:
        return self.write_page(builder, word_addr=word_addr, values=[value])

    def read_sequential(
        self, builder: CaptureBuilder, *, word_addr: int, values, datatype: str = "bytes"
    ) -> FrameHandle:
        """`values` is what the EEPROM synthetically "contains" at
        `word_addr` onward — this tool generates rather than senses real
        memory contents, so the caller supplies them."""

        values = decode_payload(values, datatype)
        addr_bytes = self._addr_bytes(word_addr)
        return self.transport.write_then_read(
            builder, address=self.address, write_data=addr_bytes, read_data=values,
            write_labels=self._addr_labels(word_addr), read_labels=[format_byte(v) for v in values],
        )

    def read_byte(self, builder: CaptureBuilder, *, word_addr: int, value: int) -> FrameHandle:
        return self.read_sequential(builder, word_addr=word_addr, values=[value])
