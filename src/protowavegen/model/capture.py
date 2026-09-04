from __future__ import annotations

from dataclasses import dataclass, field

from .annotation import Annotation
from .signal import Signal


@dataclass
class FrameHandle:
    """The sample range a transport operation just emitted.

    Returned by transport methods like `I2CBus.write(...)` so a protocol
    stacked on top (e.g. an LM75 driver) can attach its own semantic
    annotation over exactly that range without having to track cursor
    positions itself.
    """

    start: int
    end: int | None = None


class _FrameContext:
    """Context manager backing `CaptureBuilder.frame()` — see there."""

    def __init__(self, builder: "CaptureBuilder"):
        self._builder = builder
        self.handle = FrameHandle(start=builder.cursor)

    def __enter__(self) -> FrameHandle:
        return self.handle

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.handle.end = self._builder.cursor
        return False


@dataclass(frozen=True, slots=True)
class Capture:
    """Frozen, output-ready result of a `CaptureBuilder` run.

    This is the granular transport interface every output writer consumes:
    ordered signal declarations, per-signal edge (transition) lists, and a
    flat annotation list output writers can filter by `track`.
    """

    samplerate: int
    signals: tuple[Signal, ...]
    edges: dict[str, tuple[tuple[int, int], ...]]
    annotations: tuple[Annotation, ...]
    duration_samples: int

    def signal_names(self) -> list[str]:
        return [s.name for s in self.signals]


class CaptureBuilder:
    """Mutable working object protocols write into during `generate()`.

    Time is tracked as a single global sample cursor shared by every signal
    (they're all sampled at the same `samplerate`), so protocols that need to
    change several lines within one bit period just call `set_level` for each
    before `advance`-ing the shared cursor.
    """

    def __init__(self, samplerate: int):
        if samplerate <= 0:
            raise ValueError(f"samplerate must be positive, got {samplerate}")
        self.samplerate = samplerate
        self.cursor = 0
        self._signals: dict[str, Signal] = {}
        self._edges: dict[str, list[tuple[int, int]]] = {}
        self._last_level: dict[str, int] = {}
        self._annotations: list[Annotation] = []

    def register_signal(self, signal: Signal) -> None:
        if signal.name in self._signals:
            raise ValueError(f"signal {signal.name!r} already registered")
        self._signals[signal.name] = signal
        self._edges[signal.name] = [(0, signal.initial_level)]
        self._last_level[signal.name] = signal.initial_level

    def has_signal(self, name: str) -> bool:
        return name in self._signals

    def set_level(self, name: str, level: int, at: int | None = None) -> None:
        if name not in self._signals:
            raise KeyError(f"unknown signal {name!r} — register it first")
        at = self.cursor if at is None else at
        if self._last_level[name] == level:
            return
        self._edges[name].append((at, level))
        self._last_level[name] = level

    def level_of(self, name: str) -> int:
        return self._last_level[name]

    def advance(self, n_samples: int) -> None:
        if n_samples < 0:
            raise ValueError(f"cannot advance by negative samples ({n_samples})")
        self.cursor += n_samples

    def frame(self) -> _FrameContext:
        """Context manager: `with builder.frame() as fh:` yields a
        `FrameHandle` whose `.end` is filled in with the cursor position at
        block exit, ready to hand to an `annotate()` call."""

        return _FrameContext(self)

    def annotate(
        self,
        track: str,
        label: str,
        *,
        start: int | None = None,
        end: int | None = None,
        signals: tuple[str, ...] | list[str] | None = None,
        **data,
    ) -> Annotation:
        annotation = Annotation(
            track=track,
            label=label,
            start=self.cursor if start is None else start,
            end=end,
            signals=tuple(signals) if signals else None,
            data=data,
        )
        self._annotations.append(annotation)
        return annotation

    def clear_annotations(self, track: str, signals: tuple[str, ...] | None = None) -> None:
        """Remove previously recorded annotations on `track` (optionally only
        ones touching `signals`). Used by `unit_bits` overrides to replace a
        protocol's own native unit annotations instead of stacking a second,
        overlapping set on top of them."""

        def matches(a: Annotation) -> bool:
            if a.track != track:
                return False
            if signals is None:
                return True
            return a.signals is not None and any(s in signals for s in a.signals)

        self._annotations = [a for a in self._annotations if not matches(a)]

    def finish(self) -> Capture:
        return Capture(
            samplerate=self.samplerate,
            signals=tuple(self._signals.values()),
            edges={name: tuple(edges) for name, edges in self._edges.items()},
            annotations=tuple(self._annotations),
            duration_samples=self.cursor,
        )


def pad_idle(capture: Capture, fraction: float = 0.02) -> Capture:
    """Add an idle margin before and after the generated activity: `fraction`
    of the original duration (default 2%) on each side, holding each
    signal's own idle level. Every generated capture gets this — it's a
    property of the generated signal stream, not a rendering choice, so it's
    applied once here to the finished `Capture` and every output writer
    (SVG, sigrok, VCD) sees it the same way.

    Applied by `TimingDiagramApplication.run()` after `builder.finish()`, not
    baked into `CaptureBuilder.finish()` itself — protocol-level tests build
    a `CaptureBuilder` directly and assert exact sample positions; padding
    there would shift every one of those expected values for no reason.
    """

    original_duration = capture.duration_samples
    if original_duration <= 0:
        return capture

    pad = max(round(original_duration * fraction), 1)

    padded_edges = {
        name: ((0, edges[0][1]),) + tuple((sample + pad, level) for sample, level in edges)
        for name, edges in capture.edges.items()
    }
    padded_annotations = tuple(
        Annotation(
            track=a.track,
            label=a.label,
            start=a.start + pad,
            end=None if a.end is None else a.end + pad,
            signals=a.signals,
            data=a.data,
        )
        for a in capture.annotations
    )

    return Capture(
        samplerate=capture.samplerate,
        signals=capture.signals,
        edges=padded_edges,
        annotations=padded_annotations,
        duration_samples=original_duration + 2 * pad,
    )
