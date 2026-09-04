from __future__ import annotations

from pathlib import Path

import svgwrite

from ..model import Annotation, Capture
from .base import OutputWriter, register_output

_TRACK_COLORS = {
    "field": "#dd6b20",
    "bitorder": "#6b46c1",
    "error": "#c53030",
}
_DEFAULT_TRACK_COLOR = "#4a5568"
_SIGNAL_COLOR = "#1a202c"
_DEFAULT_UNIT_BAR_COLORS = ("#edf2f7", "#e2e8f0")
# Assigned to distinct labels in sorted order wherever a color (not text)
# has to carry the meaning: driver-colored waveform segments, and any
# annotation whose text would overflow into its neighbor. 16 entries (not
# the original 8) — a capture combining several protocols' own driver
# vocabularies (master/slave/pullup/reader/device/host/floating/...) can
# easily exceed 8 distinct labels; past this many, colors still repeat
# (`i % len(_LABEL_PALETTE)`) rather than crash, but 16 covers every
# realistic single capture without a repeat.
_LABEL_PALETTE = (
    "#2b6cb0", "#38a169", "#d69e2e", "#c53030", "#805ad5", "#dd6b20", "#319795", "#b83280",
    "#1a365d", "#22543d", "#742a2a", "#44337a", "#744210", "#97266d", "#2c5282", "#276749",
)
_LANE_FONT_SIZE = 9
_CHAR_WIDTH_FACTOR = 0.62  # rough monospace glyph-width-to-font-size ratio


def _estimate_text_width(text: str, font_size: float) -> float:
    return len(text) * font_size * _CHAR_WIDTH_FACTOR


