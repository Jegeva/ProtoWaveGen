import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.eeprom_24xx import Eeprom24xx
from protowavegen.protocols.i2c import I2CBus


def _setup(addr_width=1, page_size=16):
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    ee = Eeprom24xx("ee0", i2c, address=0x50, addr_width=addr_width, page_size=page_size)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return ee, builder


def test_write_byte_1byte_address():
    ee, builder = _setup()
    ee.write_byte(builder, word_addr=0x10, value=0xAB)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert "ADDR=0x10" in labels
    assert "0xAB" in labels


def test_write_page_2byte_address():
    ee, builder = _setup(addr_width=2)
    ee.write_page(builder, word_addr=0x1234, values=[0x01, 0x02])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert labels.count("ADDR=0x1234") == 2  # both address bytes labeled the same
    assert "0x01" in labels
    assert "0x02" in labels


def test_page_write_over_limit_rejected():
    ee, builder = _setup(page_size=4)
    with pytest.raises(ValueError):
        ee.write_page(builder, word_addr=0, values=[0] * 5)


def test_read_sequential_uses_repeated_start():
    ee, builder = _setup()
    fh = ee.read_sequential(builder, word_addr=0x20, values=[0x01, 0x02, 0x03])  # non-printable control bytes
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # address write + repeated START for the read

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert labels == [
        "start-condition", "ADDR=0x50 W", "ADDR=0x20",
        "start-condition", "ADDR=0x50 R", "0x01", "0x02", "0x03",
        "stop-condition",
    ]
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_address_out_of_range_rejected():
    ee, builder = _setup(addr_width=1)
    with pytest.raises(ValueError):
        ee.write_byte(builder, word_addr=0x100, value=0)
