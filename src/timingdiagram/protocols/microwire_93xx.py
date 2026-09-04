from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .microwire import MicrowireBus


@register_protocol("microwire_93xx")
class Microwire93xxEeprom(StackedProtocol):
    """93xx-series Microwire EEPROM (e.g. 93C46/93C56), stacked on
    `MicrowireBus`. `addr_bits` (6/8/9, varies by device and its ORG-pin
    state) is fixed at construction, not auto-detected; x16 (16-bit word)
    organization is assumed.

    Every command is start bit(1) + 2-bit opcode + address bits, MSB-first.
    `read()`: opcode `10`, then 16 data bits clocked back. `write()`:
    opcode `01`, then 16 data bits clocked in, followed by a busy/ready
    delay — modeled as a fixed `advance()`, not a real polling loop
    (matching `Ds28ea00`'s conversion-delay simplification) — and
    auto-issues `EWEN` (erase/write enable, opcode `00` with address bits
    `11` + don't-cares) first if it hasn't been already, mirroring how a
    real driver sequences it. `EWDS` (write disable, opcode `00` with
    address bits `00` + don't-cares) is also exposed directly.
    """

    def __init__(
        self, node_id: str, transport: MicrowireBus, *, addr_bits: int = 6, busy_delay_us: float = 5000,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        self.addr_bits = addr_bits
        self.busy_delay_us = busy_delay_us
        self._write_enabled = False

    def _address_bits(self, address: int) -> list[int]:
        limit = 1 << self.addr_bits
        if not (0 <= address < limit):
            raise ValueError(f"address {address} does not fit in {self.addr_bits} bits")
        return [(address >> i) & 1 for i in reversed(range(self.addr_bits))]

    def ewen(self, builder: CaptureBuilder) -> FrameHandle:
        bits = [1, 0, 0, 1, 1, *([0] * (self.addr_bits - 2))]
        fh = self.transport.transfer(builder, mosi_bits=bits, labels=["EWEN"])
        self._write_enabled = True
        return fh

    def ewds(self, builder: CaptureBuilder) -> FrameHandle:
        bits = [1, 0, 0, 0, 0, *([0] * (self.addr_bits - 2))]
        fh = self.transport.transfer(builder, mosi_bits=bits, labels=["EWDS"])
        self._write_enabled = False
        return fh

    def read(self, builder: CaptureBuilder, *, address: int, value: int) -> FrameHandle:
        """`value` is the synthesized 16-bit word "stored" at `address`."""

        bits = [1, 1, 0, *self._address_bits(address)]
        read_bits = [(value >> i) & 1 for i in reversed(range(16))]
        return self.transport.transfer(
            builder, mosi_bits=bits, read_bits=read_bits, labels=[f"READ[{address}]=0x{value:04X}"]
        )

    def write(self, builder: CaptureBuilder, *, address: int, value: int) -> FrameHandle:
        if not self._write_enabled:
            self.ewen(builder)
        bits = [1, 0, 1, *self._address_bits(address), *[(value >> i) & 1 for i in reversed(range(16))]]
        fh = self.transport.transfer(builder, mosi_bits=bits, labels=[f"WRITE[{address}]=0x{value:04X}"])
        delay_samples = max(round(builder.samplerate * self.busy_delay_us / 1_000_000), 1)
        builder.advance(delay_samples)
        return fh
