import pytest

from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.lin import LinBus
from timingdiagram.protocols.uart import UartTransport


def _half_duplex_uart(node_id="lin_uart", baudrate=9600):
    return UartTransport(node_id, baudrate=baudrate, duplex="half")


def test_requires_half_duplex_transport():
    full = UartTransport("u0", baudrate=9600, duplex="full")
    with pytest.raises(ValueError):
        LinBus("lin0", full)


def test_protected_id_known_values():
    assert LinBus._protected_id(0x00) == 0x80
    assert LinBus._protected_id(0x01) == 0xC1
    assert LinBus._protected_id(0x3F) == 0xBF


def test_protected_id_out_of_range_rejected():
    with pytest.raises(ValueError):
        LinBus._protected_id(0x40)


def test_checksum_classic_and_enhanced():
    assert LinBus._checksum(0xC1, [0x01, 0x02], "classic") == 0xFC
    assert LinBus._checksum(0xC1, [0x01, 0x02], "enhanced") == 0x3B
    assert LinBus._checksum(0x00, [0xFF, 0xFF], "classic") == 0x00  # end-around carry


def test_send_frame_structure_and_labels():
    transport = _half_duplex_uart()
    lin = LinBus("lin0", transport)
    builder = CaptureBuilder(samplerate=96000)  # bit_period_samples = 10
    transport.register_signals(builder)
    lin.register_signals(builder)
    fh = lin.send_frame(builder, frame_id=0x01, data=[0x01, 0x02], checksum="classic")
    capture = builder.finish()

    line = "lin_uart.data"
    assert transport.bit_period_samples == 10
    # break: 13 low + 1 delimiter-high bit-time = 140 samples
    assert capture.edges[line][:3] == ((0, 1), (0, 0), (130, 1))

    # break(140) + sync(100) + pid(100) + 2 data bytes(200) + checksum(100)
    assert capture.duration_samples == 640
    assert fh.start == 0 and fh.end == 640

    fields = [a for a in capture.annotations if a.track == "field" and a.signals == (line,)]
    assert [f.label for f in fields] == ["BREAK", "SYNC", "PID=0xC1 (ID=1)", "0x01", "0x02", "CHK=0xFC"]


def test_send_frame_without_data_skips_checksum_byte():
    transport = _half_duplex_uart()
    lin = LinBus("lin0", transport)
    builder = CaptureBuilder(samplerate=96000)
    transport.register_signals(builder)
    lin.register_signals(builder)
    lin.send_frame(builder, frame_id=0x10, data=[])
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["BREAK", "SYNC", "PID=0x50 (ID=16)"]


def test_data_field_out_of_range_rejected():
    transport = _half_duplex_uart()
    lin = LinBus("lin0", transport)
    builder = CaptureBuilder(samplerate=96000)
    transport.register_signals(builder)
    lin.register_signals(builder)
    with pytest.raises(ValueError):
        lin.send_frame(builder, frame_id=0, data=[0] * 9)
