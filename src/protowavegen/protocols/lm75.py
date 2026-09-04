from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .i2c import I2CBus

_TEMP_REG = 0x00
_CONFIG_REG = 0x01
_THYST_REG = 0x02
_TOS_REG = 0x03


@register_protocol("lm75")
class Lm75(StackedProtocol):
    """National LM75 (and register-compatible clones) temperature sensor,
    stacked on `I2CBus` (`transport`). 7-bit address 0x48-0x4F (3
    address-strap pins).

    Pointer-register idiom: a write's first byte selects the active
    register; a bare read reuses whichever register was last pointed at.
    This class tracks that pointer itself (`self._pointer`) so a repeated
    `read_temperature()` poll skips a redundant pointer-set write, matching
    how a real driver would — its first call still needs the full
    `write_then_read()` (pointer write + repeated START + read).

    Only Temp/Config/T_hyst/T_os are modeled from the real register map;
    shutdown/one-shot config bits and the OS/INT alert pin aren't
    behaviorally simulated — they're just bytes on the wire here, and the
    alert pin isn't part of the I2C waveform at all.
    """

    def __init__(
        self, node_id: str, transport: I2CBus, *, address: int = 0x48, operations: list[dict] | None = None
    ):
        super().__init__(node_id, transport, operations)
        self.address = address
        self._pointer: int | None = None

    @staticmethod
    def _encode_temp(celsius: float) -> tuple[int, int]:
        raw = (round(celsius / 0.5) * 128) & 0xFFFF  # 9-bit, 0.5C/LSB, left-justified in 16 bits
        return (raw >> 8) & 0xFF, raw & 0xFF

    def read_temperature(self, builder: CaptureBuilder, *, celsius: float) -> FrameHandle:
        hi, lo = self._encode_temp(celsius)
        label = f"TEMP={celsius:+.1f}C"
        if self._pointer == _TEMP_REG:
            return self.transport.read(builder, address=self.address, data=[hi, lo], labels=[label, label])
        fh = self.transport.write_then_read(
            builder, address=self.address, write_data=[_TEMP_REG], read_data=[hi, lo],
            write_labels=["PTR=TEMP"], read_labels=[label, label],
        )
        self._pointer = _TEMP_REG
        return fh

    def write_config(self, builder: CaptureBuilder, *, byte: int) -> FrameHandle:
        fh = self.transport.write(
            builder, address=self.address, data=[_CONFIG_REG, byte], labels=["PTR=CONFIG", f"CONFIG=0x{byte:02X}"]
        )
        self._pointer = _CONFIG_REG
        return fh

    def write_threshold(self, builder: CaptureBuilder, *, register: str, celsius: float) -> FrameHandle:
        reg = {"hyst": _THYST_REG, "os": _TOS_REG}[register]
        hi, lo = self._encode_temp(celsius)
        fh = self.transport.write(
            builder, address=self.address, data=[reg, hi, lo],
            labels=[f"PTR={register.upper()}", f"{register.upper()}={celsius:+.1f}C", f"{register.upper()}={celsius:+.1f}C"],
        )
        self._pointer = reg
        return fh
