from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from .protocols import get_protocol_class
from .protocols.payload import FloatingSpan, Payload, render_as_bin

_FORMAT_EXTENSIONS = {"svg": "svg", "sigrok": "sr", "vcd": "vcd"}
_PAYLOAD_FIELDS = {
    "data", "values", "mosi", "miso", "write_data", "read_data",
    "bits", "mosi_bits", "read_bits", "command", "answer", "byte",
    "DALI_ADDRESS", "facility_code", "card_number", "patterns", "info",
    "setup_data", "in_data", "out_data",
    # NOTE: plain "address" is deliberately NOT included — I2C's write/read
    # operations already use it as the (non-payload) slave-address field,
    # and this set is protocol-agnostic (shared across every protocol
    # type), so adding it would make I2C's own `address` kwarg look like
    # an ambiguous second payload candidate on every write/read op. DALI's
    # own address field is named DALI_ADDRESS (see dali.py) specifically to
    # avoid this collision while staying CLI-targetable.
}


def _parse_data_int(text: str) -> list[int]:
    values = []
    for tok in text.split(","):
        tok = tok.strip()
        try:
            value = int(tok, 0)
        except ValueError:
            raise ValueError(f"--data-int: {tok!r} is not a valid integer") from None
        if not (0 <= value <= 0xFF):
            raise ValueError(f"--data-int: {value} does not fit in a byte (0-255)")
        values.append(value)
    return values


def _load_data_file(path: str) -> list[int]:
    """Read a `--data-file` path's raw bytes. CWD-relative, matching
    `--config`'s own resolution (`load_json_config` below does a plain
    `open(path)` with no directory-relative logic)."""

    try:
        with open(path, "rb") as f:
            return list(f.read())
    except OSError as exc:
        raise ValueError(f"--data-file: could not read {path!r}: {exc}") from None


def _find_payload_candidates(protocols: list[dict]) -> list[tuple[int, int, str]]:
    """Every (protocol_index, op_index, field) already present in `protocols`
    whose field name is a recognized payload field."""

    candidates = []
    for p_idx, spec in enumerate(protocols):
        for op_idx, op in enumerate(spec.get("operations", [])):
            for field in op:
                if field in _PAYLOAD_FIELDS:
                    candidates.append((p_idx, op_idx, field))
    return candidates


def _describe_candidate(protocols: list[dict], p_idx: int, op_idx: int, field: str) -> str:
    op_name = protocols[p_idx]["operations"][op_idx].get("op", "?")
    return f"{protocols[p_idx]['id']}:{op_idx}:{field} (op={op_name})"


def _split_inline_target(raw: str) -> tuple[str | None, str]:
    """Split one repeatable `--data-*` flag's raw value into an optional
    inline `protocol_id:op_index[:field]` target prefix and the actual
    payload value, e.g. `"i2c0:0:data:0b1101"` -> `("i2c0:0:data",
    "0b1101")`. A leading `:` forces "no target, auto-detect" even when the
    payload value itself contains colons (`":a:b:c"` -> `(None, "a:b:c")`);
    with no leading `:`, a 3-segment field name is only recognized as part
    of the target when it's one of `_PAYLOAD_FIELDS` (a closed, known
    vocabulary), so a value with fewer than that many colons is never
    mistaken for a target. With no colon at all, the whole string is the
    value (auto-detect), matching the legacy single-flag usage."""

    if raw.startswith(":"):
        return None, raw[1:]
    parts = raw.split(":")
    if len(parts) < 3:
        return None, raw
    if len(parts) >= 4 and parts[2] in _PAYLOAD_FIELDS:
        return ":".join(parts[:3]), ":".join(parts[3:])
    return ":".join(parts[:2]), ":".join(parts[2:])


