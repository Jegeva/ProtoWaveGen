from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, microseconds_to_samples, register_protocol
from ._ir_pulse import biphase_bit, ensure_idle_gap

_HALF_BIT_US = 444.5  # 0.889ms full bit, biphase-encoded
_LEADER_MARK_HALF_BITS = 6
_LEADER_SPACE_HALF_BITS = 2


@register_protocol("ir_rc6")
class IrRc6(TransportProtocol):
    """Philips RC-6 infrared remote-control protocol (mode 0, standard):
    one demodulated IR envelope line (`sig("ir")`, active-low — see
    `_ir_pulse.py`).

    Distinctive leader (not a plain biphase bit): a 6-half-bit mark
    followed by a 2-half-bit space. Then, all regular-width biphase except
    the toggle bit: 1 start bit (always 1), 3 mode bits (mode 0 = standard
    frame shape, MSB-first), 1 **double-width** toggle bit, 8 address
    bits, 8 command bits, all MSB-first. Modes 6A/6B (short/long
    addressing variants) aren't implemented — mode 0 only.

    Every bit after the leader is encoded with the *opposite* sense from
    `_ir_pulse.biphase_bit`'s own RC-5 convention (confirmed empirically
    against sigrok's `ir_rc6` decoder): a real start bit=1 must produce a
    falling edge exactly at the leader's 2-half-bit mark, which only
    happens if its own first half is *low*, not high — the decoder's
    `auto`-polarity mode then self-adapts every later bit's recovered
    value consistently from whatever sense the sync bit exhibited, so
    inverting every bit uniformly still decodes to the correct logical
    values. A trailing 20-half-bit idle gap is also required — the
    decoder only closes out and emits the address/command summary once it
    sees a long-enough run with no further edge, mirroring how a real
    receiver needs silence to know the frame ended.
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("ir"), initial_level=1)]

    def send(
        self, builder: CaptureBuilder, *, mode: int = 0, address: int = 0, command: int = 0,
        toggle: bool = False,
    ) -> FrameHandle:
        if not (0 <= mode < 8):
            raise ValueError(f"mode {mode} does not fit in 3 bits")
        if mode != 0:
            raise ValueError("only mode 0 (standard) is implemented")
        if not (0 <= address <= 0xFF):
            raise ValueError(f"address {address} does not fit in 8 bits")
        if not (0 <= command <= 0xFF):
            raise ValueError(f"command {command} does not fit in 8 bits")

        line = self.sig("ir")
        mode_bits = [(mode >> i) & 1 for i in reversed(range(3))]
        addr_bits = [(address >> i) & 1 for i in reversed(range(8))]
        cmd_bits = [(command >> i) & 1 for i in reversed(range(8))]

        ensure_idle_gap(builder, line)
        with builder.frame() as fh:
            builder.set_level(line, 0)  # leader mark
            builder.advance(microseconds_to_samples(builder, _HALF_BIT_US * _LEADER_MARK_HALF_BITS))
            builder.set_level(line, 1)  # leader space
            builder.advance(microseconds_to_samples(builder, _HALF_BIT_US * _LEADER_SPACE_HALF_BITS))

            biphase_bit(builder, line, 0, _HALF_BIT_US)  # start bit, always 1 (inverted sense, see docstring)
            for bit in mode_bits:
                biphase_bit(builder, line, 1 - bit, _HALF_BIT_US)
            biphase_bit(builder, line, 0 if toggle else 1, _HALF_BIT_US, width=2)
            for bit in addr_bits:
                biphase_bit(builder, line, 1 - bit, _HALF_BIT_US)
            for bit in cmd_bits:
                biphase_bit(builder, line, 1 - bit, _HALF_BIT_US)
            # Trailing idle: sigrok's decoder only closes out the command
            # field once it sees at least 6 half-bits with no further edge
            # (its own "end of frame" detection) — matches how a real
            # receiver needs silence to know the frame ended.
            builder.advance(microseconds_to_samples(builder, _HALF_BIT_US * 20))

        builder.annotate(
            "field", f"ADDR=0x{address:02X} CMD=0x{command:02X}" + (" T" if toggle else ""),
            start=fh.start, end=fh.end, signals=(line,),
        )
        return fh
