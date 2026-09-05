import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.ir_nec import IrNec


def _setup():
    nec = IrNec("nec0")
    builder = CaptureBuilder(samplerate=1_000_000)
    nec.register_signals(builder)
    return nec, builder


def test_get_signals():
    nec, _ = _setup()
    names = {s.name for s in nec.get_signals()}
    assert names == {"nec0.ir"}


def test_send_starts_with_leader_mark_after_idle_guard():
    nec, builder = _setup()
    fh = nec.send(builder, address=0, command=12)
    capture = builder.finish()
    edges = capture.edges["nec0.ir"]
    assert edges[0] == (0, 1)
    assert edges[1][1] == 0  # leader mark, after a small mandatory idle guard
    assert fh.start == edges[1][0] and fh.end == capture.duration_samples


def test_address_out_of_range_rejected():
    nec, builder = _setup()
    with pytest.raises(ValueError):
        nec.send(builder, address=256, command=0)


def test_command_out_of_range_rejected():
    nec, builder = _setup()
    with pytest.raises(ValueError):
        nec.send(builder, address=0, command=256)


def test_field_annotation_shows_address_and_command():
    nec, builder = _setup()
    nec.send(builder, address=0, command=12)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "ADDR=0x00 CMD=0x0C"


def test_send_repeat_is_shorter_than_a_full_frame():
    nec, builder = _setup()
    fh1 = nec.send_repeat(builder)
    capture = builder.finish()
    assert fh1.end == capture.duration_samples

    nec2, builder2 = _setup()
    fh2 = nec2.send(builder2, address=0, command=0)
    assert fh1.end < fh2.end  # repeat has no 32 data bits, much shorter


def test_send_repeat_field_annotation():
    nec, builder = _setup()
    nec.send_repeat(builder)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "REPEAT"