def _datatype_kwarg_name(cls: type, op_name: str, field: str) -> str:
    """Which kwarg name this operation method actually uses to select
    `field`'s datatype: the shared `"datatype"` most protocols use, or a
    per-field `f"{field}_datatype"` (DALI's `send_forward_frame`, Wiegand's
    `send_card_26bit`, I2C's `write_then_read` — all have multiple
    independently-typed byte fields on one operation, so each needs its
    own datatype selector). Resolved from the method's real signature
    rather than a hardcoded protocol-name check, so this doesn't need
    updating if another protocol adopts the same per-field convention
    later."""

    method = getattr(cls, op_name, None)
    if method is None:
        raise ValueError(f"{cls.__name__} has no operation {op_name!r}")
    params = inspect.signature(method).parameters
    prefixed = f"{field}_datatype"
    if prefixed in params:
        return prefixed
    if "datatype" in params:
        return "datatype"
    raise ValueError(
        f"--data-*: {cls.__name__}.{op_name}() has no datatype parameter for field {field!r} "
        f"(expected {prefixed!r} or 'datatype')"
    )


def _resolve_data_target(protocols: list[dict], target: str | None) -> tuple[int, int, str]:
    """Resolve a `--data-target protocol_id:op_index[:field]` string (or,
    if not given, an unambiguous auto-detected payload field) to
    `(protocol_index, op_index, field)`."""

    if target is None:
        candidates = _find_payload_candidates(protocols)
        if not candidates:
            raise ValueError(
                "no data-carrying operation found to target; specify --data-target"
            )
        if len(candidates) > 1:
            described = ", ".join(_describe_candidate(protocols, *c) for c in candidates)
            raise ValueError(
                f"multiple data-carrying operations found ({described}); "
                "specify which one with --data-target protocol_id:op_index[:field]"
            )
        return candidates[0]

    parts = target.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"--data-target must be 'protocol_id:op_index' or 'protocol_id:op_index:field', got {target!r}"
        )
    protocol_id = parts[0]
    try:
        op_index = int(parts[1])
    except ValueError:
        raise ValueError(f"--data-target: {parts[1]!r} is not a valid operation index") from None
    field = parts[2] if len(parts) == 3 else None

    p_idx = next((i for i, spec in enumerate(protocols) if spec.get("id") == protocol_id), None)
    if p_idx is None:
        known = ", ".join(spec.get("id", "?") for spec in protocols)
        raise ValueError(f"--data-target: unknown protocol id {protocol_id!r} (known: {known})")

    operations = protocols[p_idx].get("operations", [])
    if not (0 <= op_index < len(operations)):
        raise ValueError(
            f"--data-target: op_index {op_index} out of range for {protocol_id!r} "
            f"(has {len(operations)} operation(s))"
        )

    if field is not None:
        if field not in _PAYLOAD_FIELDS:
            raise ValueError(
                f"--data-target: unrecognized field {field!r} (expected one of {sorted(_PAYLOAD_FIELDS)})"
            )
        # `field` being in the tool-wide _PAYLOAD_FIELDS set only means SOME
        # protocol somewhere uses that name for a real payload field (e.g.
        # DALI's `command`) -- it does NOT mean the *target* operation's own
        # `field` kwarg is that same kind of thing. A field name can
        # coincidentally collide (IrDA's `command` is a plain bool; RC-5/
        # NEC/RC-6's `command` is an IR command code, neither a byte-array)
        # while the target method still happens to expose an unrelated
        # `datatype` kwarg (meant for a different field on the same
        # operation) that would otherwise let this slip through unnoticed
        # and silently corrupt that unrelated field with a list, crashing
        # confusingly deep inside protocol logic instead of erroring here
        # (found this exact shape via `--data-int` on IrDA's `command`
        # while rewriting its end-user docs this session). Guard against it
        # by requiring `field` be a real parameter of the target method too
        # -- same signature-introspection `_resolve_set_target` already
        # does for `--set`.
        cls = get_protocol_class(protocols[p_idx]["type"])
        op_name = operations[op_index].get("op", "?")
        method = getattr(cls, op_name, None)
        if method is None:
            raise ValueError(f"--data-target: {cls.__name__} has no operation {op_name!r}")
        sig_params = inspect.signature(method).parameters
        real_params = [p for p in sig_params if p not in ("self", "builder")]
        if field not in real_params:
            raise ValueError(
                f"--data-target: {cls.__name__}.{op_name}() has no parameter {field!r} "
                f"(real parameters: {sorted(real_params)})"
            )
        # A real parameter can still be the wrong *kind* of thing despite
        # sharing a `_PAYLOAD_FIELDS` name: IrDA's `command`/`final` are
        # plain `bool` flags (the frame's C/R and P/F bits), not byte
        # arrays -- `--data-*` datatype conversion can never sanely produce
        # a bool, so a `bool`-annotated field is rejected outright rather
        # than silently corrupting it with a list and crashing confusingly
        # deep inside protocol logic (the exact failure this replaces).
        if sig_params[field].annotation in (bool, "bool"):
            raise ValueError(
                f"--data-target: {cls.__name__}.{op_name}()'s {field!r} is a boolean flag, not a "
                f"payload field -- use --set {protocols[p_idx]['id']}:{op_index}:{field}=true|false instead"
            )
        return p_idx, op_index, field

    op_fields = [f for f in operations[op_index] if f in _PAYLOAD_FIELDS]
    if not op_fields:
        raise ValueError(
            f"--data-target: operation {protocol_id}:{op_index} has no payload field; "
            f"specify one explicitly, one of {sorted(_PAYLOAD_FIELDS)}"
        )
    if len(op_fields) > 1:
        raise ValueError(
            f"--data-target: operation {protocol_id}:{op_index} has multiple payload fields "
            f"({', '.join(op_fields)}); specify which via --data-target {protocol_id}:{op_index}:<field>"
        )
    return p_idx, op_index, op_fields[0]


