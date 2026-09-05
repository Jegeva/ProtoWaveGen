import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.checksums import crc16_x25
from protowavegen.protocols.irda import IrdaBus


def _setup(samplerate=10_000_000, baudrate=115200):
    bus = IrdaBus("ir0", baudrate=baudrate)
    builder = CaptureBuilder(samplerate=samplerate)
    bus.register_signals(builder)
    return bus, builder


def test_get_signals():
    bus, _ = _setup()
    signals = bus.get_signals()
    assert [s.name for s in signals] == ["ir0.ir"]
    assert signals[0].initial_level == 1  # idle/space is logic 1, active-low envelope


def test_bind_samplerate_computes_bit_and_pulse_width():
    bus, builder = _setup(samplerate=10_000_000, baudrate=115200)
    bus.bind_samplerate(builder.samplerate)
    assert bus.bit_period_samples == round(10_000_000 / 115200)
    assert bus._pulse_samples == max(round(bus.bit_period_samples * 3 / 16), 1)
    assert bus._pulse_samples < bus.bit_period_samples


def test_bind_samplerate_rejects_samplerate_too_low_for_a_distinct_pulse():
    bus = IrdaBus("ir0", baudrate=115200)
    with pytest.raises(ValueError):
        # 1 sample/bit: even the minimum 1-sample pulse consumes the whole cell.
        bus.bind_samplerate(115200)


def test_address_out_of_range_rejected():
    bus, builder = _setup()
    with pytest.raises(ValueError):
        bus.send_frame(builder, address=0x80, control=0x00)


def test_control_with_pf_bit_set_directly_rejected():
    bus, builder = _setup()
    with pytest.raises(ValueError):
        bus.send_frame(builder, address=0x01, control=0x10)


def test_send_frame_starts_with_a_pulse_after_the_inter_frame_gap():
    bus, builder = _setup()
    fh = bus.send_frame(builder, address=0x01, control=0x00)
    capture = builder.finish()
    edges = capture.edges["ir0.ir"]
    assert edges[0] == (0, 1)
    spb = bus.bit_period_samples
    assert edges[1][0] == 16 * spb  # _INTER_FRAME_GAP_BITS
    assert edges[1][1] == 0  # start bit pulse
    assert fh.start == 16 * spb and fh.end == capture.duration_samples


def test_send_frame_encodes_address_and_control_bytes():
    """Reconstruct the transmitted bytes by scanning for a pulse (falling
    edge) in each bit cell — an independent, from-scratch decode of the
    generator's own output, not just re-deriving the same arithmetic."""

    bus, builder = _setup()
    bus.send_frame(builder, address=0x01, control=0x00, command=True, final=True)
    capture = builder.finish()
    edges = capture.edges["ir0.ir"]
    spb = bus.bit_period_samples

    falls = [t for t, level in edges if level == 0]
    byte_start = falls[0]

    def has_pulse(a, b):
        return any(a <= t < b and level == 0 for t, level in edges)

    def read_byte(start):
        bits = [1 if not has_pulse(start + i * spb, start + (i + 1) * spb) else 0 for i in range(1, 9)]
        stop = 1 if not has_pulse(start + 9 * spb, start + 10 * spb) else 0
        value = sum(b << k for k, b in enumerate(bits))
        return value, stop

    addr_byte, addr_stop = read_byte(byte_start)
    control_byte, ctrl_stop = read_byte(byte_start + 10 * spb)
    assert addr_stop == 1 and ctrl_stop == 1
    assert addr_byte == (0x01 << 1) | 1  # C/R=1 (command)
    assert control_byte == 0x10  # only the P/F bit (final=True)


def test_send_i_frame_builds_control_byte_from_ns_nr():
    bus, builder = _setup()
    bus.send_i_frame(builder, address=0x01, ns=5, nr=6, info=[0xAB], final=False)
    capture = builder.finish()
    fields = [a.label for a in capture.annotations if a.track == "field"]
    assert fields[1] == "CTRL 0x%02X" % ((5 << 1) | (6 << 5))  # no P/F bit: final=False


def test_send_i_frame_ns_nr_out_of_range_rejected():
    bus, builder = _setup()
    with pytest.raises(ValueError):
        bus.send_i_frame(builder, address=0x01, ns=8, nr=0, info=[0])
    with pytest.raises(ValueError):
        bus.send_i_frame(builder, address=0x01, ns=0, nr=8, info=[0])


def test_send_frame_fcs_is_crc16_x25_over_address_control_info():
    bus, builder = _setup()
    bus.send_frame(builder, address=0x01, control=0x00, info=[0x41, 0x42])
    capture = builder.finish()
    fcs_lo = [a for a in capture.annotations if a.track == "field" and a.label.startswith("FCS-LO")][0]
    fcs_hi = [a for a in capture.annotations if a.track == "field" and a.label.startswith("FCS-HI")][0]
    expected = crc16_x25([(0x01 << 1) | 1, 0x10, 0x41, 0x42])
    assert fcs_lo.data["value"] == expected & 0xFF
    assert fcs_hi.data["value"] == (expected >> 8) & 0xFF


def test_send_xid_info_field_layout():
    bus, builder = _setup()
    bus.send_xid(builder, source_address=0x00000001, dest_address=0xFFFFFFFF, slot=0xFF, version=0x00)
    capture = builder.finish()
    fields = [a.label for a in capture.annotations if a.track == "field"]
    # ADDR, CTRL, then 12 info bytes (format+4+4+flags+slot+version), then FCS-LO/HI.
    info_labels = [f for f in fields if f.startswith("INFO")]
    assert len(info_labels) == 12
    assert fields[0] == "ADDR 0x%02X" % ((0x7F << 1) | 1)  # broadcast address, command
    assert info_labels[0] == "INFO[0] 0x01"  # XID format identifier


def test_send_xid_field_out_of_range_rejected():
    bus, builder = _setup()
    with pytest.raises(ValueError):
        bus.send_xid(builder, source_address=1 << 32)
    with pytest.raises(ValueError):
        bus.send_xid(builder, source_address=0, slot=256)


def test_driver_annotated_as_sender_by_default():
    bus, builder = _setup()
    bus.send_frame(builder, address=0x01, control=0x00)
    drivers = {a.label for a in builder.finish().annotations if a.track == "driver"}
    assert drivers == {"sender"}


def test_driver_label_overridable():
    bus, builder = _setup()
    bus.send_frame(builder, address=0x01, control=0x00, driver="station-a")
    drivers = {a.label for a in builder.finish().annotations if a.track == "driver"}
    assert drivers == {"station-a"}


def test_bitorder_annotated_lsb():
    bus, builder = _setup()
    fh = bus.send_frame(builder, address=0x01, control=0x00)
    bitorder = [a for a in builder.finish().annotations if a.track == "bitorder"][0]
    assert bitorder.label == "lsb"
    assert bitorder.start == fh.start and bitorder.end == fh.end


def test_floating_marker_on_info_payload():
    bus, builder = _setup()
    bus.send_frame(builder, address=0x01, control=0x00, info="hl", datatype="hex")
    capture = builder.finish()
    info_field = [a for a in capture.annotations if a.track == "field" and a.label.startswith("INFO")][0]
    assert info_field.data["value"] == 0xF0  # "hl" -> nibble h(resolves 0xF), l(resolves 0x0)
    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert floating  # at least one floating-labeled driver span was recorded
