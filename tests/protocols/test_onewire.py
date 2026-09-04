from protowavegen.model import CaptureBuilder, SignalKind
from protowavegen.protocols.onewire import OneWireBus


def _builder(samplerate=1_000_000):
    b = CaptureBuilder(samplerate=samplerate)
    return b


def test_get_signals_declares_open_drain_dq():
    ow = OneWireBus("ow0")
    signals = ow.get_signals()
    assert len(signals) == 1
    assert signals[0].name == "ow0.dq"
    assert signals[0].kind == SignalKind.TRISTATE
    assert signals[0].initial_level == 1


def test_reset_with_presence_exact_edges():
    # samplerate=1_000_000 -> 1 sample per microsecond, exact timing.
    ow = OneWireBus("ow0")
    builder = _builder()
    ow.register_signals(builder)
    fh = ow.reset(builder, presence=True)
    capture = builder.finish()

    assert capture.edges["ow0.dq"] == ((0, 1), (0, 0), (480, 1), (510, 0), (630, 1))
    assert capture.duration_samples == 480 + 30 + 120 + 500
    assert fh.start == 0 and fh.end == capture.duration_samples

    drivers = [(a.label, a.start, a.end) for a in capture.annotations if a.track == "driver"]
    assert ("master", 0, 480) in drivers
    assert ("pullup", 480, 510) in drivers
    assert ("slave", 510, 630) in drivers
    assert ("pullup", 630, 1130) in drivers  # two separate pullup spans, not merged across the slave span


def test_reset_no_presence_shorter_and_skips_slave_span():
    ow = OneWireBus("ow0")
    builder = _builder()
    ow.register_signals(builder)
    ow.reset(builder, presence=False)
    capture = builder.finish()

    assert capture.edges["ow0.dq"] == ((0, 1), (0, 0), (480, 1))
    assert capture.duration_samples == 480 + 30 + 500
    assert not any(a.track == "driver" and a.label == "slave" for a in capture.annotations)
    field = [a for a in capture.annotations if a.track == "field"][0]
    assert "no presence" in field.label


def test_write_byte_lsb_first_write0_and_write1_patterns():
    ow = OneWireBus("ow0")
    builder = _builder()
    ow.register_signals(builder)
    # bit0=1 (write-1, short 6us pulse), bits1-7=0 (write-0, low for the
    # 70us slot minus a brief 2us end-of-slot recovery release).
    ow.write(builder, data=[0x01])
    capture = builder.finish()

    expected = [(0, 1)]
    t = 0
    for i in range(8):
        bit = (0x01 >> i) & 1
        expected.append((t, 0))
        expected.append((t + (6 if bit else 68), 1))
        t += 70
    assert capture.edges["ow0.dq"] == tuple(expected)
    assert capture.duration_samples == 8 * 70

    fields = [a for a in capture.annotations if a.track == "field"]
    assert fields[0].label == "0x01"
    assert fields[0].data["value"] == 1


def test_read_byte_lsb_first_read0_and_read1_patterns():
    ow = OneWireBus("ow0")
    builder = _builder()
    ow.register_signals(builder)
    ow.read(builder, data=[0x01])
    capture = builder.finish()

    expected = [(0, 1)]
    t = 0
    for i in range(8):
        bit = (0x01 >> i) & 1
        expected.append((t, 0))
        expected.append((t + (6 if bit else 30), 1))
        t += 70
    assert capture.edges["ow0.dq"] == tuple(expected)
    assert capture.duration_samples == 8 * 70


def test_bit_period_samples_exposed_after_first_operation():
    ow = OneWireBus("ow0")
    assert ow.bit_period_samples is None
    builder = _builder()
    ow.register_signals(builder)
    ow.reset(builder)
    assert ow.bit_period_samples == 70  # one 70us slot at 1 sample/us
