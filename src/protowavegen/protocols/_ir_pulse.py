"""Shared timing primitives for the IR remote-control family (RC-5, NEC,
RC-6): a single demodulated IR envelope line (mark = carrier present,
space = carrier absent), active-low by convention (matching every real
IR receiver module and sigrok's own `ir_rc5`/`ir_nec`/`ir_rc6` decoders'
`active-low` default) — idle/space is logic 1, mark is logic 0. All three
decoders are envelope-only (`inputs=['logic']`, one boolean channel): none
of them need the actual 36-38kHz sub-carrier modeled, only this on/off
envelope at the millisecond/microsecond timescale.

Frame assembly (start bits, mode bits, address/command layout, bit order)
stays in each protocol's own file — this only holds the two genuinely
shared building blocks.
"""

from __future__ import annotations

from ..model import CaptureBuilder
from .base import microseconds_to_samples


_MIN_IDLE_GUARD_US = 100.0


def ensure_idle_gap(builder: CaptureBuilder, line: str) -> None:
    """A minimum idle (space) period before starting a new frame's leader/
    first bit. Real remotes never transmit back-to-back with zero gap, but
    the more concrete reason this is mandatory: two frames sent with
    literally zero gap put a rise and an immediate fall at the *same*
    sample — sigrok's edge-based decoders (which scan for a rising or
    falling edge per iteration) misinterpret that degenerate zero-width
    space as no edge having happened at all, silently dropping the frame
    boundary. Idempotent to call even when the line is already idle."""

    builder.advance(microseconds_to_samples(builder, _MIN_IDLE_GUARD_US))


def mark_space(builder: CaptureBuilder, line: str, mark_us: float, space_us: float) -> None:
    """One mark-then-space pulse (NEC's core primitive, and every biphase
    bit's own building block below)."""

    builder.set_level(line, 0)  # mark: carrier on
    builder.advance(microseconds_to_samples(builder, mark_us))
    builder.set_level(line, 1)  # space: carrier off
    builder.advance(microseconds_to_samples(builder, space_us))


def biphase_bit(builder: CaptureBuilder, line: str, bit: int, half_bit_us: float, width: int = 1) -> None:
    """One Manchester/biphase bit (RC-5/RC-6): `bit=1` is a high-to-low
    (space-then-mark) transition at the bit's midpoint, `bit=0` is the
    reverse (mark-then-space) — matching RC-5's own documented convention
    (and sigrok's `ir_rc5`/`ir_rc6` decoders, which anchor their very
    first recognized edge as the falling edge marking a logical 1). Each
    half held for `half_bit_us * width` (RC-6's toggle bit is
    double-width — pass `width=2` there)."""

    first, second = (1, 0) if bit else (0, 1)
    builder.set_level(line, first)
    builder.advance(microseconds_to_samples(builder, half_bit_us * width))
    builder.set_level(line, second)
    builder.advance(microseconds_to_samples(builder, half_bit_us * width))
