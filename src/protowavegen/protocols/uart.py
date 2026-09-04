from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import TransportProtocol, decode_payload, format_byte, register_protocol

_VALID_PARITY = {"none", "even", "odd", "mark", "space"}
_VALID_STOP_BITS = {1, 1.5, 2}
_VALID_DUPLEX = {"full", "half"}
_VALID_FLOW_CONTROL = {"none", "rts_cts"}


@register_protocol("uart")
class UartTransport(TransportProtocol):
    """Asynchronous serial: start bit, LSB-first data bits, optional parity,
    stop bit(s). Full duplex gets independent `tx`/`rx` lines; half duplex
    shares one `data` line and relies on the `driver` annotation (set per
    `send()` call) to say who's talking.

    Hardware flow control (`flow_control="rts_cts"`) is modeled symbolically:
    a short RTS-then-CTS assert/release bracket around the frame, annotated
    on the `field` track — real RTS/CTS conventions vary by implementation,
    this is illustrative rather than a full flow-control state machine.

    LIN and Modbus RTU stack on this class (see `lin.py`): both reuse UART
    byte framing for their own higher-level fields. `send()`'s `labels`
    param exists for exactly that: a stacked protocol that knows what a byte
    *means* (a LIN sync byte, a protected ID) can pass a display label per
    byte instead of the default `format_byte()` hex/char rendering — without
    it, a stacked protocol would have to add a second overlapping `field`
    annotation over the same byte, which would just paint over this one.
    """

    def __init__(
        self,
        node_id: str,
        *,
        baudrate: int,
        data_bits: int = 8,
        parity: str = "none",
        stop_bits: float = 1,
        duplex: str = "full",
        flow_control: str = "none",
        handshake_setup_bits: int = 2,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        if not (5 <= data_bits <= 9):
            raise ValueError(f"data_bits must be 5-9, got {data_bits}")
        if parity not in _VALID_PARITY:
            raise ValueError(f"parity must be one of {_VALID_PARITY}, got {parity!r}")
        if stop_bits not in _VALID_STOP_BITS:
            raise ValueError(f"stop_bits must be one of {_VALID_STOP_BITS}, got {stop_bits}")
        if duplex not in _VALID_DUPLEX:
            raise ValueError(f"duplex must be one of {_VALID_DUPLEX}, got {duplex!r}")
        if flow_control not in _VALID_FLOW_CONTROL:
            raise ValueError(f"flow_control must be one of {_VALID_FLOW_CONTROL}, got {flow_control!r}")

        self.baudrate = baudrate
        self.data_bits = data_bits
        self.parity = parity
        self.stop_bits = stop_bits
        self.duplex = duplex
        self.flow_control = flow_control
        self.handshake_setup_bits = handshake_setup_bits
        self._samples_per_bit: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        spb = round(samplerate / self.baudrate)
        if spb < 1:
            raise ValueError(
                f"samplerate {samplerate} too low for baudrate {self.baudrate} "
                f"(need at least {self.baudrate} Hz)"
            )
        self._samples_per_bit = spb

    @property
    def bit_period_samples(self) -> int | None:
        """Samples per bit, once bound (after the first `send()`). Lets the
        app translate a `unit_bits` config override into raw samples without
        knowing this protocol's internals."""

        return self._samples_per_bit

    def get_signals(self) -> list[Signal]:
        if self.duplex == "full":
            lines = [Signal(self.sig("tx")), Signal(self.sig("rx"))]
        else:
            lines = [Signal(self.sig("data"))]
        if self.flow_control == "rts_cts":
            lines += [Signal(self.sig("rts")), Signal(self.sig("cts"))]
        return lines

    def _line(self, channel: str) -> str:
        if self.duplex == "full" and channel not in ("tx", "rx"):
            raise ValueError(f"full duplex UART channel must be 'tx' or 'rx', got {channel!r}")
        if self.duplex == "half" and channel not in ("data",):
            raise ValueError(f"half duplex UART channel must be 'data', got {channel!r}")
        return self.sig(channel)

    def _parity_bit(self, ones: int) -> int:
        if self.parity == "even":
            return ones % 2
        if self.parity == "odd":
            return 1 - (ones % 2)
        if self.parity == "mark":
            return 1
        return 0  # space

    def _send_byte(self, builder: CaptureBuilder, line: str, byte: int) -> None:
        if not (0 <= byte < (1 << self.data_bits)):
            raise ValueError(f"byte {byte} does not fit in {self.data_bits} data bits")
        spb = self._samples_per_bit

        builder.set_level(line, 0)  # start bit
        builder.advance(spb)

        ones = 0
        for i in range(self.data_bits):
            bit = (byte >> i) & 1  # LSB first
            ones += bit
            builder.set_level(line, bit)
            builder.advance(spb)

        if self.parity != "none":
            builder.set_level(line, self._parity_bit(ones))
            builder.advance(spb)

        builder.set_level(line, 1)  # stop bit(s)
        builder.advance(round(self.stop_bits * spb))

    def _flow_control_bracket(self, builder: CaptureBuilder, line: str) -> None:
        rts, cts = self.sig("rts"), self.sig("cts")
        setup = self.handshake_setup_bits * self._samples_per_bit
        with builder.frame() as fh:
            builder.set_level(rts, 0)
            builder.advance(setup)
            builder.set_level(cts, 0)
            builder.advance(setup)
        builder.annotate("field", "flow-control-request", start=fh.start, end=fh.end, signals=(rts, cts))

    def _flow_control_release(self, builder: CaptureBuilder) -> None:
        builder.set_level(self.sig("rts"), 1)
        builder.set_level(self.sig("cts"), 1)

    def send(
        self,
        builder: CaptureBuilder,
        *,
        channel: str = "tx",
        data,
        datatype: str = "bytes",
        driver: str | None = None,
        pre_delay_bits: int = 0,
        inter_byte_gap_bits: int = 0,
        labels: list[str] | None = None,
    ) -> FrameHandle:
        data = decode_payload(data, datatype)
        if self._samples_per_bit is None:
            self.bind_samplerate(builder.samplerate)
        line = self._line(channel)

        if pre_delay_bits:
            builder.advance(pre_delay_bits * self._samples_per_bit)
        if self.flow_control == "rts_cts":
            self._flow_control_bracket(builder, line)

        with builder.frame() as fh:
            for i, byte in enumerate(data):
                if i > 0 and inter_byte_gap_bits:
                    builder.advance(inter_byte_gap_bits * self._samples_per_bit)
                with builder.frame() as byte_fh:
                    self._send_byte(builder, line, byte)
                # one frame (start+data[+parity]+stop) is UART's natural
                # "unit" — the SVG writer bar-codes these; "field" carries
                # the same span's human-readable byte value for verbose mode.
                builder.annotate("unit", "byte", start=byte_fh.start, end=byte_fh.end, signals=(line,))
                label = labels[i] if labels else format_byte(byte)
                builder.annotate(
                    "field", label, start=byte_fh.start, end=byte_fh.end, signals=(line,),
                    value=byte,
                )

        if self.flow_control == "rts_cts":
            self._flow_control_release(builder)
        if driver is not None:
            builder.annotate("driver", driver, start=fh.start, end=fh.end, signals=(line,))
        builder.annotate("bitorder", "lsb", start=fh.start, end=fh.end, signals=(line,))
        return fh