def _resolve_set_target(protocols: list[dict], target: str) -> tuple[int, int, str]:
    """Resolve a `--set protocol_id:op_index:field` string to
    `(protocol_index, op_index, field)`. Unlike `_resolve_data_target`,
    always requires the full triple (no auto-detect — a scalar field name
    isn't a closed vocabulary the way `_PAYLOAD_FIELDS` is, so there's
    nothing to search over), and validates `field` against the target
    operation method's real parameter names via `inspect.signature`
    (same introspection `_datatype_kwarg_name` uses) rather than against
    `_PAYLOAD_FIELDS` — `--set` is for scalar params, `_PAYLOAD_FIELDS`
    members are byte-array params `--data-*` already owns."""

    parts = target.split(":")
    if len(parts) != 3:
        raise ValueError(f"--set target must be 'protocol_id:op_index:field', got {target!r}")
    protocol_id, op_index_str, field = parts
    try:
        op_index = int(op_index_str)
    except ValueError:
        raise ValueError(f"--set: {op_index_str!r} is not a valid operation index") from None

    p_idx = next((i for i, spec in enumerate(protocols) if spec.get("id") == protocol_id), None)
    if p_idx is None:
        known = ", ".join(spec.get("id", "?") for spec in protocols)
        raise ValueError(f"--set: unknown protocol id {protocol_id!r} (known: {known})")

    operations = protocols[p_idx].get("operations", [])
    if not (0 <= op_index < len(operations)):
        raise ValueError(
            f"--set: op_index {op_index} out of range for {protocol_id!r} "
            f"(has {len(operations)} operation(s))"
        )

    if field in _PAYLOAD_FIELDS:
        raise ValueError(
            f"--set: {field!r} is a payload (byte-array) field, not a scalar — use "
            f"--data-hex/--data-string/--data-int/--data-bin/--data-bits/--data-file instead"
        )

    cls = get_protocol_class(protocols[p_idx]["type"])
    op_name = operations[op_index].get("op", "?")
    method = getattr(cls, op_name, None)
    if method is None:
        raise ValueError(f"--set: {cls.__name__} has no operation {op_name!r}")
    real_params = [p for p in inspect.signature(method).parameters if p not in ("self", "builder")]
    if field not in real_params:
        raise ValueError(
            f"--set: {cls.__name__}.{op_name}() has no parameter {field!r} "
            f"(real parameters: {sorted(real_params)})"
        )

    return p_idx, op_index, field


