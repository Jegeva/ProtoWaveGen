import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.base import DriverTracker, bind_clock_samples, bits_of_byte, format_byte


def test_format_byte_still_works():
    assert format_byte(0x41) == "0x41 'A'"
    assert format_byte(0x07) == "0x07"


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