@register_output("svg")
class SVGWriter(OutputWriter):
    """Renders a `Capture` as a step-waveform SVG.

    Layout, top to bottom: an optional one-line note for metadata that's
    constant across the whole capture (e.g. `bitorder`, which almost never
    changes mid-capture — no point giving it its own repeated lane), the
    signal lanes themselves (with a `unit`-track background band per protocol
    framing unit, and driver-colored line segments wherever a `driver`
    annotation applies to that signal instead of a flat color), one lane per
    remaining annotation `track`, and a legend for whatever colors ended up
    carrying meaning (driver spans, or any label whose text didn't fit its
    slot and fell back to a color-only block).
    """

    def write(
        self,
        capture: Capture,
        path: Path,
        *,
        target_width: float = 1400,
        lane_height: float = 40,
        annotation_lane_height: float = 24,
        label_width: float = 110,
        margin: float = 10,
        right_margin_fraction: float = 0.02,
        unit_bar_colors: tuple[str, ...] = _DEFAULT_UNIT_BAR_COLORS,
        verbose: bool = False,
    ) -> None:
        duration = max(capture.duration_samples, 1)
        right_margin = target_width * right_margin_fraction
        plot_width = max(target_width - label_width - right_margin, 1)
        pixels_per_sample = plot_width / duration

        def x(sample: int) -> float:
            return label_width + sample * pixels_per_sample

        all_tracks = sorted({a.track for a in capture.annotations})
        unit_bands = sorted(
            (a.start, a.end if a.end is not None else duration)
            for a in capture.annotations
            if a.track == "unit"
        )
        driver_present = any(a.track == "driver" for a in capture.annotations)
        lane_candidates = [t for t in all_tracks if t not in ("unit", "driver")]

        static_notes = []
        rendered_tracks = []
        for track in lane_candidates:
            track_annotations = [a for a in capture.annotations if a.track == track]
            constant_label = self._constant_value(track_annotations, duration)
            if constant_label is not None:
                static_notes.append(f"{track}: {constant_label}")
            else:
                rendered_tracks.append(track)

        legend_labels = set()
        if driver_present:
            legend_labels.update(a.label for a in capture.annotations if a.track == "driver")
        for track in rendered_tracks:
            for a in capture.annotations:
                if a.track != track:
                    continue
                end = a.end if a.end is not None else duration
                rect_w = x(end) - x(a.start)
                _, legend_label = self._display_for_annotation(a, rect_w, verbose)
                if legend_label is not None:
                    legend_labels.add(legend_label)
        legend_colors = {
            label: _LABEL_PALETTE[i % len(_LABEL_PALETTE)] for i, label in enumerate(sorted(legend_labels))
        }

        n_signals = len(capture.signals)
        static_note_height = 16 if static_notes else 0
        legend_height = 20 if legend_colors else 0
        total_height = (
            margin * 2
            + static_note_height
            + n_signals * lane_height
            + len(rendered_tracks) * annotation_lane_height
            + legend_height
        )

        dwg = svgwrite.Drawing(str(path), size=(target_width, total_height))
        dwg.add(dwg.rect(insert=(0, 0), size=(target_width, total_height), fill="white"))

        top = margin
        if static_notes:
            dwg.add(
                dwg.text(
                    "  |  ".join(static_notes), insert=(margin, top + 11),
                    font_size="10px", font_family="monospace", fill=_DEFAULT_TRACK_COLOR,
                )
            )
            top += static_note_height
        signals_top = top

        for i, (start, end) in enumerate(unit_bands):
            color = unit_bar_colors[i % len(unit_bar_colors)]
            band_width = max(x(end) - x(start), 0.5)
            dwg.add(
                dwg.rect(
                    insert=(x(start), signals_top), size=(band_width, n_signals * lane_height), fill=color
                )
            )

        for i, signal in enumerate(capture.signals):
            lane_top = signals_top + i * lane_height
            y_low = lane_top + lane_height * 0.8
            y_high = lane_top + lane_height * 0.2
            dwg.add(
                dwg.text(
                    signal.name, insert=(margin, lane_top + lane_height * 0.55),
                    font_size="12px", font_family="monospace",
                )
            )
            edges = capture.edges.get(signal.name) or ((0, signal.initial_level),)
            spans = self._driver_spans(capture, signal.name, duration)
            self._draw_waveform(dwg, edges, spans, legend_colors, duration, x, y_high, y_low)

        for t, track in enumerate(rendered_tracks):
            lane_top = signals_top + n_signals * lane_height + t * annotation_lane_height
            track_color = _TRACK_COLORS.get(track, _DEFAULT_TRACK_COLOR)
            dwg.add(
                dwg.text(
                    track, insert=(margin, lane_top + annotation_lane_height * 0.65),
                    font_size="10px", font_family="monospace", fill=track_color,
                )
            )
            for a in capture.annotations:
                if a.track != track:
                    continue
                end = a.end if a.end is not None else duration
                start_x, end_x = x(a.start), x(end)
                width = max(end_x - start_x, 1)
                text, legend_label = self._display_for_annotation(a, width, verbose)
                overflowed = legend_label is not None
                rect_color = legend_colors[legend_label] if overflowed else track_color
                dwg.add(
                    dwg.rect(
                        insert=(start_x, lane_top + 2),
                        size=(width, annotation_lane_height - 4),
                        fill=rect_color,
                        fill_opacity=0.6 if overflowed else 0.25,
                        stroke=rect_color,
                        stroke_width=0.5,
                    )
                )
                if text is not None:
                    dwg.add(
                        dwg.text(
                            text, insert=(start_x + 2, lane_top + annotation_lane_height * 0.7),
                            font_size=f"{_LANE_FONT_SIZE}px", font_family="monospace", fill=track_color,
                        )
                    )

        if legend_colors:
            legend_top = (
                signals_top + n_signals * lane_height + len(rendered_tracks) * annotation_lane_height + 4
            )
            lx = margin
            for label, color in legend_colors.items():
                dwg.add(dwg.rect(insert=(lx, legend_top), size=(10, 10), fill=color))
                dwg.add(
                    dwg.text(
                        label, insert=(lx + 14, legend_top + 9), font_size="10px", font_family="monospace"
                    )
                )
                lx += 14 + len(label) * 6 + 16

        dwg.save()

    @staticmethod
    def _constant_value(annotations: list[Annotation], duration: int) -> str | None:
        """If every annotation on a track shares one label and together they
        cover the whole capture with no gaps, return that label — the track
        is constant and doesn't need a repeated per-sample lane."""

        if not annotations:
            return None
        labels = {a.label for a in annotations}
        if len(labels) != 1:
            return None
        spans = sorted((a.start, a.end if a.end is not None else duration) for a in annotations)
        covered = 0
        for start, end in spans:
            if start > covered:
                return None
            covered = max(covered, end)
        if covered < duration:
            return None
        return next(iter(labels))

    @staticmethod
    def _display_for_annotation(
        annotation: Annotation, rect_width: float, verbose: bool
    ) -> tuple[str | None, str | None]:
        """Pick what to draw for one annotation: `(text, None)` if some
        candidate text fits, or `(None, label)` — draw a color-only block and
        report `label` for the shared legend — if even the plain label
        doesn't fit. In verbose mode, a protocol-supplied `summary` is tried
        first and the plain label is still the fallback, so overflow never
        loses information beyond "read the legend"."""

        candidates = []
        if verbose:
            summary = annotation.data.get("summary")
            if summary:
                candidates.append(summary)
        candidates.append(annotation.label)
        for text in candidates:
            if _estimate_text_width(text, _LANE_FONT_SIZE) <= rect_width:
                return text, None
        return None, annotation.label

    @staticmethod
    def _driver_spans(
        capture: Capture, signal_name: str, duration: int
    ) -> list[tuple[int, int, str]]:
        spans = [
            (a.start, a.end if a.end is not None else duration, a.label)
            for a in capture.annotations
            if a.track == "driver" and a.signals is not None and signal_name in a.signals
        ]
        spans.sort()
        return spans

    def _draw_waveform(self, dwg, edges, spans, legend_colors, duration, x, y_high, y_low) -> None:
        if not spans:
            points = self._waveform_points(edges, duration, x, y_high, y_low)
            dwg.add(dwg.polyline(points, stroke=_SIGNAL_COLOR, fill="none", stroke_width=1.5))
            return

        def level_at(sample: int) -> int:
            level = edges[0][1]
            for s, lvl in edges:
                if s <= sample:
                    level = lvl
                else:
                    break
            return level

        def color_at(sample: int) -> str:
            for start, end, label in spans:
                if start <= sample < end:
                    return legend_colors.get(label, _SIGNAL_COLOR)
            return _SIGNAL_COLOR

        boundaries = sorted({0, duration} | {s for s, _ in edges} | {b for s, e, _ in spans for b in (s, e)})
        boundaries = [b for b in boundaries if 0 <= b <= duration]

        run_color = None
        run_points: list[tuple[float, float]] = []
        prev_level = level_at(boundaries[0])
        for t0, t1 in zip(boundaries[:-1], boundaries[1:]):
            level = level_at(t0)
            color = color_at(t0)
            y = y_high if level else y_low
            if color != run_color:
                if run_points:
                    run_points.append((x(t0), y_high if prev_level else y_low))
                    dwg.add(dwg.polyline(run_points, stroke=run_color, fill="none", stroke_width=1.5))
                run_color = color
                run_points = [(x(t0), y_high if prev_level else y_low)]
            run_points.append((x(t0), y))
            run_points.append((x(t1), y))
            prev_level = level
        if run_points:
            dwg.add(dwg.polyline(run_points, stroke=run_color, fill="none", stroke_width=1.5))

    @staticmethod
    def _waveform_points(edges, duration, x, y_high, y_low):
        points = []
        prev_sample, prev_level = edges[0]
        points.append((x(prev_sample), y_high if prev_level else y_low))
        for sample, level in edges[1:]:
            points.append((x(sample), y_high if prev_level else y_low))
            points.append((x(sample), y_high if level else y_low))
            prev_sample, prev_level = sample, level
        points.append((x(duration), y_high if prev_level else y_low))
        return points
