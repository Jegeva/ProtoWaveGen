import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.usb import UsbBus
from protowavegen.protocols.usb_msc import UsbMassStorage

# CBW/CSW signature bytes, little-endian ("USBC"/"USBS" as 32-bit LE words) --
# computed once here and cross-checked by hand against usb_msc.py's own
# _le32() during test authoring, not copy-pasted from it.
_CBW_SIG_LE = [0x55, 0x53, 0x42, 0x43]
_CSW_SIG_LE = [0x55, 0x53, 0x42, 0x53]


def _setup():
    usb = UsbBus("usb0")
    msc = UsbMassStorage("msc0", usb)
    builder = CaptureBuilder(samplerate=96_000_000)
    usb.register_signals(builder)
    return msc, builder


def _byte_values(capture):
    """Every byte-payload `field` annotation's decoded value, in emission
    (= wire) order -- same technique `test_usb.py`'s
    `test_data_packet_byte_annotations_and_unit_track` uses."""

    return [
        a.data["value"]
        for a in capture.annotations
        if a.track == "field" and a.label.startswith("0x")
    ]


def _pid_labels(capture):
    """Every DATA0/DATA1 PID `field` annotation's label, in emission order --
    lets a test check DATA0/DATA1 toggle alternation without re-deriving it
    from raw NRZI edges."""

    return [
        a.label
        for a in capture.annotations
        if a.track == "field" and a.label in ("DATA0", "DATA1")
    ]


def test_cbw_byte_layout_and_endianness():
    msc, builder = _setup()
    msc.scsi_inquiry(builder, address=5, vendor="PWGEN", product="SyntheticDisk")
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    cbw = bytes_[0:31]
    assert cbw[0:4] == _CBW_SIG_LE  # dCBWSignature, little-endian
    assert cbw[4:8] == [1, 0, 0, 0]  # dCBWTag: first transfer this instance ever sent -> tag=1
    assert cbw[8:12] == [36, 0, 0, 0]  # dCBWDataTransferLength = 36 (INQUIRY response size)
    assert cbw[12] == 0x80  # bmCBWFlags: IN
    assert cbw[13] == 0x00  # bCBWLUN
    assert cbw[14] == 6  # bCBWCBLength: INQUIRY's CDB is 6 bytes
    assert cbw[15:21] == [0x12, 0x00, 0x00, 0x00, 36, 0x00]  # CBWCB: INQUIRY opcode + alloc length
    assert cbw[21:31] == [0] * 10  # CBWCB zero-padded to 16 bytes total


def test_csw_byte_layout_and_tag_roundtrips():
    msc, builder = _setup()
    msc.scsi_inquiry(builder, address=5, vendor="PWGEN", product="SyntheticDisk")
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    csw = bytes_[31 + 36 : 31 + 36 + 13]
    assert csw[0:4] == _CSW_SIG_LE  # dCSWSignature, little-endian
    assert csw[4:8] == [1, 0, 0, 0]  # dCSWTag matches the CBW's own tag
    assert csw[8:12] == [0, 0, 0, 0]  # dCSWDataResidue: always 0 here
    assert csw[12] == 0x00  # bCSWStatus: Command Passed


def test_inquiry_response_vendor_product_padding_and_truncation():
    msc, builder = _setup()
    msc.scsi_inquiry(builder, address=5, vendor="AB", product="XY")
    capture = builder.finish()
    bytes_ = _byte_values(capture)
    response = bytes(bytes_[31:67])

    assert response[0] == 0x00  # direct-access block device
    assert response[1] == 0x80  # RMB (removable) bit set
    assert response[8:16] == b"AB      "  # padded to 8 ASCII bytes
    assert response[16:32] == b"XY              "  # padded to 16 ASCII bytes

    # A too-long vendor/product truncates instead of raising.
    msc2, builder2 = _setup()
    msc2.scsi_inquiry(builder2, address=5, vendor="TOOLONGVENDOR", product="X" * 30)
    response2 = bytes(_byte_values(builder2.finish())[31:67])
    assert response2[8:16] == b"TOOLONGV"
    assert response2[16:32] == b"X" * 16


