from __future__ import annotations

from ..model import CaptureBuilder
from .onewire import OneWireBus

SKIP_ROM = 0xCC
MATCH_ROM = 0x55


def address_rom(transport: OneWireBus, builder: CaptureBuilder, rom_id: list[int] | None) -> None:
    """Standard 1-Wire ROM-addressing prelude every function command needs:
    Skip ROM (`0xCC`, single-device bus) when `rom_id` is `None`, or Match
    ROM (`0x55` + 8-byte ROM ID) for a multi-drop bus. Shared by every
    1-Wire device class stacked on `OneWireBus` (DS2408, DS243x,
    DS28EA00, ...) rather than duplicated per class.
    """

    if rom_id is None:
        transport.write(builder, data=[SKIP_ROM], labels=["CMD=SKIP_ROM"])
    else:
        transport.write(
            builder, data=[MATCH_ROM, *rom_id],
            labels=["CMD=MATCH_ROM"] + [f"ROM[{i}]=0x{b:02X}" for i, b in enumerate(rom_id)],
        )
