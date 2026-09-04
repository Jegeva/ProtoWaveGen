from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_FORMAT_EXTENSIONS = {"svg": "svg", "sigrok": "sr", "vcd": "vcd"}


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
