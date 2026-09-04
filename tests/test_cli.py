import json

import pytest

from protowavegen.main import main


def test_cli_runs_example_config_and_writes_outputs(tmp_path):
    config = {
        "samplerate": 96000,
        "protocols": [
            {
                "id": "uart0",
                "type": "uart",
                "params": {"baudrate": 9600},
                "operations": [{"op": "send", "data": [65]}],
            }
        ],
        "outputs": [],
    }
    config_path = tmp_path / "scenario.json"
    config_path.write_text(json.dumps(config))

    out_dir = tmp_path / "out"
    rc = main(
        [
            "--config", str(config_path),
            "--output-dir", str(out_dir),
            "--format", "svg",
            "--format", "vcd",
        ]
    )
    assert rc == 0
    assert (out_dir / "capture.svg").exists()
    assert (out_dir / "capture.vcd").exists()


def test_cli_samplerate_override_takes_precedence_over_json():
    config = {
        "samplerate": 1,
        "protocols": [],
        "outputs": [],
    }
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(["--config", "unused.json", "--samplerate", "42"])
    resolved = resolve_config(config, args)
    assert resolved.samplerate == 42


def test_cli_requires_samplerate_somewhere(tmp_path):
    config_path = tmp_path / "scenario.json"
    config_path.write_text(json.dumps({"protocols": [], "outputs": []}))
    with pytest.raises(ValueError):
        main(["--config", str(config_path)])
