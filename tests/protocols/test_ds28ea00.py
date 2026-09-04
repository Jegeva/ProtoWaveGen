from protowavegen.model import CaptureBuilder
from protowavegen.protocols.checksums import crc8_1wire
from protowavegen.protocols.ds28ea00 import Ds28ea00
from protowavegen.protocols.onewire import OneWireBus


def _setup(conversion_delay_us=100):  # shorten for fast tests
    ow = OneWireBus("ow0")
    dev = Ds28ea00("temp0", ow, conversion_delay_us=conversion_delay_us)
    builder = CaptureBuilder(samplerate=2_000_000)
    ow.register_signals(builder)
    return dev, builder


def test_temperature_encoding():
    lo, hi = Ds28ea00._encode_temp(25.0625)
    assert (hi << 8 | lo) == round(25.0625 / 0.0625)


def test_read_temperature_sequence_and_crc():
    dev, builder = _setup()
    fh = dev.read_temperature(builder, celsius=23.5)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels.count("CMD=SKIP_ROM") == 2  # once for convert, once for read scratchpad
    assert "CMD=CONVERT_T" in labels
    assert "CONVERTING" in labels
    assert "CMD=READ_SP" in labels
    assert labels.count("TEMP=+23.5000C") == 2

    lo, hi = Ds28ea00._encode_temp(23.5)
    scratchpad = [lo, hi, 0, 0, 0x7F, 0xFF, 0xFF, 0xFF]
    expected_crc = crc8_1wire([0xBE, *scratchpad])
    assert f"CRC=0x{expected_crc:02X}" in labels
    assert fh.end == capture.duration_samples


def test_write_scratchpad():
    dev, builder = _setup()
    dev.write_scratchpad(builder, th=75, tl=-10 & 0xFF, config=0x3F)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert "CMD=WRITE_SP" in labels
    assert "TH=0x4B" in labels
    assert "CONFIG=0x3F" in labels
