from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, decode_payload, format_byte, register_protocol

_VALID_WIDTHS = {1, 4, 8}
_VALID_MODES = {0, 1, 2, 3}
_VALID_BIT_ORDER = {"msb", "lsb"}


@register_protocol("spi")
class SpiBus(TransportProtocol):
    """SPI (`width=1`), QSPI (`width=4`) or OctoSPI (`width=8`) — one class,
    since the only real difference is how many parallel data lines a clock
    edge carries. Push-pull bus (unlike I2C), so lines are plainly DIGITAL
    and driver annotations are static per transfer rather than per-bit.

    `transfer()` is classic full-duplex SPI (independent `mosi`/`miso`).
    `wide_transfer()` is QSPI/OctoSPI: all `width` IO lines carry one shared
    direction at a time, `width` bits per clock edge (e.g. a nibble per edge
    in QSPI). JEDEC CFI (`jedec_cfi.py`) stacks on this via `wide_transfer`
    for its command/address/data phases.

    CPOL/CPHA `mode` (0-3) follows the standard SPI convention; timing is
    generated from first principles (idle level = CPOL, data changes/gets
    sampled on leading vs trailing edge per CPHA) rather than hardcoded per
    mode.
    """

    def __init__(
        self,
        node_id: str,
        *,
        clock_hz: int,
        width: int = 1,
        mode: int = 0,
        bit_order: str = "msb",
        cs_active_low: bool = True,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        if width not in _VALID_WIDTHS:
            raise ValueError(f"width must be one of {_VALID_WIDTHS}, got {width}")
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode}")
        if bit_order not in _VALID_BIT_ORDER:
            raise ValueError(f"bit_order must be one of {_VALID_BIT_ORDER}, got {bit_order!r}")
        self.clock_hz = clock_hz
        self.width = width
        self.mode = mode
        self.bit_order = bit_order
        self.cs_active_low = cs_active_low
        self._shc: int | None = None

    @property
    def _cpol(self) -> int:
        return (self.mode >> 1) & 1

    @property
    def _cpha(self) -> int:
        return self.mode & 1

    def bind_samplerate(self, samplerate: int) -> None:
        shc = round(samplerate / (2 * self.clock_hz))
        if shc < 1:
            raise ValueError(
                f"samplerate {samplerate} too low for clock_hz {self.clock_hz} "
                f"(need at least {2 * self.clock_hz} Hz)"
            )
        self._shc = shc

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._shc is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return None if self._shc is None else self._shc * 2

    def get_signals(self) -> list[Signal]:
        signals = [Signal(self.sig("sclk"), initial_level=self._cpol)]
        if self.width == 1:
            signals += [Signal(self.sig("mosi")), Signal(self.sig("miso"))]
        else:
            signals += [Signal(self.sig(f"io{i}")) for i in range(self.width)]
        signals.append(Signal(self.sig("cs"), initial_level=1 if self.cs_active_low else 0))
        return signals

    def _bits_of(self, byte: int) -> list[int]:
        if not (0 <= byte <= 0xFF):
            raise ValueError(f"byte {byte} does not fit in 8 bits")
        order = reversed(range(8)) if self.bit_order == "msb" else range(8)
        return [(byte >> i) & 1 for i in order]

    def _assert_cs(self, builder: CaptureBuilder) -> None:
        builder.set_level(self.sig("cs"), 0 if self.cs_active_low else 1)

    def _deassert_cs(self, builder: CaptureBuilder) -> None:
        builder.set_level(self.sig("cs"), 1 if self.cs_active_low else 0)

    def _clock_edges(self, builder: CaptureBuilder, set_data) -> None:
        """Advance one bit/symbol period, calling `set_data()` at the point
        CPHA says data must change (idle-phase for CPHA0, leading-edge for
        CPHA1), toggling `sclk` leading then trailing around it."""

        sclk = self.sig("sclk")
        idle, active = self._cpol, 1 - self._cpol
        shc = self._shc
        if self._cpha == 0:
            set_data()
            builder.advance(shc)
            builder.set_level(sclk, active)
            builder.advance(shc)
            builder.set_level(sclk, idle)
        else:
            builder.set_level(sclk, active)
            set_data()
            builder.advance(shc)
            builder.set_level(sclk, idle)
            builder.advance(shc)

    def transfer(
        self,
        builder: CaptureBuilder,
        *,
        mosi=None,
        miso=None,
        datatype: str = "bytes",
        labels: list[str] | None = None,
    ) -> FrameHandle:
        """`labels`, one per byte, overrides the default `"MOSI=.. MISO=.."`
        display — lets a stacked protocol (e.g. JEDEC CFI) show what a byte
        *means* (a command opcode, an address byte) without adding a second
        annotation over the same range, which would just paint over this
        one (same reasoning as `UartTransport.send`'s `labels` param).
        `datatype` applies to both `mosi` and `miso` when given."""

        if self.width != 1:
            raise ValueError("transfer() is for width=1 (classic SPI); use wide_transfer() for QSPI/OctoSPI")
        self._ensure_bound(builder)
        mosi_bytes = decode_payload(mosi, datatype) if mosi else []
        miso_bytes = decode_payload(miso, datatype) if miso is not None else [0] * len(mosi_bytes)
        if len(mosi_bytes) != len(miso_bytes):
            raise ValueError("mosi and miso must be the same length (one shared clock drives both)")

        mosi_line, miso_line = self.sig("mosi"), self.sig("miso")
        # Minimum CS-deasserted recovery time before asserting: without it,
        # two back-to-back transfer() calls each bracket their own CS with
        # zero samples between the first's deassert and the second's
        # assert — physically meaningless (real CS needs nonzero setup
        # time) and indistinguishable from no CS toggle at all to a real
        # decoder (confirmed empirically: sigrok's max7219 decoder read
        # several genuinely separate commands as "Overlong write" this way).
        builder.advance(self._shc)
        with builder.frame() as fh:
            self._assert_cs(builder)
            for i, (mbyte, sbyte) in enumerate(zip(mosi_bytes, miso_bytes)):
                with builder.frame() as byte_fh:
                    for mbit, sbit in zip(self._bits_of(mbyte), self._bits_of(sbyte)):
                        self._clock_edges(
                            builder,
                            lambda mbit=mbit, sbit=sbit: (
                                builder.set_level(mosi_line, mbit),
                                builder.set_level(miso_line, sbit),
                            ),
                        )
                builder.annotate(
                    "unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=(mosi_line, miso_line)
                )
                label = labels[i] if labels else f"MOSI={format_byte(mbyte)} MISO={format_byte(sbyte)}"
                builder.annotate(
                    "field", label, start=byte_fh.start, end=byte_fh.end, signals=(mosi_line,),
                    mosi=mbyte, miso=sbyte,
                )
            self._deassert_cs(builder)

        builder.annotate("driver", "master", start=fh.start, end=fh.end, signals=(mosi_line,))
        builder.annotate("driver", "slave", start=fh.start, end=fh.end, signals=(miso_line,))
        builder.annotate(
            "bitorder", self.bit_order, start=fh.start, end=fh.end, signals=(mosi_line, miso_line)
        )
        return fh

    def wide_transfer(self, builder: CaptureBuilder, *, data: list[int], direction: str = "write") -> FrameHandle:
        if self.width == 1:
            raise ValueError("wide_transfer() is for width>1 (QSPI/OctoSPI); use transfer() for classic SPI")
        if direction not in ("write", "read"):
            raise ValueError(f"direction must be 'write' or 'read', got {direction!r}")
        self._ensure_bound(builder)

        io_lines = [self.sig(f"io{i}") for i in range(self.width)]
        builder.advance(self._shc)  # minimum CS recovery time — see transfer()'s comment
        with builder.frame() as fh:
            self._assert_cs(builder)
            for byte in data:
                bits = self._bits_of(byte)
                with builder.frame() as byte_fh:
                    for start in range(0, 8, self.width):
                        symbol = bits[start : start + self.width]
                        self._clock_edges(
                            builder,
                            lambda symbol=symbol: [
                                builder.set_level(name, bit) for name, bit in zip(io_lines, symbol)
                            ],
                        )
                builder.annotate("unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=tuple(io_lines))
                builder.annotate(
                    "field", f"{direction.upper()}={format_byte(byte)}",
                    start=byte_fh.start, end=byte_fh.end, signals=(io_lines[0],), value=byte,
                )
            self._deassert_cs(builder)

        owner = "master" if direction == "write" else "slave"
        for line in io_lines:
            builder.annotate("driver", owner, start=fh.start, end=fh.end, signals=(line,))
        builder.annotate("bitorder", self.bit_order, start=fh.start, end=fh.end, signals=tuple(io_lines))
        return fh
