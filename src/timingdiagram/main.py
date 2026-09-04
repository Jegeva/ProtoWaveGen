from __future__ import annotations

import argparse
import sys

from .app import TimingDiagramApplication
from .config import load_json_config, resolve_config


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timingdiagram",
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
