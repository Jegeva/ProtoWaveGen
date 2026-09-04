from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .uart import UartTransport

_BREAK_US = 100  # spec minimum 88us
_MAB_US = 12  # spec minimum 8us (mark-after-break)


@register_protocol("dmx512")
class Dmx512(StackedProtocol):
    """DMX512 lighting control, stacked on `UartTransport` (should be
    configured 250000 baud, 8 data bits, no parity, 2 stop bits, `duplex=
    "full"` — DMX512 is electrically just UART framing with its own
    break+payload convention on top, unidirectional controller->device,
    the same "reuse UART byte framing" pattern `LinBus` uses on top of
    `UartTransport`).

    Frame: BREAK (line held low, generated as a raw level hold via the
    transport's `tx` line — not a UART byte, matches `LinBus`'s break
    field) + MAB (mark-after-break, released high) + start code (`0x00` for
    standard dimmer data) + up to 512 channel bytes, sent as one
    `transport.send()` call (gets per-byte annotations for free). Only the
    standard `0x00` dimmer start code is modeled — no RDM (the
    bidirectional extension, alternate start codes).
    """

    def __init__(
        self, node_id: str, transport: UartTransport, *, break_us: float = _BREAK_US, mab_us: float = _MAB_US,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, transport, operations)
        if transport.duplex != "full":
            raise ValueError("DMX512 requires its UART transport configured with duplex='full'")
        self.break_us = break_us
        self.mab_us = mab_us

    def send_frame(self, builder: CaptureBuilder, *, channels: list[int], start_code: int = 0) -> FrameHandle:
        if len(channels) > 512:
            raise ValueError(f"DMX512 supports at most 512 channels, got {len(channels)}")
        if self.transport.bit_period_samples is None:
            self.transport.bind_samplerate(builder.samplerate)
        line = self.transport.sig("tx")
        break_samples = max(round(builder.samplerate * self.break_us / 1_000_000), 1)
        mab_samples = max(round(builder.samplerate * self.mab_us / 1_000_000), 1)

        with builder.frame() as fh:
            with builder.frame() as break_fh:
                builder.set_level(line, 0)
                builder.advance(break_samples)
                builder.set_level(line, 1)
                builder.advance(mab_samples)
            builder.annotate("unit", "break", start=break_fh.start, end=break_fh.end, signals=(line,))
            builder.annotate("field", "BREAK", start=break_fh.start, end=break_fh.end, signals=(line,))
            self.transport.send(builder, channel="tx", data=[start_code, *channels])
        return fh
