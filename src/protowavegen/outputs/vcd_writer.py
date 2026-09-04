from __future__ import annotations

import itertools
from pathlib import Path

from ..model import Capture
from .base import OutputWriter, register_output

_ID_CHARS = [chr(c) for c in range(33, 127)]  # printable ASCII, '!'..'~'


def _make_ids(n: int) -> list[str]:
    ids: list[str] = []
    length = 1
    while len(ids) < n:
        for combo in itertools.product(_ID_CHARS, repeat=length):
            ids.append("".join(combo))
            if len(ids) == n:
                break
        length += 1
    return ids


@register_output("vcd")
class VCDWriter(OutputWriter):
    """Writes a standard VCD value-change dump. `include_annotations=True`
    adds GTKWave-style `$var string` pseudo-signals (one per annotation
    `track`) so ranged annotations survive as value changes for viewers that
    support the extension; off by default since it's a non-standard
    extension to the VCD format, not something every viewer understands.
    """

    def write(self, capture: Capture, path: Path, *, include_annotations: bool = False) -> None:
        duration = max(capture.duration_samples, 1)
        signal_names = capture.signal_names()
        tracks = sorted({a.track for a in capture.annotations}) if include_annotations else []

        ids = _make_ids(len(signal_names) + len(tracks))
        sig_ids = dict(zip(signal_names, ids))
        track_ids = dict(zip(tracks, ids[len(signal_names):]))

        lines = [
            "$version protowavegen 0.1.0 $end",
            f"$timescale {max(round(1e12 / capture.samplerate), 1)} ps $end",
            "$scope module top $end",
        ]
        for name in signal_names:
            lines.append(f"$var wire 1 {sig_ids[name]} {name} $end")
        for track in tracks:
            lines.append(f"$var string 1 {track_ids[track]} anno_{track} $end")
        lines += ["$upscope $end", "$enddefinitions $end"]

        # (ident, value, kind) keyed by sample time. `kind` is tracked
        # explicitly rather than inferred from `value` because an
        # annotation's label could itself be the string "0" or "1".
        changes: dict[int, list[tuple[str, str, str]]] = {}

        def add(t: int, ident: str, value: str, kind: str) -> None:
            changes.setdefault(t, []).append((ident, value, kind))

        for name in signal_names:
            for sample, level in capture.edges.get(name, ()):
                add(sample, sig_ids[name], str(level), "bit")

        if include_annotations:
            for annotation in capture.annotations:
                ident = track_ids[annotation.track]
                end = annotation.end if annotation.end is not None else duration
                add(annotation.start, ident, annotation.label, "str")
                add(end, ident, "", "str")

        def token(ident: str, value: str, kind: str) -> str:
            return f"{value}{ident}" if kind == "bit" else f"s{value} {ident}"

        def clear_first(change: tuple[str, str, str]) -> int:
            # Two same-track annotations can be adjacent (one's `end` ==
            # another's `start`) and land in `changes[t]` in whatever order
            # `capture.annotations` happens to list them, not chronological
            # order. VCD readers apply last-write-wins per ident within a
            # timestamp, so an end-clear ("") landing after the next span's
            # start-value would silently blank a label that should be
            # showing. Sorting clears first (stable, so bit-edge order and
            # same-kind order are unaffected) guarantees a real value always
            # wins over a stale clear at the same timestamp.
            _, value, kind = change
            return 0 if kind == "str" and value == "" else 1

        # VCD has no "before time 0" — every ident's value AT time 0 is
        # whatever its last change at sample 0 resolves to (a signal may get
        # several edges at sample 0, e.g. its registered idle level
        # immediately overridden by a bit that starts with no pre-delay).
        initial: dict[str, tuple[str, str]] = {sig_ids[name]: ("0", "bit") for name in signal_names}
        initial.update({track_ids[t]: ("", "str") for t in tracks})
        for ident, value, kind in sorted(changes.get(0, []), key=clear_first):
            initial[ident] = (value, kind)

        lines.append("#0")
        lines.append("$dumpvars")
        for ident, (value, kind) in initial.items():
            lines.append(token(ident, value, kind))
        lines.append("$end")

        for t in sorted(changes):
            if t == 0:
                continue
            lines.append(f"#{t}")
            for ident, value, kind in sorted(changes[t], key=clear_first):
                lines.append(token(ident, value, kind))

        path.write_text("\n".join(lines) + "\n")
