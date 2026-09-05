import pytest

from protowavegen.model import CaptureBuilder
from protowavegen.protocols.em4100 import Em4100


def _setup():
    tag = Em4100("tag0")
    builder = CaptureBuilder(samplerate=1_000_000)
    tag.register_signals(builder)
    return tag, builder


def test_get_signals():
    tag, _ = _setup()
    names = {s.name for s in tag.get_signals()}
    assert names == {"tag0.data"}


def test_row_bits_even_parity():
    assert Em4100._row_bits(0b0001) == [0, 0, 0, 1, 1]
    assert Em4100._row_bits(0b1010) == [1, 0, 1, 0, 0]
    assert Em4100._row_bits(0b0000) == [0, 0, 0, 0, 0]
    assert Em4100._row_bits(0b1111) == [1, 1, 1, 1, 0]


def test_version_out_of_range_rejected():
    tag, builder = _setup()
    with pytest.raises(ValueError):
        tag.transmit(builder, version=256, unique_id=0)


def test_unique_id_out_of_range_rejected():
    tag, builder = _setup()
    with pytest.raises(ValueError):
        tag.transmit(builder, version=0, unique_id=1 << 32)


def test_transmit_starts_with_header_edges():
    tag, builder = _setup()
    fh = tag.transmit(builder, version=0x12, unique_id=0x3456789A)
    capture = builder.finish()
    edges = capture.edges["tag0.data"]
    # idle high, then bit=1's low-then-high Manchester pattern repeats for
    # the 9-bit header
    assert edges[0] == (0, 1)
    assert edges[1][1] == 0
    assert fh.start == 0 and fh.end == capture.duration_samples


def test_field_annotation_shows_version_and_id():
    tag, builder = _setup()
    tag.transmit(builder, version=0x12, unique_id=0x3456789A)
    fields = [a for a in builder.finish().annotations if a.track == "field"]
    assert fields[0].label == "VER=0x12 ID=0x3456789A"


def test_driver_annotated_as_tag():
    tag, builder = _setup()
    tag.transmit(builder, version=0x12, unique_id=0x3456789A)
    drivers = {a.label for a in builder.finish().annotations if a.track == "driver"}
    assert drivers == {"tag"}
