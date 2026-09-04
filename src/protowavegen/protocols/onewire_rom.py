from __future__ import annotations

from ..model import CaptureBuilder
from .base import StackedProtocol
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


class OneWireDevice(StackedProtocol):
    """Shared `__init__` for every device class stacked on `OneWireBus`
    (DS2408, DS243x, DS28EA00, ...): just the `rom_id` used for
    Skip-ROM/Match-ROM addressing, on top of `StackedProtocol`'s own
    `transport`/`operations`. `_address_rom()` is the per-call convenience
    wrapping `address_rom()` above with `self.transport`/`self.rom_id`
    already filled in — every device's own methods start with a call to it."""

    def __init__(
        self, node_id: str, transport: OneWireBus, *, rom_id: list[int] | None = None,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        self.rom_id = rom_id

    def _address_rom(self, builder: CaptureBuilder) -> None:
        address_rom(self.transport, builder, self.rom_id)
