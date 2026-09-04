import xml.etree.ElementTree as ET

from timingdiagram.model import CaptureBuilder, Signal
from timingdiagram.outputs.svg_writer import SVGWriter

_SVG_NS = "{http://www.w3.org/2000/svg}"


def _texts(path):
    return [t.text for t in ET.parse(path).getroot().findall(f".//{_SVG_NS}text")]


def _rects(path):
    return ET.parse(path).getroot().findall(f".//{_SVG_NS}rect")


def test_right_margin_is_reserved(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(100)
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path, target_width=1000, right_margin_fraction=0.02)

    polyline = ET.parse(path).getroot().find(f".//{_SVG_NS}polyline")
    xs = [float(pt.split(",")[0]) for pt in polyline.get("points").split()]
    assert max(xs) <= 1000 * 0.98 + 1e-6


def test_constant_track_suppressed_into_a_static_note(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(100)
    b.annotate("bitorder", "msb", start=0, end=100)
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path)
    texts = _texts(path)

    assert "bitorder: msb" in texts
    assert "bitorder" not in texts  # no repeated per-sample lane for a constant value


def test_varying_track_still_gets_a_lane(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(50)
    b.annotate("field", "address", start=0, end=50)
    b.advance(50)
    b.annotate("field", "data", start=50, end=100)
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path)
    texts = _texts(path)

    assert "field" in texts
    assert "address" in texts and "data" in texts


def test_driver_spans_color_the_waveform_and_produce_a_legend_not_a_lane(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("sda", initial_level=1))
    b.set_level("sda", 0)
    b.advance(10)
    b.annotate("driver", "master", start=0, end=10, signals=("sda",))
    b.set_level("sda", 1)
    b.advance(10)
    b.annotate("driver", "pullup", start=10, end=20, signals=("sda",))
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path)
    root = ET.parse(path).getroot()

    polylines = root.findall(f".//{_SVG_NS}polyline")
    assert len(polylines) >= 2  # split into per-driver-span colored segments

    texts = [t.text for t in root.findall(f".//{_SVG_NS}text")]
    assert "master" in texts and "pullup" in texts  # legend captions
    assert "driver" not in texts  # no generic annotation lane for the driver track


def test_unit_annotations_render_as_background_bands_not_a_lane(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(50)
    b.annotate("unit", "byte", start=0, end=50)
    b.advance(50)
    b.annotate("unit", "byte", start=50, end=100)
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path, unit_bar_colors=("#111111", "#222222"))

    fills = [r.get("fill") for r in _rects(path)]
    assert "#111111" in fills and "#222222" in fills
    assert "unit" not in _texts(path)


def test_text_overflow_falls_back_to_color_block_with_legend(tmp_path):
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(100)
    label = "this-is-a-very-long-label-that-cannot-possibly-fit"
    b.annotate("field", label, start=0, end=2)  # 2-sample-wide slot: guaranteed too narrow
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path, target_width=200, label_width=50, right_margin_fraction=0.0)

    texts = _texts(path)
    assert texts.count(label) == 1  # only the legend caption, not drawn inline (would overflow)
    opacities = [r.get("fill-opacity") for r in _rects(path)]
    assert "0.6" in opacities  # the color-only fallback block


def test_verbose_prefers_summary_over_label_when_it_fits(tmp_path):
    # two different labels so the track isn't "constant" (that's the
    # suppress-into-a-static-note path, covered by its own test above) and
    # the per-lane rendering path actually runs.
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(50)
    b.annotate("field", "addr", start=0, end=50, summary="ADDR=0x48 W")
    b.advance(50)
    b.annotate("field", "data", start=50, end=100)
    capture = b.finish()

    verbose_path = tmp_path / "verbose.svg"
    SVGWriter().write(capture, verbose_path, verbose=True)
    verbose_texts = _texts(verbose_path)
    assert "ADDR=0x48 W" in verbose_texts
    assert "addr" not in verbose_texts
    assert "data" in verbose_texts  # no summary provided -> falls back to the plain label

    plain_path = tmp_path / "plain.svg"
    SVGWriter().write(capture, plain_path, verbose=False)
    plain_texts = _texts(plain_path)
    assert "addr" in plain_texts
    assert "ADDR=0x48 W" not in plain_texts
