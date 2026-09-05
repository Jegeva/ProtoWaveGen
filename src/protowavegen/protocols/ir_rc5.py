from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, register_protocol
from ._ir_pulse import biphase_bit, ensure_idle_gap

_HALF_BIT_US = 889.0  # 1.78ms full bit, biphase-encoded


@register_protocol("ir_rc5")
class IrRc5(TransportProtocol):
    """Philips RC-5 infrared remote-control protocol: one demodulated IR
    envelope line (`sig("ir")`, active-low — see `_ir_pulse.py`).

    14-bit biphase (Manchester) frame, MSB-first: 2 start bits (always 1
    in standard mode; the second is the complement of command bit 6 in
    extended mode, giving a 7-bit command range), 1 toggle bit (flipped by
    a real remote each time a button is freshly pressed, not auto-tracked
    here), 5 address bits, 6 command bits.
    """

    def __init__(self, node_id: str, *, operations: list[dict] | None = None):
        super().__init__(node_id, operations)

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("ir"), initial_level=1)]

    def send(
        self, builder: CaptureBuilder, *, address: int, command: int,
        toggle: bool = False, extended: bool = False,
    ) -> FrameHandle:
        if not (0 <= address < 32):
            raise ValueError(f"address {address} does not fit in 5 bits")
        if not (0 <= command < (128 if extended else 64)):
            raise ValueError(f"command {command} does not fit in {7 if extended else 6} bits")

        line = self.sig("ir")
        start2 = (0 if (command >> 6) & 1 else 1) if extended else 1
        bits = [1, start2, 1 if toggle else 0]
        bits += [(address >> i) & 1 for i in reversed(range(5))]
        bits += [(command >> i) & 1 for i in reversed(range(6))]

        ensure_idle_gap(builder, line)
        with builder.frame() as fh:
            for bit in bits:
                biphase_bit(builder, line, bit, _HALF_BIT_US)

        builder.annotate(
            "field", f"ADDR={address} CMD={command}" + (" T" if toggle else ""),
            start=fh.start, end=fh.end, signals=(line,),
        )
        return fh
