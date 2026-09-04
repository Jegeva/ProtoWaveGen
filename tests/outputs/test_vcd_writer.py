from protowavegen.model import CaptureBuilder, Signal
from protowavegen.outputs.vcd_writer import VCDWriter


def test_edge_exactly_at_sample_zero_is_not_dropped(tmp_path):
    """Regression: an edge landing at sample 0 (e.g. a UART start bit with no
    pre-delay) must show up in $dumpvars, not silently vanish because time 0
    is handled specially."""

    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a", initial_level=1))
    b.set_level("a", 0)  # changes right at cursor 0, before any advance()
    b.advance(5)
    capture = b.finish()
    assert capture.edges["a"] == ((0, 1), (0, 0))  # sanity on the model itself

    path = tmp_path / "out.vcd"
    VCDWriter().write(capture, path)
    text = path.read_text()

    dumpvars_block = text.split("$dumpvars")[1].split("$end")[0]
    assert "0!" in dumpvars_block or "0" in dumpvars_block  # resolved value is 0, not the stale initial 1
    assert "1!" not in dumpvars_block


def test_basic_structure_has_var_and_value_changes(tmp_path):
    b = CaptureBuilder(samplerate=1_000_000)
    b.register_signal(Signal("clk", initial_level=0))
    b.advance(10)
    b.set_level("clk", 1)
    capture = b.finish()

    path = tmp_path / "out.vcd"
    VCDWriter().write(capture, path)
    text = path.read_text()

    assert "$var wire 1" in text
    assert "clk $end" in text
    assert "#10" in text
    assert "1!" in text or "1" in text.split("#10")[1].split("\n")[1]


def test_include_annotations_emits_string_track(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(5)
    b.annotate("field", "hello", start=0, end=5)
    capture = b.finish()

    path = tmp_path / "out.vcd"
    VCDWriter().write(capture, path, include_annotations=True)
    text = path.read_text()
    assert "$var string 1" in text
    assert "shello" in text
