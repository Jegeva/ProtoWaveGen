import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.dcf77 import Dcf77


def _setup():
    d = Dcf77("dcf0")
    builder = CaptureBuilder(samplerate=1000)
    d.register_signals(builder)
    return d, builder


def test_get_signals():
    d, _ = _setup()
    names = {s.name for s in d.get_signals()}
    assert names == {"dcf0.data"}


def test_frame_bits_length_is_59():
    bits = Dcf77._frame_bits(
        30, 14, 5, 4, 3, 26, call_bit=False, summer_time_announce=False,
        cest=False, cet=True, leap_second_announce=False,
    )
    assert len(bits) == 59
    assert bits[0] == 0  # start of minute
    assert bits[20] == 1  # start of encoded time


def test_frame_bits_parity_groups_are_even():
    bits = Dcf77._frame_bits(
        30, 14, 5, 4, 3, 26, call_bit=False, summer_time_announce=False,
        cest=False, cet=True, leap_second_announce=False,
    )
    minute_group = bits[21:29]
    hour_group = bits[29:36]
    date_group = bits[36:59]
    assert sum(minute_group) % 2 == 0
    assert sum(hour_group) % 2 == 0
    assert sum(date_group) % 2 == 0


def test_minute_out_of_range_rejected():
    d, builder = _setup()
    with pytest.raises(ValueError):
        d.send_minute(builder, minute=60, hour=0, day=1, weekday=1, month=1, year=0)


def test_weekday_out_of_range_rejected():
    d, builder = _setup()
    with pytest.raises(ValueError):
        d.send_minute(builder, minute=0, hour=0, day=1, weekday=8, month=1, year=0)


def test_send_minute_starts_with_a_pulse():
    d, builder = _setup()
    fh = d.send_minute(builder, minute=30, hour=14, day=5, weekday=4, month=3, year=26)
    capture = builder.finish()
    edges = capture.edges["dcf0.data"]
    assert edges[0] == (0, 0)
    assert edges[1] == (0, 1)  # bit 0's pulse starts immediately
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_send_minute_spans_60_seconds():
    d, builder = _setup()
    d.send_minute(builder, minute=30, hour=14, day=5, weekday=4, month=3, year=26)
    capture = builder.finish()
    assert capture.duration_samples == 60_000  # 60 one-second slots at 1000 samples/sec


def test_field_annotation_shows_time_and_date():
    d, builder = _setup()
    d.send_minute(builder, minute=30, hour=14, day=5, weekday=4, month=3, year=26)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "14:30 05.03.26"
