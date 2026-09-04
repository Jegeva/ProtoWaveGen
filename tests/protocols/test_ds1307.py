from datetime import datetime

from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.ds1307 import Ds1307
from timingdiagram.protocols.i2c import I2CBus


def _setup():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    rtc = Ds1307("rtc0", i2c)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return rtc, builder


def test_datetime_bcd_encoding():
    dt = datetime(2026, 3, 5, 14, 30, 45)
    values = Ds1307._datetime_bytes(dt)
    assert values == [0x45, 0x30, 0x14, dt.isoweekday(), 0x05, 0x03, 0x26]


def test_read_datetime_uses_pointer_and_repeated_start():
    rtc, builder = _setup()
    dt = datetime(2026, 3, 5, 14, 30, 45)
    fh = rtc.read_datetime(builder, dt=dt)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2

    fields = [a for a in capture.annotations if a.track == "field"]
    assert fields[1].label == "ADDR=0x68 W"
    assert fields[2].label == "PTR=SEC"
    rtc_labels = [f.label for f in fields if f.label.startswith("RTC=")]
    assert len(rtc_labels) == 7  # all 7 datetime bytes carry the same decoded label
    assert all(label == f"RTC={dt.isoformat()}" for label in rtc_labels)
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_read_datetime_accepts_iso_string_for_json_operations():
    rtc, builder = _setup()
    rtc.read_datetime(builder, dt="2026-03-05T14:30:45")
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert any(f.label == "RTC=2026-03-05T14:30:45" for f in fields)


def test_nvram_address_validation():
    import pytest

    rtc, builder = _setup()
    with pytest.raises(ValueError):
        rtc.write_nvram(builder, addr=56, values=[0])


def test_write_nvram_labels():
    rtc, builder = _setup()
    rtc.write_nvram(builder, addr=0, values=[0x01, 0x02])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x68 W", "PTR=0x08", "0x01", "0x02", "stop-condition",
    ]
