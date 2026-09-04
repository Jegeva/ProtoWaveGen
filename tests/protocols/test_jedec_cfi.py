import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.jedec_cfi import JedecCfi
from protowavegen.protocols.spi import SpiBus


def _spi(width=1):
    return SpiBus("spi0", clock_hz=1_000_000, width=width, mode=0)


def test_requires_classic_width1_transport():
    with pytest.raises(ValueError):
        JedecCfi("cfi0", _spi(width=4))


def test_read_jedec_id_labels_and_values():
    spi = _spi()
    cfi = JedecCfi("cfi0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    fh = cfi.read_jedec_id(builder, manufacturer_id=0xEF, memory_type=0x40, capacity=0x18)
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    # 0x40 is printable ('@'); format_byte appends the char
    assert [f.label for f in fields] == ["CMD=0x9F", "MFR=0xEF", "TYPE=0x40 '@'", "CAP=0x18"]
    assert [f.data["mosi"] for f in fields] == [0x9F, 0x00, 0x00, 0x00]
    assert [f.data["miso"] for f in fields] == [0x00, 0xEF, 0x40, 0x18]
    assert fh.start == 5 and fh.end == capture.duration_samples  # 5-sample CS recovery gap precedes it


def test_read_flash_command_labels_address_and_data():
    spi = _spi()
    cfi = JedecCfi("cfi0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    cfi.read(builder, address=0x001234, data=[0xDE, 0xAD])
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    # 0x34 is printable ('4'); format_byte appends the char
    assert [f.label for f in fields] == [
        "CMD=0x03", "ADDR[23:16]=0x00", "ADDR[15:8]=0x12", "ADDR[7:0]=0x34 '4'", "0xDE", "0xAD",
    ]


def test_read_flash_command_with_floating_marker_annotates_floating_and_resolves_concrete_bits():
    spi = _spi()
    cfi = JedecCfi("cfi0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    cfi.read(builder, address=0x001234, data="2h", datatype="hex")
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    assert fields[-1].label == "0x2F '/'"  # 0x2 driven, low nibble floating-high -> 0xF
    assert fields[-1].data["miso"] == 0x2F

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1  # coalesced across the 4 floating bits


def test_address_out_of_range_rejected():
    spi = _spi()
    cfi = JedecCfi("cfi0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    with pytest.raises(ValueError):
        cfi.read(builder, address=1 << 24, data=[0])