def _parse_set_value(raw: str) -> object:
    """Best-effort scalar type coercion for a `--set ...=value` value:
    int (plain decimal, or `0x`/`0b` prefixed, matching `_parse_data_int`'s
    single-token convention) first, then float, then `true`/`false`
    (case-insensitive) as bool, else the raw string as-is — needed for
    e.g. DS1307's ISO-8601 `dt` field. No list/array support — that's
    still `--data-*` or a JSON edit."""

    try:
        return int(raw, 0)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return raw


def apply_field_override(protocols: list[dict], args) -> list[dict]:
    """Apply every `--set protocol_id:op_index:field=value` override, in
    command-line order. Each occurrence must fully specify its own target
    (see `_resolve_set_target`) — there's no `--data-target`-style fallback
    since a scalar field name isn't a closed vocabulary to auto-detect
    over. Two overrides targeting the same field is a conflict, not a
    silent overwrite, same as `apply_data_override`. No-op if no `--set`
    flag was given."""

    overrides = getattr(args, "field_overrides", None) or []
    if not overrides:
        return protocols

    protocols = list(protocols)
    touched: dict[tuple[int, int, str], str] = {}

    for raw in overrides:
        target, sep, value_str = raw.partition("=")
        if not sep:
            raise ValueError(f"--set: expected 'protocol_id:op_index:field=value', got {raw!r}")
        p_idx, op_idx, field = _resolve_set_target(protocols, target)

        key = (p_idx, op_idx, field)
        this_override = f"--set {raw!r}"
        if key in touched:
            raise ValueError(
                f"conflicting --set overrides target the same field "
                f"{protocols[p_idx]['id']}:{op_idx}:{field}: {touched[key]} and {this_override}"
            )
        touched[key] = this_override

        spec = dict(protocols[p_idx])
        operations = list(spec["operations"])
        op = dict(operations[op_idx])
        op[field] = _parse_set_value(value_str)
        operations[op_idx] = op
        spec["operations"] = operations
        protocols[p_idx] = spec

    return protocols


_MASK_RESOLUTIONS = {"l", "L", "h", "H", "z", "Z"}


def _parse_data_mask(path: str) -> list[tuple[int, int | None, str]]:
    """Parse a `--data-mask` companion file: comma/newline-separated
    entries, each `byte_index:resolution` (whole byte) or
    `byte_index.bit_index:resolution` (one bit, 0=MSB convention matching
    `FloatingSpan`), resolution one of l/h/z (case preserved)."""

    try:
        with open(path) as f:
            text = f.read()
    except OSError as exc:
        raise ValueError(f"--data-mask: could not read {path!r}: {exc}") from None

    entries: list[tuple[int, int | None, str]] = []
    for raw_tok in text.replace("\n", ",").split(","):
        tok = raw_tok.strip()
        if not tok:
            continue
        try:
            position, resolution = tok.split(":")
        except ValueError:
            raise ValueError(
                f"--data-mask: invalid entry {tok!r} in {path!r} "
                "(expected 'byte_index:resolution' or 'byte_index.bit_index:resolution')"
            ) from None
        resolution = resolution.strip()
        if resolution not in _MASK_RESOLUTIONS:
            raise ValueError(
                f"--data-mask: invalid resolution {resolution!r} in entry {tok!r} (expected l/h/z)"
            )
        position = position.strip()
        if "." in position:
            byte_str, bit_str = position.split(".")
            try:
                byte_index, bit_index = int(byte_str), int(bit_str)
            except ValueError:
                raise ValueError(f"--data-mask: invalid position {position!r} in entry {tok!r}") from None
            if not (0 <= bit_index <= 7):
                raise ValueError(f"--data-mask: bit index {bit_index} out of range 0-7 in entry {tok!r}")
        else:
            try:
                byte_index = int(position)
            except ValueError:
                raise ValueError(f"--data-mask: invalid byte index {position!r} in entry {tok!r}") from None
            bit_index = None
        entries.append((byte_index, bit_index, resolution))
    return entries


