import zipfile

import numpy as np

from protowavegen.model import CaptureBuilder, Signal
from protowavegen.outputs.sigrok_writer import SigrokWriter


def _tiny_capture():
    b = CaptureBuilder(samplerate=1_000_000)
    b.register_signal(Signal("a", initial_level=0))
    b.register_signal(Signal("b", initial_level=1))
    b.advance(4)
    b.set_level("a", 1)
    b.advance(4)
    b.set_level("b", 0)
    b.advance(4)
    return b.finish()


def test_sr_zip_structure_and_metadata(tmp_path):
    capture = _tiny_capture()
    path = tmp_path / "out.sr"
    SigrokWriter().write(capture, path)

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert names == {"version", "metadata", "logic-1-1"}
        assert zf.read("version") == b"2"

        metadata = zf.read("metadata").decode()
        assert "total probes = 2" in metadata
        assert "samplerate = 1000000 Hz" in metadata
        assert "probe1 = a" in metadata
        assert "probe2 = b" in metadata
        assert "unitsize = 1" in metadata

        raw = zf.read("logic-1-1")

    assert len(raw) == capture.duration_samples  # unitsize=1 for 2 channels
    packed = np.frombuffer(raw, dtype=np.uint8)

    # bit 0 = signal a, bit 1 = signal b
    expected_a = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1]
    expected_b = [1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0]
    for i in range(capture.duration_samples):
        assert (packed[i] & 0x1) == expected_a[i]
        assert ((packed[i] >> 1) & 0x1) == expected_b[i]


def test_chunking_splits_large_captures(tmp_path):
    capture = _tiny_capture()
    path = tmp_path / "out.sr"
    writer = SigrokWriter()
    writer.CHUNK_SIZE = 3  # force multiple chunks for this tiny 12-byte capture
    writer.write(capture, path)

    with zipfile.ZipFile(path) as zf:
        chunk_names = sorted(n for n in zf.namelist() if n.startswith("logic-1-"))
        assert len(chunk_names) > 1
        reassembled = b"".join(zf.read(n) for n in chunk_names)
        assert len(reassembled) == capture.duration_samples
