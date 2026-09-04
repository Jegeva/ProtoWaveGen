from __future__ import annotations

from datetime import datetime

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .i2c import I2CBus

_SECONDS_REG = 0x00
_NVRAM_BASE = 0x08
_NVRAM_SIZE = 56


def _to_bcd(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


@register_protocol("ds1307")
class Ds1307(StackedProtocol):
    """Dallas DS1307 realtime clock, stacked on `I2CBus`. Fixed 7-bit
    address `0x68` (no address-strap pins on real hardware).

    Registers 0x00-0x06 are BCD-encoded seconds/minutes/hours/day-of-week/
    date/month/year; 0x07 is the control register (SQW output config, not
    modeled — it's a static byte here, not a behavioral square-wave
    generator); 0x08-0x3F (56 bytes) are general-purpose NVRAM. The
    clock-halt bit (seconds register bit 7) and 12/24-hour mode bit are
    accepted as plain register bits, not behaviorally simulated — every
    `read_datetime()` call just encodes whatever `datetime` the caller
    passes, consistent with this being a synthesizer, not a running clock.
    """

    ADDRESS = 0x68

    def __init__(self, node_id: str, transport: I2CBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    @staticmethod
    def _datetime_bytes(dt: datetime) -> list[int]:
        return [
            _to_bcd(dt.second), _to_bcd(dt.minute), _to_bcd(dt.hour),
            _to_bcd(dt.isoweekday()), _to_bcd(dt.day), _to_bcd(dt.month), _to_bcd(dt.year % 100),
        ]

    @staticmethod
    def _coerce_datetime(dt: datetime | str) -> datetime:
        """Accepts a real `datetime` (direct/test use) or an ISO-8601
        string (JSON `operations` can only carry JSON-native types)."""

        return dt if isinstance(dt, datetime) else datetime.fromisoformat(dt)

    def read_datetime(self, builder: CaptureBuilder, *, dt: datetime | str) -> FrameHandle:
        dt = self._coerce_datetime(dt)
        values = self._datetime_bytes(dt)
        label = f"RTC={dt.isoformat()}"
        return self.transport.write_then_read(
            builder, address=self.ADDRESS, write_data=[_SECONDS_REG], read_data=values,
            write_labels=["PTR=SEC"], read_labels=[label] * len(values),
        )

    def write_datetime(self, builder: CaptureBuilder, *, dt: datetime | str) -> FrameHandle:
        dt = self._coerce_datetime(dt)
        values = self._datetime_bytes(dt)
        label = f"RTC={dt.isoformat()}"
        return self.transport.write(
            builder, address=self.ADDRESS, data=[_SECONDS_REG, *values],
            labels=["PTR=SEC"] + [label] * len(values),
        )

    def _nvram_reg(self, addr: int) -> int:
        if not (0 <= addr < _NVRAM_SIZE):
            raise ValueError(f"NVRAM address {addr} out of range (0-{_NVRAM_SIZE - 1})")
        return _NVRAM_BASE + addr

    def read_nvram(self, builder: CaptureBuilder, *, addr: int, values: list[int]) -> FrameHandle:
        reg = self._nvram_reg(addr)
        return self.transport.write_then_read(
            builder, address=self.ADDRESS, write_data=[reg], read_data=values,
            write_labels=[f"PTR=0x{reg:02X}"], read_labels=[format_byte(v) for v in values],
        )

    def write_nvram(self, builder: CaptureBuilder, *, addr: int, values: list[int]) -> FrameHandle:
        reg = self._nvram_reg(addr)
        return self.transport.write(
            builder, address=self.ADDRESS, data=[reg, *values],
            labels=[f"PTR=0x{reg:02X}"] + [format_byte(v) for v in values],
        )
