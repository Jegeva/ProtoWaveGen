import pytest

from timingdiagram.model import CaptureBuilder, Signal, pad_idle


def test_register_signal_seeds_initial_edge():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a", initial_level=1))
    capture = b.finish()
    assert capture.edges["a"] == ((0, 1),)


def test_duplicate_signal_registration_rejected():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    with pytest.raises(ValueError):
        b.register_signal(Signal("a"))


def test_set_level_same_value_is_noop():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a", initial_level=1))
    b.set_level("a", 1)
    assert b.finish().edges["a"] == ((0, 1),)


def test_set_level_change_appends_edge():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a", initial_level=1))
    b.advance(10)
    b.set_level("a", 0)
    b.advance(5)
    b.set_level("a", 1)
    capture = b.finish()
    assert capture.edges["a"] == ((0, 1), (10, 0), (15, 1))
    assert capture.duration_samples == 15


def test_set_level_unknown_signal_raises():
    b = CaptureBuilder(samplerate=1000)
    with pytest.raises(KeyError):
        b.set_level("nope", 0)


def test_advance_negative_rejected():
    b = CaptureBuilder(samplerate=1000)
    with pytest.raises(ValueError):
        b.advance(-1)


def test_frame_context_captures_start_and_end():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    with b.frame() as fh:
        b.advance(7)
    assert fh.start == 0
    assert fh.end == 7


def test_annotate_defaults_start_to_cursor_and_supports_open_end():
    b = CaptureBuilder(samplerate=1000)
    b.advance(3)
    a = b.annotate("field", "hello", data_extra=1)
    assert a.start == 3
    assert a.end is None
    assert a.data == {"data_extra": 1}
    capture = b.finish()
    assert capture.annotations == (a,)


def test_clear_annotations_removes_matching_track_and_signals():
    b = CaptureBuilder(samplerate=1000)
    b.annotate("unit", "byte", start=0, end=5, signals=("a",))
    b.annotate("unit", "byte", start=5, end=10, signals=("b",))
    b.annotate("field", "x", start=0, end=5, signals=("a",))

    b.clear_annotations("unit", signals=("a",))
    capture = b.finish()
    tracks_left = [(a.track, a.signals) for a in capture.annotations]
    assert ("unit", ("a",)) not in tracks_left
    assert ("unit", ("b",)) in tracks_left
    assert ("field", ("a",)) in tracks_left


def test_clear_annotations_no_signal_filter_removes_whole_track():
    b = CaptureBuilder(samplerate=1000)
    b.annotate("unit", "byte", start=0, end=5, signals=("a",))
    b.annotate("unit", "byte", start=5, end=10, signals=("b",))
    b.clear_annotations("unit")
    assert b.finish().annotations == ()


def test_pad_idle_adds_2_percent_margin_before_and_after():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a", initial_level=1))
    b.advance(10)
    b.set_level("a", 0)
    b.advance(90)  # original duration 100
    b.annotate("field", "byte", start=10, end=100)
    raw = b.finish()
    assert raw.duration_samples == 100

    padded = pad_idle(raw, fraction=0.02)
    pad = 2  # round(100 * 0.02)
    assert padded.duration_samples == 100 + 2 * pad

    # idle level held from t=0 through the padding, then the real activity
    # (unchanged relative shape) starts at `pad`, ends at `100 + pad`, and
    # holds flat through the trailing pad to the new total duration.
    assert padded.edges["a"] == ((0, 1), (pad, 1), (pad + 10, 0))
    assert padded.annotations[0].start == 10 + pad
    assert padded.annotations[0].end == 100 + pad


def test_pad_idle_preserves_open_ended_annotations():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(50)
    b.annotate("bitorder", "msb", start=0)  # open-ended
    raw = b.finish()

    padded = pad_idle(raw, fraction=0.1)
    assert padded.annotations[0].end is None


def test_pad_idle_is_noop_on_empty_capture():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    raw = b.finish()
    assert raw.duration_samples == 0
    assert pad_idle(raw) is raw


def test_pad_idle_minimum_one_sample_for_tiny_captures():
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(1)
    raw = b.finish()
    padded = pad_idle(raw, fraction=0.02)
    assert padded.duration_samples == 1 + 2 * 1  # rounds up to at least 1 sample of padding per side


def test_annotation_covers_and_applies_to():
    from timingdiagram.model import Annotation

    open_ended = Annotation(track="t", label="l", start=5)
    assert not open_ended.covers(4)
    assert open_ended.covers(5)
    assert open_ended.covers(1000)

    bounded = Annotation(track="t", label="l", start=5, end=10, signals=("x",))
    assert bounded.covers(9)
    assert not bounded.covers(10)
    assert bounded.applies_to("x")
    assert not bounded.applies_to("y")
