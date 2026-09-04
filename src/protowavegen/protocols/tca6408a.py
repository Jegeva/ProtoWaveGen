from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import register_protocol
from .i2c import I2CBus, I2CDevice

_INPUT_REG = 0x00
_OUTPUT_REG = 0x01
_POLARITY_REG = 0x02
_CONFIG_REG = 0x03


@register_protocol("tca6408a")
class Tca6408a(I2CDevice):
    """Texas Instruments TCA6408A 8-bit I2C GPIO expander, stacked on
    `I2CBus`. 7-bit address `0x20-0x27` (3 address-strap pins).

    Registers: 0x00 Input (read-only), 0x01 Output, 0x02 Polarity
    Inversion, 0x03 Configuration (1=input/0=output per bit, resets
    all-input `0xFF`). This class only generates the I2C traffic —
    `get_signals()` returns nothing extra, so the 8 GPIO pins themselves
    aren't rendered as separate signals (only the register byte values on
    the wire are); showing the actual pin levels would need overriding
    `get_signals()` to add `p0..p7` and toggling them to match writes, the
    same "alias extra pins" exception `StackedProtocol`'s docstring already
    anticipates — left as a follow-up, not core to generating correct I2C
    traffic. The interrupt (INT) pin isn't modeled either.
    """

    def __init__(
        self, node_id: str, transport: I2CBus, *, address: int = 0x20, operations: list[dict] | None = None
    ):
        super().__init__(node_id, transport, address=address, operations=operations)

    def configure(self, builder: CaptureBuilder, *, mask: int) -> FrameHandle:
        return self.transport.write(
            builder, address=self.address, data=[_CONFIG_REG, mask],
            labels=["PTR=CONFIG", f"CONFIG=0b{mask:08b}"],
        )

    def set_polarity(self, builder: CaptureBuilder, *, mask: int) -> FrameHandle:
        return self.transport.write(
            builder, address=self.address, data=[_POLARITY_REG, mask],
            labels=["PTR=POLARITY", f"POLARITY=0b{mask:08b}"],
        )

    def set_output(self, builder: CaptureBuilder, *, bits: int) -> FrameHandle:
        return self.transport.write(
            builder, address=self.address, data=[_OUTPUT_REG, bits],
            labels=["PTR=OUTPUT", f"OUTPUT=0b{bits:08b}"],
        )

    def read_inputs(self, builder: CaptureBuilder, *, value: int) -> FrameHandle:
        """`value` is the synthesized input-pin state (0-0xFF) — this tool
        generates rather than senses real pin levels."""

        return self.transport.write_then_read(
            builder, address=self.address, write_data=[_INPUT_REG], read_data=[value],
            write_labels=["PTR=INPUT"], read_labels=[f"INPUT=0b{value:08b}"],
        )
