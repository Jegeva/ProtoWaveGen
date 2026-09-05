import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.base import DriverTracker
from protowavegen.protocols.i3c import I3CBus, _odd_parity_bit


def _setup(samplerate=4_000_000, clock_hz=100_000):
    i3c = I3CBus("i3c0", clock_hz=clock_hz)
    builder = CaptureBuilder(samplerate=samplerate)
    i3c.register_signals(builder)
    return i3c, builder


def test_odd_parity_bit():
    # 0x00 has 0 (even) ones -> T-bit must add a 1 to make the total odd.
    assert _odd_parity_bit(0x00) == 1
    # 0x01 has 1 (odd) one already -> T-bit stays 0.
    assert _odd_parity_bit(0x01) == 0
    # 0xFF has 8 (even) ones -> T-bit must be 1.
    assert _odd_parity_bit(0xFF) == 1
    for byte in range(256):
        total_ones = bin(byte).count("1") + _odd_parity_bit(byte)
        assert total_ones % 2 == 1


def test_start_condition_exact_edges():
    # clock_hz/samplerate chosen so samples_per_half_bit is an exact 2,
    # matching I2CBus's own equivalent test's setup.
    i3c, builder = _setup(samplerate=400_000)
    i3c._ensure_bound(builder)
    i3c._scl_driver = DriverTracker(builder, "i3c0.scl")
    i3c._sda_driver = DriverTracker(builder, "i3c0.sda")
    i3c._start_condition(builder)
    i3c._scl_driver.close()
    i3c._sda_driver.close()
    capture = builder.finish()

    # Unlike I2C's START, this one unconditionally brings SCL low and back
    # up before presenting the real START edge (see _start_condition's
    # docstring: the vendored I3C decoder needs an explicit falling SCL
    # edge to close out whatever 9th-bit state it might be in, even for
    # the very first START of a whole capture).
    assert capture.edges["i3c0.scl"] == ((0, 1), (0, 0), (2, 1), (6, 0))
    assert capture.edges["i3c0.sda"] == ((0, 1), (4, 0))


def test_stop_condition_exact_edges_from_high_sda_entry():
    """Entry state SCL=1, SDA=1 (e.g. right after a T-bit of 1) is exactly
    the shape I2C's own `_stop_condition` mishandles (see its docstring) —
    this asserts the fixed, unconditional-SCL-low-first shape instead."""

    i3c, builder = _setup(samplerate=400_000)
    i3c._ensure_bound(builder)
    i3c._scl_driver = DriverTracker(builder, "i3c0.scl")
    i3c._sda_driver = DriverTracker(builder, "i3c0.sda")
    i3c._clock_bit_pushpull(builder, 1, "controller")  # leaves SCL=1, SDA=1
    before = builder.cursor
    i3c._stop_condition(builder)
    i3c._scl_driver.close()
    i3c._sda_driver.close()
    capture = builder.finish()

    scl_after = [e for e in capture.edges["i3c0.scl"] if e[0] >= before]
    sda_after = [e for e in capture.edges["i3c0.sda"] if e[0] >= before]
    assert scl_after == [(before, 0), (before + 4, 1)]
    assert sda_after == [(before + 2, 0), (before + 6, 1)]  # low first, then the real STOP edge


def test_pushpull_bit_actively_drives_high_no_pullup_label():
    """Core semantic distinguishing I3C's native data phase from I2C: a
    push-pull `1` bit is actively driven by its owner, never labeled
    `"pullup"` the way I2C's open-drain bits are."""

    i3c, builder = _setup()
    i3c._ensure_bound(builder)
    i3c._scl_driver = DriverTracker(builder, "i3c0.scl")
    i3c._sda_driver = DriverTracker(builder, "i3c0.sda")
    i3c._clock_bit_pushpull(builder, 1, "controller")
    i3c._clock_bit_pushpull(builder, 0, "controller")
    i3c._scl_driver.close()
    i3c._sda_driver.close()
    capture = builder.finish()

    drivers = [a for a in capture.annotations if a.track == "driver" and "i3c0.sda" in a.signals]
    assert drivers  # at least one annotation
    assert all(a.label == "controller" for a in drivers)  # never "pullup", even while high


