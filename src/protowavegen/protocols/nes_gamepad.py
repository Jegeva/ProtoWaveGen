from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, microseconds_to_samples, register_protocol

_BUTTON_ORDER = ["A", "B", "Select", "Start", "Up", "Down", "Left", "Right"]


@register_protocol("nes_gamepad")
class NesGamepad(TransportProtocol):
    """NES controller's 4021 shift register interface: the host pulses
    LATCH to snapshot button states in parallel, then clocks them out
    serially — one bit per CLOCK pulse on DATA, active-low (0 = pressed).
    The first bit is valid immediately after LATCH falls, before any CLOCK
    pulse; each subsequent CLOCK rising edge shifts out the next bit. Order:
    A, B, Select, Start, Up, Down, Left, Right.

    Doesn't reuse `SpiBus`: real NES timing is independently-timed LATCH and
    CLOCK pulses, not a continuous SPI-style clock bracketed by one CS per
    multi-byte transfer, so this is its own small transport mirroring
    `SpiBus`'s shape (one clock-and-shift primitive) instead of forcing SPI's
    CS-per-transfer model onto it.

    Single controller only — no daisy-chained second controller (NES Four
    Score multitap) support.
    """

    def __init__(
        self, node_id: str, *, latch_us: float = 12, clock_us: float = 6,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        self.latch_us = latch_us
        self.clock_us = clock_us

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("latch"), initial_level=0),
            Signal(self.sig("clock"), initial_level=0),
            Signal(self.sig("data"), initial_level=1),
        ]

    def read_buttons(self, builder: CaptureBuilder, *, buttons: dict[str, bool]) -> FrameHandle:
        latch, clock, data = self.sig("latch"), self.sig("clock"), self.sig("data")
        bits = [0 if buttons.get(name, False) else 1 for name in _BUTTON_ORDER]  # active-low
        latch_samples = microseconds_to_samples(builder, self.latch_us)
        clock_samples = microseconds_to_samples(builder, self.clock_us)

        with builder.frame() as fh:
            builder.set_level(latch, 1)
            builder.advance(latch_samples)
            builder.set_level(latch, 0)
            builder.set_level(data, bits[0])  # valid immediately after latch, before any clock
            for bit in bits[1:]:
                builder.advance(clock_samples)
                builder.set_level(clock, 1)
                builder.advance(clock_samples)
                builder.set_level(clock, 0)
                builder.set_level(data, bit)
            builder.advance(clock_samples)

        pressed = [name for name, bit in zip(_BUTTON_ORDER, bits) if bit == 0]
        builder.annotate(
            "field", "buttons=" + (",".join(pressed) if pressed else "none"),
            start=fh.start, end=fh.end, signals=(data,),
        )
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(data,))
        return fh
