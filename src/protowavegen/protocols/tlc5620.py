from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, bind_clock_samples, register_protocol

_DAC_NAMES = ["A", "B", "C", "D"]


@register_protocol("tlc5620")
class Tlc5620(TransportProtocol):
    """Texas Instruments TLC5620 8-bit quad-output DAC: `clk`/`data`/`load`
    shift-register interface (mirrors `MicrowireBus`'s own `_clock_bit`
    shape, not built on `SpiBus` — no CS/mode-variant complexity here).

    Data shifts into the DAC on CLK's *falling* edge, MSB-first, in a
    fixed 11-bit frame: 2 DAC-select bits (0-3 -> A-D), 1 gain bit
    (0 -> x1, 1 -> x2), 8 value bits. A LOAD falling edge then latches
    that frame. `ldac` is tied permanently low (voltage updates
    immediately on LOAD, rather than being held in a register for a
    later combined update across all 4 DACs) — sigrok's own `tlc5620`
    decoder only emits its `dac-select`/`gain`/`value` annotations once a
    LOAD falling edge is seen, so every `set_channel()` call ends with one.
    """

    def __init__(self, node_id: str, *, clock_hz: int, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self.clock_hz = clock_hz
        self._half_bit_samples: int | None = None

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("clk"), initial_level=0),
            Signal(self.sig("data"), initial_level=0),
            Signal(self.sig("load"), initial_level=1),
            Signal(self.sig("ldac"), initial_level=0),
        ]

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._half_bit_samples is None:
            self._half_bit_samples = bind_clock_samples(
                builder.samplerate, self.clock_hz, hz_label="clock_hz"
            )

    def _clock_bit(self, builder: CaptureBuilder, bit: int) -> None:
        clk, data = self.sig("clk"), self.sig("data")
        shb = self._half_bit_samples
        builder.set_level(data, bit)  # setup while CLK is low
        builder.set_level(clk, 1)
        builder.advance(shb)
        builder.set_level(clk, 0)  # falling edge: DATA shifted in
        builder.advance(shb)

    def set_channel(self, builder: CaptureBuilder, *, channel: int, gain: int, value: int) -> FrameHandle:
        if not (0 <= channel <= 3):
            raise ValueError(f"channel {channel} out of range 0-3")
        if gain not in (1, 2):
            raise ValueError(f"gain {gain} must be 1 or 2")
        if not (0 <= value <= 0xFF):
            raise ValueError(f"value {value} does not fit in 8 bits")

        self._ensure_bound(builder)
        load = self.sig("load")
        bits = [(channel >> 1) & 1, channel & 1, gain - 1] + [(value >> i) & 1 for i in reversed(range(8))]

        with builder.frame() as fh:
            for bit in bits:
                self._clock_bit(builder, bit)
            builder.set_level(load, 0)  # falling edge: latches the frame
            builder.advance(self._half_bit_samples)
            builder.set_level(load, 1)
            builder.advance(self._half_bit_samples)

        builder.annotate(
            "field", f"DAC{_DAC_NAMES[channel]}=x{gain}:{value}",
            start=fh.start, end=fh.end, signals=(self.sig("data"),),
        )
        return fh
