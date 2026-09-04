from protowavegen.model import CaptureBuilder
from protowavegen.protocols.spi import SpiBus


def test_mode0_msb_first_exact_edges():
    # clock_hz/samplerate chosen so samples_per_half_clock is an exact 5.
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0, bit_order="msb")
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)

    fh = spi.transfer(builder, mosi=[0x80])  # miso defaults to [0]
    capture = builder.finish()

    # a fixed 5-sample (half-clock) CS recovery gap precedes every transfer —
    # see SpiBus.transfer()'s comment — so everything here is offset by 5.
    assert capture.duration_samples == 85
    assert fh.start == 5 and fh.end == 85

    # 0x80 = 0b10000000: MSB=1 matches idle-high default (no edge), then 0
    assert capture.edges["spi0.mosi"] == ((0, 1), (15, 0))
    # miso defaults to 0x00: first bit (0) differs from idle-high immediately
    assert capture.edges["spi0.miso"] == ((0, 1), (5, 0))
    # CS active-low: asserted once the recovery gap elapses, released once the frame ends
    assert capture.edges["spi0.cs"] == ((0, 1), (5, 0), (85, 1))

    expected_sclk = [(0, 0)]
    t = 5
    for _ in range(8):
        t += 5
        expected_sclk.append((t, 1))
        t += 5
        expected_sclk.append((t, 0))
    assert capture.edges["spi0.sclk"] == tuple(expected_sclk)


def test_driver_and_bitorder_annotations():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0, bit_order="lsb")
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    spi.transfer(builder, mosi=[0x01], miso=[0x02])
    capture = builder.finish()

    drivers = {a.signals: a.label for a in capture.annotations if a.track == "driver"}
    assert drivers[("spi0.mosi",)] == "master"
    assert drivers[("spi0.miso",)] == "slave"

    bitorder = [a for a in capture.annotations if a.track == "bitorder"]
    assert len(bitorder) == 1 and bitorder[0].label == "lsb"


def test_transfer_with_floating_marker_annotates_floating_and_resolves_concrete_bits():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0, bit_order="msb")
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    # mosi "2h" -> 0x2F; miso "l3" -> 0x03 (low nibble driven), high nibble floating-low
    spi.transfer(builder, mosi="2h", miso="l3", datatype="hex")
    capture = builder.finish()

    mosi_floating = [
        a for a in capture.annotations
        if a.track == "driver" and a.label == "floating" and a.signals == ("spi0.mosi",)
    ]
    miso_floating = [
        a for a in capture.annotations
        if a.track == "driver" and a.label == "floating" and a.signals == ("spi0.miso",)
    ]
    assert len(mosi_floating) == 1
    assert len(miso_floating) == 1

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.data["mosi"] == 0x2F
    assert field.data["miso"] == 0x03

    # master/slave labels still appear for the driven nibbles
    assert any(a.label == "master" for a in capture.annotations if a.track == "driver")
    assert any(a.label == "slave" for a in capture.annotations if a.track == "driver")


def test_wide_transfer_with_floating_marker_annotates_floating():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=4, mode=0)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    # "hl" -> 0xF0 (high nibble floating-high, low nibble floating-low)
    spi.wide_transfer(builder, data="hl", datatype="hex", direction="write")
    capture = builder.finish()

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.data["value"] == 0xF0

    floating = [a for a in capture.annotations if a.track == "driver" and a.label == "floating"]
    # coalesced into one span per io line (all 8 bits of the single byte are floating)
    assert len(floating) == 4
    assert {a.signals[0] for a in floating} == {"spi0.io0", "spi0.io1", "spi0.io2", "spi0.io3"}


def test_wide_transfer_qspi_four_lines():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=4, mode=0)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    for i in range(4):
        assert builder.has_signal(f"spi0.io{i}")

    fh = spi.wide_transfer(builder, data=[0xAB], direction="write")
    capture = builder.finish()

    # width=4 -> 2 clock edges per byte (nibble per edge) instead of 8
    sclk_edges = capture.edges["spi0.sclk"]
    assert len(sclk_edges) == 1 + 2 * 2  # initial + 2 nibbles * (rise+fall)
    assert fh.start == 5  # 5-sample CS recovery gap precedes every transfer
    assert fh.end == 25  # 5 (gap) + 2 symbols * 2 half-clocks * 5 samples

    drivers = [a for a in capture.annotations if a.track == "driver"]
    assert {a.label for a in drivers} == {"master"}
    assert {a.signals[0] for a in drivers} == {f"spi0.io{i}" for i in range(4)}


def test_transfer_labels_override_default_display():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    spi.transfer(builder, mosi=[0x9F, 0x00], miso=[0x00, 0xEF], labels=["CMD=0x9F", "MFR=0xEF"])
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert [f.label for f in fields] == ["CMD=0x9F", "MFR=0xEF"]


def test_per_byte_unit_and_field_annotations():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0)
    assert spi.bit_period_samples is None
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    spi.transfer(builder, mosi=[0xAA, 0xBB], miso=[0x11, 0x22])
    capture = builder.finish()

    assert spi.bit_period_samples == 10  # 2 * shc (5)
    units = [a for a in capture.annotations if a.track == "unit"]
    fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("MOSI=")]
    assert len(units) == len(fields) == 2
    # value shown directly in the label, always — not gated behind verbose mode
    assert fields[0].label == "MOSI=0xAA MISO=0x11"
    assert fields[1].label == "MOSI=0xBB MISO=0x22 '\"'"  # 0x22 is printable ('"')


def test_wide_transfer_unit_and_field_annotations():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=4, mode=0)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    spi.wide_transfer(builder, data=[0xAB, 0xCD], direction="read")
    capture = builder.finish()

    units = [a for a in capture.annotations if a.track == "unit"]
    fields = [a for a in capture.annotations if a.track == "field" and a.label.startswith("READ=")]
    assert len(units) == len(fields) == 2
    assert fields[0].label == "READ=0xAB"
    assert fields[1].label == "READ=0xCD"


def test_cs_active_high_option():
    spi = SpiBus("spi0", clock_hz=1_000_000, width=1, mode=0, cs_active_low=False)
    builder = CaptureBuilder(samplerate=10_000_000)
    spi.register_signals(builder)
    assert builder.level_of("spi0.cs") == 0  # idle level for active-high CS
    spi.transfer(builder, mosi=[0x00])
    capture = builder.finish()
    assert capture.edges["spi0.cs"][1] == (5, 1)  # asserted (driven high) once the recovery gap elapses
