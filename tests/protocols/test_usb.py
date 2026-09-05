import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols._usb_nrzi import nrzi_encode, stuff_bits
from protowavegen.protocols.usb import UsbBus, _crc5, _crc16, _pid_byte


def _level_at(edges, sample):
    """The level in effect at `sample`, from a `Capture.edges_for(...)`-
    shaped tuple of (sample, level) pairs."""

    level = edges[0][1]
    for s, lvl in edges:
        if s <= sample:
            level = lvl
        else:
            break
    return level


# -- _usb_nrzi.py -----------------------------------------------------------


def test_stuff_bits_inserts_zero_after_six_consecutive_ones():
    bits = [1, 1, 1, 1, 1, 1, 0, 1]
    roles = ["x"] * len(bits)
    stuffed, stuffed_roles, stuffed_floating = stuff_bits(bits, roles, [False] * len(bits))
    assert stuffed == [1, 1, 1, 1, 1, 1, 0, 0, 1]
    assert stuffed_roles == ["x"] * 6 + ["stuff", "x", "x"]
    assert stuffed_floating == [False] * len(stuffed)


def test_stuff_bits_noop_when_no_run_reaches_six():
    bits = [1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1]
    stuffed, _, _ = stuff_bits(bits, ["x"] * len(bits), [False] * len(bits))
    assert stuffed == bits


def test_stuff_bits_does_not_stuff_runs_of_zero():
    bits = [0, 0, 0, 0, 0, 0, 0, 1]  # SYNC field's own logical bits
    stuffed, _, _ = stuff_bits(bits, ["sync"] * len(bits), [False] * len(bits))
    assert stuffed == bits  # a run of six/seven ZEROS is never stuffed


def test_stuff_bits_handles_a_run_created_across_the_stuff_boundary():
    # 6 ones -> stuffed 0 -> immediately followed by 6 more ones must stuff again.
    bits = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    stuffed, _, _ = stuff_bits(bits, ["x"] * len(bits), [False] * len(bits))
    assert stuffed == [1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0]


def test_stuff_bits_carries_floating_flag_and_marks_stuff_bit_not_floating():
    bits = [1, 1, 1, 1, 1, 1, 0]
    floating = [True, False, True, False, True, True, False]
    _, _, stuffed_floating = stuff_bits(bits, ["x"] * len(bits), floating)
    assert stuffed_floating == [True, False, True, False, True, True, False, False]


def test_nrzi_encode_sync_byte_produces_kjkjkjkk():
    # SYNC field logical bits (0x80, LSB first): 0,0,0,0,0,0,0,1.
    bits = [0, 0, 0, 0, 0, 0, 0, 1]
    states = nrzi_encode(bits, initial_state=1)  # start from J (idle)
    # 7 toggles (each logical 0) then one hold (the final logical 1):
    # K,J,K,J,K,J,K,K -- the textbook USB SYNC line pattern.
    assert states == [0, 1, 0, 1, 0, 1, 0, 0]


def test_nrzi_encode_all_ones_holds_state():
    states = nrzi_encode([1, 1, 1, 1], initial_state=1)
    assert states == [1, 1, 1, 1]


# -- usb.py private helpers --------------------------------------------------


def test_pid_byte_matches_sigrok_usb_packet_wire_table():
    # SETUP's type nibble (0b1101) with its complement (0b0010) in the high
    # nibble, sent LSB-first, must equal sigrok's own PID table entry
    # '10110100' for SETUP (see usb.py's module docstring).
    assert _pid_byte(0b1101) == 0x2D


def test_crc5_matches_sigrok_calc_crc5_reference_values():
    # Cross-checked directly against a transliteration of sigrok's
    # usb_packet/pd.py calc_crc5() during implementation (see usb.py's
    # module docstring) -- these are the resulting wire-order bit lists
    # for a handful of address/endpoint combinations.
    def bits_lsb_first(value, width):
        return [(value >> i) & 1 for i in range(width)]

    cases = {
        (5, 0): [0, 1, 0, 1, 1],
        (0, 0): [0, 1, 0, 0, 0],
        (127, 15): [0, 0, 0, 1, 0],
        (1, 1): [1, 1, 0, 1, 0],
    }
    for (addr, ep), expected in cases.items():
        field_bits = bits_lsb_first(addr, 7) + bits_lsb_first(ep, 4)
        assert _crc5(field_bits) == expected, (addr, ep)


def test_crc16_of_empty_payload_is_all_ones_complemented_pattern():
    # An empty bit input never enters the CRC loop, so the register is
    # just the seed (all-ones) complemented -- i.e. zero -- transmitted
    # MSB-first (all zero bits either way).
    assert _crc16([]) == [0] * 16


