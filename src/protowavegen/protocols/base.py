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


def decode_payload(value, datatype: str = "bytes") -> list[int]:
    """Normalize a JSON operation's payload field into a `list[int]`
    regardless of how the config author chose to write it: `"bytes"`
    (default) is the original `list[int]` form, `"text"` is a JSON string
    UTF-8-encoded, `"hex"` is a hex-digit string decoded via
    `bytes.fromhex`. Called at each JSON-facing operation method's entry —
    every existing per-byte validation downstream (range checks, CRCs,
    `format_byte()` display) then runs unchanged on the returned ints."""

    if datatype == "bytes":
        return list(value)
    if datatype == "text":
        return list(value.encode("utf-8"))
    if datatype == "hex":
        return list(bytes.fromhex(value))
    raise ValueError(f"unknown datatype {datatype!r} (expected 'bytes', 'text', or 'hex')")


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