def _apply_data_mask(values: list[int], mask_entries: list[tuple[int, int | None, str]]) -> str:
    """Render `values` (a plain byte list) as a flat `bin`-datatype string,
    substituting each mask entry's l/h/z character for the bits it covers
    and the real 0/1 digit everywhere else — resolution happens later,
    downstream, the same way a hand-typed `--data-bin` marker would. Built
    on `render_as_bin` (`protocols/payload.py`), the same byte-list-plus-
    floating-positions-to-flat-bin-string logic a stacked protocol uses to
    fold a floating-marked payload field into a larger combined byte list."""

    floating: list[FloatingSpan] = []
    for byte_index, bit_index, resolution in mask_entries:
        if not (0 <= byte_index < len(values)):
            raise ValueError(
                f"--data-mask: byte index {byte_index} out of range (payload has {len(values)} byte(s))"
            )
        if bit_index is None:
            floating.extend(
                FloatingSpan(byte_index=byte_index, bit_index=b, resolution=resolution) for b in range(8)
            )
        else:
            floating.append(FloatingSpan(byte_index=byte_index, bit_index=bit_index, resolution=resolution))

    return render_as_bin(Payload(values=values, floating=tuple(floating)))


def apply_data_mask(protocols: list[dict], args) -> list[dict]:
    """Apply every `--data-mask` (second pass, after `apply_data_override`
    has resolved every `--data-*` value override). Each mask's target must
    currently resolve to a plain `list[int]` under datatype `"bytes"` — the
    common shape for a `--data-file`-loaded payload too large to hand-type
    a marker into directly."""

    masks = getattr(args, "data_masks", None) or []
    if not masks:
        return protocols

    protocols = list(protocols)
    touched: dict[tuple[int, int, str], str] = {}

    for raw_mask in masks:
        inline_target, path = _split_inline_target(raw_mask)
        target = inline_target if inline_target is not None else args.data_target
        p_idx, op_idx, field = _resolve_data_target(protocols, target)

        key = (p_idx, op_idx, field)
        this_mask = f"--data-mask {raw_mask!r}"
        if key in touched:
            raise ValueError(
                f"conflicting --data-mask overrides target the same field "
                f"{protocols[p_idx]['id']}:{op_idx}:{field}: {touched[key]} and {this_mask}"
            )
        touched[key] = this_mask

        spec = dict(protocols[p_idx])
        operations = list(spec["operations"])
        op = dict(operations[op_idx])
        cls = get_protocol_class(spec["type"])
        op_name = operations[op_idx]["op"]
        datatype_kwarg = _datatype_kwarg_name(cls, op_name, field)

        current_datatype = op.get(datatype_kwarg, "bytes")
        if current_datatype != "bytes":
            raise ValueError(
                f"--data-mask: {protocols[p_idx]['id']}:{op_idx}:{field} is using datatype "
                f"{current_datatype!r}, not 'bytes' — a mask only applies to a concrete byte list"
            )
        values = op.get(field)
        if not isinstance(values, list):
            raise ValueError(
                f"--data-mask: {protocols[p_idx]['id']}:{op_idx}:{field} is not a byte-list field "
                f"(got {type(values).__name__})"
            )

        mask_entries = _parse_data_mask(path)
        op[field] = _apply_data_mask(values, mask_entries)
        op[datatype_kwarg] = "bin"
        operations[op_idx] = op
        spec["operations"] = operations
        protocols[p_idx] = spec

    return protocols


