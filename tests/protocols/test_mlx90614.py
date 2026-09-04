import pytest

from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.i2c import I2CBus
from timingdiagram.protocols.mlx90614 import Mlx90614, _pec8


def test_pec8_self_check_is_zero():
    data = [0xB4, 0x07, 0xB5, 0x12, 0x34]
    pec = _pec8(data)
    assert 0 <= pec <= 0xFF
    assert _pec8(data + [pec]) == 0x00


def test_pec8_sensitive_to_input():
    assert _pec8([0x01, 0x02]) != _pec8([0x01, 0x03])


def _setup(address=0x5A):
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    mlx = Mlx90614("mlx0", i2c, address=address)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return mlx, builder


def test_read_object_temperature_includes_correct_pec():
    mlx, builder = _setup()
    mlx.read_object_temperature(builder, celsius=36.5, source=1)
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert "PTR=0x07" in labels
    assert labels.count("T_obj1=+36.5C") == 2

    lo, hi = Mlx90614._encode_temp(36.5)
    expected_pec = _pec8([0x5A << 1, 0x07, (0x5A << 1) | 1, lo, hi])
    assert f"PEC=0x{expected_pec:02X}" in labels


def test_read_ambient_temperature_uses_correct_register():
    mlx, builder = _setup()
    mlx.read_ambient_temperature(builder, celsius=22.0)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert "PTR=0x06" in labels
    assert "T_a=+22.0C" in labels


def test_invalid_source_rejected():
    mlx, builder = _setup()
    with pytest.raises(ValueError):
        mlx.read_object_temperature(builder, celsius=25.0, source=3)
