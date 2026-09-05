from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import DriverTracker, TransportProtocol, microseconds_to_samples, register_protocol

_HEADER_ONES = 9


@register_protocol("em4100")
class Em4100(TransportProtocol):
    """EM4100 125kHz RFID tag: one Manchester-encoded `data` line, active
    (tag driving) whenever it's within read range — modeled as a plain
    `TransportProtocol` signal, no reader/tag handshake (real EM4100 tags
    are passive and just continuously transmit once powered by the
    reader's field). Uses the same Manchester bit convention as
    `dali.py`'s `_manchester_bit` (bit=1: low-then-high, bit=0:
    high-then-low) — decoding it needs sigrok's `em4100` decoder given
    `polarity=active-low` explicitly (confirmed empirically; its own
    default is `active-high`, which decodes every bit inverted).

    Fixed 64-bit frame: a 9-bit header (all 1s), then 10 rows of 4 data
    bits + 1 even row-parity bit (first 2 rows = an 8-bit version/customer
    byte, remaining 8 rows = a 32-bit unique ID), then 4 even column-parity
    bits (one per data-bit column, XORed down all 10 rows) + 1 stop bit
    (0). `DriverTracker` is used for annotation-style consistency with
    `wiegand.py`'s single-transmitter precedent, even though there's no
    real bus arbitration here (`owner="tag"` throughout).

    Real tags transmit continuously, and matching that is also what makes
    this reliably decodable: sigrok's decoder bootstraps its bit-pairing
    state off whatever edge it happens to see first, so a single isolated
    frame (or even two back-to-back) can decode with every row shifted out
    of phase — confirmed empirically by comparing its raw per-bit output
    against the exact bits generated. Three or more `transmit()` calls
    back-to-back let the decoder's pairing state settle by the first
    frame boundary, after which every subsequent frame (and the first
    frame's own row/column-parity fields and `"Tag: ..."` summary) decodes
    cleanly — the same kind of repeat-for-a-clean-decode shape as this
    repo's existing PS/2/LIN sigrok round-trip workarounds.
    """

    def __init__(self, node_id: str, *, bit_us: float = 512.0, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self.bit_us = bit_us

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("data"), initial_level=1)]

    def _manchester_bit(self, builder: CaptureBuilder, bit: int, tracker: DriverTracker) -> None:
        line = self.sig("data")
        half = microseconds_to_samples(builder, self.bit_us / 2)
        first, second = (0, 1) if bit else (1, 0)
        builder.set_level(line, first)
        tracker.set("tag")
        builder.advance(half)
        builder.set_level(line, second)
        tracker.set("tag")
        builder.advance(half)

    @staticmethod
    def _row_bits(nibble: int) -> list[int]:
        bits = [(nibble >> i) & 1 for i in reversed(range(4))]
        return bits + [bits[0] ^ bits[1] ^ bits[2] ^ bits[3]]

    def transmit(self, builder: CaptureBuilder, *, version: int, unique_id: int) -> FrameHandle:
        if not (0 <= version <= 0xFF):
            raise ValueError(f"version {version} does not fit in 8 bits")
        if not (0 <= unique_id <= 0xFFFFFFFF):
            raise ValueError(f"unique_id {unique_id} does not fit in 32 bits")

        nibbles = [(version >> 4) & 0xF, version & 0xF]
        nibbles += [(unique_id >> (4 * i)) & 0xF for i in reversed(range(8))]

        rows = [self._row_bits(nibble) for nibble in nibbles]
        col_parity = [0, 0, 0, 0]
        for row in rows:
            for c in range(4):
                col_parity[c] ^= row[c]

        bits = [1] * _HEADER_ONES
        for row in rows:
            bits += row
        bits += col_parity + [0]  # trailer: 4 column-parity bits + stop bit

        line = self.sig("data")
        tracker = DriverTracker(builder, line)
        with builder.frame() as fh:
            for bit in bits:
                self._manchester_bit(builder, bit, tracker)
        tracker.close()

        builder.annotate(
            "field", f"VER=0x{version:02X} ID=0x{unique_id:08X}", start=fh.start, end=fh.end, signals=(line,),
        )
        return fh
