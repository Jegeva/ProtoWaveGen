from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import DriverTracker, TransportProtocol, format_byte, register_protocol


@register_protocol("ps2")
class Ps2Bus(TransportProtocol):
    """PS/2 keyboard/mouse interface: open-collector `clock`+`data`
    (`SignalKind.TRISTATE`, tracked via `DriverTracker` the same way I2C/
    1-Wire/Wiegand are — `"device"` drives clock+data during a normal
    device->host frame and the ACK bit during a host->device frame,
    `"host"` drives the inhibit/request-to-send sequence, `"pullup"`
    whenever released).

    Device->host (`send_from_device`): 11 bits, device-generated clock —
    start(0), 8 data bits LSB-first, odd parity, stop(1); data changes
    while clock is high, sampled by the host on the falling edge.
    Host->device (`send_to_host`): host holds clock low >=`inhibit_us`
    (inhibit), then pulls data low (the host's start bit) and releases
    clock; the device then generates the remaining clock pulses for
    data+parity+stop and drives a final ACK bit low itself.

    Fixed representative timing (`clock_hz`/`inhibit_us`, not tied to one
    real device). No inhibit-collision/retransmit modeling.
    """

    def __init__(
        self, node_id: str, *, clock_hz: int = 12_500, inhibit_us: float = 100,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        self.clock_hz = clock_hz
        self.inhibit_us = inhibit_us
        self._half_period_samples: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        shp = round(samplerate / (2 * self.clock_hz))
        if shp < 1:
            raise ValueError(
                f"samplerate {samplerate} too low for clock_hz {self.clock_hz} "
                f"(need at least {2 * self.clock_hz} Hz)"
            )
        self._half_period_samples = shp

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._half_period_samples is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return None if self._half_period_samples is None else self._half_period_samples * 2

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("clock"), kind=SignalKind.TRISTATE, initial_level=1),
            Signal(self.sig("data"), kind=SignalKind.TRISTATE, initial_level=1),
        ]

    @staticmethod
    def _odd_parity(byte: int) -> int:
        return 1 - (bin(byte).count("1") % 2)

    def _device_clocked_bit(
        self, builder: CaptureBuilder, level: int, clock_tracker: DriverTracker, data_tracker: DriverTracker,
        data_owner: str,
    ) -> None:
        """One device-generated clock cycle: data set while clock is high,
        sampled on the falling edge — same shape whichever direction owns
        the data bit (the device always generates the clock itself)."""

        clock, data = self.sig("clock"), self.sig("data")
        shp = self._half_period_samples
        builder.set_level(clock, 1)
        clock_tracker.set("pullup")
        builder.set_level(data, level)
        data_tracker.set(data_owner if level == 0 else "pullup")
        builder.advance(shp)
        builder.set_level(clock, 0)
        clock_tracker.set("device")
        builder.advance(shp)

    def send_from_device(self, builder: CaptureBuilder, *, byte: int) -> FrameHandle:
        self._ensure_bound(builder)
        clock, data = self.sig("clock"), self.sig("data")
        clock_tracker, data_tracker = DriverTracker(builder, clock), DriverTracker(builder, data)
        bits = [0, *((byte >> i) & 1 for i in range(8)), self._odd_parity(byte), 1]

        with builder.frame() as fh:
            for bit in bits:
                self._device_clocked_bit(builder, bit, clock_tracker, data_tracker, "device")
        clock_tracker.close()
        data_tracker.close()

        builder.annotate("unit", "byte", start=fh.start, end=fh.end, signals=(data,))
        builder.annotate("field", format_byte(byte), start=fh.start, end=fh.end, signals=(data,), value=byte)
        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(data,))
        return fh

    def send_to_host(self, builder: CaptureBuilder, *, byte: int) -> FrameHandle:
        self._ensure_bound(builder)
        clock, data = self.sig("clock"), self.sig("data")
        clock_tracker, data_tracker = DriverTracker(builder, clock), DriverTracker(builder, data)
        inhibit_samples = max(round(builder.samplerate * self.inhibit_us / 1_000_000), 1)
        bits = [*((byte >> i) & 1 for i in range(8)), self._odd_parity(byte), 1]

        with builder.frame() as fh:
            builder.set_level(clock, 0)  # host inhibit
            clock_tracker.set("host")
            builder.advance(inhibit_samples)
            builder.set_level(data, 0)  # host's start bit
            data_tracker.set("host")
            builder.set_level(clock, 1)  # host releases the clock; device takes over
            clock_tracker.set("pullup")
            builder.advance(self._half_period_samples)
            for bit in bits:
                self._device_clocked_bit(builder, bit, clock_tracker, data_tracker, "host")
            self._device_clocked_bit(builder, 0, clock_tracker, data_tracker, "device")  # ACK
        clock_tracker.close()
        data_tracker.close()

        builder.annotate("unit", "byte", start=fh.start, end=fh.end, signals=(data,))
        builder.annotate("field", format_byte(byte), start=fh.start, end=fh.end, signals=(data,), value=byte)
        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(data,))
        return fh
