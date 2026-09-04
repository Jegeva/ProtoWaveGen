from protowavegen.model import CaptureBuilder
from protowavegen.protocols.max7219 import Max7219
from protowavegen.protocols.spi import SpiBus


def _setup():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0)
    disp = Max7219("disp0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    return disp, builder


def test_init_sends_four_separate_words():
    disp, builder = _setup()
    disp.init(builder, intensity=5)
    capture = builder.finish()

    cs_edges = capture.edges["spi0.cs"]
    # idle-high(1) + assert/deassert(2 edges) per word * 4 words = 1 + 8 = 9
    assert len(cs_edges) == 9

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert "SHUTDOWN=OFF" in labels
    assert "DECODE=BCD_ALL" in labels
    assert "SCAN_LIMIT=8" in labels
    assert "INTENSITY=5" in labels


def test_set_digit_word_content():
    disp, builder = _setup()
    fh = disp.set_digit(builder, position=3, value=7)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["REG=0x04", "DIGIT3=7"]
    assert fh.end == capture.duration_samples


def test_invalid_digit_position_rejected():
    import pytest

    disp, builder = _setup()
    with pytest.raises(ValueError):
        disp.set_digit(builder, position=8, value=0)
