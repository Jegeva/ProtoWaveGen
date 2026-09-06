import pytest

from protowavegen.protocols.payload import (
    FloatingSpan,
    Payload,
    decode_payload,
    decode_payload_with_floating,
    group_floating_by_byte,
    render_as_bin,
    resolve_single_byte,
)


def test_decode_payload_bytes_default():
    assert decode_payload([72, 101, 108, 108, 111]) == [72, 101, 108, 108, 111]
    assert decode_payload([72, 101, 108, 108, 111], "bytes") == [72, 101, 108, 108, 111]


def test_decode_payload_text_encodes_utf8():
    assert decode_payload("Hello", "text") == [72, 101, 108, 108, 111]
    # multi-byte UTF-8 characters expand to multiple wire bytes
    assert decode_payload("é", "text") == list("é".encode("utf-8"))


def test_decode_payload_hex_decodes_hex_string():
    assert decode_payload("deadbeef", "hex") == [0xDE, 0xAD, 0xBE, 0xEF]
    assert decode_payload("48656c6c6f", "hex") == [72, 101, 108, 108, 111]


def test_decode_payload_hex_and_text_agree_with_bytes_for_same_content():
    assert decode_payload("Hello", "text") == decode_payload([72, 101, 108, 108, 111], "bytes")
    assert decode_payload("deadbeef", "hex") == decode_payload([222, 173, 190, 239], "bytes")


def test_decode_payload_bad_hex_raises():
    with pytest.raises(ValueError):
        decode_payload("abc", "hex")  # odd length
    with pytest.raises(ValueError):
        decode_payload("gg", "hex")  # non-hex, non-floating chars
    with pytest.raises(ValueError):
        decode_payload("zz", "hex")  # 'z'/'Z' needs tristate=True, plain decode_payload() never sets it


def test_decode_payload_unknown_datatype_raises():
    with pytest.raises(ValueError):
        decode_payload([1, 2, 3], "nonsense")


# -- floating-bit sentinel alphabet (l/L, h/H, z/Z) ----------------------


def test_decode_payload_hex_lh_nibbles_resolve_without_tristate():
    payload = decode_payload_with_floating("lh", "hex")
    assert payload.values == [0x0F]
    assert payload.floating == (
        FloatingSpan(byte_index=0, bit_index=0, resolution="l"),
        FloatingSpan(byte_index=0, bit_index=1, resolution="l"),
        FloatingSpan(byte_index=0, bit_index=2, resolution="l"),
        FloatingSpan(byte_index=0, bit_index=3, resolution="l"),
        FloatingSpan(byte_index=0, bit_index=4, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=5, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=6, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=7, resolution="h"),
    )


def test_decode_payload_hex_l_h_case_insensitive_equivalence():
    assert decode_payload_with_floating("ll", "hex").values == decode_payload_with_floating(
        "LL", "hex"
    ).values
    assert decode_payload_with_floating("hh", "hex").values == decode_payload_with_floating(
        "HH", "hex"
    ).values


def test_decode_payload_hex_z_raises_without_tristate_and_resolves_with_it():
    with pytest.raises(ValueError):
        decode_payload_with_floating("zz", "hex", tristate=False)
    payload = decode_payload_with_floating("zz", "hex", tristate=True)
    assert payload.values == [0xFF]  # every TRISTATE signal here is pull-high
    assert all(span.resolution == "z" for span in payload.floating)


def test_decode_payload_hex_mixed_driven_and_floating_nibbles():
    payload = decode_payload_with_floating("2h", "hex")
    assert payload.values == [0x2F]
    assert payload.floating == (
        FloatingSpan(byte_index=0, bit_index=4, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=5, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=6, resolution="h"),
        FloatingSpan(byte_index=0, bit_index=7, resolution="h"),
    )


def test_decode_payload_bin_datatype_single_byte():
    payload = decode_payload_with_floating("0b00101010", "bin")
    assert payload.values == [0x2A]
    assert payload.floating == ()


def test_decode_payload_bin_datatype_comma_separated_multi_byte():
    payload = decode_payload_with_floating("0b00000001,0b00101010", "bin")
    assert payload.values == [0x01, 0x2A]


def test_decode_payload_bin_datatype_floating_bits():
    payload = decode_payload_with_floating("0b11010010hhhhllll", "bin")
    assert payload.values == [0xD2, 0xF0]
    assert group_floating_by_byte(payload.floating) == {1: frozenset(range(8))}


