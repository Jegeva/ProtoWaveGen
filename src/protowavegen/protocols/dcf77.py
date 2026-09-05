from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, microseconds_to_samples, register_protocol

_ZERO_HIGH_US = 100_000.0
_ONE_HIGH_US = 200_000.0
_SLOT_US = 1_000_000.0  # one full second per bit slot


def _bcd_bits(value: int, unit_bits: int, tens_bits: int) -> list[int]:
    """BCD-encode `value` as `unit_bits` LSB-first bits (the units digit)
    followed by `tens_bits` LSB-first bits (the tens digit) — DCF77's own
    per-field bit order."""

    units, tens = value % 10, value // 10
    return [(units >> i) & 1 for i in range(unit_bits)] + [(tens >> i) & 1 for i in range(tens_bits)]


@register_protocol("dcf77")
class Dcf77(TransportProtocol):
    """DCF77 European longwave time signal: one demodulated `data` line.
    Confirmed against sigrok's own `dcf77` decoder: it triggers on
    *rising* edges and measures the following *high* period's duration to
    classify each bit (40-160ms -> 0, 161-260ms -> 1, both centered on the
    real 100ms/200ms nominal values with wide margin) — the opposite
    envelope sense from every other pulse-timed protocol in this repo
    (mark/low-vs-space/high) is exactly what this decoder expects, not a
    choice made here. Idle between each bit's pulse is held low for the
    rest of that 1-second slot.

    59 bits per minute, one per second, BCD fields: bit 0 (start of
    minute, always 0), bits 1-14 (special bits, always 0 here — civil
    warning/weather forecast data isn't modeled), bit 15 (call bit),
    bits 16-19 (summer-time announcement/CEST/CET/leap-second flags),
    bit 20 (start of encoded time, always 1), bits 21-28 (minute BCD +
    even parity), bits 29-35 (hour BCD + even parity), bits 36-58 (day/
    weekday/month/year BCD + even parity over the whole date block).

    Bit 59 is never transmitted at all — a full extra silent second in
    its place is what marks the new-minute boundary (confirmed via the
    decoder's own rising-edge-to-rising-edge gap check, 1600-2400ms).
    Real-time duration is a non-issue for this tool: `CaptureBuilder`'s
    edge list is sparse and scales with edge count (~118 edges/minute),
    not real-time span.

    **The decoder only starts annotating real fields after it sees that
    minute-gap** (`dcf77_bitnumber_is_known` starts false) — a meaningful
    round-trip needs `send_minute()` called at least twice back-to-back
    (the first as a priming/sync minute, the second to get real decoded
    fields), the same repeat-for-a-clean-decode shape as this repo's
    existing PS/2/LIN/EM4100 sigrok round-trip cases.
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("data"), initial_level=0)]

    @staticmethod
    def _frame_bits(
        minute: int, hour: int, day: int, weekday: int, month: int, year: int,
        *, call_bit: bool, summer_time_announce: bool, cest: bool, cet: bool, leap_second_announce: bool,
    ) -> list[int]:
        bits = [0]  # bit 0: start of minute
        bits += [0] * 14  # bits 1-14: special bits, not modeled
        bits += [
            1 if call_bit else 0, 1 if summer_time_announce else 0,
            1 if cest else 0, 1 if cet else 0, 1 if leap_second_announce else 0,
        ]
        bits.append(1)  # bit 20: start of encoded time

        minute_bits = _bcd_bits(minute, 4, 3)
        minute_parity = 0
        for b in minute_bits:
            minute_parity ^= b
        bits += minute_bits + [minute_parity]

        hour_bits = _bcd_bits(hour, 4, 2)
        hour_parity = 0
        for b in hour_bits:
            hour_parity ^= b
        bits += hour_bits + [hour_parity]

        date_bits = (
            _bcd_bits(day, 4, 2)
            + [(weekday >> i) & 1 for i in range(3)]
            + _bcd_bits(month, 4, 1)
            + _bcd_bits(year, 4, 4)
        )
        date_parity = 0
        for b in date_bits:
            date_parity ^= b
        bits += date_bits + [date_parity]

        return bits

    def send_minute(
        self, builder: CaptureBuilder, *, minute: int, hour: int, day: int, weekday: int, month: int,
        year: int, call_bit: bool = False, summer_time_announce: bool = False, cest: bool = False,
        cet: bool = True, leap_second_announce: bool = False,
    ) -> FrameHandle:
        if not (0 <= minute <= 59):
            raise ValueError(f"minute {minute} out of range 0-59")
        if not (0 <= hour <= 23):
            raise ValueError(f"hour {hour} out of range 0-23")
        if not (1 <= day <= 31):
            raise ValueError(f"day {day} out of range 1-31")
        if not (1 <= weekday <= 7):
            raise ValueError(f"weekday {weekday} out of range 1-7 (1=Monday)")
        if not (1 <= month <= 12):
            raise ValueError(f"month {month} out of range 1-12")
        if not (0 <= year <= 99):
            raise ValueError(f"year {year} out of range 0-99")

        bits = self._frame_bits(
            minute, hour, day, weekday, month, year, call_bit=call_bit,
            summer_time_announce=summer_time_announce, cest=cest, cet=cet,
            leap_second_announce=leap_second_announce,
        )
        line = self.sig("data")

        with builder.frame() as fh:
            for bit in bits:
                high_us = _ONE_HIGH_US if bit else _ZERO_HIGH_US
                builder.set_level(line, 1)
                builder.advance(microseconds_to_samples(builder, high_us))
                builder.set_level(line, 0)
                builder.advance(microseconds_to_samples(builder, _SLOT_US - high_us))
            builder.advance(microseconds_to_samples(builder, _SLOT_US))  # bit 59: omitted (new-minute gap)

        builder.annotate(
            "field", f"{hour:02d}:{minute:02d} {day:02d}.{month:02d}.{year:02d}",
            start=fh.start, end=fh.end, signals=(line,),
        )
        return fh
