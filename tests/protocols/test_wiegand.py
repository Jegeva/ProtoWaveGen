import pytest

from protowavegen.model import CaptureBuilder, SignalKind
from protowavegen.protocols.wiegand import WiegandBus


def _setup(pulse_us=50, interval_us=2000):
    wg = WiegandBus("wg0", pulse_us=pulse_us, interval_us=interval_us)
    builder = CaptureBuilder(samplerate=1_000_000)  # 1 sample/us
    wg.register_signals(builder)
    return wg, builder


def test_get_signals_open_drain():
    wg, _ = _setup()
    signals = wg.get_signals()
    assert {s.name for s in signals} == {"wg0.d0", "wg0.d1"}
    assert all(s.kind == SignalKind.TRISTATE and s.initial_level == 1 for s in signals)


def test_send_bits_pulses_correct_line():
    wg, builder = _setup()
    wg.send_bits(builder, bits=[0, 1])
    capture = builder.finish()

    d0_edges = capture.edges["wg0.d0"]
    d1_edges = capture.edges["wg0.d1"]
    assert d0_edges == ((0, 1), (0, 0), (50, 1))  # bit0=0 pulses d0
    assert d1_edges == ((0, 1), (2000, 0), (2050, 1))  # bit1=1 pulses d1, one interval later


def test_last_bit_has_no_trailing_interval():
    wg, builder = _setup()
    fh = wg.send_bits(builder, bits=[0])
    capture = builder.finish()
    assert fh.end == 50  # pulse only, no interval after the last bit
    assert capture.duration_samples == 50


def test_26bit_card_parity_properties():
    wg, builder = _setup()
    wg.send_card_26bit(builder, facility_code=12, card_number=34567)
    capture = builder.finish()

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.label == "FC=12 CARD=34567"

    # reconstruct the 26-bit frame from the generated pulses to verify parity
    d0 = dict(capture.edges["wg0.d0"])
    d1 = dict(capture.edges["wg0.d1"])
    low_starts = sorted([s for s, lvl in d0.items() if lvl == 0] + [s for s, lvl in d1.items() if lvl == 0])
    bits = [1 if s in d1 and d1[s] == 0 else 0 for s in low_starts]
    assert len(bits) == 26
    assert sum(bits[0:13]) % 2 == 0  # leading parity bit makes bits 1-13 even
    assert sum(bits[13:26]) % 2 == 1  # trailing parity bit makes bits 14-26 odd


def test_facility_code_out_of_range_rejected():
    wg, builder = _setup()
    with pytest.raises(ValueError):
        wg.send_card_26bit(builder, facility_code=256, card_number=0)
