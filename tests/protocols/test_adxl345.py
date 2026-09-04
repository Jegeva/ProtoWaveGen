from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.adxl345 import Adxl345
from timingdiagram.protocols.i2c import I2CBus


def _setup(address=0x1D):
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    accel = Adxl345("acc0", i2c, address=address)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return accel, builder


def test_enable_measurement():
    accel, builder = _setup()
    accel.enable_measurement(builder)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x1d W", "PTR=POWER_CTL", "MEASURE=1", "stop-condition",
    ]


def test_encode_axis_negative_two_complement():
    lo, hi = Adxl345._encode_axis(-1)
    assert (hi << 8 | lo) == 0xFFFF


def test_read_acceleration_burst_uses_repeated_start():
    accel, builder = _setup()
    fh = accel.read_acceleration(builder, x=100, y=-50, z=250)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2

    fields = [a for a in capture.annotations if a.track == "field"]
    assert "PTR=DATAX0" in [f.label for f in fields]
    assert [f.label for f in fields].count("X=100 Y=-50 Z=250") == 6
    assert fh.end == capture.duration_samples
