"""Payload/datatype decoding: normalizing a JSON operation's byte/bit
payload field (however the config author chose to write it — a plain
int list, text, hex, a binary literal, a flat bit string) into concrete
values, plus the `l/L/h/H/z/Z` floating-bit sentinel alphabet that marks
a position as "not this party's turn to drive" instead of a real value.

Self-contained: no dependency on `CaptureBuilder`/`Signal` or anything
else from the protocol runtime (`base.py`) — purely string/int transforms,
which is what keeps this file split out from `base.py` in the first
place. `base.py`'s `DriverTracker` is what actually *renders* a
`FloatingSpan` as a `"floating"` driver-annotation span; this module only
ever produces the data describing where those positions are.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


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


def render_as_bin(payload: Payload, prefix_bytes: Sequence[int] = ()) -> str:
    """Render `prefix_bytes` (fixed, concrete bytes with no floating
    positions of their own) followed by `payload.values`
    (`payload.floating`'s positions substituted with their `l`/`h`/`z`
    marker character) as one flat `bin`-datatype string.

    For a stacked protocol whose payload field gets folded into a larger
    combined byte list alongside fixed protocol bytes (opcode, address,
    dummy/CRC-placeholder bytes) before reaching a transport method with
    one shared `datatype` parameter for the whole list (e.g.
    `SpiBus.transfer`'s `mosi`/`miso`) — this is the only way to carry the
    payload field's floating markers through that concatenation, since the
    combined list must all be the same datatype. `payload.floating`'s
    byte indices are relative to `payload.values` (index 0 is its own
    first byte); they're offset by `len(prefix_bytes)` here to land at the
    right position in the combined list."""

    prefix_len = len(prefix_bytes)
    floating_by_position = {
        (prefix_len + span.byte_index, span.bit_index): span.resolution for span in payload.floating
    }
    combined = [*prefix_bytes, *payload.values]
    chars = []
    for byte_index, byte in enumerate(combined):
        for bit_index in range(8):
            resolution = floating_by_position.get((byte_index, bit_index))
            chars.append(resolution if resolution is not None else str((byte >> (7 - bit_index)) & 1))
    return "".join(chars)


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
