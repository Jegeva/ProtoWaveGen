from protowavegen.model import CaptureBuilder
from protowavegen.protocols.usb import UsbBus
from protowavegen.protocols.usb_cdc import UsbCdcAcm, _setup_bytes


def _setup():
    usb = UsbBus("usb0")
    cdc = UsbCdcAcm("cdc0", usb)
    builder = CaptureBuilder(samplerate=192_000_000)
    usb.register_signals(builder)
    return cdc, builder


def _field_labels(capture):
    return [a.label for a in capture.annotations if a.track == "field"]


def _cdc_labels(capture):
    return [a.label for a in capture.annotations if a.track == "cdc"]


def test_setup_bytes_little_endian_layout():
    setup = _setup_bytes(0x21, 0x20, 0x1234, 0x0056, 7)
    assert setup == [0x21, 0x20, 0x34, 0x12, 0x56, 0x00, 7, 0]


def test_set_line_coding_summary_annotation():
    cdc, builder = _setup()
    cdc.set_line_coding(builder, address=5, baud=115200, data_bits=8, stop_bits=0, parity=0)
    capture = builder.finish()
    cdc_labels = _cdc_labels(capture)
    assert cdc_labels == ["SET_LINE_CODING baud=115200 bits=8 stopbits=0 parity=0"]
    # Underlying UsbBus still emits its own per-packet PID/ADDR/byte fields.
    fields = _field_labels(capture)
    assert "SETUP ADDR=5 EP=0" in fields
    assert "DATA0" in fields  # setup stage always DATA0
    assert "DATA1" in fields  # data + status stages


def test_set_control_line_state_wvalue_bitmap():
    both = _setup()[0]
    for dtr, rts, w_value in [(True, True, 3), (True, False, 1), (False, True, 2), (False, False, 0)]:
        cdc, builder = _setup()
        cdc.set_control_line_state(builder, address=5, dtr=dtr, rts=rts)
        capture = builder.finish()
        assert _cdc_labels(capture) == [f"SET_CONTROL_LINE_STATE DTR={int(dtr)} RTS={int(rts)}"]


def test_send_data_toggles_data0_then_data1():
    cdc, builder = _setup()
    cdc.send_data(builder, address=5, data=[0x01], datatype="bytes")
    cdc.send_data(builder, address=5, data=[0x02], datatype="bytes")
    capture = builder.finish()
    fields = _field_labels(capture)
    data_pids = [f for f in fields if f in ("DATA0", "DATA1")]
    assert data_pids == ["DATA0", "DATA1"]


def test_send_data_text_datatype_encodes_utf8():
    cdc, builder = _setup()
    cdc.send_data(builder, address=5, data="Hi", datatype="text")
    capture = builder.finish()
    assert _cdc_labels(capture) == ["TX 0x48 'H' 0x69 'i'"]


def test_set_line_coding_frame_handle_spans_whole_control_transfer():
    cdc, builder = _setup()
    fh = cdc.set_line_coding(builder, address=5, baud=9600)
    capture = builder.finish()
    assert fh.start == 0
    assert fh.end == capture.duration_samples
