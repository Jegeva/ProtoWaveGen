from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .checksums import crc8_1wire
from .onewire import OneWireBus
from .onewire_rom import address_rom

_CONVERT_T = 0x44
_READ_SCRATCHPAD = 0xBE
_WRITE_SCRATCHPAD = 0x4E
_DEFAULT_CONVERSION_DELAY_US = 750_000  # 750ms, typical max for 12-bit resolution


@register_protocol("ds28ea00")
class Ds28ea00(StackedProtocol):
    """DS18B20-family digital thermometer with an extra 2-channel PIO and
    sequence-detect (chaining) support, stacked on `OneWireBus`. Only the
    temperature path is modeled — sequence-detect/PIO access isn't.

    `read_temperature()`: `0x44` Convert T, then a conversion-delay hold
    (external-power mode assumed — the line just idles high, real
    parasite-power operation would need the master holding DQ low instead,
    not modeled), then `0xBE` Read Scratchpad (9 bytes: temp lo/hi, TH, TL,
    config, 3 reserved 0xFF bytes, CRC8). Temperature is the DS18B20 12-bit
    fixed-point format (0.0625C/LSB, 16-bit two's complement).
    """

    def __init__(
        self, node_id: str, transport: OneWireBus, *, rom_id: list[int] | None = None,
        conversion_delay_us: int = _DEFAULT_CONVERSION_DELAY_US, operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        self.rom_id = rom_id
        self.conversion_delay_us = conversion_delay_us

    @staticmethod
    def _encode_temp(celsius: float) -> tuple[int, int]:
        raw = round(celsius / 0.0625) & 0xFFFF
        return raw & 0xFF, (raw >> 8) & 0xFF

    def read_temperature(
        self, builder: CaptureBuilder, *, celsius: float, th: int = 0, tl: int = 0, config: int = 0x7F
    ) -> FrameHandle:
        dq = self.transport.sig("dq")
        address_rom(self.transport, builder, self.rom_id)
        self.transport.write(builder, data=[_CONVERT_T], labels=["CMD=CONVERT_T"])

        delay_samples = max(round(builder.samplerate * self.conversion_delay_us / 1_000_000), 1)
        with builder.frame() as delay_fh:
            builder.advance(delay_samples)  # external-power mode: DQ just idles high
        builder.annotate("field", "CONVERTING", start=delay_fh.start, end=delay_fh.end, signals=(dq,))

        address_rom(self.transport, builder, self.rom_id)
        lo, hi = self._encode_temp(celsius)
        scratchpad = [lo, hi, th & 0xFF, tl & 0xFF, config & 0xFF, 0xFF, 0xFF, 0xFF]
        crc = crc8_1wire([_READ_SCRATCHPAD, *scratchpad])
        temp_label = f"TEMP={celsius:+.4f}C"

        self.transport.write(builder, data=[_READ_SCRATCHPAD], labels=["CMD=READ_SP"])
        return self.transport.read(
            builder, data=[*scratchpad, crc],
            labels=[
                temp_label, temp_label, f"TH=0x{th & 0xFF:02X}", f"TL=0x{tl & 0xFF:02X}",
                f"CONFIG=0x{config & 0xFF:02X}", "RSVD", "RSVD", "RSVD", f"CRC=0x{crc:02X}",
            ],
        )

    def write_scratchpad(self, builder: CaptureBuilder, *, th: int, tl: int, config: int) -> FrameHandle:
        address_rom(self.transport, builder, self.rom_id)
        return self.transport.write(
            builder, data=[_WRITE_SCRATCHPAD, th & 0xFF, tl & 0xFF, config & 0xFF],
            labels=["CMD=WRITE_SP", f"TH=0x{th & 0xFF:02X}", f"TL=0x{tl & 0xFF:02X}", f"CONFIG=0x{config & 0xFF:02X}"],
        )
