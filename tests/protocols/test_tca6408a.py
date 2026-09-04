from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.i2c import I2CBus
from timingdiagram.protocols.tca6408a import Tca6408a


def _setup(address=0x20):
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    gpio = Tca6408a("gpio0", i2c, address=address)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return gpio, builder


def test_configure_sets_direction_register():
    gpio, builder = _setup()
    gpio.configure(builder, mask=0b00001111)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "start-condition", "ADDR=0x20 W", "PTR=CONFIG", "CONFIG=0b00001111", "stop-condition",
    ]


def test_set_output_and_read_inputs():
    gpio, builder = _setup()
    gpio.set_output(builder, bits=0b10101010)
    fh = gpio.read_inputs(builder, value=0b01010101)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 3  # set_output START + read_inputs START + its repeated START

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert "OUTPUT=0b10101010" in labels
    assert "PTR=INPUT" in labels
    assert "INPUT=0b01010101" in labels
    assert fh.end == capture.duration_samples
