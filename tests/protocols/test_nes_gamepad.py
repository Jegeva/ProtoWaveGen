from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.nes_gamepad import NesGamepad, _BUTTON_ORDER


def _setup():
    nes = NesGamepad("nes0", latch_us=12, clock_us=6)
    builder = CaptureBuilder(samplerate=1_000_000)
    nes.register_signals(builder)
    return nes, builder


def test_get_signals():
    nes, _ = _setup()
    names = {s.name for s in nes.get_signals()}
    assert names == {"nes0.latch", "nes0.clock", "nes0.data"}


def test_no_buttons_pressed_data_all_high():
    nes, builder = _setup()
    fh = nes.read_buttons(builder, buttons={})
    capture = builder.finish()

    data_edges = capture.edges["nes0.data"]
    assert all(level == 1 for _, level in data_edges)  # nothing pressed -> all bits released (1)
    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.label == "buttons=none"
    assert fh.end == capture.duration_samples


def test_pressed_buttons_pull_data_low_and_are_named():
    nes, builder = _setup()
    nes.read_buttons(builder, buttons={"A": True, "Start": True})
    capture = builder.finish()

    field = [a for a in capture.annotations if a.track == "field"][0]
    assert field.label == "buttons=A,Start"


def test_first_bit_valid_immediately_after_latch_before_any_clock():
    nes, builder = _setup()
    nes.read_buttons(builder, buttons={"A": True})  # A is first in _BUTTON_ORDER
    capture = builder.finish()

    latch_edges = capture.edges["nes0.latch"]
    data_edges = capture.edges["nes0.data"]
    clock_edges = capture.edges["nes0.clock"]
    # data changes to reflect bit0 at the same sample latch falls, before
    # clock's first rising edge
    latch_fall = latch_edges[-1][0]  # latch: rise then fall
    first_clock_rise = min(s for s, level in clock_edges if level == 1)
    assert any(s == latch_fall and level == 0 for s, level in data_edges)  # A pressed -> data goes low
    assert latch_fall < first_clock_rise


def test_button_order_is_standard_nes_order():
    assert _BUTTON_ORDER == ["A", "B", "Select", "Start", "Up", "Down", "Left", "Right"]
