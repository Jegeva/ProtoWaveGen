from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, register_protocol


@register_protocol("dali")
class DaliBus(TransportProtocol):
    """DALI (Digital Addressable Lighting Interface), stacked directly as a
    `TransportProtocol`: a single logical `dali` line (the differential
    current-loop pair collapses to one logical signal, same simplification
    `CanBus` uses), `SignalKind.DIGITAL` — single transmitter per frame like
    CAN, so no open-drain/pullup concept is needed here either.

    Manchester encoded (G.E. Thomas convention): bit `1` = low->high
    transition at bit-center, bit `0` = high->low. ~1200bps (bit period
    ~833us by spec; exposed as a configurable `baudrate` for flexibility).

    Forward frame (controller->ballast): START bit (fixed `1`) + 8-bit
    address byte (MSB-first) + 8-bit command/data byte (MSB-first) + 2 stop
    bits (idle-high settling, no transitions). Backward frame (ballast->
    controller reply): START bit + 8-bit answer byte + 2 stop bits, no
    address byte. The address byte's addressing-mode bits (short/group/
    broadcast) aren't decoded into separate fields, and the command opcode
    table isn't validated — both are treated as plain bytes on the wire.
    """

    def __init__(self, node_id: str, *, baudrate: int = 1200, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self.baudrate = baudrate
        self._bit_samples: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        spb = round(samplerate / self.baudrate)
        if spb < 2:
            raise ValueError(
                f"samplerate {samplerate} too low for baudrate {self.baudrate} "
                f"(need at least {2 * self.baudrate} Hz for Manchester encoding)"
            )
        self._bit_samples = spb

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._bit_samples is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return self._bit_samples

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("dali"), initial_level=1)]

    def _manchester_bit(self, builder: CaptureBuilder, bit: int) -> None:
        """G.E. Thomas Manchester: a `1` is a low->high transition at
        bit-center; a `0` is high->low — each half-bit is one clocked
        level, so this is one `set_level`+`advance` pair per half."""

        dali = self.sig("dali")
        half = self._bit_samples // 2
        first, second = (0, 1) if bit else (1, 0)
        builder.set_level(dali, first)
        builder.advance(half)
        builder.set_level(dali, second)
        builder.advance(self._bit_samples - half)

    def _send_bits(self, builder: CaptureBuilder, bits: list[int]) -> None:
        for bit in bits:
            self._manchester_bit(builder, bit)

    def send_forward_frame(self, builder: CaptureBuilder, *, address: int, command: int) -> FrameHandle:
        self._ensure_bound(builder)
        dali = self.sig("dali")
        addr_bits = [(address >> i) & 1 for i in reversed(range(8))]
        cmd_bits = [(command >> i) & 1 for i in reversed(range(8))]

        with builder.frame() as fh:
            self._send_bits(builder, [1])  # START
            with builder.frame() as addr_fh:
                self._send_bits(builder, addr_bits)
            builder.annotate("unit", "byte", start=addr_fh.start, end=addr_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"ADDR=0x{address:02X}", start=addr_fh.start, end=addr_fh.end, signals=(dali,),
                value=address,
            )
            with builder.frame() as cmd_fh:
                self._send_bits(builder, cmd_bits)
            builder.annotate("unit", "byte", start=cmd_fh.start, end=cmd_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"CMD=0x{command:02X}", start=cmd_fh.start, end=cmd_fh.end, signals=(dali,),
                value=command,
            )
            builder.set_level(dali, 1)
            builder.advance(2 * self._bit_samples)  # 2 stop bits: idle-high settling, no transitions

        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(dali,))
        return fh

    def send_backward_frame(self, builder: CaptureBuilder, *, answer: int) -> FrameHandle:
        self._ensure_bound(builder)
        dali = self.sig("dali")
        answer_bits = [(answer >> i) & 1 for i in reversed(range(8))]

        with builder.frame() as fh:
            self._send_bits(builder, [1])  # START
            with builder.frame() as ans_fh:
                self._send_bits(builder, answer_bits)
            builder.annotate("unit", "byte", start=ans_fh.start, end=ans_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"ANSWER=0x{answer:02X}", start=ans_fh.start, end=ans_fh.end, signals=(dali,),
                value=answer,
            )
            builder.set_level(dali, 1)
            builder.advance(2 * self._bit_samples)

        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(dali,))
        return fh
