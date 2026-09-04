from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .i2c import I2CBus

_AMBIENT_REG = 0x06
_OBJECT_REGS = {1: 0x07, 2: 0x08}


def _pec8(data: list[int]) -> int:
    """SMBus Packet Error Code: CRC-8-CCITT, polynomial 0x07, MSB-first,
    not reflected — a different CRC-8 variant from 1-Wire's (see
    `checksums.crc8_1wire`), kept local since it's SMBus/PEC-specific."""

    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


@register_protocol("mlx90614")
class Mlx90614(StackedProtocol):
    """Melexis MLX90614 infrared thermometer, stacked on `I2CBus`. Factory
    default 7-bit address `0x5A` (field-reprogrammable via an EEPROM
    register — that reprogramming procedure isn't modeled).

    Every transaction ends with a PEC (Packet Error Code, SMBus CRC-8)
    byte — the one thing that genuinely distinguishes this from the other
    simple I2C sensors here. PEC is computed over the logical transaction
    bytes as they appear on the bus: write-address byte, command/register
    byte, repeated-START read-address byte, then the data bytes.

    Only the RAM temperature registers (0x06 ambient, 0x07/0x08 object 1/2)
    are modeled, read-only — the EEPROM calibration/config registers
    (emissivity, address reprogramming, etc.) aren't.
    """

    def __init__(
        self, node_id: str, transport: I2CBus, *, address: int = 0x5A, operations: list[dict] | None = None
    ):
        super().__init__(node_id, transport, operations)
        self.address = address

    @staticmethod
    def _encode_temp(celsius: float) -> tuple[int, int]:
        raw = round((celsius + 273.15) / 0.02) & 0xFFFF
        return raw & 0xFF, (raw >> 8) & 0xFF  # low byte first, matching SMBus read-word order

    def _read_temp_register(self, builder: CaptureBuilder, *, reg: int, celsius: float, label: str) -> FrameHandle:
        lo, hi = self._encode_temp(celsius)
        pec = _pec8([self.address << 1, reg, (self.address << 1) | 1, lo, hi])
        return self.transport.write_then_read(
            builder, address=self.address, write_data=[reg], read_data=[lo, hi, pec],
            write_labels=[f"PTR=0x{reg:02X}"], read_labels=[label, label, f"PEC=0x{pec:02X}"],
        )

    def read_ambient_temperature(self, builder: CaptureBuilder, *, celsius: float) -> FrameHandle:
        return self._read_temp_register(builder, reg=_AMBIENT_REG, celsius=celsius, label=f"T_a={celsius:+.1f}C")

    def read_object_temperature(self, builder: CaptureBuilder, *, celsius: float, source: int = 1) -> FrameHandle:
        if source not in _OBJECT_REGS:
            raise ValueError(f"source must be 1 or 2, got {source}")
        return self._read_temp_register(
            builder, reg=_OBJECT_REGS[source], celsius=celsius, label=f"T_obj{source}={celsius:+.1f}C"
        )
