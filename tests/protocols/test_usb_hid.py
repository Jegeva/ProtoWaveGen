from protowavegen.model import CaptureBuilder
from protowavegen.protocols.usb import UsbBus
from protowavegen.protocols.usb_hid import REPORT_DESCRIPTOR, UsbHid


def _setup(samplerate: int = 48_000_000):
    usb = UsbBus("usb0")
    hid = UsbHid("hid0", usb)
    builder = CaptureBuilder(samplerate=samplerate)
    usb.register_signals(builder)
    return hid, builder


def _byte_values(capture):
    """Every payload-byte `field` annotation's decoded value, in wire
    order -- mirrors `test_usb.py`'s own filtering of `0x..`-labeled
    `field` annotations."""

    return [
        a.data["value"]
        for a in capture.annotations
        if a.track == "field" and a.label.startswith("0x")
    ]


def _pid_labels(capture):
    return [
        a.label
        for a in capture.annotations
        if a.track == "field" and a.label in ("SETUP", "IN", "OUT", "DATA0", "DATA1", "ACK")
    ]


def test_get_hid_descriptor_setup_and_data_bytes():
    hid, builder = _setup()
    hid.get_hid_descriptor(builder, address=5, endpoint=0)
    capture = builder.finish()

    bytes_ = _byte_values(capture)
    # 8 SETUP bytes + 9 IN-stage (HID descriptor) bytes + 0 status bytes.
    assert len(bytes_) == 17
    setup_bytes, hid_desc_bytes = bytes_[:8], bytes_[8:17]

    assert setup_bytes[0] == 0x81  # bmRequestType: device-to-host, standard, interface
    assert setup_bytes[1] == 0x06  # bRequest: GET_DESCRIPTOR
    assert setup_bytes[2:4] == [0x00, 0x21]  # wValue LE: descriptor type 0x21 (HID) << 8
    assert setup_bytes[4:6] == [0x00, 0x00]  # wIndex LE: interface 0
    assert setup_bytes[6:8] == [9, 0x00]  # wLength LE: 9

    report_len = len(REPORT_DESCRIPTOR)
    assert hid_desc_bytes == [
        9, 0x21,           # bLength, bDescriptorType (HID)
        0x10, 0x01,        # bcdHID = 1.10 LE
        0x00,               # bCountryCode
        0x01,               # bNumDescriptors
        0x22,               # bDescriptorType (REPORT)
        report_len & 0xFF, (report_len >> 8) & 0xFF,
    ]

    assert _pid_labels(capture) == ["SETUP", "DATA0", "ACK", "IN", "DATA1", "ACK", "OUT", "DATA1", "ACK"]


def test_get_report_descriptor_returns_report_descriptor_bytes():
    hid, builder = _setup()
    hid.get_report_descriptor(builder, address=5, endpoint=0)
    capture = builder.finish()

    bytes_ = _byte_values(capture)
    setup_bytes = bytes_[:8]
    report_bytes = bytes_[8 : 8 + len(REPORT_DESCRIPTOR)]

    assert setup_bytes[2:4] == [0x00, 0x22]  # wValue LE: descriptor type 0x22 (REPORT) << 8
    assert setup_bytes[6:8] == [len(REPORT_DESCRIPTOR) & 0xFF, (len(REPORT_DESCRIPTOR) >> 8) & 0xFF]
    assert report_bytes == list(REPORT_DESCRIPTOR)


def test_descriptor_summary_annotations_present():
    hid, builder = _setup()
    hid.get_hid_descriptor(builder, address=5, endpoint=0)
    hid.get_report_descriptor(builder, address=5, endpoint=0)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert "GET_DESCRIPTOR(HID)" in labels
    assert "GET_DESCRIPTOR(REPORT)" in labels


def test_send_report_toggles_data_pid_across_calls():
    hid, builder = _setup()
    hid.send_report(builder, buttons=1, x=1, y=1, address=5, endpoint=1)
    hid.send_report(builder, buttons=0, x=0, y=0, address=5, endpoint=1)
    hid.send_report(builder, buttons=2, x=2, y=2, address=5, endpoint=1)
    capture = builder.finish()

    data_pids = [a.label for a in capture.annotations if a.track == "field" and a.label in ("DATA0", "DATA1")]
    assert data_pids == ["DATA0", "DATA1", "DATA0"]


def test_send_report_byte_values_two_complement():
    hid, builder = _setup()
    hid.send_report(builder, buttons=0x03, x=-5, y=-1, address=5, endpoint=1)
    capture = builder.finish()

    bytes_ = _byte_values(capture)
    assert bytes_ == [0x03, 0xFB, 0xFF]

    pid_labels = [a.label for a in capture.annotations if a.track == "field" and a.label in ("IN", "DATA0", "ACK")]
    assert pid_labels == ["IN", "DATA0", "ACK"]


def test_send_report_summary_annotation_spans_whole_frame():
    hid, builder = _setup()
    fh = hid.send_report(builder, buttons=1, x=10, y=-5, address=5, endpoint=1)
    capture = builder.finish()

    summary = [a for a in capture.annotations if a.track == "field" and a.label.startswith("HID report")]
    assert len(summary) == 1
    assert summary[0].label == "HID report buttons=0x01 x=10 y=-5"
    assert summary[0].start == fh.start
    assert summary[0].end == fh.end
