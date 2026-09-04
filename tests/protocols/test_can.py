from protowavegen.model import CaptureBuilder
from protowavegen.protocols.can import CanBus, _crc15, _stuff


def test_crc15_of_all_zero_bits_is_zero():
    assert _crc15([0] * 27) == [0] * 15


def test_crc15_is_deterministic_and_sensitive_to_input():
    bits = [1, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 1, 1, 0, 0, 0, 1]
    result = _crc15(bits)
    assert len(result) == 15
    assert result == _crc15(bits)
    assert result != _crc15([0] * len(bits))


def test_stuff_inserts_opposite_bit_after_five_identical():
    bits = [0, 0, 0, 0, 0, 1, 1]
    roles = ["a"] * 5 + ["b", "b"]
    stuffed_bits, stuffed_roles, stuffed_floating = _stuff(bits, roles, [False] * len(bits))
    assert stuffed_bits == [0, 0, 0, 0, 0, 1, 1, 1]
    assert stuffed_roles == ["a", "a", "a", "a", "a", "stuff", "b", "b"]
    assert stuffed_floating == [False] * len(stuffed_bits)


def test_stuff_noop_when_no_run_reaches_five():
    bits = [0, 0, 1, 1, 0, 0, 1]
    roles = ["x"] * len(bits)
    stuffed_bits, stuffed_roles, _ = _stuff(bits, roles, [False] * len(bits))
    assert stuffed_bits == bits
    assert stuffed_roles == roles


def test_stuff_handles_a_run_created_by_a_previous_stuff_bit():
    # 4 zeros + the stuff-created 1 could itself start a new run if followed
    # by four more 1s — the tracker must reset off the inserted bit, not the
    # original one.
    bits = [0, 0, 0, 0, 0, 1, 1, 1, 1]
    stuffed, _, _ = _stuff(bits, ["r"] * len(bits), [False] * len(bits))
    assert stuffed == [0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0]


def test_stuff_carries_floating_flag_through_and_marks_inserted_bit_not_floating():
    bits = [0, 0, 0, 0, 0, 1, 1]
    roles = ["a"] * 5 + ["b", "b"]
    floating = [True, False, True, False, True, True, False]
    stuffed_bits, _, stuffed_floating = _stuff(bits, roles, floating)
    # the inserted stuff bit (index 5 in the output) is never floating
    assert stuffed_floating == [True, False, True, False, True, False, True, False]


def test_get_signals_and_bit_period():
    can = CanBus("can0", bitrate=500_000)
    assert can.bit_period_samples is None
    signals = can.get_signals()
    assert len(signals) == 1
    assert signals[0].name == "can0.can"
    assert signals[0].initial_level == 1


def test_send_standard_frame_structure():
    can = CanBus("can0", bitrate=500_000)
    builder = CaptureBuilder(samplerate=8_000_000)  # 16 samples/bit
    can.register_signals(builder)
    can.send(builder, identifier=0x123, data=[0xDE, 0xAD], rtr=False)
    capture = builder.finish()
    line = "can0.can"

    assert can.bit_period_samples == 16
    # SOF: dominant from sample 0 (idle was recessive/1)
    assert capture.edges[line][:2] == ((0, 1), (0, 0))

    drivers = [a for a in capture.annotations if a.track == "driver"]
    slave_spans = [a for a in drivers if a.label == "slave"]
    assert len(slave_spans) == 1
    assert slave_spans[0].end - slave_spans[0].start == 16  # exactly one bit period (the ACK slot)
    assert all(a.label == "master" for a in drivers if a is not slave_spans[0])

    id_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ID=0x123")]
    assert len(id_fields) == 1
    assert id_fields[0].data == {"identifier": 0x123, "dlc": 2, "rtr": False, "extended": False}

    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    assert [f.label for f in byte_fields] == ["0xDE", "0xAD"]  # neither byte is printable ASCII
    assert [f.data["value"] for f in byte_fields] == [0xDE, 0xAD]

    assert any(a.track == "bitorder" and a.label == "msb" for a in capture.annotations)


def test_send_with_floating_data_byte_annotates_floating_and_resolves_concrete_value():
    can = CanBus("can0", bitrate=500_000)
    builder = CaptureBuilder(samplerate=8_000_000)
    can.register_signals(builder)
    # "lh" -> high nibble floating-low (0x0), low nibble floating-high (0xF) -> 0x0F
    can.send(builder, identifier=0x123, data="lh", datatype="hex")
    capture = builder.finish()

    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    assert [f.data["value"] for f in byte_fields] == [0x0F]

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1  # coalesced into one span (CRC-15 is deterministic over the resolved bits)


def test_send_rtr_frame_has_no_data_bytes():
    can = CanBus("can0", bitrate=500_000)
    builder = CaptureBuilder(samplerate=8_000_000)
    can.register_signals(builder)
    can.send(builder, identifier=0x42, rtr=True)
    capture = builder.finish()

    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    assert byte_fields == []
    id_field = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ID=0x")][0]
    assert id_field.label == "ID=0x042 RTR"
    assert id_field.data["rtr"] is True


def test_extended_frame_uses_29_bit_identifier():
    can = CanBus("can0", bitrate=500_000, extended=True)
    builder = CaptureBuilder(samplerate=8_000_000)
    can.register_signals(builder)
    can.send(builder, identifier=0x1FFFFFFF, data=[0x01])
    capture = builder.finish()

    id_field = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ID=0x")][0]
    assert id_field.data["identifier"] == 0x1FFFFFFF
    assert id_field.data["extended"] is True


def test_data_field_out_of_range_rejected():
    import pytest

    can = CanBus("can0", bitrate=500_000)
    builder = CaptureBuilder(samplerate=8_000_000)
    can.register_signals(builder)
    with pytest.raises(ValueError):
        can.send(builder, identifier=0, data=[0] * 9)
