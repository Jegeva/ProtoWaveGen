import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.ir_rc6 import IrRc6


def _setup():
    rc6 = IrRc6("rc60")
    builder = CaptureBuilder(samplerate=1_000_000)
    rc6.register_signals(builder)
    return rc6, builder


def test_get_signals():
    rc6, _ = _setup()
    names = {s.name for s in rc6.get_signals()}
    assert names == {"rc60.ir"}


def test_only_mode_0_is_supported():
    rc6, builder = _setup()
    with pytest.raises(ValueError):
        rc6.send(builder, mode=6, address=0, command=0)


def test_address_out_of_range_rejected():
    rc6, builder = _setup()
    with pytest.raises(ValueError):
        rc6.send(builder, address=256, command=0)


def test_command_out_of_range_rejected():
    rc6, builder = _setup()
    with pytest.raises(ValueError):
        rc6.send(builder, address=0, command=256)


def test_send_starts_with_leader_mark_after_idle_guard():
    rc6, builder = _setup()
    fh = rc6.send(builder, address=0x12, command=0x34, toggle=True)
    capture = builder.finish()
    edges = capture.edges["rc60.ir"]
    assert edges[0] == (0, 1)
    assert edges[1][1] == 0
    assert fh.start == 100 and fh.end == capture.duration_samples  # after the idle guard


def test_field_annotation_shows_address_and_command():
    rc6, builder = _setup()
    rc6.send(builder, address=0x12, command=0x34, toggle=True)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "ADDR=0x12 CMD=0x34 T"
