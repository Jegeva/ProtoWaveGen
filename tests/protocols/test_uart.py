from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.uart import UartTransport


def _generate(uart, samplerate, **send_kwargs):
    builder = CaptureBuilder(samplerate=samplerate)
    uart.register_signals(builder)
    fh = uart.send(builder, **send_kwargs)
    return builder.finish(), fh


def test_full_duplex_lsb_first_no_parity_one_stop_bit():
    # baudrate/samplerate chosen so samples_per_bit is an exact 10, no rounding.
    uart = UartTransport("uart0", baudrate=9600, data_bits=8, parity="none", stop_bits=1)
    capture, fh = _generate(uart, samplerate=96000, data=[0x55])

    # 0x55 = 0b01010101, LSB first: 1,0,1,0,1,0,1,0
    expected = (
        (0, 1),  # idle
        (0, 0),  # start bit
        (10, 1), (20, 0), (30, 1), (40, 0), (50, 1), (60, 0), (70, 1), (80, 0),  # data bits
        (90, 1),  # stop bit
    )
    assert capture.edges["uart0.tx"] == expected
    assert capture.duration_samples == 100
    assert fh.start == 0 and fh.end == 100

    bitorder = [a for a in capture.annotations if a.track == "bitorder"]
    assert len(bitorder) == 1 and bitorder[0].label == "lsb"


def test_even_parity_bit_computed_correctly():
    uart = UartTransport("uart0", baudrate=9600, parity="even", stop_bits=1)
    # 0x0F = 0b00001111 has four 1-bits -> even parity bit is 0
    capture, _ = _generate(uart, samplerate=96000, data=[0x0F])
    edges = capture.edges["uart0.tx"]
    # slots: start[0,10) data0-3=1[10,50) data4-7=0[50,90) parity=0[90,100) stop[100,110)
    # parity bit 0 is a no-op (already low); stop bit rises at the parity slot's end
    assert (100, 1) in edges
    assert capture.duration_samples == 110


def test_odd_parity_forces_extra_edge():
    uart = UartTransport("uart0", baudrate=9600, parity="odd", stop_bits=1)
    # 0x0F has four 1-bits (even) -> odd parity bit must be 1
    capture, _ = _generate(uart, samplerate=96000, data=[0x0F])
    edges = dict(capture.edges["uart0.tx"])
    # data bits 0-7 end at sample 90 (last data bit 0x0F bit7=0, level 0 held);
    # parity=1 must produce a rising edge at sample 90
    assert edges.get(90) == 1


def test_half_duplex_shares_one_line_and_tags_driver():
    uart = UartTransport("uart0", baudrate=9600, duplex="half")
    builder = CaptureBuilder(samplerate=96000)
    uart.register_signals(builder)
    assert builder.has_signal("uart0.data")
    assert not builder.has_signal("uart0.tx")

    fh = uart.send(builder, channel="data", data=[0x01], driver="node-a")
    capture = builder.finish()
    drivers = [a for a in capture.annotations if a.track == "driver"]
    assert len(drivers) == 1
    assert drivers[0].label == "node-a"
    assert drivers[0].start == fh.start and drivers[0].end == fh.end


def test_labels_override_the_default_format_byte_display():
    uart = UartTransport("uart0", baudrate=9600)
    capture, _ = _generate(uart, samplerate=96000, data=[0x55, 0xAA], labels=["SYNC", "PID=0x2A"])
    fields = [a for a in capture.annotations if a.track == "field"]
    assert [f.label for f in fields] == ["SYNC", "PID=0x2A"]
    assert fields[0].data["value"] == 0x55  # value still recorded even with a custom label


def test_per_byte_unit_and_field_annotations():
    uart = UartTransport("uart0", baudrate=9600, stop_bits=1)
    capture, fh = _generate(uart, samplerate=96000, data=[0x41, 0x42])

    units = [a for a in capture.annotations if a.track == "unit"]
    assert len(units) == 2
    assert units[0].start == 0 and units[0].end == 100
    assert units[1].start == 100 and units[1].end == 200

    fields = [a for a in capture.annotations if a.track == "field"]
    assert [f.data["value"] for f in fields] == [0x41, 0x42]
    # value shown directly in the label, always — not gated behind verbose mode
    assert fields[0].label == "0x41 'A'"
    assert fields[1].label == "0x42 'B'"


def test_bit_period_samples_exposed_after_first_send():
    uart = UartTransport("uart0", baudrate=9600)
    assert uart.bit_period_samples is None
    _generate(uart, samplerate=96000, data=[0])
    assert uart.bit_period_samples == 10


def test_flow_control_adds_rts_cts_signals_and_bracket():
    uart = UartTransport("uart0", baudrate=9600, flow_control="rts_cts")
    builder = CaptureBuilder(samplerate=96000)
    uart.register_signals(builder)
    assert builder.has_signal("uart0.rts")
    assert builder.has_signal("uart0.cts")

    uart.send(builder, data=[0x00])
    capture = builder.finish()
    # both released back to idle-high after the transaction
    assert capture.edges["uart0.rts"][-1][1] == 1
    assert capture.edges["uart0.cts"][-1][1] == 1
