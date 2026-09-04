from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_FORMAT_EXTENSIONS = {"svg": "svg", "sigrok": "sr", "vcd": "vcd"}
_PAYLOAD_FIELDS = {"data", "values", "mosi", "miso", "write_data", "read_data"}


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


def apply_data_override(protocols: list[dict], args) -> list[dict]:
    """Apply `--data-hex`/`--data-string`/`--data-int` (at most one given)
    to the operation named by `--data-target`, or an unambiguous
    auto-detected one if `--data-target` is omitted. No-op if none of the
    three `--data-*` flags were given."""

    if args.data_hex is not None:
        value, datatype = args.data_hex, "hex"
    elif args.data_string is not None:
        value, datatype = args.data_string, "text"
    elif args.data_int is not None:
        value, datatype = _parse_data_int(args.data_int), "bytes"
    else:
        return protocols

    p_idx, op_idx, field = _resolve_data_target(protocols, args.data_target)

    protocols = list(protocols)
    spec = dict(protocols[p_idx])
    operations = list(spec["operations"])
    op = dict(operations[op_idx])
    op[field] = value
    op["datatype"] = datatype
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
    `--format` with no `--output-dir` defaults to `./output`.
    """

    samplerate = args.samplerate if args.samplerate is not None else json_cfg.get("samplerate")
    if not samplerate:
        raise ValueError("samplerate must be set via the JSON config's 'samplerate' or --samplerate")

    protocols = json_cfg.get("protocols", [])
    protocols = apply_data_override(protocols, args)
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
