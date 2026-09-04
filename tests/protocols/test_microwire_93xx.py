from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.microwire import MicrowireBus
from timingdiagram.protocols.microwire_93xx import Microwire93xxEeprom


def _setup(addr_bits=6):
    mw = MicrowireBus("mw0", clock_hz=1_000_000)
    ee = Microwire93xxEeprom("ee0", mw, addr_bits=addr_bits)
    builder = CaptureBuilder(samplerate=10_000_000)
    mw.register_signals(builder)
    return ee, builder


def test_write_auto_enables_once():
    ee, builder = _setup()
    ee.write(builder, address=1, value=0x1234)
    ee.write(builder, address=2, value=0x5678)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels.count("EWEN") == 1  # only enabled once, tracked via _write_enabled
    assert "WRITE[1]=0x1234" in labels
    assert "WRITE[2]=0x5678" in labels


def test_ewds_resets_enabled_flag():
    ee, builder = _setup()
    ee.ewen(builder)
    ee.ewds(builder)
    ee.write(builder, address=0, value=0)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels.count("EWEN") == 2  # re-enabled after ewds


def test_read_returns_synthesized_value():
    ee, builder = _setup()
    fh = ee.read(builder, address=5, value=0xABCD)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels == ["READ[5]=0xABCD"]
    assert fh.end == builder.cursor


def test_address_out_of_range_rejected():
    import pytest

    ee, builder = _setup(addr_bits=6)
    with pytest.raises(ValueError):
        ee.read(builder, address=64, value=0)
