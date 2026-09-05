from datetime import datetime

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.i2c import I2CBus
from protowavegen.protocols.rtc8564 import Rtc8564


def _setup():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    rtc = Rtc8564("rtc0", i2c)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return rtc, builder


def test_datetime_bcd_encoding():
    dt = datetime(2026, 3, 5, 14, 30, 45)  # Thursday
    values = Rtc8564._datetime_bytes(dt, voltage_low=False, century=False)
    assert values == [0x45, 0x30, 0x14, 0x05, dt.isoweekday() % 7, 0x03, 0x26]


def test_voltage_low_and_century_bits_set_high_bit():
    dt = datetime(2026, 3, 5, 14, 30, 45)
    values = Rtc8564._datetime_bytes(dt, voltage_low=True, century=True)
    assert values[0] == 0x45 | 0x80  # seconds' bit 7 = VL
    assert values[5] == 0x03 | 0x80  # month's bit 7 = century


def test_write_datetime_pointer_starts_at_seconds_register():
    rtc, builder = _setup()
    dt = datetime(2026, 3, 5, 14, 30, 45)
    rtc.write_datetime(builder, dt=dt)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "start-condition"
    assert fields[1].label == "ADDR=0x51 W"
    assert fields[2].label == "PTR=SEC"
    rtc_labels = [f.label for f in fields if f.label.startswith("RTC=")]
    assert len(rtc_labels) == 7


def test_read_datetime_uses_pointer_and_repeated_start():
    rtc, builder = _setup()
    dt = datetime(2026, 3, 5, 14, 30, 45)
    fh = rtc.read_datetime(builder, dt=dt)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2

    fields = [a for a in capture.annotations if a.track == "field"]
    rtc_labels = [f.label for f in fields if f.label.startswith("RTC=")]
    assert len(rtc_labels) == 7
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_read_datetime_accepts_iso_string_for_json_operations():
    rtc, builder = _setup()
    rtc.read_datetime(builder, dt="2026-03-05T14:30:45")
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert any(f.label == "RTC=2026-03-05T14:30:45" for f in fields)


def test_fixed_address_used():
    rtc, builder = _setup()
    rtc.write_datetime(builder, dt=datetime(2026, 1, 1))
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert any("0x51" in f.label for f in fields)
