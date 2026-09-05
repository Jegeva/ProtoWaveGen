from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, microseconds_to_samples, register_protocol
from ._ir_pulse import ensure_idle_gap, mark_space

_LEADER_MARK_US = 9000.0
_LEADER_SPACE_US = 4500.0
_REPEAT_SPACE_US = 2250.0
_BIT_MARK_US = 562.5
_ZERO_SPACE_US = 562.5
_ONE_SPACE_US = 1687.5
_STOP_MARK_US = 562.5


@register_protocol("ir_nec")
class IrNec(TransportProtocol):
    """NEC infrared remote-control protocol: one demodulated IR envelope
    line (`sig("ir")`, active-low — see `_ir_pulse.py`).

    Pulse-distance encoding: each bit is a fixed-width mark (562.5us)
    followed by a space whose width selects the bit value (562.5us = 0,
    1687.5us = 1) — sigrok's `ir_nec` decoder measures edge-to-edge
    (mark+space) distance, so mark width alone never carries the bit.
    Classic 8-bit form only: address, its bitwise complement, command, its
    complement, all LSB-first, then a final stop-bit mark closing out the
    last data bit's timing measurement. The decoder hard-rejects a frame
    whose address doesn't complement-check against ~address, so extended
    16-bit addressing isn't supported here.
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("ir"), initial_level=1)]

    @staticmethod
    def _bits_lsb_first(byte: int) -> list[int]:
        return [(byte >> i) & 1 for i in range(8)]

    def send(self, builder: CaptureBuilder, *, address: int, command: int) -> FrameHandle:
        if not (0 <= address <= 0xFF):
            raise ValueError(f"address {address} does not fit in 8 bits")
        if not (0 <= command <= 0xFF):
            raise ValueError(f"command {command} does not fit in 8 bits")

        line = self.sig("ir")
        bytes_to_send = [address, address ^ 0xFF, command, command ^ 0xFF]
        ensure_idle_gap(builder, line)
        with builder.frame() as fh:
            mark_space(builder, line, _LEADER_MARK_US, _LEADER_SPACE_US)
            for byte in bytes_to_send:
                for bit in self._bits_lsb_first(byte):
                    mark_space(builder, line, _BIT_MARK_US, _ONE_SPACE_US if bit else _ZERO_SPACE_US)
            builder.set_level(line, 0)  # stop bit
            builder.advance(microseconds_to_samples(builder, _STOP_MARK_US))
            builder.set_level(line, 1)

        builder.annotate("field", f"ADDR=0x{address:02X} CMD=0x{command:02X}", start=fh.start, end=fh.end, signals=(line,))
        return fh

    def send_repeat(self, builder: CaptureBuilder) -> FrameHandle:
        """A repeat code: shorter leader (9ms mark + 2.25ms space), no
        data bits, followed by the same stop-bit mark."""

        line = self.sig("ir")
        ensure_idle_gap(builder, line)
        with builder.frame() as fh:
            mark_space(builder, line, _LEADER_MARK_US, _REPEAT_SPACE_US)
            builder.set_level(line, 0)  # stop bit
            builder.advance(microseconds_to_samples(builder, _STOP_MARK_US))
            builder.set_level(line, 1)

        builder.annotate("field", "REPEAT", start=fh.start, end=fh.end, signals=(line,))
        return fh
