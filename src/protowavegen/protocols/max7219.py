from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .spi import SpiBus

_DECODE_MODE_REG = 0x09
_INTENSITY_REG = 0x0A
_SCAN_LIMIT_REG = 0x0B
_SHUTDOWN_REG = 0x0C


@register_protocol("max7219")
class Max7219(StackedProtocol):
    """Maxim MAX7219 8-digit LED display driver, stacked on `SpiBus`
    (`width=1` classic SPI). Each command is one 16-bit word (register byte
    + data byte); this chip latches its own LOAD/CS after every word, not
    once per multi-word burst, so every command here is its own
    `transport.transfer()` call rather than one big transfer.

    Doesn't model daisy-chaining (multiple MAX7219s sharing MOSI/CLK with
    data shifted through) — single device only.
    """

    def __init__(self, node_id: str, transport: SpiBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    def _command(self, builder: CaptureBuilder, reg: int, data: int, label: str) -> FrameHandle:
        return self.transport.transfer(builder, mosi=[reg, data], labels=[f"REG=0x{reg:02X}", label])

    def init(self, builder: CaptureBuilder, *, intensity: int = 8) -> FrameHandle:
        self._command(builder, _SHUTDOWN_REG, 0x01, "SHUTDOWN=OFF")
        self._command(builder, _DECODE_MODE_REG, 0xFF, "DECODE=BCD_ALL")
        self._command(builder, _SCAN_LIMIT_REG, 0x07, "SCAN_LIMIT=8")
        return self._command(builder, _INTENSITY_REG, intensity & 0x0F, f"INTENSITY={intensity}")

    def set_digit(self, builder: CaptureBuilder, *, position: int, value: int) -> FrameHandle:
        if not (0 <= position <= 7):
            raise ValueError(f"digit position {position} must be 0-7")
        return self._command(builder, 0x01 + position, value & 0xFF, f"DIGIT{position}={value}")
