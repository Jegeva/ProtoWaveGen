from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
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


@dataclass(frozen=True)
class FloatingSpan:
    """One payload bit the config/CLI author explicitly marked as "not this
    party's turn to drive" rather than a real value, via the `l`/`h`/`z`
    sentinel alphabet (see `_resolve_bit`/`_resolve_nibble`). `bit_index` is
    0 for the MSB, matching every transport's own existing bit order. This
    never affects the resolved numeric byte value (see `Payload.values`) —
    it only tells a transport's bit-clocking loop to label that bit's
    driver-annotation span `"floating"` instead of the normal owner."""

    byte_index: int
    bit_index: int
    resolution: str  # 'l' (resolves low), 'h' (resolves high), or 'z' (protocol-defined pull)


@dataclass(frozen=True)
class Payload:
    """Result of decoding a payload field: `values` is the fully resolved
    `list[int]` (identical in shape/meaning to plain `decode_payload()`'s
    return, safe for CRCs/`format_byte()`/arithmetic — a floating bit's
    value is baked in here, never a placeholder), plus `floating`, the
    positions that were marked as not-driven, for transports that want to
    render that in the `"driver"` annotation track."""

    values: list[int]
    floating: tuple[FloatingSpan, ...] = ()


_HEX_DIGITS = "0123456789abcdefABCDEF"
_FLOAT_RESOLUTIONS = {"l": "l", "L": "l", "h": "h", "H": "h", "z": "z", "Z": "z"}


def _resolve_bit(char: str, tristate: bool) -> tuple[int, str | None]:
    """One binary-literal character -> (bit value 0/1, floating resolution
    letter, or None if actually driven)."""

    if char in "01":
        return int(char), None
    resolution = _FLOAT_RESOLUTIONS.get(char)
    if resolution is None:
        raise ValueError(f"invalid bit character {char!r} (expected 0, 1, l/L, h/H, or z/Z)")
    if resolution == "l":
        return 0, "l"
    if resolution == "h":
        return 1, "h"
    if not tristate:
        raise ValueError(
            "'z'/'Z' floating marker has no defined pull for this signal; use 'l'/'L' "
            "(resolves low) or 'h'/'H' (resolves high) explicitly"
        )
    return 1, "z"  # every TRISTATE signal in this codebase today is pull-high


def _resolve_nibble(char: str, tristate: bool) -> tuple[int, str | None]:
    """One hex-string character -> (4-bit value 0-15, floating resolution
    letter, or None if actually driven)."""

    if char in _HEX_DIGITS:
        return int(char, 16), None
    resolution = _FLOAT_RESOLUTIONS.get(char)
    if resolution is None:
        raise ValueError(f"invalid hex character {char!r} (expected a hex digit, l/L, h/H, or z/Z)")
    if resolution == "l":
        return 0x0, "l"
    if resolution == "h":
        return 0xF, "h"
    if not tristate:
        raise ValueError(
            "'z'/'Z' floating marker has no defined pull for this signal; use 'l'/'L' "
            "(resolves low) or 'h'/'H' (resolves high) explicitly"
        )
    return 0xF, "z"  # every TRISTATE signal in this codebase today is pull-high


