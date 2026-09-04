from protowavegen.model import CaptureBuilder
from protowavegen.protocols.i2c import I2CBus
from protowavegen.protocols.lm75 import Lm75


def _setup(address=0x48):
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    lm75 = Lm75("lm75_0", i2c, address=address)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return i2c, lm75, builder


def test_first_read_does_pointer_write_then_read():
    i2c, lm75, builder = _setup()
    fh = lm75.read_temperature(builder, celsius=23.5)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # pointer write + repeated START for the read
    assert lm75._pointer == 0x00

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert "PTR=TEMP" in labels
    assert labels.count("TEMP=+23.5C") == 2  # both bytes of the 16-bit register
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_repeated_read_skips_redundant_pointer_write():
    i2c, lm75, builder = _setup()
    lm75.read_temperature(builder, celsius=23.5)
    lm75.read_temperature(builder, celsius=24.0)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    # first read: START + repeated-START (2); second read (pointer already
    # set): plain read() needs only its own single START (1) -> 3 total
    assert len(starts) == 3


def test_temperature_encoding_negative_and_positive():
    assert Lm75._encode_temp(0.0) == (0x00, 0x00)
    assert Lm75._encode_temp(25.0) == (0x19, 0x00)
    hi, lo = Lm75._encode_temp(-25.0)
    assert hi == 0xE7  # two's complement, 9-bit value left-justified


def test_write_config_sets_pointer():
    i2c, lm75, builder = _setup()
    lm75.write_config(builder, byte=0x01)
    assert lm75._pointer == 0x01
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x48 W", "PTR=CONFIG", "CONFIG=0x01", "stop-condition",
    ]
