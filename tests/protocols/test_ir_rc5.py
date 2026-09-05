import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.ir_rc5 import IrRc5


def _setup():
    rc5 = IrRc5("rc0")
    builder = CaptureBuilder(samplerate=1_000_000)
    rc5.register_signals(builder)
    return rc5, builder


def test_get_signals():
    rc5, _ = _setup()
    names = {s.name for s in rc5.get_signals()}
    assert names == {"rc0.ir"}


def test_idle_level_is_high():
    rc5, builder = _setup()
    assert builder.level_of("rc0.ir") == 1


def test_first_half_bit_is_the_start_bits_high_to_low_transition():
    rc5, builder = _setup()
    fh = rc5.send(builder, address=0, command=0)
    capture = builder.finish()
    edges = capture.edges["rc0.ir"]
    # idle high, a small mandatory idle guard, then start bit 1 (=1): high
    # held, falls to low at the bit's midpoint (889us in samples)
    assert edges[0] == (0, 1)
    assert edges[1][1] == 0
    assert fh.start == 100 and fh.end == capture.duration_samples  # after the idle guard


def test_address_out_of_range_rejected():
    rc5, builder = _setup()
    with pytest.raises(ValueError):
        rc5.send(builder, address=32, command=0)


def test_command_out_of_range_rejected():
    rc5, builder = _setup()
    with pytest.raises(ValueError):
        rc5.send(builder, address=0, command=64)


def test_extended_command_allows_7_bits():
    rc5, builder = _setup()
    rc5.send(builder, address=0, command=100, extended=True)  # would overflow standard 6-bit range


def test_field_annotation_shows_address_and_command():
    rc5, builder = _setup()
    rc5.send(builder, address=5, command=12, toggle=True)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "ADDR=5 CMD=12 T"
