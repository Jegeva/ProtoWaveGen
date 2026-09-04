from protowavegen.model import CaptureBuilder
from protowavegen.protocols.checksums import crc16_modbus
from protowavegen.protocols.ds243x import Ds243x
from protowavegen.protocols.onewire import OneWireBus


def _setup():
    ow = OneWireBus("ow0")
    ee = Ds243x("ee0", ow)
    builder = CaptureBuilder(samplerate=2_000_000)
    ow.register_signals(builder)
    return ee, builder


def test_write_memory_does_three_transactions():
    ee, builder = _setup()
    fh = ee.write_memory(builder, address=0x0010, data=[0xAA, 0xBB])
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels.count("CMD=SKIP_ROM") == 3  # ROM addressed fresh for each of the 3 transactions
    assert "CMD=WRITE_SP" in labels
    assert "CMD=READ_SP" in labels
    assert "CMD=COPY_SP" in labels
    assert fh.end == capture.duration_samples


def test_write_memory_scratchpad_crc_is_correct():
    ee, builder = _setup()
    ee.write_memory(builder, address=0x0010, data=[0xAA, 0xBB])
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]

    expected_crc = crc16_modbus([0xAA, 0x10, 0x00, 0x01, 0xAA, 0xBB])
    assert labels.count(f"CRC=0x{expected_crc:04X}") == 2  # low + high CRC bytes share the label


def test_read_memory_direct_path_no_scratchpad():
    ee, builder = _setup()
    fh = ee.read_memory(builder, address=0x0020, data=[0x01, 0x02, 0x03])
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels.count("CMD=SKIP_ROM") == 1
    assert "CMD=READ_MEM" in labels
    assert "CMD=WRITE_SP" not in labels
    assert [a.data["value"] for a in capture.annotations if a.track == "field" and "value" in a.data][-3:] == [
        0x01, 0x02, 0x03,
    ]
    assert fh.end == capture.duration_samples


def test_read_memory_with_floating_marker_annotates_floating():
    ee, builder = _setup()
    # "2h" -> 0x2 driven, low nibble floating-high -> 0x2F
    ee.read_memory(builder, address=0x0020, data="2h", datatype="hex")
    capture = builder.finish()

    values = [a.data["value"] for a in capture.annotations if a.track == "field" and "value" in a.data]
    assert values[-1] == 0x2F

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1


def test_read_memory_plain_list_backward_compat_unaffected():
    ee, builder = _setup()
    ee.read_memory(builder, address=0x0020, data=[0x01, 0x02, 0x03])
    capture = builder.finish()
    assert not any(a.label == "floating" for a in capture.annotations if a.track == "driver")
