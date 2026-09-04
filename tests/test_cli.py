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


def _uart_config():
    return {
        "samplerate": 96000,
        "protocols": [
            {"id": "uart0", "type": "uart", "params": {"baudrate": 9600}, "operations": [{"op": "send", "data": [65]}]}
        ],
        "outputs": [],
    }


def _i2c_config():
    return {
        "samplerate": 400_000,
        "protocols": [
            {
                "id": "i2c0", "type": "i2c", "params": {"clock_hz": 100_000},
                "operations": [
                    {"op": "write", "address": 0x48, "data": [1, 42]},
                    {"op": "read", "address": 0x48, "data": [0, 150]},
                ],
            }
        ],
        "outputs": [],
    }


def _spi_config():
    return {
        "samplerate": 10_000_000,
        "protocols": [
            {
                "id": "spi0", "type": "spi", "params": {"clock_hz": 1_000_000},
                "operations": [{"op": "transfer", "mosi": [1, 2], "miso": [3, 4]}],
            }
        ],
        "outputs": [],
    }


def test_data_hex_auto_detects_single_unambiguous_operation():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(["--config", "unused.json", "--data-hex", "48656c6c6f"])
    resolved = resolve_config(_uart_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["data"] == "48656c6c6f"
    assert op["datatype"] == "hex"


def test_data_hex_ambiguous_without_target_raises_and_lists_candidates():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(["--config", "unused.json", "--data-hex", "0102"])
    with pytest.raises(ValueError) as excinfo:
        resolve_config(_i2c_config(), args)
    message = str(excinfo.value)
    assert "i2c0:0:data (op=write)" in message
    assert "i2c0:1:data (op=read)" in message


def test_data_target_selects_one_of_two_operations():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-hex", "0102", "--data-target", "i2c0:0"]
    )
    resolved = resolve_config(_i2c_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[0]["data"] == "0102" and ops[0]["datatype"] == "hex"
    assert ops[1]["data"] == [0, 150]  # untouched
    assert "datatype" not in ops[1]


def test_data_target_selects_one_of_two_fields_in_same_operation():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-string", "Hi", "--data-target", "spi0:0:mosi"]
    )
    resolved = resolve_config(_spi_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["mosi"] == "Hi" and op["datatype"] == "text"
    assert op["miso"] == [3, 4]  # untouched


def test_data_int_comma_separated_parses_to_matching_bytes():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-int", "72, 101, 108, 108, 111"]
    )
    resolved = resolve_config(_uart_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["data"] == [72, 101, 108, 108, 111]
    assert op["datatype"] == "bytes"


@pytest.mark.parametrize(
    "target",
    ["nope:0", "i2c0:99", "i2c0:0:bogus_field", "i2c0"],
)
def test_data_target_bad_values_raise_clear_errors(target):
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-hex", "01", "--data-target", target]
    )
    with pytest.raises(ValueError):
        resolve_config(_i2c_config(), args)
