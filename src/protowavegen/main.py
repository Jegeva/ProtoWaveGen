from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .app import TimingDiagramApplication
from .config import load_json_config, resolve_config

_DATA_OVERRIDE_DATATYPES = {
    "--data-hex": "hex",
    "--data-string": "text",
    "--data-int": "bytes",
    "--data-bin": "bin",
    "--data-bits": "bits",
    "--data-file": "file",
}


class _DataMaskAction(argparse.Action):
    """Collects every `--data-mask` occurrence into its own `data_masks`
    list, separate from `data_overrides` — masks apply in a second pass
    after every `--data-*` value override has resolved, so they can't be
    mixed into that chain's ordering/conflict logic."""

    def __call__(self, parser, namespace, values, option_string=None):
        masks = getattr(namespace, "data_masks", None)
        if masks is None:
            masks = []
            setattr(namespace, "data_masks", masks)
        masks.append(values)


class _SetOverrideAction(argparse.Action):
    """Collects every `--set` occurrence, in order, into `field_overrides`
    — simpler than `_DataOverrideAction` since `--set` is its own single
    flag (no sibling `--data-hex`/`--data-string`/etc. flags sharing one
    dest to interleave)."""

    def __call__(self, parser, namespace, values, option_string=None):
        overrides = getattr(namespace, "field_overrides", None)
        if overrides is None:
            overrides = []
            setattr(namespace, "field_overrides", overrides)
        overrides.append(values)


class _DataOverrideAction(argparse.Action):
    """Collects every `--data-hex`/`--data-string`/`--data-int`/`--data-bin`
    occurrence, across all four flags, into one shared `data_overrides` list
    of `(option_string, datatype, raw_value)` tuples in true command-line
    order. A plain per-flag `action="append"` would keep each flag's own
    values in order but lose the *relative* order between different flags —
    needed here since `apply_data_override` (`config.py`) reports a
    same-target conflict by naming which override came first."""

    def __call__(self, parser, namespace, values, option_string=None):
        overrides = getattr(namespace, "data_overrides", None)
        if overrides is None:
            overrides = []
            setattr(namespace, "data_overrides", overrides)
        overrides.append((option_string, _DATA_OVERRIDE_DATATYPES[option_string], values))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="protowavegen",
        description="Synthesize embedded-protocol timing diagrams to SVG and sigrok/VCD captures.",
    )
    parser.add_argument("--config", required=True, help="JSON scenario config file")
    parser.add_argument(
        "--output-dir",
        help="Directory outputs are written to (overrides/relocates JSON outputs). "
        "Defaults to ./output when used with --format.",
    )
    parser.add_argument("--samplerate", type=int, help="Override the config's samplerate (Hz)")
    parser.add_argument(
        "--format",
        action="append",
        choices=["svg", "sigrok", "vcd"],
        help="Replace the JSON outputs list with one capture.<ext> per given format (repeatable)",
    )
    parser.add_argument(
        "--unit-bits", type=int, dest="unit_bits",
        help="Override the per-protocol framing unit with a fixed N-bit grouping for SVG unit bars",
    )
    parser.add_argument(
        "--svg-verbose", action="store_true", dest="svg_verbose",
        help="Render protocol field descriptions inline on any SVG output",
    )
    parser.add_argument(
        "--data-hex", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]HEX",
        help="Override an operation's payload with a hex-digit string (e.g. deadbeef; also "
        "accepts l/L, h/H, z/Z per nibble as a floating-bit marker). Repeatable, chainable "
        "with the other --data-* flags; each occurrence may carry its own inline "
        "protocol_id:op_index[:field]: target prefix (see --data-target).",
    )
    parser.add_argument(
        "--data-string", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]TEXT",
        help="Override an operation's payload with a UTF-8 text string (supports \\xNN escapes: "
        "NN is 2 hex digits for a raw byte, or l/L, h/H, z/Z for a floating-bit marker). "
        "Repeatable/chainable, same inline-target syntax as --data-hex.",
    )
    parser.add_argument(
        "--data-int", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]INTS",
        help="Override an operation's payload with comma-separated byte values (e.g. "
        "72,101,108,108,111). Repeatable/chainable, same inline-target syntax as --data-hex.",
    )
    parser.add_argument(
        "--data-bin", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]BIN",
        help="Override an operation's payload with a comma-separable 0b-prefixed binary literal "
        "(e.g. 0b11010100; also accepts l/L, h/H, z/Z per bit). Repeatable/chainable, same "
        "inline-target syntax as --data-hex.",
    )
    parser.add_argument(
        "--data-bits", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]BITS",
        help="Override a flat bit-list payload (Microwire's mosi_bits/read_bits, Wiegand's bits) "
        "with a 0/1/l/L/h/H/z/Z string, one character per bit, no byte-alignment required (e.g. "
        "0z1). Repeatable/chainable, same inline-target syntax as --data-hex.",
    )
    parser.add_argument(
        "--data-file", action=_DataOverrideAction, dest="data_overrides", metavar="[TARGET:]PATH",
        help="Override an operation's payload with raw bytes read from a file (CWD-relative, "
        "same as --config). No floating-marker capability of its own (raw bytes only). "
        "Repeatable/chainable, same inline-target syntax as --data-hex.",
    )
    parser.add_argument(
        "--data-mask", action=_DataMaskAction, dest="data_masks", metavar="[TARGET:]PATH",
        help="Mark byte/bit positions of an already-resolved plain-bytes payload (e.g. one "
        "loaded via --data-file) as floating, from a companion mask file: comma/newline- "
        "separated entries, each 'byte_index:resolution' (whole byte) or "
        "'byte_index.bit_index:resolution' (one bit, 0=MSB), resolution one of l/h/z (e.g. "
        "'3:h,7:l,10.3:z'). Applied after every --data-* value override resolves; the "
        "target's current datatype must be 'bytes'. Repeatable, same inline-target syntax "
        "as --data-hex.",
    )
    parser.add_argument(
        "--set", action=_SetOverrideAction, dest="field_overrides", metavar="TARGET:FIELD=VALUE",
        help="Override any scalar (non-payload) operation field, fully targeted — "
        "protocol_id:op_index:field=value (no auto-detect target, unlike --data-*: a scalar "
        "field name isn't a closed vocabulary to search over). Value is coerced to int "
        "(plain or 0x/0b prefixed), float, true/false as bool, else left as a string. "
        "Rejects any field --data-* already owns (byte-array payload fields) with a pointer "
        "to the right flag. Repeatable; two --set occurrences targeting the same field conflict.",
    )
    parser.add_argument(
        "--data-target", dest="data_target", default=None,
        help="Fallback target for any --data-hex/--data-string/--data-int/--data-bin/--data-bits/"
        "--data-file occurrence that has no inline target prefix of its own: "
        "protocol_id:op_index[:field]. Required only when the config has more than one "
        "data-carrying operation/field and the flag occurrence doesn't specify its own target; "
        "the error message lists the candidates.",
    )
    parser.add_argument(
        "--save-settings", dest="save_settings", default=None, metavar="PATH",
        help="Write the fully resolved config (JSON config plus every CLI override applied) to "
        "PATH as JSON — directly reloadable later via --config.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    json_cfg = load_json_config(args.config)
    config = resolve_config(json_cfg, args)

    if args.save_settings:
        out_path = Path(args.save_settings)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(dataclasses.asdict(config), f, indent=2)

    if args.verbose:
        print(
            f"samplerate={config.samplerate} protocols={len(config.protocols)} "
            f"outputs={len(config.outputs)}"
        )

    TimingDiagramApplication(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
