from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

from ..model import Capture
from .base import OutputWriter, register_output


def _rasterize(edges: tuple[tuple[int, int], ...], duration: int) -> np.ndarray:
    """Expand an edge (transition) list into one level per sample."""

    arr = np.empty(duration, dtype=np.uint8)
    bounds = [start for start, _ in edges] + [duration]
    for (start, level), end in zip(edges, bounds[1:]):
        arr[start:end] = level
    return arr


def _pack_samples(capture: Capture) -> tuple[bytes, int]:
    duration = max(capture.duration_samples, 1)
    n_channels = len(capture.signals)
    unitsize = max((n_channels + 7) // 8, 1)
    packed = np.zeros((duration, unitsize), dtype=np.uint8)
    for idx, signal in enumerate(capture.signals):
        edges = capture.edges_for(signal.name)
        levels = _rasterize(edges, duration)
        byte_idx, bit_idx = divmod(idx, 8)
        packed[:, byte_idx] |= levels << bit_idx
    return packed.tobytes(), unitsize


@register_output("sigrok")
class SigrokWriter(OutputWriter):
    """Writes a real sigrok `.sr` session file: a zip containing `version`,
    a `metadata` INI, and bit-packed `logic-1-N` binary chunks — opens
    directly in PulseView/sigrok-cli as if a logic analyzer captured it.

    Documented limitation: sigrok's native session format has no slot for
    annotations, so they're dropped here. The SVG writer is the path that
    preserves them.
    """

    CHUNK_SIZE = 4 * 1024 * 1024

    def write(self, capture: Capture, path: Path, **options) -> None:
        raw, unitsize = _pack_samples(capture)
        n_channels = len(capture.signals)

        metadata_lines = [
            "[global]",
            "sigrok version = 0.1.0",
            "",
            "[device 1]",
            "capturefile = logic-1",
            f"unitsize = {unitsize}",
            f"total probes = {n_channels}",
            f"samplerate = {capture.samplerate} Hz",
        ]
        for i, signal in enumerate(capture.signals, start=1):
            metadata_lines.append(f"probe{i} = {signal.name}")
        metadata = "\n".join(metadata_lines) + "\n"

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("version", "2")
            zf.writestr("metadata", metadata)
            chunk_idx = 1
            for offset in range(0, max(len(raw), 1), self.CHUNK_SIZE):
                zf.writestr(f"logic-1-{chunk_idx}", raw[offset : offset + self.CHUNK_SIZE])
                chunk_idx += 1
            if not raw:
                zf.writestr("logic-1-1", b"")
