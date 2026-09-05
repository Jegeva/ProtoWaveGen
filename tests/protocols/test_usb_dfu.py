from protowavegen.model import CaptureBuilder
from protowavegen.protocols.usb import UsbBus
from protowavegen.protocols.usb_dfu import (
    DFU_DNBUSY,
    DFU_IDLE,
    UsbDfu,
)


def _field_labels(capture):
    return [a.label for a in capture.annotations if a.track == "field"]


def _build(samplerate=48_000_000):
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=samplerate)
    usb.register_signals(builder)
    dfu = UsbDfu("dfu0", usb)
    return usb, dfu, builder


def test_dnload_produces_expected_summary_annotation():
    usb, dfu, builder = _build()
    dfu.dnload(builder, address=5, block_num=3, data=[0xDE, 0xAD])
    capture = builder.finish()

    assert "DFU DNLOAD block=3 len=2" in _field_labels(capture)


def test_upload_produces_expected_summary_annotation():
    usb, dfu, builder = _build()
    dfu.upload(builder, address=5, block_num=1, data=[0x01, 0x02, 0x03])
    capture = builder.finish()

    assert "DFU UPLOAD block=1 len=3" in _field_labels(capture)


def test_abort_produces_expected_summary_annotation():
    usb, dfu, builder = _build()
    dfu.abort(builder, address=5)
    capture = builder.finish()

    assert "DFU ABORT" in _field_labels(capture)


def test_get_status_summary_annotation_names_the_real_state():
    usb, dfu, builder = _build()
    dfu.get_status(builder, address=5, status=0, state=DFU_DNBUSY, poll_timeout_ms=0x0203)
    capture = builder.finish()

    assert "DFU GETSTATUS status=0 state=dfuDNBUSY" in _field_labels(capture)


def test_get_status_encodes_six_byte_response_little_endian():
    usb, dfu, builder = _build()
    dfu.get_status(builder, address=5, status=7, state=DFU_DNBUSY, poll_timeout_ms=0x0203)
    capture = builder.finish()

    # The status response is the IN-direction DATA1 payload -- UsbBus's own
    # per-byte "field" annotations (format_byte()-labeled, from
    # _annotate_role) carry the actual byte values. Collect every
    # byte-shaped ("0x..") field annotation and take the last 6 -- the
    # GETSTATUS response is the only DATA stage with a payload in this
    # capture (the SETUP stage's own DATA0 payload also produces byte
    # annotations, so we can't just take "all of them").
    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    values = [a.data["value"] for a in byte_fields]
    status_bytes = values[-6:]

    # bStatus=7, bwPollTimeout=0x000203 (LE: 0x03,0x02,0x00), bState=DFU_DNBUSY, iString=0.
    assert status_bytes == [7, 0x03, 0x02, 0x00, DFU_DNBUSY, 0]


def test_dnload_empty_data_signals_download_complete_with_real_zero_length_out_packet():
    """The brief's explicit requirement: `dnload(block_num=0, data=[])` must
    produce a genuine zero-length OUT DATA1 packet (Setup -> OUT Data stage
    -> IN Status stage), not silently collapse into a zero-length-data-stage
    request (Setup -> IN Status stage only, no OUT stage at all) the way
    `dnload`'s own `data` field would if `out_data=None` were passed to
    `control_transfer` instead of `out_data=[]`.

    Proven by comparing sample-for-sample against directly driving
    `UsbBus.control_transfer` twice on fresh builders: once the way
    `dnload(data=[])` should behave (`out_data=[]`) and once the way it must
    NOT behave (`out_data=None`, a true zero-length-data-stage request) --
    the two must differ, and `dnload`'s own output must match the former,
    not the latter.
    """

    usb, dfu, builder = _build()
    dfu.dnload(builder, address=5, block_num=0, data=[])
    dfu_capture = builder.finish()

    setup = [0x21, 1, 0, 0, 0, 0, 0, 0]  # bmRequestType, DFU_DNLOAD, wValue=0, wIndex=0, wLength=0

    real_out_usb = UsbBus("usb0")
    real_out_builder = CaptureBuilder(samplerate=48_000_000)
    real_out_usb.register_signals(real_out_builder)
    real_out_usb.control_transfer(real_out_builder, address=5, endpoint=0, setup_data=setup, out_data=[])
    real_out_capture = real_out_builder.finish()

    skipped_stage_usb = UsbBus("usb0")
    skipped_stage_builder = CaptureBuilder(samplerate=48_000_000)
    skipped_stage_usb.register_signals(skipped_stage_builder)
    skipped_stage_usb.control_transfer(skipped_stage_builder, address=5, endpoint=0, setup_data=setup)
    skipped_stage_capture = skipped_stage_builder.finish()

    dfu_dp_edges = dfu_capture.edges["usb0.dp"]
    real_out_dp_edges = real_out_capture.edges["usb0.dp"]
    skipped_stage_dp_edges = skipped_stage_capture.edges["usb0.dp"]

    # dnload(data=[]) must match the real out_data=[] shape exactly...
    assert dfu_dp_edges == real_out_dp_edges
    # ...and that shape must genuinely differ from the skipped-Data-stage
    # shape (proving the extra OUT+DATA1(empty)+ACK triple is really there).
    assert dfu_dp_edges != skipped_stage_dp_edges

    # Concretely: the real out_data=[] shape has 3 token+data+handshake
    # triples (SETUP, OUT, IN status); the skipped-stage shape has only 2
    # (SETUP, IN status) -- an OUT-direction PID never appears in the latter.
    field_annotations = [a for a in dfu_capture.annotations if a.track == "field"]
    assert any(a.label == "OUT ADDR=5 EP=0" for a in field_annotations)

    skipped_field_annotations = [a for a in skipped_stage_capture.annotations if a.track == "field"]
    assert not any(a.label.startswith("OUT ") for a in skipped_field_annotations)


def test_abort_is_a_zero_length_data_stage_request_with_no_out_stage():
    usb, dfu, builder = _build()
    dfu.abort(builder, address=5)
    capture = builder.finish()

    field_annotations = [a for a in capture.annotations if a.track == "field"]
    assert not any(a.label.startswith("OUT ") for a in field_annotations)
    assert any(a.label == "IN ADDR=5 EP=0" for a in field_annotations)


def test_dfu_state_name_table_matches_dfu_1_1_spec_values():
    from protowavegen.protocols.usb_dfu import _STATE_NAMES

    assert _STATE_NAMES[DFU_IDLE] == "dfuIDLE"
    assert _STATE_NAMES[DFU_DNBUSY] == "dfuDNBUSY"