def test_open_drain_address_byte_releases_high_via_pullup():
    """Contrast case: the address phase is still open-drain, so a `1` bit
    there IS labeled `"pullup"`, same convention as I2C."""

    i3c, builder = _setup()
    i3c.private_write(builder, address=0x08, data=[0x00])
    capture = builder.finish()

    address_span = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR"))
    drivers_in_address = [
        a for a in capture.annotations
        if a.track == "driver" and "i3c0.sda" in a.signals and a.start < address_span.end
    ]
    assert any(a.label == "pullup" for a in drivers_in_address)


def test_private_write_field_labels_and_tbits():
    i3c, builder = _setup()
    i3c.private_write(builder, address=0x08, data=[0x01, 0x2A])
    capture = builder.finish()

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR")]
    assert len(address_fields) == 1
    assert address_fields[0].label == "ADDR=0x08 W"
    assert address_fields[0].data["value"] == 0x10  # 0x08 << 1 | 0
    assert address_fields[0].data["ack"] is True

    data_fields = [a for a in capture.annotations if a.track == "field" and "tbit" in a.data]
    assert [f.data["value"] for f in data_fields] == [0x01, 0x2A]
    assert data_fields[0].data["tbit"] == _odd_parity_bit(0x01)
    assert data_fields[1].data["tbit"] == _odd_parity_bit(0x2A)
    assert data_fields[0].label == "0x01"
    assert data_fields[1].label == "0x2A '*'"


def test_private_read_data_driven_by_target():
    i3c, builder = _setup()
    i3c.private_read(builder, address=0x08, data=[0x17, 0x80])
    capture = builder.finish()

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR")]
    assert address_fields[0].label == "ADDR=0x08 R"
    assert address_fields[0].data["value"] == 0x11  # 0x08 << 1 | 1

    data_fields = [a for a in capture.annotations if a.track == "field" and "tbit" in a.data]
    assert [f.data["value"] for f in data_fields] == [0x17, 0x80]

    # data bytes are driven by "target" in push-pull, never floated to a pullup
    data_start, last_data_end = data_fields[0].start, data_fields[-1].end
    drivers = [
        a for a in capture.annotations
        if a.track == "driver" and "i3c0.sda" in a.signals
        and a.start < last_data_end and a.end > data_start
    ]
    assert drivers
    assert all(a.label == "target" for a in drivers)


def test_private_write_rejects_broadcast_address():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="not a valid 7-bit target address"):
        i3c.private_write(builder, address=0x7E, data=[0x00])


def test_broadcast_ccc_structure_and_data():
    i3c, builder = _setup()
    i3c.broadcast_ccc(builder, code=0x0C, data=[0x01])
    capture = builder.finish()

    ccc_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("CCC="))
    assert ccc_field.label == "CCC=0x0C"
    assert ccc_field.data["value"] == 0x0C

    address_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("CCC ("))
    assert address_field.data["value"] == 0xFC  # 0x7E << 1 | 0

    data_fields = [
        a for a in capture.annotations
        if a.track == "field" and "tbit" in a.data and a.start > ccc_field.start
    ]
    assert [f.data["value"] for f in data_fields] == [0x01]


def test_broadcast_ccc_rejects_direct_code():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="out of range"):
        i3c.broadcast_ccc(builder, code=0x80)


def test_direct_ccc_read_uses_repeated_start_and_target_driven_data():
    i3c, builder = _setup()
    i3c.direct_ccc(builder, address=0x08, code=0x8F, read=True, data=[0x00] * 6)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # initial START + the repeated START to the specific target

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith(("CCC (", "ADDR"))]
    assert [f.label for f in address_fields] == ["CCC (0x7E W)", "ADDR=0x08 R"]

    data_fields = [a for a in capture.annotations if a.track == "field" and "tbit" in a.data]
    assert len(data_fields) == 7  # CCC code byte + 6 defining bytes
    assert data_fields[0].data["value"] == 0x8F


