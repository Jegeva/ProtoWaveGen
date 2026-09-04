from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.ds2408 import Ds2408
from timingdiagram.protocols.onewire import OneWireBus


def _setup(rom_id=None):
    ow = OneWireBus("ow0")
    ds = Ds2408("ds0", ow, rom_id=rom_id)
    builder = CaptureBuilder(samplerate=2_000_000)
    ow.register_signals(builder)
    return ds, builder


def test_skip_rom_by_default():
    ds, builder = _setup()
    ds.read_pio(builder, state=0b10101010)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels[0] == "CMD=SKIP_ROM"


def test_match_rom_when_rom_id_given():
    ds, builder = _setup(rom_id=[0x01, 0x02, 0x03])
    ds.read_pio(builder, state=0)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels[0] == "CMD=MATCH_ROM"
    assert "ROM[0]=0x01" in labels


def test_read_pio_includes_correct_crc8():
    from timingdiagram.protocols.checksums import crc8_1wire

    ds, builder = _setup()
    fh = ds.read_pio(builder, state=0xAA)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert "PIO=0b10101010" in labels
    expected_crc = crc8_1wire([0xF0, 0xAA])
    assert f"CRC=0x{expected_crc:02X}" in labels
    assert fh.end == capture.duration_samples


def test_write_pio_command_and_ack():
    ds, builder = _setup()
    ds.write_pio(builder, bits=0b11110000)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert "BITS=0b11110000" in labels
    assert "~BITS" in labels
    assert "ACK=0xAA" in labels
    assert "STATE=0b11110000" in labels
