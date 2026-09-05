import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.tlc5620 import Tlc5620


def _setup():
    dac = Tlc5620("dac0", clock_hz=1_000_000)
    builder = CaptureBuilder(samplerate=10_000_000)
    dac.register_signals(builder)
    return dac, builder


def test_get_signals():
    dac, _ = _setup()
    names = {s.name for s in dac.get_signals()}
    assert names == {"dac0.clk", "dac0.data", "dac0.load", "dac0.ldac"}


def test_ldac_stays_low():
    dac, builder = _setup()
    assert builder.level_of("dac0.ldac") == 0
    dac.set_channel(builder, channel=0, gain=1, value=200)
    assert builder.level_of("dac0.ldac") == 0


def test_channel_out_of_range_rejected():
    dac, builder = _setup()
    with pytest.raises(ValueError):
        dac.set_channel(builder, channel=4, gain=1, value=0)


def test_gain_must_be_1_or_2():
    dac, builder = _setup()
    with pytest.raises(ValueError):
        dac.set_channel(builder, channel=0, gain=3, value=0)


def test_value_out_of_range_rejected():
    dac, builder = _setup()
    with pytest.raises(ValueError):
        dac.set_channel(builder, channel=0, gain=1, value=256)


def test_ends_with_load_pulse():
    dac, builder = _setup()
    fh = dac.set_channel(builder, channel=0, gain=1, value=200)
    capture = builder.finish()
    load_edges = capture.edges["dac0.load"]
    assert load_edges[-2][1] == 0  # LOAD falls to latch
    assert load_edges[-1][1] == 1  # then returns high
    assert fh.end == capture.duration_samples


def test_field_annotation_shows_channel_gain_and_value():
    dac, builder = _setup()
    dac.set_channel(builder, channel=2, gain=2, value=64)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "DACC=x2:64"
