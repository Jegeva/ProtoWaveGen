from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import StackedProtocol, register_protocol
from .spi import SpiBus

# Common-cathode 7-segment patterns, MSB-first bit order dp,g,f,e,d,c,b,a.
DIGIT_PATTERNS = {
    0: 0b00111111, 1: 0b00000110, 2: 0b01011011, 3: 0b01001111, 4: 0b01100110,
    5: 0b01101101, 6: 0b01111101, 7: 0b00000111, 8: 0b01111111, 9: 0b01101111,
}


@register_protocol("seven_segment")
class SevenSegmentDisplay(StackedProtocol):
    """Generic 74HC595-style serial-in/parallel-out shift register driving
    a 7-segment digit bank, stacked on `SpiBus` (`width=1`) — the generic,
    display-agnostic case, as distinct from `Max7219` (a smart driver chip
    with its own register interface).

    SER/SRCLK map straight onto SPI's MOSI/SCLK; `get_signals()` adds one
    extra `latch` pin (the "NES latch/clock aliasing" pattern
    `StackedProtocol`'s docstring anticipates) for the RCLK strobe — this
    chip has no CS-as-frame-bracket concept (many boards tie OE permanently
    low), so `SpiBus`'s own CS line is simply left unused by this class.

    One byte per digit, MSB-first = `dp,g,f,e,d,c,b,a` (a common but not
    universal convention). `set_digits()` shifts all digit bytes in one
    `SpiBus.transfer()` call (reuses its own per-byte annotations, per the
    same "don't duplicate a wide overlapping one" lesson from SPI/CAN),
    then pulses `latch` once — real hardware doesn't latch per digit. No
    common-anode inversion, no multiplexed/scanned-display timing (static
    latch only), no BCD-decode mode (that's `Max7219`'s territory).
    """

    def __init__(self, node_id: str, transport: SpiBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        if transport.width != 1:
            raise ValueError("SevenSegmentDisplay requires a width=1 (classic SPI-shaped) transport")

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("latch"), initial_level=0)]

    def _pulse_latch(self, builder: CaptureBuilder) -> None:
        latch = self.sig("latch")
        period = self.transport.bit_period_samples or 1
        with builder.frame() as latch_fh:
            builder.set_level(latch, 1)
            builder.advance(period)
            builder.set_level(latch, 0)
        builder.annotate("field", "LATCH", start=latch_fh.start, end=latch_fh.end, signals=(latch,))

    def set_digits(self, builder: CaptureBuilder, *, patterns, datatype: str = "bytes") -> FrameHandle:
        """`patterns`, one raw segment-bit byte per digit, chain order =
        shift order (the first pattern ends up in the last/farthest
        shift-register stage once latched, matching real daisy-chained
        74HC595 wiring). Forwarded straight to `SpiBus.transfer()`'s
        `mosi`/`datatype` — reaches it completely unmixed with anything
        else, so it inherits full floating-marker support for free."""

        fh = self.transport.transfer(builder, mosi=patterns, datatype=datatype)
        self._pulse_latch(builder)
        return fh

    def set_digit_values(self, builder: CaptureBuilder, *, values: list[int]) -> FrameHandle:
        for v in values:
            if v not in DIGIT_PATTERNS:
                raise ValueError(f"digit value {v} not in 0-9")
        return self.set_digits(builder, patterns=[DIGIT_PATTERNS[v] for v in values])
