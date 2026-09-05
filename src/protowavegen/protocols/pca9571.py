from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .i2c import I2CBus


@register_protocol("pca9571")
class Pca9571(StackedProtocol):
    """NXP PCA9571 8-bit I2C GPIO expander, stacked on `I2CBus`. Fixed 7-bit
    address `0x25` (no address-strap pins on real hardware — sigrok's own
    `pca9571` decoder hardcodes this same address when checking a capture).

    No register pointer at all: a single-byte `write()` sets all 8 outputs
    directly, a single-byte `read()` reads them straight back. The
    interrupt (INT) pin isn't modeled.
    """

    ADDRESS = 0x25

    def __init__(self, node_id: str, transport: I2CBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    def set_outputs(self, builder: CaptureBuilder, *, mask: int) -> FrameHandle:
        return self.transport.write(
            builder, address=self.ADDRESS, data=[mask], labels=[f"OUT=0b{mask:08b}"]
        )

    def read_outputs(self, builder: CaptureBuilder, *, mask: int) -> FrameHandle:
        """`mask` is the synthesized readback (real chips echo the last
        write) — this tool generates rather than senses pin state."""

        return self.transport.read(
            builder, address=self.ADDRESS, data=[mask], labels=[f"IN=0b{mask:08b}"]
        )
