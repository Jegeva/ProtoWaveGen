import xml.etree.ElementTree as ET

from protowavegen.model import CaptureBuilder, Signal
from protowavegen.outputs.svg_writer import SVGWriter

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


def test_more_than_eight_distinct_driver_labels_get_distinct_colors(tmp_path):
    # a capture combining several protocols' own driver vocabularies can
    # easily exceed 8 distinct labels — the palette must not repeat colors
    # for any realistic single capture (16 entries, see svg_writer.py).
    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("sda", initial_level=1))
    labels = [f"driver{i}" for i in range(12)]
    t = 0
    for label in labels:
        b.set_level("sda", t % 2)
        b.advance(10)
        b.annotate("driver", label, start=t, end=t + 10, signals=("sda",))
        t += 10
    capture = b.finish()

    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path)

    legend_swatches = [r for r in _rects(path) if r.get("width") == "10" and r.get("height") == "10"]
    colors = [r.get("fill") for r in legend_swatches]
    assert len(colors) == len(labels)
    assert len(set(colors)) == len(labels)  # every label gets its own distinct color


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


_DENSE_BYTE_COUNT = 40


def _dense_capture():
    """40 short annotated bytes packed tightly enough that even a fairly
    narrow single-row render would draw each one far under any legible
    width -- forces multi-row layout."""

    b = CaptureBuilder(samplerate=1_000_000)
    b.register_signal(Signal("a"))
    for i in range(_DENSE_BYTE_COUNT):
        b.advance(20)
        b.annotate("field", f"0x{i:02X}", start=i * 20, end=i * 20 + 20)
    return b.finish()


def test_already_legible_capture_stays_single_row(tmp_path):
    """A short, sparse capture must render byte-for-byte the same way as
    before multi-row support existed -- no row header, one lane block."""

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
    assert not any(t and t.startswith("chunk") for t in texts)


def test_dense_capture_splits_into_multiple_rows(tmp_path):
    capture = _dense_capture()
    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path, target_width=300)
    texts = _texts(path)

    chunk_headers = [t for t in texts if t and t.startswith("chunk")]
    assert len(chunk_headers) > 1
    # every byte's label still appears exactly once across the whole image
    for i in range(_DENSE_BYTE_COUNT):
        assert texts.count(f"0x{i:02X}") == 1


def test_multirow_annotation_widths_meet_the_legibility_floor(tmp_path):
    capture = _dense_capture()
    path = tmp_path / "out.svg"
    SVGWriter().write(capture, path, target_width=300, min_feature_px=6.0)

    field_rects = [
        r for r in _rects(path)
        if r.get("height") == str(24 - 4)  # annotation_lane_height - 4, the field-lane rect height
    ]
    assert field_rects
    for r in field_rects:
        assert float(r.get("width")) >= 6.0 - 1e-6


def test_multirow_never_produces_an_unallocated_legend_color(tmp_path):
    """Regression test: a row boundary landing inside a long annotation
    must classify text-fit/overflow using the annotation's full width
    (matching the global legend pass), not the row-clipped width -- a
    mismatch there raised a raw KeyError on the legend dict, found while
    tuning this feature against real usb_dfu/wiegand example captures."""

    b = CaptureBuilder(samplerate=1000)
    b.register_signal(Signal("a"))
    b.advance(1000)
    long_label = "this-label-is-long-enough-to-overflow-a-narrow-slot"
    # Starts at sample 1 (not 0) so `_constant_value` doesn't collapse this
    # into a static note (it requires full [0, duration) coverage) -- a
    # real lane is what actually reproduced the original bug, mirroring
    # Wiegand's own single frame-spanning "FC=.. CARD=.." annotation.
    b.annotate("field", long_label, start=1, end=1000)
    capture = b.finish()

    path = tmp_path / "out.svg"
    # Force multi-row despite there being only one annotation to cut
    # through, by demanding a very fine feature resolution.
    SVGWriter().write(capture, path, target_width=300, min_feature_px=50.0)
    texts = _texts(path)
    assert texts.count(long_label) == 1  # drawn once, in its starting row
