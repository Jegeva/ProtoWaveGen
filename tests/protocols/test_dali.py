from protowavegen.model import CaptureBuilder
from protowavegen.protocols.base import DriverTracker
from protowavegen.protocols.dali import DaliBus


def _setup(baudrate=1200):
    dali = DaliBus("dali0", baudrate=baudrate)
    builder = CaptureBuilder(samplerate=12_000)  # 10 samples/bit
    dali.register_signals(builder)
    return dali, builder


def test_manchester_bit_1_is_low_to_high_transition():
    dali, builder = _setup()
    dali._ensure_bound(builder)
    dali._manchester_bit(builder, 1, DriverTracker(builder, "dali0.dali"))
    capture = builder.finish()
    line = "dali0.dali"
    assert capture.edges[line] == ((0, 1), (0, 0), (5, 1))  # low half, then high half


def test_manchester_bit_0_is_high_to_low_transition():
    dali, builder = _setup()
    dali._ensure_bound(builder)
    dali._manchester_bit(builder, 0, DriverTracker(builder, "dali0.dali"))
    capture = builder.finish()
    line = "dali0.dali"
    assert capture.edges[line] == ((0, 1), (5, 0))  # starts high (idle), drops at half-bit


def test_forward_frame_structure():
    dali, builder = _setup()
    fh = dali.send_forward_frame(builder, DALI_ADDRESS=0x01, command=0xFE)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["ADDR=0x01", "CMD=0xFE"]
    # START(1 bit) + ADDR(8) + CMD(8) + 2 stop bits = 19 bit periods * 10 samples
    assert capture.duration_samples == 19 * 10
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_backward_frame_structure():
    dali, builder = _setup()
    dali.send_backward_frame(builder, answer=0xFF)
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["ANSWER=0xFF"]
    # START(1) + ANSWER(8) + 2 stop bits = 11 bit periods
    assert capture.duration_samples == 11 * 10


def test_forward_frame_with_floating_marker_annotates_floating_and_resolves_concrete_bits():
    dali, builder = _setup()
    fh = dali.send_forward_frame(
        builder, DALI_ADDRESS="2h", command=0xFE, DALI_ADDRESS_datatype="hex",
    )
    capture = builder.finish()
    assert fh is not None

    fields = [a for a in capture.annotations if a.track == "field"]
    assert fields[0].label == "ADDR=0x2F"  # 0x2 driven, low nibble floating-high -> 0xF
    assert fields[1].label == "CMD=0xFE"  # unaffected, still plain-int path

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1  # coalesced across the 4 floating bits


def test_backward_frame_with_floating_marker():
    dali, builder = _setup()
    dali.send_backward_frame(builder, answer="ll", answer_datatype="hex")
    capture = builder.finish()

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.label == "ANSWER=0x00"

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1


def test_forward_frame_plain_int_backward_compat_unaffected():
    dali, builder = _setup()
    dali.send_forward_frame(builder, DALI_ADDRESS=0x01, command=0xFE)
    capture = builder.finish()
    assert not any(a.label == "floating" for a in capture.annotations if a.track == "driver")


def test_bit_period_samples_exposed_after_bind():
    dali, builder = _setup()
    assert dali.bit_period_samples is None
    dali._ensure_bound(builder)
    assert dali.bit_period_samples == 10
