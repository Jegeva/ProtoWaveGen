from protowavegen.model import CaptureBuilder
from protowavegen.protocols.sd_spi import SdCardSpi, _crc7
from protowavegen.protocols.spi import SpiBus


def test_crc7_known_value_cmd0():
    # CMD0 with argument 0 is a well-known, widely-published SD command CRC
    # example: command bytes 40 00 00 00 00 -> CRC byte 0x95.
    assert _crc7([0x40, 0x00, 0x00, 0x00, 0x00]) == 0x95


def test_crc7_known_value_cmd8():
    # CMD8 with argument 0x1AA: command bytes 48 00 00 01 AA -> CRC byte 0x87.
    assert _crc7([0x48, 0x00, 0x00, 0x01, 0xAA]) == 0x87


def _setup():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0)
    sd = SdCardSpi("sd0", spi)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    return sd, builder


def test_command_bytes_end_in_fixed_stop_bit():
    cmd_bytes = SdCardSpi._command_bytes(0, 0)
    assert cmd_bytes[0] == 0x40
    assert cmd_bytes[-1] & 0x01 == 1  # fixed stop bit
    assert cmd_bytes[-1] == 0x95  # well-known CMD0 CRC byte


def test_init_sequence_labels():
    sd, builder = _setup()
    sd.init(builder)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert "CMD0" in labels
    assert "R1=IDLE" in labels
    assert "CMD8" in labels
    assert "R7" in labels
    assert "CMD55" in labels
    assert "CMD41" in labels
    assert "R1=READY" in labels


def test_read_block_includes_start_token_and_data():
    sd, builder = _setup()
    fh = sd.read_block(builder, address=0x1000, data=[0xDE, 0xAD, 0xBE, 0xEF])
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert "CMD17" in labels
    assert "TOKEN=0xFE" in labels
    assert "0xDE" in labels and "0xAD" in labels and "0xBE" in labels and "0xEF" in labels
    assert labels.count("CRC16") == 2
    assert fh.end == capture.duration_samples
