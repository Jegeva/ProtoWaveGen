from __future__ import annotations

import argparse
import sys

from .app import TimingDiagramApplication
from .config import load_json_config, resolve_config


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
    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument(
        "--data-hex", dest="data_hex", default=None,
        help="Override an operation's payload with a hex-digit string (e.g. deadbeef)",
    )
    data_group.add_argument(
        "--data-string", dest="data_string", default=None,
        help="Override an operation's payload with a UTF-8 text string",
    )
    data_group.add_argument(
        "--data-int", dest="data_int", default=None,
        help="Override an operation's payload with comma-separated byte values (e.g. 72,101,108,108,111)",
    )
    parser.add_argument(
        "--data-target", dest="data_target", default=None,
        help="Which operation --data-hex/--data-string/--data-int applies to: "
        "protocol_id:op_index[:field]. Required only when the config has more "
        "than one data-carrying operation/field; the error message lists the candidates.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    json_cfg = load_json_config(args.config)
    config = resolve_config(json_cfg, args)

    if args.verbose:
        print(
            f"samplerate={config.samplerate} protocols={len(config.protocols)} "
            f"outputs={len(config.outputs)}"
        )

    TimingDiagramApplication(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