def apply_data_override(protocols: list[dict], args) -> list[dict]:
    """Apply every `--data-hex`/`--data-string`/`--data-int`/`--data-bin`/
    `--data-file` override — each repeatable and independently targeted —
    to the config,
    in the exact order given on the command line (see `_DataOverrideAction`
    in `main.py`, which is what makes cross-flag order survive argparse).
    Each occurrence's raw value may carry an inline
    `protocol_id:op_index[:field]:` target prefix (`_split_inline_target`);
    with none, the target falls back to the legacy global `--data-target`
    flag, auto-detecting if that's unset too. Two overrides in the same
    invocation resolving to the same operation/field is a conflict, not a
    silent overwrite. No-op if no `--data-*` flag was given."""

    overrides = getattr(args, "data_overrides", None) or []
    if not overrides:
        return protocols

    protocols = list(protocols)
    touched: dict[tuple[int, int, str], str] = {}

    for option_string, datatype, raw_value in overrides:
        inline_target, value = _split_inline_target(raw_value)
        target = inline_target if inline_target is not None else args.data_target
        p_idx, op_idx, field = _resolve_data_target(protocols, target)

        key = (p_idx, op_idx, field)
        this_override = f"{option_string} {raw_value!r}"
        if key in touched:
            raise ValueError(
                f"conflicting --data-* overrides target the same field "
                f"{protocols[p_idx]['id']}:{op_idx}:{field}: {touched[key]} and {this_override}"
            )
        touched[key] = this_override

        if datatype == "bytes":
            value = _parse_data_int(value)
        elif datatype == "file":
            value = _load_data_file(value)
            datatype = "bytes"

        spec = dict(protocols[p_idx])
        operations = list(spec["operations"])
        op = dict(operations[op_idx])
        cls = get_protocol_class(spec["type"])
        op_name = operations[op_idx]["op"]
        op[field] = value
        op[_datatype_kwarg_name(cls, op_name, field)] = datatype
        operations[op_idx] = op
        spec["operations"] = operations
        protocols[p_idx] = spec

    return protocols


@dataclass
class Config:
    samplerate: int
    protocols: list[dict]
    outputs: list[dict]
    unit_bits: int | None = None
    idle_margin_fraction: float = 0.02


def load_json_config(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


def resolve_config(json_cfg: dict, args) -> Config:
    """Merge JSON scenario config with CLI overrides.

    Precedence: built-in defaults < JSON config file < CLI arguments.
    `--format` replaces the JSON `outputs` list entirely (an explicit,
    all-or-nothing override, since format selection and per-format options
    can't both come from a flat CLI flag); with no `--format`, `--output-dir`
    alone just relocates the JSON-declared outputs into that directory.
    `--format` with no `--output-dir` defaults to `./output`. Overrides
    resolve in three passes: `apply_field_override` applies every `--set`
    scalar-field override first, then `apply_data_override` applies every
    `--data-*` payload value flag, then `apply_data_mask` applies every
    `--data-mask` on top — a mask requires its target to have already
    resolved to datatype `"bytes"`. `--set` and `--data-*` can never target
    the same field (`--set` rejects any field in `_PAYLOAD_FIELDS`), so the
    three passes can't conflict with each other.
    """

    samplerate = args.samplerate if args.samplerate is not None else json_cfg.get("samplerate")
    if not samplerate:
        raise ValueError("samplerate must be set via the JSON config's 'samplerate' or --samplerate")

    protocols = json_cfg.get("protocols", [])
    protocols = apply_field_override(protocols, args)
    protocols = apply_data_override(protocols, args)
    protocols = apply_data_mask(protocols, args)
    outputs = list(json_cfg.get("outputs", []))
    unit_bits = getattr(args, "unit_bits", None) if getattr(args, "unit_bits", None) is not None else json_cfg.get("unit_bits")
    idle_margin_fraction = json_cfg.get("idle_margin_fraction", 0.02)

    if args.format:
        output_dir = Path(args.output_dir or "./output")
        outputs = [
            {"type": fmt, "path": str(output_dir / f"capture.{_FORMAT_EXTENSIONS[fmt]}")}
            for fmt in args.format
        ]
    elif args.output_dir:
        output_dir = Path(args.output_dir)
        outputs = [{**o, "path": str(output_dir / Path(o["path"]).name)} for o in outputs]

    if getattr(args, "svg_verbose", False):
        outputs = [{**o, "verbose": True} if o.get("type") == "svg" else o for o in outputs]

    return Config(
        samplerate=samplerate, protocols=protocols, outputs=outputs,
        unit_bits=unit_bits, idle_margin_fraction=idle_margin_fraction,
    )
