from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.dali import DaliBus


def _setup(baudrate=1200):
    dali = DaliBus("dali0", baudrate=baudrate)
    builder = CaptureBuilder(samplerate=12_000)  # 10 samples/bit
    dali.register_signals(builder)
    return dali, builder


def test_manchester_bit_1_is_low_to_high_transition():
    dali, builder = _setup()
    dali._ensure_bound(builder)
    dali._manchester_bit(builder, 1)
    capture = builder.finish()
    line = "dali0.dali"
    assert capture.edges[line] == ((0, 1), (0, 0), (5, 1))  # low half, then high half


def test_manchester_bit_0_is_high_to_low_transition():
    dali, builder = _setup()
    dali._ensure_bound(builder)
    dali._manchester_bit(builder, 0)
    capture = builder.finish()
    line = "dali0.dali"
    assert capture.edges[line] == ((0, 1), (5, 0))  # starts high (idle), drops at half-bit


def test_forward_frame_structure():
    dali, builder = _setup()
    fh = dali.send_forward_frame(builder, address=0x01, command=0xFE)
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


def test_bit_period_samples_exposed_after_bind():
    dali, builder = _setup()
    assert dali.bit_period_samples is None
    dali._ensure_bound(builder)
    assert dali.bit_period_samples == 10
