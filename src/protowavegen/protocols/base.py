"""Protocol-authoring runtime: the registry/dispatch machinery every
protocol plugs into (`Protocol`/`TransportProtocol`/`StackedProtocol`/
`register_protocol`/`get_protocol_class`), driver/annotation tracking
(`DriverTracker`), display formatting (`format_byte`), and shared timing/
bit-order helpers. Payload/datatype decoding (the `l/L/h/H/z/Z` floating-
marker alphabet and friends) lives in the sibling `payload` module —
self-contained, no `CaptureBuilder`/`Signal` dependency, so it doesn't
belong in this file."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..model import CaptureBuilder, Signal

_REGISTRY: dict[str, type["Protocol"]] = {}


def register_protocol(name: str) -> Callable[[type], type]:
    """Class decorator registering a protocol under `name` for use in JSON
    scenario files' `protocols[].type` field. New protocols plug in without
    touching the app/core — just import the module once (see
    `protocols/__init__.py`)."""

    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"protocol {name!r} already registered")
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_protocol_class(name: str) -> type["Protocol"]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown protocol type {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def format_byte(byte: int) -> str:
    """Render a byte as its hex value, plus the printable ASCII character it
    represents when there is one — e.g. `"0x41 'A'"` vs `"0x07"`. Used
    unconditionally (not just in verbose mode) for any full data-unit
    metadata — UART bytes, I2C data bytes, SPI transfer bytes — since the
    actual payload is the core identity of that block, not extra detail."""

    if 32 <= byte < 127:
        return f"0x{byte:02X} {chr(byte)!r}"
    return f"0x{byte:02X}"


def bind_clock_samples(
    samplerate: int, hz: int, *, hz_label: str, divisor: int = 2, minimum: int = 1,
    minimum_note: str = "",
) -> int:
    """Shared formula+validation behind every clocked transport's own
    `bind_samplerate()` (I2C/SPI/Microwire/PS2's per-half-clock, CAN/UART's
    per-bit, DALI's per-bit-with-a-2-sample-minimum for Manchester): convert
    a `clock_hz`/`bitrate`/`baudrate` value into a sample count, raising a
    consistently-worded error when the samplerate can't represent it.
    `divisor=2` for a half-clock/half-bit count (the common case — data
    changes and gets sampled at different points within one bit), `1` for a
    whole-bit count. `minimum=2` (with `minimum_note` explaining why) covers
    Manchester encoding, which needs both halves of a bit distinguishable.
    Each caller keeps its own method name/ivar/`bit_period_samples` shape —
    this only replaces the copy-pasted arithmetic+message inside it."""

    samples = round(samplerate / (divisor * hz))
    if samples < minimum:
        needed = divisor * hz * minimum
        note = f" {minimum_note}" if minimum_note else ""
        raise ValueError(f"samplerate {samplerate} too low for {hz_label} {hz} (need at least {needed} Hz{note})")
    return samples


def bits_of_byte(byte: int, order: str = "msb") -> list[int]:
    """One byte's 8 bits as a list. `order="msb"` (default) puts the MSB
    first — index 0 then directly matches `FloatingSpan.bit_index`'s own
    MSB-first (0=MSB) convention via plain `enumerate()`, no separate
    `7 - i` conversion needed at the call site. `order="lsb"` puts the LSB
    first instead (UART/1-Wire/PS2's wire order) — those callers still need
    the `7 - i` conversion to get back to `FloatingSpan`'s convention.
    Range-checked here (`0 <= byte <= 0xFF`) rather than at each call
    site — the one validation every 8-bit transport needs regardless of
    bit order."""

    if not (0 <= byte <= 0xFF):
        raise ValueError(f"byte {byte} does not fit in 8 bits")
    bit_positions = reversed(range(8)) if order == "msb" else range(8)
    return [(byte >> i) & 1 for i in bit_positions]


def microseconds_to_samples(builder: CaptureBuilder, microseconds: float) -> int:
    """Fixed-microsecond timing (1-Wire slot/reset timing, Wiegand pulse/
    interval, NES gamepad latch/clock) converted to samples at the
    capture's samplerate, floored at 1 sample so a very short interval
    never rounds away to a zero-length (i.e. invisible) pulse."""

    return max(round(builder.samplerate * microseconds / 1_000_000), 1)


class Protocol(ABC):
    """Base for every protocol node, transport or stacked.

    Two-phase use by the app (`app.py`): every node's `register_signals` runs
    before any node's `generate`, so signal declaration order in the JSON
    `protocols` list never matters — a stacked protocol can safely call its
    transport's methods regardless of which one was declared first.
    """

    def __init__(self, node_id: str):
        self.node_id = node_id

    def sig(self, local_name: str) -> str:
        """Node-id-prefixed signal name (e.g. `"uart1.tx"`), so two instances
        of the same protocol type in one capture never collide."""

        return f"{self.node_id}.{local_name}"

    @abstractmethod
    def get_signals(self) -> list[Signal]:
        """Signals this node introduces onto the bus. Stacked protocols that
        reuse a transport's wires without adding their own return []."""

    @abstractmethod
    def generate(self, builder: CaptureBuilder) -> None:
        """Replay this node's configured operations into `builder`."""

    def register_signals(self, builder: CaptureBuilder) -> None:
        for signal in self.get_signals():
            if not builder.has_signal(signal.name):
                builder.register_signal(signal)


class _OperationReplayMixin:
    """Shared `generate()` for both TransportProtocol and StackedProtocol:
    replay a JSON-sourced `operations` list by dispatching each `{"op": name,
    ...kwargs}` entry to a same-named method. Keeps everything data-driven
    from JSON without a generic dynamic-dispatch layer living in the ABC."""

    operations: list[dict]

    def generate(self, builder: CaptureBuilder) -> None:
        for raw_op in self.operations:
            op = dict(raw_op)
            name = op.pop("op")
            method = getattr(self, name, None)
            if method is None or not callable(method):
                raise ValueError(f"{type(self).__name__} has no operation {name!r}")
            method(builder, **op)


class DriverTracker:
    """Coalesces who's-driving-this-signal spans into one annotation per
    contiguous span instead of one per bit/half-cycle.

    Built for open-drain buses (I2C, 1-Wire): a wire is never actively driven
    high, only actively pulled low or released (pulled high by a resistor).
    Callers pass the *effective* driver at each level change — pass
    `"pullup"` (or whatever label represents the passive pull network)
    whenever the level being set is the released/high state, and the real
    owner (`"master"`, `"slave"`, ...) whenever it's actively pulled low. The
    tracker only emits an annotation when the driver actually changes, so a
    device holding the line low (or the bus sitting released) for many bit
    periods in a row produces a single annotation, not one per bit.
    """

    def __init__(self, builder: CaptureBuilder, signal_name: str):
        self._builder = builder
        self._signal = signal_name
        self._driver: str | None = None
        self._start: int | None = None

    def set(self, driver: str, at: int | None = None) -> None:
        at = self._builder.cursor if at is None else at
        if driver == self._driver:
            return
        self._flush(at)
        self._driver = driver
        self._start = at

    def _flush(self, end: int) -> None:
        if self._driver is not None and end > self._start:
            self._builder.annotate(
                "driver", self._driver, start=self._start, end=end, signals=(self._signal,)
            )

    def close(self, at: int | None = None) -> None:
        at = self._builder.cursor if at is None else at
        self._flush(at)
        self._driver = None
        self._start = None


class TransportProtocol(_OperationReplayMixin, Protocol):
    """Link-layer protocol (UART/I2C/SPI/...) driven by a JSON `operations`
    list. Subclasses expose one real method per supported `op` name (e.g.
    `.write(builder, ...)`), each returning a `FrameHandle` so a protocol
    stacked on top can annotate the exact range just emitted."""

    def __init__(self, node_id: str, operations: list[dict] | None = None):
        super().__init__(node_id)
        self.operations = operations or []


class StackedProtocol(_OperationReplayMixin, Protocol):
    """Application-layer protocol wrapping an already-built transport
    instance (e.g. LM75 on I2C, LIN/Modbus on UART, JEDEC CFI/NES-gamepad on
    SPI). Adds no signals of its own unless a subclass overrides
    `get_signals` (e.g. to alias an extra latch line)."""

    def __init__(self, node_id: str, transport: Protocol, operations: list[dict] | None = None):
        super().__init__(node_id)
        self.transport = transport
        self.operations = operations or []

    def get_signals(self) -> list[Signal]:
        return []
