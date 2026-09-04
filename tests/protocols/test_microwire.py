from protowavegen.model import CaptureBuilder
from protowavegen.protocols.microwire import MicrowireBus


def _setup():
    mw = MicrowireBus("mw0", clock_hz=1_000_000)
    builder = CaptureBuilder(samplerate=10_000_000)  # shc = 5
    mw.register_signals(builder)
    return mw, builder


def test_get_signals_cs_active_high():
    mw, builder = _setup()
    assert builder.level_of("mw0.cs") == 0  # idle low, active-high


def test_transfer_asserts_cs_and_clocks_bits():
    mw, builder = _setup()
    fh = mw.transfer(builder, mosi_bits=[1, 0, 1], read_bits=[1, 1])
    capture = builder.finish()

    cs_edges = capture.edges["mw0.cs"]
    # a 5-sample (half-clock) CS recovery gap precedes every transfer
    assert cs_edges == ((0, 0), (5, 1), (fh.end, 0))

    clk_edges = capture.edges["mw0.clk"]
    # initial(0) + 5 bits * (fall+rise), minus the very first fall which is
    # a no-op (clk already idles at 0), plus one final explicit fall back
    # to idle-low before CS drops (real hardware always deasserts CS with
    # SK already low; also required for sigrok's microwire decoder to flush
    # the last clocked bit — see transfer()'s comment)
    assert len(clk_edges) == 1 + 2 * 5 - 1 + 1


def test_di_do_carry_correct_bits():
    mw, builder = _setup()
    mw.transfer(builder, mosi_bits=[1, 0], read_bits=[0, 1])
    capture = builder.finish()
    di_edges = capture.edges["mw0.di"]
    do_edges = capture.edges["mw0.do"]
    # each bit occupies a full 2*shc=10-sample period. mosi bit0=1 matches
    # idle (no edge); bit1=0 changes at its period's start (t=10); the read
    # phase then fixes di=1 throughout (t=20, since bit1 left di at 0).
    # everything shifted +5 by the mandatory CS recovery gap
    assert di_edges == ((0, 1), (15, 0), (25, 1))
    # do stays fixed at 1 through the whole mosi phase (no edge, matches
    # idle); do now changes on the *rising* edge (not simultaneously with
    # di on the falling edge) — see _clock_bit's comment — so the read
    # phase's first bit (0) takes effect one half-clock later than di would.
    assert do_edges == ((0, 1), (30, 0), (40, 1))
