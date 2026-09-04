import pytest

from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.dmx512 import Dmx512
from timingdiagram.protocols.uart import UartTransport


def _setup():
    uart = UartTransport("uart0", baudrate=250_000, data_bits=8, parity="none", stop_bits=2, duplex="full")
    dmx = Dmx512("dmx0", uart)
    builder = CaptureBuilder(samplerate=1_000_000)
    uart.register_signals(builder)
    return dmx, uart, builder


def test_requires_full_duplex_transport():
    half = UartTransport("u0", baudrate=250_000, duplex="half")
    with pytest.raises(ValueError):
        Dmx512("dmx0", half)


def test_send_frame_break_and_mab_timing():
    dmx, uart, builder = _setup()
    fh = dmx.send_frame(builder, channels=[1, 2, 3])
    capture = builder.finish()

    line = "uart0.tx"
    assert capture.edges[line][:3] == ((0, 1), (0, 0), (100, 1))  # break low 100us, then MAB high

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels[0] == "BREAK"
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_too_many_channels_rejected():
    dmx, uart, builder = _setup()
    with pytest.raises(ValueError):
        dmx.send_frame(builder, channels=[0] * 513)


def test_start_code_and_channel_bytes_sent():
    dmx, uart, builder = _setup()
    dmx.send_frame(builder, channels=[10, 20, 30], start_code=0)
    fields = [a for a in builder.finish().annotations if a.track == "field" and "value" in a.data]
    assert [f.data["value"] for f in fields] == [0, 10, 20, 30]
