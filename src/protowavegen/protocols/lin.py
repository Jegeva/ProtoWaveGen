from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .uart import UartTransport

_VALID_CHECKSUM = {"classic", "enhanced"}


@register_protocol("lin")
class LinBus(StackedProtocol):
    """LIN stacks on `UartTransport` (`transport`, which must be configured
    `duplex="half"` — LIN is a single-wire bus): sync/PID/data/checksum are
    all just normal UART bytes (`transport.send()`), given custom `labels`
    so they show LIN's own meaning instead of plain hex (see `UartTransport
    .send`'s `labels` param). Only the break field needs anything special —
    it's a raw line-level hold, not a UART byte (no valid start bit would
    fit inside 13 dominant bit-times).

    Frame: break (>=13 low bit-times + >=1 high delimiter bit-time), sync
    byte (0x55), protected ID (6-bit frame ID + 2 parity bits per the LIN
    2.x formula), 0-8 data bytes, checksum (classic: complement of the
    end-around-carry sum of the data bytes; enhanced: same but the sum also
    includes the protected ID byte).
    """

    def __init__(self, node_id: str, transport: UartTransport, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)
        if transport.duplex != "half":
            raise ValueError("LIN requires its UART transport configured with duplex='half' (single-wire bus)")

    @staticmethod
    def _protected_id(frame_id: int) -> int:
        if not (0 <= frame_id < 0x40):
            raise ValueError(f"LIN frame ID {frame_id} must be 0-63 (6 bits)")
        bits = [(frame_id >> i) & 1 for i in range(6)]
        p0 = bits[0] ^ bits[1] ^ bits[2] ^ bits[4]
        p1 = 1 - (bits[1] ^ bits[3] ^ bits[4] ^ bits[5])
        return frame_id | (p0 << 6) | (p1 << 7)

    @staticmethod
    def _checksum(protected_id: int, data: list[int], mode: str) -> int:
        if mode not in _VALID_CHECKSUM:
            raise ValueError(f"checksum mode must be one of {_VALID_CHECKSUM}, got {mode!r}")
        total = protected_id if mode == "enhanced" else 0
        for byte in data:
            total += byte
            if total > 0xFF:
                total -= 0xFF  # end-around carry (one's-complement addition)
        return (~total) & 0xFF

    def send_frame(
        self, builder: CaptureBuilder, *, frame_id: int, data: list[int], checksum: str = "enhanced"
    ) -> FrameHandle:
        if not (0 <= len(data) <= 8):
            raise ValueError(f"LIN data field is 0-8 bytes, got {len(data)}")
        if self.transport.bit_period_samples is None:
            self.transport.bind_samplerate(builder.samplerate)
        bit_samples = self.transport.bit_period_samples
        line = self.transport.sig("data")
        protected_id = self._protected_id(frame_id)

        with builder.frame() as fh:
            with builder.frame() as break_fh:
                builder.set_level(line, 0)
                builder.advance(13 * bit_samples)
                builder.set_level(line, 1)
                builder.advance(bit_samples)  # break delimiter
            builder.annotate("unit", "break", start=break_fh.start, end=break_fh.end, signals=(line,))
            builder.annotate("field", "BREAK", start=break_fh.start, end=break_fh.end, signals=(line,))

            self.transport.send(builder, channel="data", data=[0x55], labels=["SYNC"])
            self.transport.send(
                builder, channel="data", data=[protected_id], labels=[f"PID=0x{protected_id:02X} (ID={frame_id})"]
            )
            if data:
                self.transport.send(builder, channel="data", data=list(data))
                checksum_byte = self._checksum(protected_id, data, checksum)
                self.transport.send(
                    builder, channel="data", data=[checksum_byte], labels=[f"CHK=0x{checksum_byte:02X}"]
                )

        return fh
