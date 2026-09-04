from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import (
    DriverTracker,
    TransportProtocol,
    bind_clock_samples,
    bits_of_byte,
    register_protocol,
    resolve_single_byte,
)


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
        self._bit_samples = bind_clock_samples(
            samplerate, self.baudrate, hz_label="baudrate", divisor=1, minimum=2,
            minimum_note="for Manchester encoding",
        )

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._bit_samples is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return self._bit_samples

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("dali"), initial_level=1)]

    def _manchester_bit(
        self, builder: CaptureBuilder, bit: int, tracker: DriverTracker, floating: bool = False
    ) -> None:
        """G.E. Thomas Manchester: a `1` is a low->high transition at
        bit-center; a `0` is high->low — each half-bit is one clocked
        level, so this is one `set_level`+`advance` pair per half."""

        dali = self.sig("dali")
        half = self._bit_samples // 2
        first, second = (0, 1) if bit else (1, 0)
        builder.set_level(dali, first)
        tracker.set("floating" if floating else "master")
        builder.advance(half)
        builder.set_level(dali, second)
        tracker.set("floating" if floating else "master")
        builder.advance(self._bit_samples - half)

    def _send_bits(
        self,
        builder: CaptureBuilder,
        bits: list[int],
        tracker: DriverTracker,
        floating_positions: frozenset[int] = frozenset(),
    ) -> None:
        for i, bit in enumerate(bits):
            self._manchester_bit(builder, bit, tracker, floating=i in floating_positions)

    def send_forward_frame(
        self, builder: CaptureBuilder, *, DALI_ADDRESS: int, command: int,
        DALI_ADDRESS_datatype: str = "bytes", command_datatype: str = "bytes",
    ) -> FrameHandle:
        """`DALI_ADDRESS`/`command` are plain ints (`*_datatype="bytes"`,
        default, unchanged from before) or, with a hex/bin/text
        `*_datatype`, a single-byte payload via `resolve_single_byte` — the
        `l/L/h/H/z/Z` floating-marker alphabet then applies. `dali` has no
        protocol-defined pull (single transmitter per frame, same reasoning
        as `CanBus` — see the class docstring), so `z`/`Z` always needs
        `l`/`h` used explicitly instead.

        The field is named `DALI_ADDRESS` (not `address`) specifically so
        it can live in `_PAYLOAD_FIELDS` (`config.py`) without colliding
        with `I2CBus.write`/`.read`'s own unrelated `address` kwarg — that
        set is shared across every protocol type, not scoped per protocol,
        so a plain `address` here would make I2C's device-address param
        look like an ambiguous second payload candidate on every I2C
        write/read op."""

        self._ensure_bound(builder)
        address, addr_floating = resolve_single_byte(DALI_ADDRESS, DALI_ADDRESS_datatype)
        command, cmd_floating = resolve_single_byte(command, command_datatype)
        dali = self.sig("dali")
        addr_bits = bits_of_byte(address)
        cmd_bits = bits_of_byte(command)
        tracker = DriverTracker(builder, dali)

        with builder.frame() as fh:
            self._send_bits(builder, [1], tracker)  # START
            with builder.frame() as addr_fh:
                self._send_bits(builder, addr_bits, tracker, addr_floating)
            builder.annotate("unit", "byte", start=addr_fh.start, end=addr_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"ADDR=0x{address:02X}", start=addr_fh.start, end=addr_fh.end, signals=(dali,),
                value=address,
            )
            with builder.frame() as cmd_fh:
                self._send_bits(builder, cmd_bits, tracker, cmd_floating)
            builder.annotate("unit", "byte", start=cmd_fh.start, end=cmd_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"CMD=0x{command:02X}", start=cmd_fh.start, end=cmd_fh.end, signals=(dali,),
                value=command,
            )
            builder.set_level(dali, 1)
            tracker.set("master")
            builder.advance(2 * self._bit_samples)  # 2 stop bits: idle-high settling, no transitions
        tracker.close()

        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(dali,))
        return fh

    def send_backward_frame(
        self, builder: CaptureBuilder, *, answer: int, answer_datatype: str = "bytes"
    ) -> FrameHandle:
        self._ensure_bound(builder)
        answer, answer_floating = resolve_single_byte(answer, answer_datatype)
        dali = self.sig("dali")
        answer_bits = bits_of_byte(answer)
        tracker = DriverTracker(builder, dali)

        with builder.frame() as fh:
            self._send_bits(builder, [1], tracker)  # START
            with builder.frame() as ans_fh:
                self._send_bits(builder, answer_bits, tracker, answer_floating)
            builder.annotate("unit", "byte", start=ans_fh.start, end=ans_fh.end, signals=(dali,))
            builder.annotate(
                "field", f"ANSWER=0x{answer:02X}", start=ans_fh.start, end=ans_fh.end, signals=(dali,),
                value=answer,
            )
            builder.set_level(dali, 1)
            tracker.set("master")
            builder.advance(2 * self._bit_samples)
        tracker.close()

        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(dali,))
        return fh
