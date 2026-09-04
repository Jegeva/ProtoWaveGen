from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import (
    DriverTracker,
    TransportProtocol,
    bits_of_byte,
    decode_bits_with_floating,
    microseconds_to_samples,
    register_protocol,
)


@register_protocol("wiegand")
class WiegandBus(TransportProtocol):
    """Wiegand access-control reader interface: two open-collector lines
    `d0`/`d1` (`SignalKind.TRISTATE` — same "0 = driven low, 1 = pullup-
    released" semantics as I2C/1-Wire, tracked via `DriverTracker`), idle
    high, no clock — each bit is a brief pulse on exactly one line (a 0
    pulses `d0`, a 1 pulses `d1`).

    `pulse_us`/`interval_us` are representative defaults, not tied to any
    one reader's datasheet. `send_bits()` is the raw primitive;
    `send_card_26bit()` builds the standard 26-bit format (leading even
    parity over the first 12 data bits, 8-bit facility code, 16-bit card
    number, trailing odd parity over the last 12 data bits) — the most
    common Wiegand card format, though far from the only one in the field.
    """

    def __init__(
        self, node_id: str, *, pulse_us: float = 50, interval_us: float = 2000,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        self.pulse_us = pulse_us
        self.interval_us = interval_us

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("d0"), kind=SignalKind.TRISTATE, initial_level=1),
            Signal(self.sig("d1"), kind=SignalKind.TRISTATE, initial_level=1),
        ]

    def send_bits(self, builder: CaptureBuilder, *, bits, datatype: str = "bytes") -> FrameHandle:
        """`bits` is a plain `list[int]` (`datatype="bytes"`, default,
        unchanged from before) or, with `datatype="bits"`, a flat
        `0`/`1`/`l/L`/`h/H`/`z/Z` string decoded via
        `decode_bits_with_floating` — `d0`/`d1` are `TRISTATE` pull-high, so
        `z`/`Z` auto-resolves the same way as I2C/1-Wire. A floating
        position's pulse still happens exactly as any other bit's (its
        resolved 0/1 value decides which line gets pulsed), only the
        `DriverTracker` label for that pulse becomes `"floating"` instead of
        `"reader"`."""

        if datatype == "bits":
            bits, floating_positions = decode_bits_with_floating(bits, tristate=True)
        else:
            floating_positions = frozenset()
        d0, d1 = self.sig("d0"), self.sig("d1")
        pulse = microseconds_to_samples(builder, self.pulse_us)
        interval = microseconds_to_samples(builder, self.interval_us)
        tracker0, tracker1 = DriverTracker(builder, d0), DriverTracker(builder, d1)

        with builder.frame() as fh:
            for i, bit in enumerate(bits):
                line, tracker, idle_tracker = (d1, tracker1, tracker0) if bit else (d0, tracker0, tracker1)
                floating = i in floating_positions
                builder.set_level(line, 0)
                tracker.set("floating" if floating else "reader")
                builder.advance(pulse)
                builder.set_level(line, 1)
                tracker.set("floating" if floating else "pullup")
                idle_tracker.set("pullup")
                if i < len(bits) - 1:
                    builder.advance(interval - pulse)
        tracker0.close()
        tracker1.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(d0, d1))
        return fh

    @staticmethod
    def _parity_of(bits: list[int]) -> int:
        return sum(bits) % 2

    def send_card_26bit(self, builder: CaptureBuilder, *, facility_code: int, card_number: int) -> FrameHandle:
        if not (0 <= facility_code < 0x100):
            raise ValueError(f"facility_code {facility_code} does not fit in 8 bits")
        if not (0 <= card_number < 0x10000):
            raise ValueError(f"card_number {card_number} does not fit in 16 bits")

        data_bits = bits_of_byte(facility_code) + [
            (card_number >> i) & 1 for i in reversed(range(16))
        ]
        leading_parity = self._parity_of(data_bits[:12])  # makes bits 1-13 even
        trailing_parity = 1 - self._parity_of(data_bits[12:])  # makes bits 14-26 odd
        frame = [leading_parity, *data_bits, trailing_parity]

        fh = self.send_bits(builder, bits=frame)
        builder.annotate(
            "field", f"FC={facility_code} CARD={card_number}", start=fh.start, end=fh.end,
            signals=(self.sig("d0"), self.sig("d1")), facility_code=facility_code, card_number=card_number,
        )
        return fh
