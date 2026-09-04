from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import register_protocol
from .checksums import crc8_1wire
from .onewire_rom import OneWireDevice

_CHANNEL_ACCESS_WRITE = 0x5A
_READ_PIO_REGISTERS = 0xF0


@register_protocol("ds2408")
class Ds2408(OneWireDevice):
    """1-Wire 8-channel addressable switch, stacked on `OneWireBus`.

    ROM addressing: Skip ROM (`0xCC`, single-device bus) by default, or
    Match ROM (`0x55` + 8-byte ROM ID) when `rom_id` is given. Function
    commands: `0xF0` Read PIO Registers (2-byte target address + register
    bytes + CRC16 in the real part — simplified here to just the PIO logic
    state byte + a 1-Wire CRC8 over what was read, since the full register
    map's CRC16 isn't the interesting part to model) and `0x5A`
    Channel-Access Write (comparator byte + its complement, device replies
    `0xAA` + new state byte). Real per-channel activity-latch semantics and
    the write's success/failure path aren't modeled — writes always
    "succeed".
    """

    def read_pio(self, builder: CaptureBuilder, *, state: int) -> FrameHandle:
        """`state` is the synthesized 8-bit PIO logic-state byte."""

        self._address_rom(builder)
        crc = crc8_1wire([_READ_PIO_REGISTERS, state])
        self.transport.write(builder, data=[_READ_PIO_REGISTERS], labels=["CMD=READ_PIO"])
        return self.transport.read(
            builder, data=[state, crc], labels=[f"PIO=0b{state:08b}", f"CRC=0x{crc:02X}"]
        )

    def write_pio(self, builder: CaptureBuilder, *, bits: int) -> FrameHandle:
        self._address_rom(builder)
        fh = self.transport.write(
            builder, data=[_CHANNEL_ACCESS_WRITE, bits, (~bits) & 0xFF],
            labels=["CMD=WRITE", f"BITS=0b{bits:08b}", "~BITS"],
        )
        self.transport.read(builder, data=[0xAA, bits], labels=["ACK=0xAA", f"STATE=0b{bits:08b}"])
        return fh
