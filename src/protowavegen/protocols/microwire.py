from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import (
    DriverTracker,
    TransportProtocol,
    bind_clock_samples,
    decode_bits_with_floating,
    register_protocol,
)


@register_protocol("microwire")
class MicrowireBus(TransportProtocol):
    """Microwire: a 3-wire half-duplex synchronous bus (`clk`, `cs`, `di`,
    `do` — real hardware sometimes has DI/DO tied together, but keeping
    them as two separate always-idle-high `DIGITAL` lines avoids needing
    open-drain/tri-state bookkeeping for something that isn't actually
    open-drain on most parts, and keeps sigrok/VCD output unambiguous about
    which physical pin carries which byte).

    Unlike `SpiBus`, Microwire has no CPOL/CPHA mode variants: clock idles
    low, data changes on the falling edge, sampled on the rising edge — one
    fixed timing, so this has its own small `_clock_edges`-style primitive
    rather than reusing `SpiBus`'s mode-parameterized one. `cs` is
    active-**high** (the opposite of SPI's usual convention — an easy
    mistake, called out explicitly here).

    `transfer(mosi_bits, read_bits=0)`: clocks `len(mosi_bits)` bits out on
    `di` (MSB-first, as sent), then `read_bits` more clock cycles reading
    back whatever the caller supplies on `do`.
    """

    def __init__(self, node_id: str, *, clock_hz: int, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self.clock_hz = clock_hz
        self._shc: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        self._shc = bind_clock_samples(samplerate, self.clock_hz, hz_label="clock_hz")

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._shc is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return None if self._shc is None else self._shc * 2

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("clk"), initial_level=0),
            Signal(self.sig("cs"), initial_level=0),  # active-high
            Signal(self.sig("di"), initial_level=1),
            Signal(self.sig("do"), initial_level=1),
        ]

    def _clock_bit(
        self,
        builder: CaptureBuilder,
        di_bit: int,
        do_bit: int,
        di_tracker: DriverTracker,
        do_tracker: DriverTracker,
        di_floating: bool = False,
        do_floating: bool = False,
    ) -> None:
        # SI (di, master-driven) and SO (do, slave-driven) change on
        # opposite edges of the clock, not simultaneously — a real
        # Microwire master sets DI while the clock is low (so it's stable
        # for the slave to sample on the rising edge), while a real slave
        # changes SO right at the rising edge (so it's stable for the
        # master to sample on the following falling edge). Setting both on
        # the same edge is electrically indistinguishable to a human eye on
        # a waveform plot, but it broke sigrok's `microwire` decoder: it
        # samples SO specifically on the falling edge, so if DO already
        # holds *this* bit's new value at that same falling edge (as it
        # would if both changed together), the decoder reads the *next*
        # bit's value instead — shifting every decoded SO bit by one
        # position (confirmed empirically: a read came back exactly
        # left-shifted by 1 bit).
        clk, di, do = self.sig("clk"), self.sig("di"), self.sig("do")
        shc = self._shc
        builder.set_level(clk, 0)  # falling edge: DI (SI) changes here
        builder.set_level(di, di_bit)
        di_tracker.set("floating" if di_floating else "master")
        builder.advance(shc)
        builder.set_level(clk, 1)  # rising edge: DO (SO) changes here
        builder.set_level(do, do_bit)
        do_tracker.set("floating" if do_floating else "slave")
        builder.advance(shc)

    def transfer(
        self, builder: CaptureBuilder, *, mosi_bits, read_bits=None, datatype: str = "bytes",
        labels: list[str] | None = None,
    ) -> FrameHandle:
        """`mosi_bits`/`read_bits` are plain `list[int]` (`datatype="bytes"`,
        default, unchanged from before) or, with `datatype="bits"`, flat
        `0`/`1`/`l/L`/`h/H`/`z/Z` strings decoded via
        `decode_bits_with_floating`. `di`/`do` are plain `DIGITAL` (no
        protocol-defined pull — see the class docstring), so `z`/`Z` always
        needs `l`/`h` used explicitly instead (`tristate=False`)."""

        self._ensure_bound(builder)
        if datatype == "bits":
            mosi_bits, mosi_floating = decode_bits_with_floating(mosi_bits, tristate=False)
            read_bits, read_floating = (
                decode_bits_with_floating(read_bits, tristate=False) if read_bits else ([], frozenset())
            )
        else:
            mosi_floating = frozenset()
            read_bits = read_bits or []
            read_floating = frozenset()
        clk, cs, di, do = self.sig("clk"), self.sig("cs"), self.sig("di"), self.sig("do")
        di_tracker, do_tracker = DriverTracker(builder, di), DriverTracker(builder, do)

        # Minimum CS-deasserted recovery time before asserting — see
        # SpiBus.transfer()'s comment for why; confirmed here too via
        # sigrok's microwire decoder merging separate commands together.
        builder.advance(self._shc)
        with builder.frame() as fh:
            builder.set_level(cs, 1)  # active-high assert
            for i, bit in enumerate(mosi_bits):
                self._clock_bit(builder, bit, 1, di_tracker, do_tracker, di_floating=i in mosi_floating)
            for i, bit in enumerate(read_bits):
                self._clock_bit(builder, 1, bit, di_tracker, do_tracker, do_floating=i in read_floating)
            # Bring the clock back to idle-low before dropping CS. Without
            # this, CS falls while clk is still high (the last `_clock_bit`
            # leaves it there) — real hardware always deasserts CS with SK
            # already low, and sigrok's `microwire` decoder specifically
            # requires it: its "end of packet" bit flush only fires when
            # `cs` falls *while `sk` is also 0*, so with clk left high the
            # decoder silently drops the final clocked bit of every
            # transfer (confirmed empirically — this is exactly why word
            # data came up one bit short while the address, decoded
            # earlier in the stream, was unaffected).
            builder.set_level(clk, 0)
            builder.advance(self._shc)
            builder.set_level(cs, 0)
        di_tracker.close()
        do_tracker.close()

        label = labels[0] if labels else f"DI={len(mosi_bits)}b DO={len(read_bits)}b"
        builder.annotate("field", label, start=fh.start, end=fh.end, signals=(di, do))
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(di, do))
        return fh
