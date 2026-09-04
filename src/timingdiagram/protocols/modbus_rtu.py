from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .checksums import crc16_modbus
from .uart import UartTransport

_READ_HOLDING_REGISTERS = 0x03
_WRITE_SINGLE_REGISTER = 0x06


@register_protocol("modbus_rtu")
class ModbusRtu(StackedProtocol):
    """Modbus RTU, stacked on `UartTransport` (should be configured 8N1 or
    8E1 — Modbus RTU is just byte-oriented UART framing with its own
    addressing/function-code/CRC16 layer on top, sent as one `send()` call
    so it gets UART's own per-byte annotations for free).

    Frame: 1-byte slave address + 1-byte function code + data + 2-byte
    CRC16 (`checksums.crc16_modbus`, low byte first on the wire), bracketed
    by >=3.5-character-time silence on both sides — modeled as `advance()`
    before/after the frame (same idea as LIN's break field: raw idle time,
    not a UART byte), sized from the transport's own bit period times how
    many bit-times one whole byte actually takes (start + data + parity +
    stop), not just the raw baud rate.

    Only function codes `0x03` (Read Holding Registers) and `0x06` (Write
    Single Register) are modeled — enough to demonstrate the framing
    pattern. No exception-response frames, no Modbus ASCII variant.
    """

    def __init__(
        self, node_id: str, transport: UartTransport, *, silence_char_times: float = 3.5,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        self.silence_char_times = silence_char_times

    def _silence_samples(self, builder: CaptureBuilder) -> int:
        if self.transport.bit_period_samples is None:
            self.transport.bind_samplerate(builder.samplerate)
        bits_per_char = (
            1 + self.transport.data_bits + (0 if self.transport.parity == "none" else 1)
            + self.transport.stop_bits
        )
        return round(self.transport.bit_period_samples * bits_per_char * self.silence_char_times)

    def _send_frame(self, builder: CaptureBuilder, *, frame_bytes: list[int], labels: list[str]) -> FrameHandle:
        silence = self._silence_samples(builder)
        builder.advance(silence)
        crc = crc16_modbus(frame_bytes)
        full = [*frame_bytes, crc & 0xFF, (crc >> 8) & 0xFF]
        full_labels = [*labels, f"CRC=0x{crc:04X}", f"CRC=0x{crc:04X}"]
        fh = self.transport.send(builder, data=full, labels=full_labels)
        builder.advance(silence)
        return fh

    def read_holding_registers(self, builder: CaptureBuilder, *, slave: int, start_addr: int, count: int) -> FrameHandle:
        frame = [
            slave, _READ_HOLDING_REGISTERS, (start_addr >> 8) & 0xFF, start_addr & 0xFF,
            (count >> 8) & 0xFF, count & 0xFF,
        ]
        labels = [
            f"SLAVE={slave}", "FN=READ_HOLDING", f"ADDR=0x{start_addr:04X}", f"ADDR=0x{start_addr:04X}",
            f"COUNT={count}", f"COUNT={count}",
        ]
        return self._send_frame(builder, frame_bytes=frame, labels=labels)

    def write_single_register(self, builder: CaptureBuilder, *, slave: int, addr: int, value: int) -> FrameHandle:
        frame = [slave, _WRITE_SINGLE_REGISTER, (addr >> 8) & 0xFF, addr & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
        labels = [
            f"SLAVE={slave}", "FN=WRITE_SINGLE", f"ADDR=0x{addr:04X}", f"ADDR=0x{addr:04X}",
            f"VALUE=0x{value:04X}", f"VALUE=0x{value:04X}",
        ]
        return self._send_frame(builder, frame_bytes=frame, labels=labels)
