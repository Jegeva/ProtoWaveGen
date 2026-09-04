from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import decode_payload, format_byte, register_protocol
from .checksums import crc16_modbus
from .onewire_rom import OneWireDevice

_WRITE_SCRATCHPAD = 0x0F
_READ_SCRATCHPAD = 0xAA
_COPY_SCRATCHPAD = 0x55
_READ_MEMORY = 0xF0


@register_protocol("ds243x")
class Ds243x(OneWireDevice):
    """1-Wire EEPROM (e.g. DS2433), stacked on `OneWireBus`.

    `write_memory()` does the real 3-transaction sequence real drivers use:
    Write Scratchpad (2-byte target address + data), Read Scratchpad
    (TA1/TA2/ending-offset + data + CRC16, to verify) — using the same
    CRC16 as Modbus's (`checksums.crc16_modbus`) as a stand-in for the real
    part's checksum, since it's the same style of check, not because the
    real part uses the Modbus polynomial specifically — then Copy
    Scratchpad (TA1/TA2/ES authorization bytes) to commit. `read_memory()`
    uses the simpler, direct `0xF0` Read Memory command instead (no
    scratchpad step, just address + data straight back).
    """

    def write_memory(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        data = decode_payload(data, datatype)
        addr_lo, addr_hi = address & 0xFF, (address >> 8) & 0xFF
        ending_offset = (len(data) - 1) & 0x1F

        self._address_rom(builder)
        self.transport.write(
            builder, data=[_WRITE_SCRATCHPAD, addr_lo, addr_hi, *data],
            labels=["CMD=WRITE_SP", f"TA1=0x{addr_lo:02X}", f"TA2=0x{addr_hi:02X}"]
            + [format_byte(b) for b in data],
        )

        self._address_rom(builder)
        crc = crc16_modbus([_READ_SCRATCHPAD, addr_lo, addr_hi, ending_offset, *data])
        self.transport.write(builder, data=[_READ_SCRATCHPAD], labels=["CMD=READ_SP"])
        self.transport.read(
            builder, data=[addr_lo, addr_hi, ending_offset, *data, crc & 0xFF, (crc >> 8) & 0xFF],
            labels=(
                [f"TA1=0x{addr_lo:02X}", f"TA2=0x{addr_hi:02X}", f"E/S=0x{ending_offset:02X}"]
                + [format_byte(b) for b in data]
                + [f"CRC=0x{crc:04X}", f"CRC=0x{crc:04X}"]
            ),
        )

        self._address_rom(builder)
        return self.transport.write(
            builder, data=[_COPY_SCRATCHPAD, addr_lo, addr_hi, ending_offset],
            labels=["CMD=COPY_SP", f"TA1=0x{addr_lo:02X}", f"TA2=0x{addr_hi:02X}", f"E/S=0x{ending_offset:02X}"],
        )

    def read_memory(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """`data` is the synthesized memory contents at `address` onward."""

        data = decode_payload(data, datatype)
        addr_lo, addr_hi = address & 0xFF, (address >> 8) & 0xFF
        self._address_rom(builder)
        self.transport.write(
            builder, data=[_READ_MEMORY, addr_lo, addr_hi],
            labels=["CMD=READ_MEM", f"ADDR_LO=0x{addr_lo:02X}", f"ADDR_HI=0x{addr_hi:02X}"],
        )
        return self.transport.read(builder, data=data, labels=[format_byte(b) for b in data])