def test_read_capacity10_response_big_endian():
    # last_lba deliberately avoids any byte with a long run of consecutive
    # 1 bits (e.g. 0xFF): USB's own bit-stuffing (see usb.py/_usb_nrzi.py)
    # inserts an extra 0 bit after 6 consecutive 1s in the *wire* bitstream,
    # which splits that one logical byte's "field" annotation into two
    # separate same-value annotations in emission order -- a real, harmless
    # property of the encoded waveform (confirmed correct byte-for-byte via
    # the sigrok round-trip test), but it would corrupt this test's own
    # naive one-annotation-per-byte extraction. 256's big-endian bytes
    # (0x00,0x00,0x01,0x00) have no such run.
    msc, builder = _setup()
    msc.scsi_read_capacity10(builder, address=5, last_lba=256, block_size=512)
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    cbw = bytes_[0:31]
    assert cbw[14] == 10  # READ CAPACITY(10)'s CDB is 10 bytes
    assert cbw[15:25] == [0x25, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    response = bytes_[31 : 31 + 8]
    assert response == [0, 0, 0x01, 0x00, 0, 0, 0x02, 0x00]  # last_lba=256 BE, block_size=512 BE


def test_test_unit_ready_has_no_data_stage():
    msc, builder = _setup()
    fh = msc.scsi_test_unit_ready(builder, address=5)
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    assert len(bytes_) == 31 + 13  # CBW + CSW only, no data stage
    cbw = bytes_[0:31]
    assert cbw[8:12] == [0, 0, 0, 0]  # dCBWDataTransferLength = 0
    assert cbw[14] == 6
    assert cbw[15:21] == [0x00, 0, 0, 0, 0, 0]
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_read10_cdb_big_endian_lba_and_blocks():
    msc, builder = _setup()
    data = [0] * 1024  # 2 blocks
    msc.scsi_read10(builder, address=5, lba=0x00010203, data=data)
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    cbw = bytes_[0:31]
    assert cbw[12] == 0x80  # IN direction
    assert cbw[15] == 0x28  # READ(10) opcode
    assert cbw[16] == 0  # reserved
    assert cbw[17:21] == [0x00, 0x01, 0x02, 0x03]  # LBA, big-endian -- NOT the CBW's own little-endian order
    assert cbw[21] == 0  # reserved
    assert cbw[22:24] == [0x00, 0x02]  # 2 blocks, big-endian
    assert cbw[8:12] == [0, 4, 0, 0]  # dCBWDataTransferLength = 1024, little-endian


def test_write10_cdb_opcode_and_out_direction():
    msc, builder = _setup()
    data = [0xAB] * 512  # 1 block
    msc.scsi_write10(builder, address=5, lba=1, data=data)
    capture = builder.finish()
    bytes_ = _byte_values(capture)

    cbw = bytes_[0:31]
    assert cbw[12] == 0x00  # bmCBWFlags: OUT (unlike read10's 0x80)
    assert cbw[15] == 0x2A  # WRITE(10) opcode
    assert cbw[17:21] == [0x00, 0x00, 0x00, 0x01]  # LBA=1, big-endian
    assert cbw[22:24] == [0x00, 0x01]  # 1 block, big-endian

    out_payload = bytes_[31 : 31 + 512]
    assert out_payload == [0xAB] * 512


def test_read10_and_write10_reject_non_block_aligned_length():
    msc, builder = _setup()
    with pytest.raises(ValueError):
        msc.scsi_read10(builder, address=5, lba=0, data=[0] * 100)

    msc2, builder2 = _setup()
    with pytest.raises(ValueError):
        msc2.scsi_write10(builder2, address=5, lba=0, data=[])


def test_toggle_alternates_per_endpoint_across_calls():
    msc, builder = _setup()
    msc.scsi_test_unit_ready(builder, address=5)  # endpoint_out=1 default, endpoint_in=2 default
    msc.scsi_test_unit_ready(builder, address=5)
    capture = builder.finish()
    pids = _pid_labels(capture)

    # Two commands, each CBW(OUT ep1)+CSW(IN ep2): 4 DATA packets total.
    assert pids == ["DATA0", "DATA0", "DATA1", "DATA1"]
    # i.e. ep1's toggle: DATA0 (1st CBW), DATA1 (2nd CBW) -- but interleaved
    # with ep2's own independent toggle in emission order:
    assert pids[0] == "DATA0"  # 1st CBW on ep1 (out)
    assert pids[1] == "DATA0"  # 1st CSW on ep2 (in) -- independent toggle, also starts DATA0
    assert pids[2] == "DATA1"  # 2nd CBW on ep1
    assert pids[3] == "DATA1"  # 2nd CSW on ep2


def test_frame_handle_spans_whole_bot_transaction():
    msc, builder = _setup()
    fh = msc.scsi_inquiry(builder, address=5, vendor="A", product="B")
    capture = builder.finish()
    assert fh.start == 0
    assert fh.end == capture.duration_samples

    summary = [a for a in capture.annotations if a.track == "field" and a.label.startswith("INQUIRY")]
    assert len(summary) == 1
    assert summary[0].start == fh.start and summary[0].end == fh.end
