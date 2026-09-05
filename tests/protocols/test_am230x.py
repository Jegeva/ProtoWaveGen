import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.am230x import Am230x


def _setup():
    sensor = Am230x("sensor0")
    builder = CaptureBuilder(samplerate=1_000_000)
    sensor.register_signals(builder)
    return sensor, builder


def test_get_signals():
    sensor, _ = _setup()
    names = {s.name for s in sensor.get_signals()}
    assert names == {"sensor0.sda"}


def test_encode_positive_temperature():
    bytes5 = Am230x._encode(65.2, 23.4)
    assert bytes5[:2] == [0x02, 0x8C]  # 652 = 0x28C
    assert bytes5[2:4] == [0x00, 0xEA]  # 234 = 0xEA, sign bit clear
    assert bytes5[4] == sum(bytes5[:4]) & 0xFF


def test_encode_negative_temperature_sets_sign_bit():
    bytes5 = Am230x._encode(45.5, -12.3)
    assert bytes5[2] & 0x80  # sign bit set
    assert (bytes5[2] << 8 | bytes5[3]) & 0x7FFF == 123  # magnitude 12.3 -> 123


def test_humidity_out_of_range_rejected():
    sensor, builder = _setup()
    with pytest.raises(ValueError):
        sensor.send_reading(builder, humidity=10000.0, temperature=0.0)


def test_send_reading_starts_with_start_low_pulse():
    sensor, builder = _setup()
    fh = sensor.send_reading(builder, humidity=65.2, temperature=23.4)
    capture = builder.finish()
    edges = capture.edges["sensor0.sda"]
    assert edges[0] == (0, 1)
    assert edges[1] == (0, 0)  # host pulls low immediately
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_field_annotation_shows_humidity_and_temperature():
    sensor, builder = _setup()
    sensor.send_reading(builder, humidity=65.2, temperature=23.4)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "RH=65.2% T=23.4C"
