from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import DriverTracker, TransportProtocol, decode_payload, format_byte, register_protocol


@register_protocol("onewire")
class OneWireBus(TransportProtocol):
    """Dallas/Maxim 1-Wire, standard speed: single open-drain `dq` line
    (`SignalKind.TRISTATE` — see `Signal` docs), same "0 = driven low, 1 =
    pullup-released" semantics as I2C, tracked the same way via
    `DriverTracker` (`"master"` initiating a slot, `"slave"` holding a
    read-0 low past the master's pulse, `"pullup"` whenever released).

    Timing (standard speed, fixed — no overdrive mode):
    - `reset()`: master drives DQ low 480us, releases; if `presence=True`
      (default) the target asserts a presence pulse 30us later, held 120us.
    - Each bit is a 60us slot the master initiates by pulling DQ low: a
      write-1 or read slot is a short 6us low pulse then release; a write-0
      slot holds DQ low for most of the slot, releasing for a brief recovery
      window right at the end (real masters always start a slot with a
      fresh falling edge, so even two back-to-back 0 bits need a momentary
      release between them — holding continuously low across slot
      boundaries is indistinguishable from one long, invalid pulse). A
      read-0 is modeled as the device taking over the low pulse from the
      master and holding it well past the ~15us point a real master samples
      at (see `_READ0_HOLD_US`) before releasing — real 1-Wire reads sense
      whatever the device does; here the device's bit is a caller-supplied
      value being synthesized, not sensed.
    - `write()`/`read()` send/receive whole bytes LSB-first, matching the
      1-Wire spec's bit order for ROM/function commands.

    Timing constants were cross-checked against sigrok's own
    `onewire_link` decoder (its exact min/max thresholds per phase) rather
    than picked from memory alone — e.g. the presence-pulse delay and the
    read-0 hold time both deliberately sit with margin inside that
    decoder's classification windows rather than at their edges, since a
    value sitting exactly on a threshold is one sample-quantization error
    away from being misclassified.
    """

    _RESET_LOW_US = 480
    _PRESENCE_DELAY_US = 30
    _PRESENCE_LOW_US = 120
    _RESET_RECOVERY_US = 500  # >=480us (RSTH) of quiet time after presence, with margin
    _SLOT_US = 70  # >60us (SLOT min) with margin — see class docstring on boundary values
    _SHORT_PULSE_US = 6
    _READ_SAMPLE_DELAY_US = 15  # when a real master samples; see _READ0_HOLD_US
    _READ0_HOLD_US = 30  # how long the synthesized device actually holds a 0
    _INTER_SLOT_RECOVERY_US = 2  # minimum release between any two bit slots

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self._slot_samples: int | None = None

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._slot_samples is None:
            self._slot_samples = self._us(builder, self._SLOT_US)

    def _us(self, builder: CaptureBuilder, microseconds: float) -> int:
        return max(round(builder.samplerate * microseconds / 1_000_000), 1)

    @property
    def bit_period_samples(self) -> int | None:
        return self._slot_samples

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("dq"), kind=SignalKind.TRISTATE, initial_level=1)]

    def _write_bit(self, builder: CaptureBuilder, bit: int, tracker: DriverTracker) -> None:
        dq = self.sig("dq")
        slot = self._slot_samples
        if bit:
            low = self._us(builder, self._SHORT_PULSE_US)
        else:
            # hold low for most of the slot, but always leave a brief
            # release at the end so consecutive 0 bits still get a fresh
            # falling edge each slot instead of merging into one long low.
            low = slot - self._us(builder, self._INTER_SLOT_RECOVERY_US)
        builder.set_level(dq, 0)
        tracker.set("master")
        builder.advance(low)
        builder.set_level(dq, 1)
        tracker.set("pullup")
        builder.advance(slot - low)

    def _read_bit(self, builder: CaptureBuilder, bit: int, tracker: DriverTracker) -> None:
        dq = self.sig("dq")
        slot = self._slot_samples
        pulse = self._us(builder, self._SHORT_PULSE_US)
        builder.set_level(dq, 0)
        tracker.set("master")
        builder.advance(pulse)
        if bit:
            builder.set_level(dq, 1)
            tracker.set("pullup")
            builder.advance(slot - pulse)
        else:
            hold = self._us(builder, self._READ0_HOLD_US)
            tracker.set("slave")  # device takes over the low pulse, no level change yet
            builder.advance(max(hold - pulse, 0))
            builder.set_level(dq, 1)
            tracker.set("pullup")
            builder.advance(max(slot - hold, 0))

    def reset(self, builder: CaptureBuilder, *, presence: bool = True) -> FrameHandle:
        self._ensure_bound(builder)
        dq = self.sig("dq")
        tracker = DriverTracker(builder, dq)
        with builder.frame() as fh:
            builder.set_level(dq, 0)
            tracker.set("master")
            builder.advance(self._us(builder, self._RESET_LOW_US))
            builder.set_level(dq, 1)
            tracker.set("pullup")
            builder.advance(self._us(builder, self._PRESENCE_DELAY_US))
            if presence:
                builder.set_level(dq, 0)
                tracker.set("slave")
                builder.advance(self._us(builder, self._PRESENCE_LOW_US))
                builder.set_level(dq, 1)
                tracker.set("pullup")
            builder.advance(self._us(builder, self._RESET_RECOVERY_US))
        tracker.close()
        builder.annotate("unit", "reset", start=fh.start, end=fh.end, signals=(dq,))
        builder.annotate(
            "field", "RESET" + (" (presence)" if presence else " (no presence)"),
            start=fh.start, end=fh.end, signals=(dq,), presence=presence,
        )
        return fh

    def write(
        self, builder: CaptureBuilder, *, data, datatype: str = "bytes", labels: list[str] | None = None
    ) -> FrameHandle:
        """`labels`, one per byte, overrides the default `format_byte`
        display — same reasoning as `UartTransport.send`'s `labels` param,
        for a stacked protocol's ROM/function command bytes."""

        data = decode_payload(data, datatype)
        self._ensure_bound(builder)
        dq = self.sig("dq")
        tracker = DriverTracker(builder, dq)
        with builder.frame() as fh:
            for i, byte in enumerate(data):
                with builder.frame() as byte_fh:
                    for bit in range(8):  # LSB first
                        self._write_bit(builder, (byte >> bit) & 1, tracker)
                builder.annotate("unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=(dq,))
                label = labels[i] if labels else format_byte(byte)
                builder.annotate(
                    "field", label, start=byte_fh.start, end=byte_fh.end, signals=(dq,), value=byte,
                )
        tracker.close()
        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(dq,))
        return fh

    def read(
        self, builder: CaptureBuilder, *, data, datatype: str = "bytes", labels: list[str] | None = None
    ) -> FrameHandle:
        """`data` is the byte sequence being synthesized as the target's
        response — this tool generates diagrams, it doesn't sense a real
        device, so the bytes "read back" are supplied by the caller."""

        data = decode_payload(data, datatype)
        self._ensure_bound(builder)
        dq = self.sig("dq")
        tracker = DriverTracker(builder, dq)
        with builder.frame() as fh:
            for i, byte in enumerate(data):
                with builder.frame() as byte_fh:
                    for bit in range(8):  # LSB first
                        self._read_bit(builder, (byte >> bit) & 1, tracker)
                builder.annotate("unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=(dq,))
                label = labels[i] if labels else format_byte(byte)
                builder.annotate(
                    "field", label, start=byte_fh.start, end=byte_fh.end, signals=(dq,), value=byte,
                )
        tracker.close()
        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(dq,))
        return fh
