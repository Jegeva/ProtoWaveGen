from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .payload import Payload, decode_payload_with_floating, render_as_bin
from .spi import SpiBus

_WRSR = 0x01
_PP = 0x02
_READ = 0x03
_WRDI = 0x04
_RDSR = 0x05
_WREN = 0x06
_FAST_READ = 0x0B
_SE = 0x20
_CE = 0x60


@register_protocol("spiflash")
class SpiFlash(StackedProtocol):
    """Generic SPI-NOR flash/EEPROM (`xx25` family — matches sigrok's own
    `spiflash` decoder's default target), stacked on `SpiBus` (`transport`,
    must be `width=1` classic SPI). Standard opcode set: `WREN`/`WRDI`
    (write-enable latch), `RDSR`/`WRSR` (status register), `READ`/
    `FAST_READ` (data out), `PP` (page program, data in), `SE`/`CE`
    (sector/chip erase). Not implemented: dual/quad-I/O reads (needs a
    `width>1` transport, out of scope here), security-register and
    deep-power-down commands, and Adesto-style buffered `WRITE1`/`WRITE2`.

    No WIP-bit polling after program/erase — this is a synthesis tool
    generating an intentional bus-transaction shape from a JSON operations
    list, not a hardware timing simulator (`read_status`'s returned value
    is caller-supplied, same as every other synthesized response in this
    codebase). No write-enable-latch state tracking either: sigrok's own
    decoder only *warns* if `WREN` looks missing before a write/erase, it
    doesn't reject the transaction, so JSON operations are free to omit it
    when that warning doesn't matter to the scenario being modeled.

    For manufacturer-ID queries (`RDID`, opcode `0x9F`), stack a separate
    `jedec_cfi` node on the same `SpiBus` instance instead of duplicating
    `JedecCfi.read_jedec_id` here — nothing prevents two different stacked
    protocols sharing one transport.
    """

    def __init__(self, node_id: str, transport: SpiBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        if transport.width != 1:
            raise ValueError("SpiFlash requires a width=1 (classic SPI) transport")

    @staticmethod
    def _addr_bytes(address: int) -> list[int]:
        if not (0 <= address < (1 << 24)):
            raise ValueError(f"address {address} does not fit in 3 bytes")
        return [(address >> 16) & 0xFF, (address >> 8) & 0xFF, address & 0xFF]

    def write_enable(self, builder: CaptureBuilder) -> FrameHandle:
        return self.transport.transfer(builder, mosi=[_WREN], labels=["CMD=WREN"])

    def write_disable(self, builder: CaptureBuilder) -> FrameHandle:
        return self.transport.transfer(builder, mosi=[_WRDI], labels=["CMD=WRDI"])

    def read_status(self, builder: CaptureBuilder, *, value: int) -> FrameHandle:
        """`value` is the synthesized status-register contents — this tool
        generates rather than senses real flash state."""

        return self.transport.transfer(
            builder, mosi=[_RDSR, 0x00], miso=[0x00, value],
            labels=["CMD=RDSR", f"SR1={format_byte(value)}"],
        )

    def write_status(self, builder: CaptureBuilder, *, value: int) -> FrameHandle:
        return self.transport.transfer(
            builder, mosi=[_WRSR, value], labels=["CMD=WRSR", f"SR1={format_byte(value)}"]
        )

    def read(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """Standard `READ` (0x03): opcode + 3-byte address, then the flash
        shifts back `len(data)` bytes. `data` supports floating markers —
        same `render_as_bin`/`decode_payload_with_floating` technique
        `jedec_cfi.read` uses, since it's the MISO payload folded in after
        4 fixed prefix bytes."""

        payload = decode_payload_with_floating(data, datatype, tristate=False)
        data = payload.values
        addr_bytes = self._addr_bytes(address)
        mosi_bytes = [_READ, *addr_bytes, *([0x00] * len(data))]
        labels = [
            "CMD=READ", f"ADDR[23:16]={format_byte(addr_bytes[0])}",
            f"ADDR[15:8]={format_byte(addr_bytes[1])}", f"ADDR[7:0]={format_byte(addr_bytes[2])}",
            *(format_byte(byte) for byte in data),
        ]
        mosi = render_as_bin(Payload(values=mosi_bytes))
        miso = render_as_bin(payload, prefix_bytes=[0x00, 0x00, 0x00, 0x00])
        return self.transport.transfer(builder, mosi=mosi, miso=miso, datatype="bin", labels=labels)

    def fast_read(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """`FAST_READ` (0x0B): like `read`, plus one dummy byte between the
        address and the data phase."""

        payload = decode_payload_with_floating(data, datatype, tristate=False)
        data = payload.values
        addr_bytes = self._addr_bytes(address)
        mosi_bytes = [_FAST_READ, *addr_bytes, 0x00, *([0x00] * len(data))]
        labels = [
            "CMD=FAST_READ", f"ADDR[23:16]={format_byte(addr_bytes[0])}",
            f"ADDR[15:8]={format_byte(addr_bytes[1])}", f"ADDR[7:0]={format_byte(addr_bytes[2])}",
            "DUMMY", *(format_byte(byte) for byte in data),
        ]
        mosi = render_as_bin(Payload(values=mosi_bytes))
        miso = render_as_bin(payload, prefix_bytes=[0x00, 0x00, 0x00, 0x00, 0x00])
        return self.transport.transfer(builder, mosi=mosi, miso=miso, datatype="bin", labels=labels)

    def page_program(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """`PP` (0x02): opcode + 3-byte address, then `len(data)` bytes
        clocked out on MOSI. `data` supports floating markers too — the
        first *write-direction* (MOSI-side) use of `render_as_bin` in this
        codebase, same technique just applied to `mosi` instead of `miso`."""

        payload = decode_payload_with_floating(data, datatype, tristate=False)
        data = payload.values
        addr_bytes = self._addr_bytes(address)
        labels = [
            "CMD=PP", f"ADDR[23:16]={format_byte(addr_bytes[0])}",
            f"ADDR[15:8]={format_byte(addr_bytes[1])}", f"ADDR[7:0]={format_byte(addr_bytes[2])}",
            *(format_byte(byte) for byte in data),
        ]
        mosi = render_as_bin(payload, prefix_bytes=[_PP, *addr_bytes])
        return self.transport.transfer(builder, mosi=mosi, datatype="bin", labels=labels)

    def sector_erase(self, builder: CaptureBuilder, *, address: int) -> FrameHandle:
        """`SE` (0x20): opcode + 3-byte sector address. Real chips require
        a sector-aligned address (4KiB, matching sigrok's own decoder
        check) — enforced here too so a mistyped address fails immediately
        rather than producing a capture the decoder flags as invalid."""

        if address % 4096 != 0:
            raise ValueError(f"sector address {address} must be 4096-byte aligned")
        addr_bytes = self._addr_bytes(address)
        return self.transport.transfer(
            builder, mosi=[_SE, *addr_bytes],
            labels=[
                "CMD=SE", f"ADDR[23:16]={format_byte(addr_bytes[0])}",
                f"ADDR[15:8]={format_byte(addr_bytes[1])}", f"ADDR[7:0]={format_byte(addr_bytes[2])}",
            ],
        )

    def chip_erase(self, builder: CaptureBuilder) -> FrameHandle:
        return self.transport.transfer(builder, mosi=[_CE], labels=["CMD=CE"])
