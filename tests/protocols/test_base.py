import pytest

from protowavegen.protocols.base import decode_payload, format_byte


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
        decode_payload("zz", "hex")  # non-hex chars


def test_decode_payload_unknown_datatype_raises():
    with pytest.raises(ValueError):
        decode_payload([1, 2, 3], "nonsense")


def test_format_byte_still_works():
    assert format_byte(0x41) == "0x41 'A'"
    assert format_byte(0x07) == "0x07"