def test_decode_payload_bin_datatype_without_prefix_is_accepted():
    assert decode_payload_with_floating("00101010", "bin").values == [0x2A]


def test_decode_payload_bin_bad_length_raises():
    with pytest.raises(ValueError):
        decode_payload("0b101", "bin")  # not a multiple of 8


def test_decode_payload_bin_invalid_char_raises():
    with pytest.raises(ValueError):
        decode_payload("0b1010101x", "bin")


def test_decode_payload_bin_z_requires_tristate():
    with pytest.raises(ValueError):
        decode_payload_with_floating("0bzzzzzzzz", "bin", tristate=False)
    payload = decode_payload_with_floating("0bzzzzzzzz", "bin", tristate=True)
    assert payload.values == [0xFF]


def test_decode_payload_text_plain_utf8_unaffected_by_escape_support():
    assert decode_payload("Hello", "text") == [72, 101, 108, 108, 111]
    assert decode_payload("é", "text") == list("é".encode("utf-8"))


def test_decode_payload_text_raw_byte_escape():
    assert decode_payload("\\x41toto", "text") == [0x41, ord("t"), ord("o"), ord("t"), ord("o")]


def test_decode_payload_text_floating_escape():
    payload = decode_payload_with_floating("\\xhhtoto", "text", tristate=False)
    assert payload.values == [0xFF, ord("t"), ord("o"), ord("t"), ord("o")]
    assert group_floating_by_byte(payload.floating) == {0: frozenset(range(8))}


def test_decode_payload_text_z_escape_requires_tristate():
    with pytest.raises(ValueError):
        decode_payload("\\xzztoto", "text")
    payload = decode_payload_with_floating("\\xzztoto", "text", tristate=True)
    assert payload.values[0] == 0xFF


def test_decode_payload_text_truncated_escape_raises():
    with pytest.raises(ValueError):
        decode_payload("abc\\x4", "text")


def test_decode_payload_text_lone_backslash_passes_through_literally():
    # a backslash not followed by 'x' is just a literal character, same as
    # today's plain UTF-8 encoding for any text without escapes.
    assert decode_payload("a\\b", "text") == list("a\\b".encode("utf-8"))


def test_render_as_bin_no_prefix_no_floating():
    payload = Payload(values=[0x41])
    assert render_as_bin(payload) == "01000001"


def test_render_as_bin_with_prefix_bytes():
    payload = Payload(values=[0xFF])
    assert render_as_bin(payload, prefix_bytes=[0x00, 0x01]) == "00000000" + "00000001" + "11111111"


def test_render_as_bin_substitutes_floating_positions_offset_by_prefix():
    payload = Payload(
        values=[0x2F],
        floating=(
            FloatingSpan(byte_index=0, bit_index=4, resolution="h"),
            FloatingSpan(byte_index=0, bit_index=5, resolution="h"),
            FloatingSpan(byte_index=0, bit_index=6, resolution="h"),
            FloatingSpan(byte_index=0, bit_index=7, resolution="h"),
        ),
    )
    # one fixed prefix byte (0xAA) shifts the payload's own byte_index=0 to
    # combined byte_index=1
    assert render_as_bin(payload, prefix_bytes=[0xAA]) == "10101010" + "0010hhhh"


def test_resolve_single_byte_bytes_datatype_accepts_bare_int():
    assert resolve_single_byte(65, "bytes") == (65, frozenset())


def test_resolve_single_byte_bytes_datatype_accepts_single_element_list():
    """Regression test: `--data-int`/`--data-file`
    (`config.py::apply_data_override`) always build a `list[int]` for
    `datatype="bytes"`, with no way to know a given field is historically
    a bare int (DALI's address/command/answer, PS/2's byte) rather than a
    real payload list — this crashed with a raw TypeError in
    `bits_of_byte()` before being fixed, found independently while
    rewriting DALI's, PS/2's, and Wiegand's end-user docs this session."""

    assert resolve_single_byte([65], "bytes") == (65, frozenset())


def test_resolve_single_byte_bytes_datatype_rejects_multi_element_list():
    with pytest.raises(ValueError, match="expected exactly one byte"):
        resolve_single_byte([65, 66], "bytes")


def test_resolve_single_byte_hex_datatype_still_works():
    assert resolve_single_byte("41", "hex") == (0x41, frozenset())
