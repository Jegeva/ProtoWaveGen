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


def _dali_config():
    return {
        "samplerate": 12_000,
        "protocols": [
            {
                "id": "dali0", "type": "dali",
                "operations": [
                    {"op": "send_forward_frame", "DALI_ADDRESS": 1, "command": 254},
                    {"op": "send_backward_frame", "answer": 255},
                ],
            }
        ],
        "outputs": [],
    }


def _microwire_config():
    return {
        "samplerate": 10_000_000,
        "protocols": [
            {
                "id": "mw0", "type": "microwire", "params": {"clock_hz": 1_000_000},
                "operations": [{"op": "transfer", "mosi_bits": [1, 1, 0], "read_bits": [0, 1]}],
            }
        ],
        "outputs": [],
    }


def _ds2408_config():
    return {
        "samplerate": 2_000_000,
        "protocols": [
            {"id": "ow0", "type": "onewire", "operations": []},
            {
                "id": "ds0", "type": "ds2408", "stack_on": "ow0",
                "operations": [{"op": "write_pio", "bits": 240}],
            },
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


def test_data_bin_datatype_with_inline_target_parses_binary_literal():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-bin", "i2c0:0:0b0000000100101010"]
    )
    resolved = resolve_config(_i2c_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[0]["data"] == "0b0000000100101010" and ops[0]["datatype"] == "bin"
    assert ops[1]["data"] == [0, 150]  # untouched


def test_chaining_two_different_data_flags_with_inline_targets_in_one_invocation():
    """Mirrors the user's own worked example: one --data-bin for the write
    op, one --data-string for the read op, each with its own inline target,
    mixed datatypes in a single invocation."""

    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        [
            "--config", "unused.json",
            "--data-bin", "i2c0:0:0b0000000100101010",
            "--data-string", "i2c0:1:data:\\xhhtoto",
        ]
    )
    resolved = resolve_config(_i2c_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[0]["data"] == "0b0000000100101010" and ops[0]["datatype"] == "bin"
    assert ops[1]["data"] == "\\xhhtoto" and ops[1]["datatype"] == "text"


def test_chaining_same_target_twice_raises_conflict_with_both_locations():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        [
            "--config", "unused.json",
            "--data-hex", "i2c0:0:0102",
            "--data-bin", "i2c0:0:0b0000000100101010",
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        resolve_config(_i2c_config(), args)
    message = str(excinfo.value)
    assert "i2c0:0:data" in message
    assert "--data-hex" in message and "--data-bin" in message


def test_chaining_empty_target_prefix_forces_auto_detect_for_colon_bearing_value():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-string", ":a:b:c"]
    )
    resolved = resolve_config(_uart_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["data"] == "a:b:c" and op["datatype"] == "text"


def test_legacy_data_target_flag_still_works_as_fallback_for_untargeted_occurrence():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-hex", "0102", "--data-target", "i2c0:1"]
    )
    resolved = resolve_config(_i2c_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[1]["data"] == "0102" and ops[1]["datatype"] == "hex"
    assert ops[0]["data"] == [1, 42]  # untouched


def test_data_file_loads_raw_bytes_and_stores_as_bytes_datatype(tmp_path):
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    raw_path = tmp_path / "payload.bin"
    raw_path.write_bytes(bytes([0x01, 0x2A]))

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-file", f"i2c0:0:data:{raw_path}"]
    )
    resolved = resolve_config(_i2c_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["data"] == [0x01, 0x2A] and op["datatype"] == "bytes"


def test_data_file_missing_file_raises_clear_error():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-file", "i2c0:0:data:/no/such/file.bin"]
    )
    with pytest.raises(ValueError, match="--data-file"):
        resolve_config(_i2c_config(), args)


def test_data_file_chained_with_another_data_flag(tmp_path):
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    raw_path = tmp_path / "payload.bin"
    raw_path.write_bytes(bytes([0x99]))

    args = build_arg_parser().parse_args(
        [
            "--config", "unused.json",
            "--data-file", f"i2c0:0:data:{raw_path}",
            "--data-bin", "i2c0:1:data:0b00000001",
        ]
    )
    resolved = resolve_config(_i2c_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[0]["data"] == [0x99] and ops[0]["datatype"] == "bytes"
    assert ops[1]["data"] == "0b00000001" and ops[1]["datatype"] == "bin"


def test_save_settings_round_trips_through_load_json_config(tmp_path):
    from protowavegen.config import load_json_config, resolve_config
    from protowavegen.main import main

    config_path = tmp_path / "scenario.json"
    config_path.write_text(json.dumps(_i2c_config()))
    save_path = tmp_path / "saved.json"

    rc = main(
        [
            "--config", str(config_path),
            "--data-hex", "i2c0:0:data:0102",
            "--save-settings", str(save_path),
        ]
    )
    assert rc == 0
    assert save_path.exists()

    saved_json = load_json_config(save_path)
    assert saved_json["protocols"][0]["operations"][0]["data"] == "0102"
    assert saved_json["protocols"][0]["operations"][0]["datatype"] == "hex"

    # the saved file is itself a valid --config
    from protowavegen.main import build_arg_parser
    reload_args = build_arg_parser().parse_args(["--config", str(save_path)])
    reresolved = resolve_config(saved_json, reload_args)
    assert reresolved.protocols == saved_json["protocols"]


def test_data_override_on_dali_address_uses_prefixed_datatype_kwarg():
    """DALI's send_forward_frame has no bare `datatype` param — it uses
    DALI_ADDRESS_datatype/command_datatype instead (multiple independently
    -typed fields on one op). The override must write the correct kwarg
    name, not the generic "datatype" every other protocol uses."""

    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-hex", "dali0:0:DALI_ADDRESS:2h"]
    )
    resolved = resolve_config(_dali_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["DALI_ADDRESS"] == "2h"
    assert op["DALI_ADDRESS_datatype"] == "hex"
    assert "datatype" not in op
    assert op["command"] == 254  # untouched


def test_data_override_on_dali_command_and_answer_use_prefixed_datatype_kwarg():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        [
            "--config", "unused.json",
            "--data-hex", "dali0:0:command:fe",
            "--data-hex", "dali0:1:answer:ff",
        ]
    )
    resolved = resolve_config(_dali_config(), args)
    ops = resolved.protocols[0]["operations"]
    assert ops[0]["command_datatype"] == "hex"
    assert ops[1]["answer_datatype"] == "hex"


def test_data_override_end_to_end_dali_cli_run_no_longer_crashes():
    from protowavegen.main import main

    rc = main(
        [
            "--config", "examples/dali_basic.json",
            "--data-hex", "dali0:0:DALI_ADDRESS:2h",
            "--output-dir", "output",
        ]
    )
    assert rc == 0


def test_data_bits_datatype_targets_microwire_mosi_bits():
    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-bits", "mw0:0:mosi_bits:0z1"]
    )
    resolved = resolve_config(_microwire_config(), args)
    op = resolved.protocols[0]["operations"][0]
    assert op["mosi_bits"] == "0z1" and op["datatype"] == "bits"
    assert op["read_bits"] == [0, 1]  # untouched


def test_data_bits_end_to_end_microwire_cli_run_no_longer_crashes():
    from protowavegen.main import main

    rc = main(
        [
            "--config", "examples/microwire_basic.json",
            "--data-bits", "mw0:0:mosi_bits:1100hh10",
            "--output-dir", "output",
        ]
    )
    assert rc == 0


def test_data_override_on_field_with_no_datatype_param_raises_clear_error():
    """DS2408's write_pio(bits: int) has no datatype capability at all —
    used to fail deep inside generate() with a confusing TypeError; now
    raises immediately at override-apply time naming the field."""

    from protowavegen.config import resolve_config
    from protowavegen.main import build_arg_parser

    args = build_arg_parser().parse_args(
        ["--config", "unused.json", "--data-int", "ds0:0:bits:15"]
    )
    with pytest.raises(ValueError, match="bits"):
        resolve_config(_ds2408_config(), args)
