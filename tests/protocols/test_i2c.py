from protowavegen.model import CaptureBuilder
from protowavegen.protocols.i2c import I2CBus


def _generate(i2c, samplerate):
    builder = CaptureBuilder(samplerate=samplerate)
    i2c.register_signals(builder)
    return builder, i2c


def test_start_condition_exact_edges():
    # clock_hz/samplerate chosen so samples_per_half_bit is an exact 2.
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    builder, i2c = _generate(i2c, samplerate=400_000)
    i2c._ensure_bound(builder)
    from protowavegen.protocols.base import DriverTracker

    i2c._scl_driver = DriverTracker(builder, "i2c0.scl")
    i2c._sda_driver = DriverTracker(builder, "i2c0.sda")
    i2c._start_condition(builder)
    i2c._scl_driver.close()
    i2c._sda_driver.close()
    capture = builder.finish()

    # both idle high, SDA falls while SCL still high (START) at t=0, then SCL
    # is taken low one half-bit-period later (t=shb=2) to begin the clock
    assert capture.edges["i2c0.sda"] == ((0, 1), (0, 0))
    assert capture.edges["i2c0.scl"] == ((0, 1), (2, 0))


def test_open_drain_driver_annotations_never_call_a_high_level_driven():
    """Core semantic the user asked for: level 1 on SCL/SDA is always the
    pullup releasing the line, never a device "driving high"."""

    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    i2c.write(builder, address=0x48, data=[0x01, 0x2A])
    capture = builder.finish()

    for line in ("i2c0.scl", "i2c0.sda"):
        edges = dict(capture.edges[line])
        driver_annotations = [a for a in capture.annotations if a.track == "driver" and line in a.signals]
        for a in driver_annotations:
            level_at_start = _level_at(capture.edges[line], a.start)
            if a.label == "pullup":
                assert level_at_start == 1, f"{line} tagged pullup but level is {level_at_start}"
            else:
                assert a.label in ("master", "slave")
                assert level_at_start == 0, f"{line} driven by {a.label} but level is {level_at_start}"


def test_write_7bit_address_and_rw_bit():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    i2c.write(builder, address=0x48, data=[0x01])
    capture = builder.finish()

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR")]
    assert len(address_fields) == 1
    assert address_fields[0].label == "ADDR=0x48 W"  # read/write bit shown directly, not just the raw value
    # 0x48 << 1 | 0 (write) = 0x90
    assert address_fields[0].data["value"] == 0x90
    assert address_fields[0].data["ack"] is True  # no nack requested


def test_unit_annotations_bracket_each_byte_and_bit_period_exposed():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    assert i2c.bit_period_samples is None
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    i2c.write(builder, address=0x48, data=[0x01, 0x2A])
    capture = builder.finish()

    assert i2c.bit_period_samples == 40  # 2 * samples_per_half_bit (4_000_000 / (2*100_000) = 20)
    units = [a for a in capture.annotations if a.track == "unit"]
    fields = [a for a in capture.annotations if a.track == "field" and "ack" in a.data]
    assert len(units) == len(fields) == 3  # address + 2 data bytes
    assert [u.label for u in units] == ["address", "data", "data"]  # stable unit-bar category
    for u, f in zip(units, fields):
        assert u.start == f.start and u.end == f.end

    # field labels always show the full value directly, not gated behind verbose mode
    assert fields[0].label == "ADDR=0x48 W"
    assert fields[1].label == "0x01"
    assert fields[2].label == "0x2A '*'"


def test_write_then_read_is_one_frame_with_a_single_repeated_start():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    i2c.write_then_read(builder, address=0x48, write_data=[0x00], read_data=[0x17, 0x80])
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # initial START + the one repeated START

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR")]
    assert [f.label for f in address_fields] == ["ADDR=0x48 W", "ADDR=0x48 R"]

    data_fields = [a for a in capture.annotations if a.track == "field" and "ack" in a.data and a.label.startswith("0x")]
    assert [f.data["value"] for f in data_fields] == [0x00, 0x17, 0x80]
    assert data_fields[-1].data["ack"] is False  # last read byte nacked by default


def test_10bit_read_uses_repeated_start():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=10)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    i2c.read(builder, address=0x123, data=[0xAA])
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2  # initial START + repeated START for direction switch

    address_fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("ADDR")]
    assert len(address_fields) == 3  # write-header, addr-low, read-header
    read_header = address_fields[-1].data["value"]
    assert read_header & 1 == 1  # R/W bit set
    assert address_fields[-1].label.endswith(" R")  # direction visible directly, not just the raw bit
    assert address_fields[0].label.endswith(" W")


def _level_at(edges, sample):
    level = edges[0][1]
    for s, lvl in edges:
        if s <= sample:
            level = lvl
        else:
            break
    return level
