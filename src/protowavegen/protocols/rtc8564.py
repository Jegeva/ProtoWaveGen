from __future__ import annotations

from datetime import datetime

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .i2c import I2CBus

_SECONDS_REG = 0x02


def _to_bcd(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


@register_protocol("rtc8564")
class Rtc8564(StackedProtocol):
    """NXP/Philips PCF8563 ("RTC-8564" family) realtime clock, stacked on
    `I2CBus`. Fixed 7-bit address `0x51` (no address-strap pins on real
    hardware).

    Unlike DS1307, the date/time register block starts at 0x02 (seconds),
    not 0x00 — registers 0x00/0x01 are control bits (not modeled), and
    sigrok's own `rtc8564` decoder only assembles its date/time summary
    annotation when a burst starts exactly at 0x02 and covers all 7 bytes
    through 0x08. Seconds' bit 7 is the voltage-low (VL) flag; month's bit
    7 is the century flag (0=20xx, 1=19xx on most real parts) — both
    accepted as plain register bits here, not behaviorally simulated, same
    as `ds1307.py`'s clock-halt/12-24-hour bits. Registers 0x09-0x0F
    (alarm/timer/CLKOUT) aren't modeled.
    """

    ADDRESS = 0x51

    def __init__(self, node_id: str, transport: I2CBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    @staticmethod
    def _datetime_bytes(dt: datetime, *, voltage_low: bool, century: bool) -> list[int]:
        return [
            _to_bcd(dt.second) | (0x80 if voltage_low else 0),
            _to_bcd(dt.minute),
            _to_bcd(dt.hour),
            _to_bcd(dt.day),
            _to_bcd(dt.isoweekday() % 7),  # chip's weekday is 0-6, not ISO's 1-7
            _to_bcd(dt.month) | (0x80 if century else 0),
            _to_bcd(dt.year % 100),
        ]

    @staticmethod
    def _coerce_datetime(dt: datetime | str) -> datetime:
        """Accepts a real `datetime` (direct/test use) or an ISO-8601
        string (JSON `operations` can only carry JSON-native types)."""

        return dt if isinstance(dt, datetime) else datetime.fromisoformat(dt)

    def write_datetime(
        self, builder: CaptureBuilder, *, dt: datetime | str, voltage_low: bool = False, century: bool = False
    ) -> FrameHandle:
        dt = self._coerce_datetime(dt)
        values = self._datetime_bytes(dt, voltage_low=voltage_low, century=century)
        label = f"RTC={dt.isoformat()}"
        return self.transport.write(
            builder, address=self.ADDRESS, data=[_SECONDS_REG, *values],
            labels=["PTR=SEC"] + [label] * len(values),
        )

    def read_datetime(
        self, builder: CaptureBuilder, *, dt: datetime | str, voltage_low: bool = False, century: bool = False
    ) -> FrameHandle:
        dt = self._coerce_datetime(dt)
        values = self._datetime_bytes(dt, voltage_low=voltage_low, century=century)
        label = f"RTC={dt.isoformat()}"
        return self.transport.write_then_read(
            builder, address=self.ADDRESS, write_data=[_SECONDS_REG], read_data=values,
            write_labels=["PTR=SEC"], read_labels=[label] * len(values),
        )