def _decode_hex(value: str, tristate: bool) -> Payload:
    if len(value) % 2 != 0:
        raise ValueError(f"hex payload {value!r} has odd length {len(value)}; nibbles come in pairs")
    values: list[int] = []
    floating: list[FloatingSpan] = []
    for byte_index in range(len(value) // 2):
        hi_char, lo_char = value[byte_index * 2], value[byte_index * 2 + 1]
        try:
            hi, hi_res = _resolve_nibble(hi_char, tristate)
            lo, lo_res = _resolve_nibble(lo_char, tristate)
        except ValueError as exc:
            raise ValueError(f"hex payload {value!r}: byte {byte_index}: {exc}") from exc
        values.append((hi << 4) | lo)
        if hi_res is not None:
            floating.extend(
                FloatingSpan(byte_index=byte_index, bit_index=i, resolution=hi_res) for i in range(4)
            )
        if lo_res is not None:
            floating.extend(
                FloatingSpan(byte_index=byte_index, bit_index=4 + i, resolution=lo_res)
                for i in range(4)
            )
    return Payload(values=values, floating=tuple(floating))


def _decode_bin(value: str, tristate: bool) -> Payload:
    values: list[int] = []
    floating: list[FloatingSpan] = []
    byte_index = 0
    for seg_index, raw_segment in enumerate(value.split(",")):
        segment = raw_segment.strip()
        if segment[:2] in ("0b", "0B"):
            segment = segment[2:]
        if not segment or len(segment) % 8 != 0:
            raise ValueError(
                f"bin payload segment {seg_index} ({raw_segment.strip()!r}) must be a nonzero "
                f"multiple of 8 bits, got {len(segment)}"
            )
        for chunk_start in range(0, len(segment), 8):
            chunk = segment[chunk_start : chunk_start + 8]
            byte_value = 0
            byte_floating: list[FloatingSpan] = []
            for bit_index, char in enumerate(chunk):
                try:
                    bit, resolution = _resolve_bit(char, tristate)
                except ValueError as exc:
                    raise ValueError(
                        f"bin payload segment {seg_index}, byte {byte_index}, bit {bit_index}: {exc}"
                    ) from exc
                byte_value = (byte_value << 1) | bit
                if resolution is not None:
                    byte_floating.append(
                        FloatingSpan(byte_index=byte_index, bit_index=bit_index, resolution=resolution)
                    )
            values.append(byte_value)
            floating.extend(byte_floating)
            byte_index += 1
    return Payload(values=values, floating=tuple(floating))


def _decode_text(value: str, tristate: bool) -> Payload:
    """UTF-8 encodes the string, except for `\\xNN` escapes: `NN` is either
    2 hex digits (a literal raw byte) or 2 chars from the `l/L/h/H/z/Z`
    floating alphabet (same nibble grammar as the `hex` datatype), letting
    e.g. `\\xzz` mark a whole floating byte inline in otherwise-plain text."""

    values: list[int] = []
    floating: list[FloatingSpan] = []
    byte_index = 0
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "\\" and i + 1 < n and value[i + 1] == "x":
            if i + 4 > n:
                raise ValueError(f"text payload {value!r}: truncated \\x escape at offset {i}")
            hi_char, lo_char = value[i + 2], value[i + 3]
            try:
                hi, hi_res = _resolve_nibble(hi_char, tristate)
                lo, lo_res = _resolve_nibble(lo_char, tristate)
            except ValueError as exc:
                raise ValueError(f"text payload {value!r}: escape at offset {i}: {exc}") from exc
            values.append((hi << 4) | lo)
            if hi_res is not None:
                floating.extend(
                    FloatingSpan(byte_index=byte_index, bit_index=bi, resolution=hi_res)
                    for bi in range(4)
                )
            if lo_res is not None:
                floating.extend(
                    FloatingSpan(byte_index=byte_index, bit_index=4 + bi, resolution=lo_res)
                    for bi in range(4)
                )
            byte_index += 1
            i += 4
        else:
            for b in value[i].encode("utf-8"):
                values.append(b)
                byte_index += 1
            i += 1
    return Payload(values=values, floating=tuple(floating))


def _decode_payload_full(value, datatype: str = "bytes", tristate: bool = False) -> Payload:
    if datatype == "bytes":
        return Payload(values=list(value))
    if datatype == "text":
        return _decode_text(value, tristate)
    if datatype == "hex":
        return _decode_hex(value, tristate)
    if datatype == "bin":
        return _decode_bin(value, tristate)
    raise ValueError(f"unknown datatype {datatype!r} (expected 'bytes', 'text', 'hex', or 'bin')")


def decode_payload(value, datatype: str = "bytes") -> list[int]:
    """Normalize a JSON operation's payload field into a `list[int]`
    regardless of how the config author chose to write it: `"bytes"`
    (default) is the original `list[int]` form, `"text"` is a JSON string
    UTF-8-encoded (plus `\\xNN` escapes), `"hex"` is a hex-digit string
    decoded via nibble pairs, `"bin"` is a comma-separable `0b`-prefixed
    binary literal. `hex`/`bin`/`text` nibbles also accept the `l/L/h/H`
    floating-marker alphabet (see `Payload`/`FloatingSpan`); `z/Z` requires
    `decode_payload_with_floating(..., tristate=True)` since a bare
    `decode_payload()` caller has no signal to resolve it against. Called at
    each JSON-facing operation method's entry — every existing per-byte
    validation downstream (range checks, CRCs, `format_byte()` display) then
    runs unchanged on the returned ints, since floating bits are always
    fully resolved to concrete values here, never a placeholder."""

    return _decode_payload_full(value, datatype).values


def decode_payload_with_floating(value, datatype: str = "bytes", tristate: bool = False) -> Payload:
    """Like `decode_payload()`, but also returns which bit positions were
    explicitly marked as floating (not this party's turn to drive) via the
    `l/L/h/H/z/Z` sentinel alphabet, for transports that render that in the
    `"driver"` annotation track (see `DriverTracker`). `tristate=True` says
    the target signal has a protocol-defined pull direction (this codebase's
    open-drain buses are all pull-high), so a bare `z`/`Z` marker resolves
    silently; otherwise `z`/`Z` raises, since guessing a resolution for a
    signal with no defined pull risks being wrong — the caller must use
    `l`/`h` explicitly there."""

    return _decode_payload_full(value, datatype, tristate)


def group_floating_by_byte(floating: tuple[FloatingSpan, ...]) -> dict[int, frozenset[int]]:
    """Group a `Payload.floating` list by byte index, so a transport's
    bit-clocking loop can check "is bit N of this byte floating" with a
    plain set-membership test per byte instead of re-scanning the whole
    list for every byte it clocks out."""

    grouped: dict[int, set[int]] = {}
    for span in floating:
        grouped.setdefault(span.byte_index, set()).add(span.bit_index)
    return {byte_index: frozenset(bits) for byte_index, bits in grouped.items()}


def resolve_single_byte(value: int, datatype: str, tristate: bool = False) -> tuple[int, frozenset[int]]:
    """For an operation field that's historically been a bare int
    (DALI's `address`/`command`/`answer`, PS/2's `byte`) rather than a
    `list[int]` payload: `datatype="bytes"` (default) passes `value`
    through unchanged (today's behavior, `value` is already a plain int,
    not iterable the way `decode_payload`'s `"bytes"` branch expects) —
    any other `datatype` treats `value` as a hex/bin/text string decoded
    via `decode_payload_with_floating`, requiring it resolve to exactly one
    byte. Returns `(resolved_byte, floating_bit_positions)` — the latter in
    `FloatingSpan`'s MSB-first (0=MSB) convention, ready for a per-bit
    `DriverTracker` loop."""

    if datatype == "bytes":
        return value, frozenset()
    payload = decode_payload_with_floating(value, datatype, tristate)
    if len(payload.values) != 1:
        raise ValueError(f"expected exactly one byte, got {len(payload.values)} from {value!r}")
    return payload.values[0], group_floating_by_byte(payload.floating).get(0, frozenset())


def decode_bits_with_floating(value: str, tristate: bool = False) -> tuple[list[int], frozenset[int]]:
    """Like `decode_payload_with_floating()`, but for a transport whose
    payload is a flat, not-necessarily-byte-aligned bit list (Wiegand's
    26-bit card frame, Microwire's opcode+address bit strings) rather than
    a byte array — the nibble-oriented `Payload`/`FloatingSpan` model
    doesn't fit those. Same `0`/`1`/`l/L`/`h/H`/`z/Z` alphabet as
    `_resolve_bit`, one character per logical bit, any length. Returns a
    flat `list[int]` (one 0/1 per position) and a flat `frozenset[int]` of
    which positions were floating — no byte grouping, since there's no byte
    structure to group by here."""

    bits: list[int] = []
    floating: set[int] = set()
    for i, char in enumerate(value):
        try:
            bit, resolution = _resolve_bit(char, tristate)
        except ValueError as exc:
            raise ValueError(f"bit payload {value!r}: position {i}: {exc}") from exc
        bits.append(bit)
        if resolution is not None:
            floating.add(i)
    return bits, frozenset(floating)


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
