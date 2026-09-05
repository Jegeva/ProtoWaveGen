from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import DriverTracker, TransportProtocol, bits_of_byte, microseconds_to_samples, register_protocol

_START_LOW_US = 18000.0
_START_HIGH_US = 30.0
_RESPONSE_LOW_US = 80.0
_RESPONSE_HIGH_US = 80.0
_BIT_LOW_US = 50.0
_BIT_0_HIGH_US = 27.0
_BIT_1_HIGH_US = 70.0
_END_LOW_US = 50.0


@register_protocol("am230x")
class Am230x(TransportProtocol):
    """Aosong AM230x/DHTxx/RHTxx humidity/temperature sensor: single
    open-drain `sda` line (`SignalKind.TRISTATE`), pulse-width-timed —
    real hardware, no ROM addressing (unlike 1-Wire, which this otherwise
    resembles in shape: host-initiated low pulse, device responds with an
    ack-style pulse, then pulse-width-encoded bits).

    Timing constants confirmed against sigrok's own `am230x` decoder's
    exact thresholds (its `timing` dict) — every value here sits
    comfortably mid-window rather than at a boundary (e.g. `BIT 0 HIGH`
    is 20-35us and `BIT 1 HIGH` is 65-80us, a 30us dead zone between them;
    `START LOW`/`RESPONSE LOW`/`RESPONSE HIGH` windows are similarly wide)
    — the same "don't sit on a real decoder's classification threshold"
    lesson `onewire.py` already had to learn the hard way, applied
    proactively here since the margins are wide enough that it costs
    nothing.

    Single-call frame: host pulls SDA low (`START LOW`), releases
    (`START HIGH`); sensor acks low then high (`RESPONSE LOW`/`RESPONSE
    HIGH`); then 40 bits (`BIT LOW` + `BIT 0/1 HIGH` per bit), MSB-first:
    16-bit humidity, 16-bit temperature (bit 0 = sign, remaining 15 bits =
    magnitude), 8-bit checksum (sum of the first 4 bytes, mod 256). A
    final low-then-release pulse closes the frame (sigrok's decoder just
    waits for that release, no timing check on it).
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("sda"), initial_level=1, kind=SignalKind.TRISTATE)]

    @staticmethod
    def _encode(humidity: float, temperature: float) -> list[int]:
        h = round(humidity * 10)
        if not (0 <= h <= 0xFFFF):
            raise ValueError(f"humidity {humidity} out of encodable range")
        t_mag = round(abs(temperature) * 10)
        if not (0 <= t_mag <= 0x7FFF):
            raise ValueError(f"temperature {temperature} out of encodable range")
        t = (0x8000 if temperature < 0 else 0) | t_mag
        bytes4 = [(h >> 8) & 0xFF, h & 0xFF, (t >> 8) & 0xFF, t & 0xFF]
        checksum = sum(bytes4) & 0xFF
        return bytes4 + [checksum]

    def send_reading(self, builder: CaptureBuilder, *, humidity: float, temperature: float) -> FrameHandle:
        line = self.sig("sda")
        payload = self._encode(humidity, temperature)
        tracker = DriverTracker(builder, line)

        with builder.frame() as fh:
            builder.set_level(line, 0)
            tracker.set("host")
            builder.advance(microseconds_to_samples(builder, _START_LOW_US))
            builder.set_level(line, 1)
            tracker.set("pullup")
            builder.advance(microseconds_to_samples(builder, _START_HIGH_US))

            builder.set_level(line, 0)
            tracker.set("sensor")
            builder.advance(microseconds_to_samples(builder, _RESPONSE_LOW_US))
            builder.set_level(line, 1)
            tracker.set("pullup")
            builder.advance(microseconds_to_samples(builder, _RESPONSE_HIGH_US))

            for byte in payload:
                for bit in bits_of_byte(byte, order="msb"):
                    builder.set_level(line, 0)
                    tracker.set("sensor")
                    builder.advance(microseconds_to_samples(builder, _BIT_LOW_US))
                    builder.set_level(line, 1)
                    tracker.set("pullup")
                    builder.advance(microseconds_to_samples(builder, _BIT_1_HIGH_US if bit else _BIT_0_HIGH_US))

            builder.set_level(line, 0)
            tracker.set("sensor")
            builder.advance(microseconds_to_samples(builder, _END_LOW_US))
            builder.set_level(line, 1)
            tracker.set("pullup")

        tracker.close()
        builder.annotate(
            "field", f"RH={humidity:.1f}% T={temperature:.1f}C", start=fh.start, end=fh.end, signals=(line,),
        )
        return fh