def test_direct_ccc_rejects_broadcast_code():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="out of range"):
        i3c.direct_ccc(builder, address=0x08, code=0x0C)


def test_direct_ccc_rejects_broadcast_address():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="not a valid 7-bit target address"):
        i3c.direct_ccc(builder, address=0x7E, code=0x8F)


def test_entdaa_structure_pid_bcr_dcr_and_dynamic_address():
    i3c, builder = _setup()
    i3c.entdaa(
        builder,
        targets=[{"pid": 0x123456789ABC, "bcr": 0x10, "dcr": 0x63, "dynamic_address": 0x08}],
    )
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # initial START + the repeated START before the read header

    address_fields = [
        a for a in capture.annotations if a.track == "field" and a.label.startswith("ENTDAA (")
    ]
    assert [f.data["value"] for f in address_fields] == [0xFC, 0xFD]  # 0x7E W, 0x7E R

    ccc_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("CCC="))
    assert ccc_field.data["value"] == 0x07

    id_fields = [a for a in capture.annotations if a.track == "unit" and a.label == "entdaa"]
    assert len(id_fields) == 8  # 6 PID bytes + BCR + DCR, no ACK/T-bit between them

    pid_field_values = [
        a.data["value"] for a in capture.annotations
        if a.track == "field" and a.label.startswith("PID[")
    ]
    assert pid_field_values == [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC]
    bcr_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("BCR="))
    dcr_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("DCR="))
    assert bcr_field.data["value"] == 0x10
    assert dcr_field.data["value"] == 0x63

    assign_field = next(a for a in capture.annotations if a.track == "field" and a.label.startswith("Assign DA"))
    assert assign_field.data["value"] == 0x10  # 0x08 << 1


def test_entdaa_requires_exactly_one_target():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="exactly one"):
        i3c.entdaa(builder, targets=[])
    with pytest.raises(ValueError, match="exactly one"):
        i3c.entdaa(
            builder,
            targets=[
                {"pid": 1, "bcr": 0, "dcr": 0, "dynamic_address": 8},
                {"pid": 2, "bcr": 0, "dcr": 0, "dynamic_address": 9},
            ],
        )


def test_entdaa_validates_pid_and_dynamic_address_ranges():
    i3c, builder = _setup()
    with pytest.raises(ValueError, match="48 bits"):
        i3c.entdaa(builder, targets=[{"pid": 1 << 48, "bcr": 0, "dcr": 0, "dynamic_address": 8}])
    with pytest.raises(ValueError, match="7-bit target address"):
        i3c.entdaa(builder, targets=[{"pid": 1, "bcr": 0, "dcr": 0, "dynamic_address": 0x7E}])


def test_write_with_floating_marker_resolves_and_annotates():
    """Same floating-marker-resolves-to-concrete-bits guarantee established
    for I2C/SPI/CAN/etc. elsewhere in this codebase, now for I3C's
    push-pull data phase — "2h" -> 0x2F, still driven (not "pullup")."""

    i3c, builder = _setup()
    i3c.private_write(builder, address=0x08, data="2h", datatype="hex")
    capture = builder.finish()

    field = next(a for a in capture.annotations if a.track == "field" and "tbit" in a.data)
    assert field.data["value"] == 0x2F

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    assert len(floating) == 1  # one coalesced span for the 4 floating bits


def test_bit_period_samples_exposed():
    i3c, builder = _setup(samplerate=4_000_000, clock_hz=100_000)
    assert i3c.bit_period_samples is None
    i3c.private_write(builder, address=0x08, data=[0x00])
    assert i3c.bit_period_samples == 40  # 2 * (4_000_000 / (2 * 100_000))
