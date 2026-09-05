import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.spi import SpiBus
from protowavegen.protocols.spiflash import SpiFlash


def _spi(width=1):
    return SpiBus("spi0", clock_hz=1_000_000, width=width, mode=0)


def _setup():
    spi = _spi()
    flash = SpiFlash("flash0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    return flash, builder


def test_requires_classic_width1_transport():
    with pytest.raises(ValueError):
        SpiFlash("flash0", _spi(width=4))


def test_write_enable_and_disable_labels():
    flash, builder = _setup()
    flash.write_enable(builder)
    flash.write_disable(builder)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == ["CMD=WREN", "CMD=WRDI"]


def test_read_status_and_write_status():
    flash, builder = _setup()
    flash.write_status(builder, value=0x02)
    flash.read_status(builder, value=0x02)
    capture = builder.finish()
    fields = [a for a in capture.annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "CMD=WRSR", "SR1=0x02", "CMD=RDSR", "SR1=0x02",
    ]


def test_page_program_labels_address_and_data():
    flash, builder = _setup()
    flash.page_program(builder, address=0x001000, data=[0xDE, 0xAD])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "CMD=PP", "ADDR[23:16]=0x00", "ADDR[15:8]=0x10", "ADDR[7:0]=0x00", "0xDE", "0xAD",
    ]


def test_read_labels_address_and_data():
    flash, builder = _setup()
    flash.read(builder, address=0x001000, data=[0xDE, 0xAD])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "CMD=READ", "ADDR[23:16]=0x00", "ADDR[15:8]=0x10", "ADDR[7:0]=0x00", "0xDE", "0xAD",
    ]


def test_fast_read_includes_dummy_byte():
    flash, builder = _setup()
    flash.fast_read(builder, address=0x001000, data=[0xDE])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "CMD=FAST_READ", "ADDR[23:16]=0x00", "ADDR[15:8]=0x10", "ADDR[7:0]=0x00", "DUMMY", "0xDE",
    ]


def test_sector_erase_requires_4k_alignment():
    flash, builder = _setup()
    with pytest.raises(ValueError):
        flash.sector_erase(builder, address=100)


def test_sector_erase_labels():
    flash, builder = _setup()
    fh = flash.sector_erase(builder, address=4096)
    capture = builder.finish()
    fields = [a for a in capture.annotations if a.track == "field"]
    assert [f.label for f in fields] == [
        "CMD=SE", "ADDR[23:16]=0x00", "ADDR[15:8]=0x10", "ADDR[7:0]=0x00",
    ]
    assert fh.end == capture.duration_samples


def test_chip_erase_label():
    flash, builder = _setup()
    flash.chip_erase(builder)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == ["CMD=CE"]


def test_page_program_with_floating_marker_resolves_concrete_bits():
    flash, builder = _setup()
    flash.page_program(builder, address=0x1000, data="2h", datatype="hex")
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    assert fields[-1].label == "0x2F '/'"  # 0x2 driven, low nibble floating-high -> 0xF

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1
