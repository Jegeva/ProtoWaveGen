from protowavegen.model import CaptureBuilder
from protowavegen.protocols.i2c import I2CBus
from protowavegen.protocols.pca9571 import Pca9571


def _setup():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    gpio = Pca9571("gpio0", i2c)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return gpio, builder


def test_set_outputs_writes_single_byte_no_pointer():
    gpio, builder = _setup()
    gpio.set_outputs(builder, mask=0b00111100)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x25 W", "OUT=0b00111100", "stop-condition",
    ]


def test_read_outputs_reads_single_byte_no_pointer():
    gpio, builder = _setup()
    fh = gpio.read_outputs(builder, mask=0b00111100)
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x25 R", "IN=0b00111100", "stop-condition",
    ]
    assert fh.end == capture.duration_samples


def test_fixed_address_used():
    gpio, builder = _setup()
    gpio.set_outputs(builder, mask=0)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert any("0x25" in f.label for f in fields)