def test_crc5_and_crc16_are_deterministic_and_sensitive_to_input():
    a = _crc5([0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1])
    b = _crc5([1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1])
    assert a == _crc5([0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1])
    assert a != b


# -- UsbBus -------------------------------------------------------------


def test_get_signals_and_bit_period():
    usb = UsbBus("usb0")
    assert usb.bit_period_samples is None
    signals = usb.get_signals()
    assert [s.name for s in signals] == ["usb0.dp", "usb0.dm"]
    # Full-Speed idle is J: dp=1, dm=0 (see module docstring -- NOT the
    # same polarity as Low-Speed).
    assert signals[0].initial_level == 1
    assert signals[1].initial_level == 0


def test_bind_samplerate_rejects_too_low_a_samplerate():
    usb = UsbBus("usb0")
    with pytest.raises(ValueError):
        usb.bind_samplerate(1_000_000)  # well under 12 Mbit/s


def test_sync_field_exact_line_states():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)  # 4 samples/bit
    usb.register_signals(builder)
    usb.handshake(builder, pid="ACK", driver="device")
    capture = builder.finish()

    dp_edges = capture.edges["usb0.dp"]
    dm_edges = capture.edges["usb0.dm"]
    bs = usb.bit_period_samples
    assert bs == 4

    # K,J,K,J,K,J,K,K over the 8 SYNC bit-periods -> dp mirrors the state
    # (1=J,0=K), dm is the complement. Sample at each bit's center.
    expected_dp = [0, 1, 0, 1, 0, 1, 0, 0]
    expected_dm = [1, 0, 1, 0, 1, 0, 1, 1]
    for i in range(8):
        center = i * bs + bs // 2
        assert _level_at(dp_edges, center) == expected_dp[i], i
        assert _level_at(dm_edges, center) == expected_dm[i], i

    # First transition (idle J -> SYNC's first bit, K) happens at sample 0.
    assert dp_edges[:2] == ((0, 1), (0, 0))
    assert dm_edges[:2] == ((0, 0), (0, 1))


def test_eop_exact_timing_after_handshake():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.handshake(builder, pid="ACK", driver="device")
    capture = builder.finish()
    bs = usb.bit_period_samples

    # ACK is SYNC(8) + PID(8) = 16 bits, no CRC, no stuffing (verified by
    # test_stuff_bits_noop_when_no_run_reaches_six-style reasoning: ACK's
    # PID nibble 0b0010 has no run of 6 ones anywhere in SYNC+PID).
    eop_start = 16 * bs
    dp_edges = capture.edges["usb0.dp"]
    dm_edges = capture.edges["usb0.dm"]

    # SE0 for the first 2 bit periods of EOP.
    assert _level_at(dp_edges, eop_start) == 0
    assert _level_at(dm_edges, eop_start) == 0
    assert _level_at(dp_edges, eop_start + 2 * bs - 1) == 0
    assert _level_at(dm_edges, eop_start + 2 * bs - 1) == 0

    # Then J for 1 bit period.
    assert _level_at(dp_edges, eop_start + 2 * bs) == 1
    assert _level_at(dm_edges, eop_start + 2 * bs) == 0

    # And idle (still J) through the mandatory inter-packet gap.
    packet_end = eop_start + 3 * bs
    assert _level_at(dp_edges, packet_end + 2 * bs - 1) == 1
    assert _level_at(dm_edges, packet_end + 2 * bs - 1) == 0


def test_token_field_annotation():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.token(builder, pid="SETUP", address=5, endpoint=0)
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert "SETUP" in labels
    assert "SETUP ADDR=5 EP=0" in labels
    addrep = [f for f in fields if f.label == "SETUP ADDR=5 EP=0"][0]
    assert addrep.data == {"address": 5, "endpoint": 0}

    drivers = [a for a in capture.annotations if a.track == "driver"]
    assert len(drivers) == 1
    assert drivers[0].label == "host"
    assert drivers[0].signals == ("usb0.dp", "usb0.dm")


def test_data_packet_byte_annotations_and_unit_track():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.data_packet(builder, pid="DATA0", data=[0x41, 0x00], driver="host")
    capture = builder.finish()

    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    assert [f.label for f in byte_fields] == ["0x41 'A'", "0x00"]
    assert [f.data["value"] for f in byte_fields] == [0x41, 0x00]

    units = [a for a in capture.annotations if a.track == "unit"]
    assert len(units) == 2

    assert any(a.track == "field" and a.label == "DATA0" for a in capture.annotations)


