import pytest

from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.seven_segment import DIGIT_PATTERNS, SevenSegmentDisplay
from timingdiagram.protocols.spi import SpiBus


def _setup():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0)
    disp = SevenSegmentDisplay("seg0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    disp.register_signals(builder)
    return disp, spi, builder


def test_requires_width1_transport():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=4, mode=0)
    with pytest.raises(ValueError):
        SevenSegmentDisplay("seg0", spi)


def test_get_signals_adds_latch_only():
    disp, spi, builder = _setup()
    assert [s.name for s in disp.get_signals()] == ["seg0.latch"]


def test_set_digits_shifts_then_latches():
    disp, spi, builder = _setup()
    fh = disp.set_digits(builder, patterns=[0b00111111, 0b00000110])
    capture = builder.finish()

    latch_edges = capture.edges["seg0.latch"]
    assert latch_edges == ((0, 0), (fh.end, 1), (fh.end + spi.bit_period_samples, 0))

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert "LATCH" in labels


def test_set_digit_values_uses_pattern_table():
    disp, spi, builder = _setup()
    disp.set_digit_values(builder, values=[8, 0])
    fields = [a for a in builder.finish().annotations if a.track == "field" and "mosi" in a.data]
    assert [f.data["mosi"] for f in fields] == [DIGIT_PATTERNS[8], DIGIT_PATTERNS[0]]


def test_invalid_digit_value_rejected():
    disp, spi, builder = _setup()
    with pytest.raises(ValueError):
        disp.set_digit_values(builder, values=[10])
