from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import DriverTracker, TransportProtocol, microseconds_to_samples, register_protocol
from .payload import decode_bits_with_floating


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

    @staticmethod
    def _resolve_card_field(value, datatype: str, *, width: int, name: str) -> tuple[list[int], frozenset[int]]:
        """`value` is a plain int (`datatype="bytes"`, default, unchanged
        from before — range-checked to `width` bits) or, with
        `datatype="bits"`, a flat `0`/`1`/`l/L`/`h/H`/`z/Z` bit-string via
        `decode_bits_with_floating` (`d0`/`d1` are TRISTATE pull-high, so
        `z`/`Z` auto-resolves), which must decode to exactly `width` bits —
        the byte-oriented `"hex"`/`"bin"`/`"text"` datatypes don't apply to
        a field this narrow, so they're rejected rather than silently
        misinterpreted as a bit-string. A single-element `list[int]` is
        also accepted under `datatype="bytes"` and unwrapped: `--data-int`/
        `--data-file` (`config.py::apply_data_override`) always build a
        `list[int]` for `"bytes"` with no way to know this field is a bare
        int rather than a real payload list — same fix as
        `payload.py::resolve_single_byte`, which found this same bug shape
        on DALI/PS-2's single-byte fields."""

        if datatype == "bytes":
            if isinstance(value, list):
                if len(value) != 1:
                    raise ValueError(f"{name}: expected exactly one value, got {len(value)} from {value!r}")
                value = value[0]
            if not (0 <= value < (1 << width)):
                raise ValueError(f"{name} {value} does not fit in {width} bits")
            return [(value >> i) & 1 for i in reversed(range(width))], frozenset()
        if datatype != "bits":
            raise ValueError(f"{name}: datatype must be 'bytes' or 'bits', got {datatype!r}")
        bits, floating = decode_bits_with_floating(value, tristate=True)
        if len(bits) != width:
            raise ValueError(f"{name}: expected {width} bits, got {len(bits)} from {value!r}")
        return bits, floating

    def send_card_26bit(
        self, builder: CaptureBuilder, *, facility_code, card_number,
        facility_code_datatype: str = "bytes", card_number_datatype: str = "bytes",
    ) -> FrameHandle:
        """`facility_code`/`card_number` are plain ints (`*_datatype=
        "bytes"`, default, unchanged from before) or, with `*_datatype=
        "bits"`, a flat bit-string decoded via `_resolve_card_field` —
        the `l/L/h/H/z/Z` floating-marker alphabet then applies. Parity is
        computed over the already-resolved concrete bits either way, so a
        floating marker never affects the parity calculation's
        correctness."""

        facility_bits, facility_floating = self._resolve_card_field(
            facility_code, facility_code_datatype, width=8, name="facility_code"
        )
        card_bits, card_floating = self._resolve_card_field(
            card_number, card_number_datatype, width=16, name="card_number"
        )
        data_bits = facility_bits + card_bits
        data_floating = facility_floating | {8 + i for i in card_floating}

        leading_parity = self._parity_of(data_bits[:12])  # makes bits 1-13 even
        trailing_parity = 1 - self._parity_of(data_bits[12:])  # makes bits 14-26 odd
        frame_bits = [leading_parity, *data_bits, trailing_parity]
        frame_floating = {1 + i for i in data_floating}  # +1 for the leading parity bit

        if frame_floating:
            # re-render as a floating-capable bit-string for send_bits to
            # decode again — 'l'/'h' chosen to match the bit already
            # resolved above, so this round-trip never changes the value.
            frame = "".join(
                ("h" if bit else "l") if i in frame_floating else str(bit)
                for i, bit in enumerate(frame_bits)
            )
            fh = self.send_bits(builder, bits=frame, datatype="bits")
        else:
            fh = self.send_bits(builder, bits=frame_bits)

        facility_value = int("".join(map(str, facility_bits)), 2)
        card_value = int("".join(map(str, card_bits)), 2)
        builder.annotate(
            "field", f"FC={facility_value} CARD={card_value}", start=fh.start, end=fh.end,
            signals=(self.sig("d0"), self.sig("d1")), facility_code=facility_value, card_number=card_value,
        )
        return fh
