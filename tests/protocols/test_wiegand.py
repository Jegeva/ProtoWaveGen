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


def test_send_bits_datatype_bits_with_floating_marker():
    wg, builder = _setup()
    # "0z1" -> bit0=0 (driven), bit1=z (floating, resolves high via pullup), bit2=1 (driven)
    fh = wg.send_bits(builder, bits="0z1", datatype="bits")
    capture = builder.finish()

    d0_edges = capture.edges["wg0.d0"]
    d1_edges = capture.edges["wg0.d1"]
    assert d0_edges == ((0, 1), (0, 0), (50, 1))  # bit0=0 pulses d0
    # z resolves to 1 (pull-high) -> pulses d1, same as a real 1 would
    assert d1_edges[1] == (2000, 0)
    assert fh.end == 4050

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1
    assert floating[0].signals == ("wg0.d1",)  # the z bit pulsed d1


def test_send_bits_plain_list_backward_compat_unaffected_by_datatype_param():
    wg, builder = _setup()
    wg.send_bits(builder, bits=[0, 1])  # no datatype given -> unchanged today's behavior
    capture = builder.finish()
    assert not any(a.label == "floating" for a in capture.annotations if a.track == "driver")


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


def test_send_card_26bit_bits_datatype_with_floating_marker():
    wg, builder = _setup()
    # "0000110z" -> facility_code 0b0000110(z=1) = 13, matches plain int 13
    fh = wg.send_card_26bit(
        builder, facility_code="0000110z", facility_code_datatype="bits", card_number=34567,
    )
    capture = builder.finish()

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.label == "FC=13 CARD=34567"
    assert field.data["facility_code"] == 13 and field.data["card_number"] == 34567

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1
    assert fh is not None


def test_send_card_26bit_bits_datatype_wrong_length_raises():
    wg, builder = _setup()
    with pytest.raises(ValueError, match="facility_code"):
        wg.send_card_26bit(builder, facility_code="0000110", facility_code_datatype="bits", card_number=1)


def test_send_card_26bit_rejects_byte_oriented_datatype():
    wg, builder = _setup()
    with pytest.raises(ValueError, match="'bytes' or 'bits'"):
        wg.send_card_26bit(builder, facility_code="0c", facility_code_datatype="hex", card_number=1)


def test_send_card_26bit_plain_int_backward_compat_unaffected():
    wg, builder = _setup()
    wg.send_card_26bit(builder, facility_code=12, card_number=34567)
    capture = builder.finish()
    assert not any(a.label == "floating" for a in capture.annotations if a.track == "driver")
