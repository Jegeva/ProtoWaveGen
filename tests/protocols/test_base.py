import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.base import (
    DriverTracker,
    FloatingSpan,
    bind_clock_samples,
    bits_of_byte,
    decode_payload,
    decode_payload_with_floating,
    format_byte,
    group_floating_by_byte,
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


def test_format_byte_still_works():
    assert format_byte(0x41) == "0x41 'A'"
    assert format_byte(0x07) == "0x07"


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


# -- DriverTracker ---------------------------------------------------------


def test_driver_tracker_coalesces_same_driver_into_one_span():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    tracker.set("master")
    builder.advance(10)
    tracker.set("master")  # no-op, same driver — must not start a new span
    builder.advance(10)
    tracker.close()

    annotations = [a for a in builder.finish().annotations if a.track == "driver"]
    assert len(annotations) == 1
    assert annotations[0].label == "master" and annotations[0].start == 0 and annotations[0].end == 20


def test_driver_tracker_emits_one_annotation_per_driver_change():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    tracker.set("master")
    builder.advance(5)
    tracker.set("pullup")
    builder.advance(5)
    tracker.set("slave")
    builder.advance(5)
    tracker.close()

    annotations = [a for a in builder.finish().annotations if a.track == "driver"]
    assert [(a.label, a.start, a.end) for a in annotations] == [
        ("master", 0, 5), ("pullup", 5, 10), ("slave", 10, 15),
    ]


def test_driver_tracker_zero_length_span_never_emitted():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    tracker.set("master")
    tracker.set("slave")  # driver changed with zero samples elapsed
    builder.advance(5)
    tracker.close()

    annotations = [a for a in builder.finish().annotations if a.track == "driver"]
    assert len(annotations) == 1 and annotations[0].label == "slave"  # "master" span was zero-length


def test_driver_tracker_no_set_calls_emits_nothing():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    builder.advance(10)
    tracker.close()
    assert not [a for a in builder.finish().annotations if a.track == "driver"]


def test_driver_tracker_explicit_at_overrides_cursor():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    tracker.set("master", at=100)
    tracker.close(at=200)

    annotations = [a for a in builder.finish().annotations if a.track == "driver"]
    assert annotations[0].start == 100 and annotations[0].end == 200


def test_driver_tracker_annotation_signals_tuple_matches_constructor_signal():
    builder = CaptureBuilder(samplerate=1_000_000)
    tracker = DriverTracker(builder, "sig0")
    tracker.set("master")
    builder.advance(1)
    tracker.close()
    annotation = [a for a in builder.finish().annotations if a.track == "driver"][0]
    assert annotation.signals == ("sig0",)


# -- bits_of_byte / bind_clock_samples --------------------------------------


def test_bits_of_byte_msb_first_default():
    assert bits_of_byte(0b10000001) == [1, 0, 0, 0, 0, 0, 0, 1]
    assert bits_of_byte(0x00) == [0] * 8
    assert bits_of_byte(0xFF) == [1] * 8


def test_bits_of_byte_lsb_first():
    assert bits_of_byte(0b10000001, "lsb") == [1, 0, 0, 0, 0, 0, 0, 1]
    assert bits_of_byte(0b00000010, "lsb") == [0, 1, 0, 0, 0, 0, 0, 0]


def test_bits_of_byte_out_of_range_raises():
    with pytest.raises(ValueError):
        bits_of_byte(256)
    with pytest.raises(ValueError):
        bits_of_byte(-1)


def test_bind_clock_samples_half_clock_default():
    assert bind_clock_samples(10_000_000, 1_000_000, hz_label="clock_hz") == 5


def test_bind_clock_samples_whole_bit_divisor():
    assert bind_clock_samples(500_000, 500_000, hz_label="bitrate", divisor=1) == 1


def test_bind_clock_samples_too_low_raises_with_expected_message():
    with pytest.raises(ValueError, match=r"samplerate 100 too low for clock_hz 1000000 \(need at least 2000000 Hz\)"):
        bind_clock_samples(100, 1_000_000, hz_label="clock_hz")


def test_bind_clock_samples_custom_minimum_and_note():
    with pytest.raises(ValueError, match=r"need at least 2400 Hz for Manchester encoding"):
        bind_clock_samples(1200, 1200, hz_label="baudrate", divisor=1, minimum=2, minimum_note="for Manchester encoding")
    assert bind_clock_samples(2400, 1200, hz_label="baudrate", divisor=1, minimum=2) == 2
