from protowavegen.model import CaptureBuilder, SignalKind
from protowavegen.protocols.ps2 import Ps2Bus


def _setup(clock_hz=12_500, inhibit_us=100):
    ps2 = Ps2Bus("ps2_0", clock_hz=clock_hz, inhibit_us=inhibit_us)
    builder = CaptureBuilder(samplerate=1_000_000)
    ps2.register_signals(builder)
    return ps2, builder


def test_get_signals_open_drain():
    ps2, _ = _setup()
    signals = ps2.get_signals()
    assert {s.name for s in signals} == {"ps2_0.clock", "ps2_0.data"}
    assert all(s.kind == SignalKind.TRISTATE and s.initial_level == 1 for s in signals)


def test_odd_parity():
    assert Ps2Bus._odd_parity(0x00) == 1  # zero data bits -> parity bit makes count odd (1)
    assert Ps2Bus._odd_parity(0xFF) == 1  # eight data bits (even) -> parity 1 to stay odd... wait check
    assert Ps2Bus._odd_parity(0x01) == 0  # one data bit (odd already) -> parity 0


def test_send_from_device_frame_length_and_content():
    ps2, builder = _setup()
    fh = ps2.send_from_device(builder, byte=0x41)
    capture = builder.finish()

    # 11 bits (start+8 data+parity+stop) * 2 half-periods each
    half = ps2.bit_period_samples // 2
    assert capture.duration_samples == 11 * 2 * half
    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["0x41 'A'"]
    assert fh.end == capture.duration_samples


def test_send_to_host_includes_inhibit_and_ack():
    ps2, builder = _setup()
    fh = ps2.send_to_host(builder, byte=0x01)
    capture = builder.finish()

    clock_edges = capture.edges["ps2_0.clock"]
    assert clock_edges[1] == (0, 0)  # inhibit starts immediately

    drivers = [a.label for a in capture.annotations if a.track == "driver"]
    assert "host" in drivers
    assert "device" in drivers
    assert fh.end == capture.duration_samples


def test_bit_period_samples_after_bind():
    ps2, builder = _setup()
    assert ps2.bit_period_samples is None
    ps2.send_from_device(builder, byte=0)
    assert ps2.bit_period_samples == round(1_000_000 / (2 * 12_500)) * 2