def test_data_packet_with_floating_bytes_resolves_and_annotates():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    # "lh" -> high nibble floating-low (0x0), low nibble floating-high (0xF) -> 0x0F
    usb.data_packet(builder, pid="DATA1", data="lh", datatype="hex", driver="device")
    capture = builder.finish()

    byte_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("0x")]
    assert [f.data["value"] for f in byte_fields] == [0x0F]

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert floating == []  # USB is push-pull, not open-drain: no "floating" driver label exists here

    driver_spans = [a for a in capture.annotations if a.track == "driver"]
    assert [d.label for d in driver_spans] == ["device"]


def test_handshake_pid_annotation_and_no_crc_no_data():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    fh = usb.handshake(builder, pid="STALL", driver="device")
    capture = builder.finish()

    labels = [a.label for a in capture.annotations if a.track == "field"]
    assert labels == ["STALL"]
    # SYNC(8) + PID(8) + EOP(3) bit periods, no CRC/payload bits at all.
    assert fh.end - fh.start == 19 * usb.bit_period_samples


def test_control_transfer_in_data_stage_full_structure():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.control_transfer(
        builder,
        address=5,
        endpoint=0,
        setup_data=[0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x08, 0x00],
        in_data=[0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x40],
    )
    capture = builder.finish()

    pid_labels = [
        a.label
        for a in capture.annotations
        if a.track == "field" and a.label in ("SETUP", "IN", "OUT", "DATA0", "DATA1", "ACK")
    ]
    # Setup stage (SETUP+DATA0+ACK), Data stage (IN+DATA1+ACK), Status
    # stage (opposite direction, OUT+DATA1+ACK since the Data stage was IN).
    assert pid_labels == [
        "SETUP", "DATA0", "ACK",
        "IN", "DATA1", "ACK",
        "OUT", "DATA1", "ACK",
    ]

    driver_labels = [a.label for a in capture.annotations if a.track == "driver"]
    assert driver_labels == ["host", "host", "device", "host", "device", "host", "host", "host", "device"]

    setup_bytes = [
        f.data["value"]
        for f in capture.annotations
        if f.track == "field" and f.label.startswith("0x")
    ]
    # 8 SETUP bytes + 8 IN-stage bytes + 0 status bytes.
    assert len(setup_bytes) == 16


def test_control_transfer_out_data_stage_status_is_in():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.control_transfer(
        builder,
        address=1,
        endpoint=2,
        setup_data=[0x00, 0x09, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00],
        out_data=[0xAA],
    )
    capture = builder.finish()

    pid_labels = [
        a.label
        for a in capture.annotations
        if a.track == "field" and a.label in ("SETUP", "IN", "OUT", "DATA0", "DATA1", "ACK")
    ]
    assert pid_labels == [
        "SETUP", "DATA0", "ACK",
        "OUT", "DATA1", "ACK",
        "IN", "DATA1", "ACK",
    ]


def test_control_transfer_no_data_stage_status_is_in():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    usb.control_transfer(
        builder,
        address=1,
        endpoint=0,
        setup_data=[0x00, 0x05, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00],  # SET_ADDRESS
    )
    capture = builder.finish()

    pid_labels = [
        a.label
        for a in capture.annotations
        if a.track == "field" and a.label in ("SETUP", "IN", "OUT", "DATA0", "DATA1", "ACK")
    ]
    assert pid_labels == ["SETUP", "DATA0", "ACK", "IN", "DATA1", "ACK"]


# -- error paths --------------------------------------------------------


def test_token_rejects_unknown_pid():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.token(builder, pid="ACK", address=0, endpoint=0)


def test_token_rejects_out_of_range_address():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.token(builder, pid="OUT", address=128, endpoint=0)


def test_token_rejects_out_of_range_endpoint():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.token(builder, pid="OUT", address=0, endpoint=16)


def test_data_packet_rejects_unknown_pid():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.data_packet(builder, pid="ACK", data=[0], driver="host")


def test_data_packet_rejects_oversized_payload():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.data_packet(builder, pid="DATA0", data=[0] * 1025, driver="host")


def test_handshake_rejects_unknown_pid():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.handshake(builder, pid="SETUP", driver="host")


def test_control_transfer_rejects_setup_data_of_wrong_length():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.control_transfer(builder, address=0, endpoint=0, setup_data=[0, 1, 2])


def test_control_transfer_rejects_both_in_and_out_data():
    usb = UsbBus("usb0")
    builder = CaptureBuilder(samplerate=48_000_000)
    usb.register_signals(builder)
    with pytest.raises(ValueError):
        usb.control_transfer(
            builder,
            address=0,
            endpoint=0,
            setup_data=[0] * 8,
            in_data=[1],
            out_data=[2],
        )
