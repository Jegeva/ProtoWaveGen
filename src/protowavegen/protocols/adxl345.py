from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import register_protocol
from .i2c import I2CBus, I2CDevice

_POWER_CTL_REG = 0x2D
_DATA_FORMAT_REG = 0x31
_DATAX0_REG = 0x32


@register_protocol("adxl345")
class Adxl345(I2CDevice):
    """Analog Devices ADXL345 3-axis accelerometer, I2C mode, stacked on
    `I2CBus`. 7-bit address `0x1D` (or `0x53` with `SDO` grounded).

    Only `POWER_CTL` (measurement enable) and the 6 axis data registers
    (`DATAX0..DATAZ1`, 16-bit little-endian two's-complement per axis, one
    burst read via auto-increment) are modeled — not `DATA_FORMAT`'s
    range/resolution bits, offset/threshold/FIFO registers, or SPI mode.
    """

    def __init__(
        self, node_id: str, transport: I2CBus, *, address: int = 0x1D, operations: list[dict] | None = None
    ):
        super().__init__(node_id, transport, address=address, operations=operations)

    def enable_measurement(self, builder: CaptureBuilder) -> FrameHandle:
        return self.transport.write(
            builder, address=self.address, data=[_POWER_CTL_REG, 0x08],
            labels=["PTR=POWER_CTL", "MEASURE=1"],
        )

    @staticmethod
    def _encode_axis(value: int) -> tuple[int, int]:
        raw = value & 0xFFFF  # 16-bit container; chip itself only uses 10 significant bits
        return raw & 0xFF, (raw >> 8) & 0xFF

    def read_acceleration(self, builder: CaptureBuilder, *, x: int, y: int, z: int) -> FrameHandle:
        x_lo, x_hi = self._encode_axis(x)
        y_lo, y_hi = self._encode_axis(y)
        z_lo, z_hi = self._encode_axis(z)
        label = f"X={x} Y={y} Z={z}"
        return self.transport.write_then_read(
            builder, address=self.address, write_data=[_DATAX0_REG],
            read_data=[x_lo, x_hi, y_lo, y_hi, z_lo, z_hi],
            write_labels=["PTR=DATAX0"], read_labels=[label] * 6,
        )
