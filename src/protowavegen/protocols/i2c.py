from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import DriverTracker, TransportProtocol, decode_payload, format_byte, register_protocol

_VALID_ADDR_BITS = {7, 10}


@register_protocol("i2c")
class I2CBus(TransportProtocol):
    """I2C bus: open-drain SCL/SDA (`SignalKind.TRISTATE` — see `Signal`
    docs). Level 1 on either wire is always the pullup holding it released,
    never a device driving high; level 0 is always some device actively
    sinking it. `driver` annotations reflect exactly that: `"pullup"` for
    every released/high span, `"master"`/`"slave"` for whoever is actually
    pulling low (address/data bytes, and the ACK/NACK bit driven by whichever
    side is receiving).

    Supports 7-bit and 10-bit addressing (10-bit uses the standard two
    header-byte encoding with a repeated START to switch direction for
    reads, per the I2C spec's combined format). Every address byte's `field`
    annotation shows its R/W direction directly (`"ADDR=0x48 W"`/`"...R"`),
    not just the raw address — always visible, not gated behind verbose
    mode. Data bytes show their full value the same way (`format_byte`):
    hex, plus the printable ASCII character when there is one.

    LM75-style application drivers stack on this class: hold a constructed
    `I2CBus` instance and call `.write()`/`.read()`, then annotate the
    returned `FrameHandle`'s range with a decoded semantic (e.g. "temp=23.5C").
    """

    def __init__(
        self,
        node_id: str,
        *,
        clock_hz: int,
        addr_bits: int = 7,
        operations: list[dict] | None = None,
    ):
        super().__init__(node_id, operations)
        if addr_bits not in _VALID_ADDR_BITS:
            raise ValueError(f"addr_bits must be one of {_VALID_ADDR_BITS}, got {addr_bits}")
        self.clock_hz = clock_hz
        self.addr_bits = addr_bits
        self._samples_per_half_bit: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        shb = round(samplerate / (2 * self.clock_hz))
        if shb < 1:
            raise ValueError(
                f"samplerate {samplerate} too low for clock_hz {self.clock_hz} "
                f"(need at least {2 * self.clock_hz} Hz)"
            )
        self._samples_per_half_bit = shb

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._samples_per_half_bit is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return None if self._samples_per_half_bit is None else self._samples_per_half_bit * 2

    def get_signals(self) -> list[Signal]:
        return [
            Signal(self.sig("scl"), kind=SignalKind.TRISTATE, initial_level=1),
            Signal(self.sig("sda"), kind=SignalKind.TRISTATE, initial_level=1),
        ]

    # -- open-drain bit/condition primitives -------------------------------

    def _clock_bit(self, builder: CaptureBuilder, level: int, owner: str) -> None:
        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        # SCL low: SDA changes here (data must be stable before SCL rises)
        builder.set_level(scl, 0)
        self._scl_driver.set("master")
        builder.set_level(sda, level)
        self._sda_driver.set(owner if level == 0 else "pullup")
        builder.advance(shb)
        # SCL high: data held stable, this is when a receiver samples it
        builder.set_level(scl, 1)
        self._scl_driver.set("pullup")
        builder.advance(shb)

    def _start_condition(self, builder: CaptureBuilder) -> None:
        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        with builder.frame() as fh:
            if builder.level_of(scl) == 0:
                builder.set_level(scl, 1)
                self._scl_driver.set("pullup")
                builder.advance(shb)
            if builder.level_of(sda) == 0:
                builder.set_level(sda, 1)
                self._sda_driver.set("pullup")
                builder.advance(shb)
            builder.set_level(sda, 0)  # SDA falls while SCL high == START
            self._sda_driver.set("master")
            builder.advance(shb)
            builder.set_level(scl, 0)  # master takes the clock low to begin
            self._scl_driver.set("master")
            builder.advance(shb)
        builder.annotate("field", "start-condition", start=fh.start, end=fh.end, signals=(sda, scl))

    def _stop_condition(self, builder: CaptureBuilder) -> None:
        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        with builder.frame() as fh:
            if builder.level_of(sda) == 1:
                builder.set_level(sda, 0)
                self._sda_driver.set("master")
                builder.advance(shb)
            builder.set_level(scl, 1)
            self._scl_driver.set("pullup")
            builder.advance(shb)
            builder.set_level(sda, 1)  # SDA rises while SCL high == STOP
            self._sda_driver.set("pullup")
            builder.advance(shb)
        builder.annotate("field", "stop-condition", start=fh.start, end=fh.end, signals=(sda, scl))

    def _transfer_byte(
        self,
        builder: CaptureBuilder,
        byte: int,
        sender: str,
        unit_label: str,
        display_label: str,
        nack: bool = False,
    ) -> FrameHandle:
        """`unit_label` is the stable category (`"address"`/`"data"`) used for
        the `unit` color-bar track; `display_label` is what's actually shown
        on the `field` lane — always the fully-formatted value (e.g.
        `"ADDR=0x48 W"`, `"0x2A '*'"`), not gated behind verbose mode, since
        the value itself is the point of a `field` annotation, not an extra."""

        if not (0 <= byte <= 0xFF):
            raise ValueError(f"byte {byte} does not fit in 8 bits")
        receiver = "slave" if sender == "master" else "master"
        with builder.frame() as fh:
            for i in reversed(range(8)):  # MSB first
                self._clock_bit(builder, (byte >> i) & 1, sender)
            self._clock_bit(builder, 1 if nack else 0, receiver)
        sda = self.sig("sda")
        # address+R/W+ACK (or data+ACK) is I2C's natural "unit" — the SVG
        # writer bar-codes these.
        builder.annotate("unit", unit_label, start=fh.start, end=fh.end, signals=(sda,))
        builder.annotate(
            "field", display_label, start=fh.start, end=fh.end,
            signals=(sda,), value=byte, ack=not nack,
        )
        return fh

    def _address_display_labels(self, address: int, rw: int) -> list[str]:
        """Display labels matching `_address_bytes(address, rw)` byte-for-byte
        — always shows the R/W direction explicitly (this is what makes the
        read/write bit visible without needing verbose mode)."""

        direction = "R" if rw else "W"
        if self.addr_bits == 7:
            return [f"ADDR=0x{address:02x} {direction}"]
        return [f"ADDR=0x{address:03x} {direction}", f"ADDR-LO=0x{address & 0xFF:02x}"]

    def _address_bytes(self, address: int, rw: int) -> list[int]:
        if self.addr_bits == 7:
            if not (0 <= address < 0x80):
                raise ValueError(f"7-bit address {address} out of range")
            return [(address << 1) | rw]
        if not (0 <= address < 0x400):
            raise ValueError(f"10-bit address {address} out of range")
        return [0b11110000 | ((address >> 8) & 0x3) << 1 | rw, address & 0xFF]

    def _send_address_for_write(self, builder: CaptureBuilder, address: int) -> None:
        for byte, display in zip(self._address_bytes(address, rw=0), self._address_display_labels(address, rw=0)):
            self._transfer_byte(builder, byte, sender="master", unit_label="address", display_label=display)

    def _send_address_for_read(self, builder: CaptureBuilder, address: int) -> None:
        """Full address phase for starting a read from scratch: 7-bit is
        one byte; 10-bit is the standard combined-format two-header-byte
        write prelude, a repeated START, then the read header."""

        if self.addr_bits == 7:
            self._transfer_byte(
                builder, self._address_bytes(address, rw=1)[0], sender="master",
                unit_label="address", display_label=self._address_display_labels(address, rw=1)[0],
            )
        else:
            self._send_address_for_write(builder, address)
            self._start_condition(builder)  # repeated START to switch direction
            self._transfer_byte(
                builder, self._address_bytes(address, rw=1)[0], sender="master",
                unit_label="address", display_label=self._address_display_labels(address, rw=1)[0],
            )

    # -- public operations (JSON `op` targets) ------------------------------

    def write(
        self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes", nack: bool = False,
        labels: list[str] | None = None,
    ) -> FrameHandle:
        """`labels`, one per data byte, overrides the default `format_byte`
        display — lets a stacked protocol (LM75, an EEPROM, ...) show what a
        byte means (a register pointer, a decoded value) without a second
        annotation over the same range (same reasoning as `UartTransport
        .send`'s `labels` param)."""

        data = decode_payload(data, datatype)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._send_address_for_write(builder, address)
            for i, byte in enumerate(data):
                is_last = i == len(data) - 1
                label = labels[i] if labels else format_byte(byte)
                self._transfer_byte(
                    builder, byte, sender="master", unit_label="data", display_label=label,
                    nack=(nack and is_last),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def write_then_read(
        self, builder: CaptureBuilder, *, address: int, write_data, read_data,
        datatype: str = "bytes",
        nack_last: bool = True, write_labels: list[str] | None = None, read_labels: list[str] | None = None,
    ) -> FrameHandle:
        """The common "set a register pointer, then read it back" idiom:
        one write phase (no STOP), a repeated START, then a read phase — as
        one continuous I2C frame instead of `write()` and `read()` as two
        separate transactions with a STOP/START between them. Most sensors
        (LM75-style pointer registers, EEPROM random reads, burst reads)
        actually do this rather than a plain STOP-separated pair.
        `write_labels`/`read_labels` work like `write()`'s `labels`.
        `datatype` applies to both `write_data` and `read_data`."""

        write_data = decode_payload(write_data, datatype)
        read_data = decode_payload(read_data, datatype)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._send_address_for_write(builder, address)
            for i, byte in enumerate(write_data):
                label = write_labels[i] if write_labels else format_byte(byte)
                self._transfer_byte(builder, byte, sender="master", unit_label="data", display_label=label)
            self._start_condition(builder)  # repeated START, switch to read
            self._transfer_byte(
                builder, self._address_bytes(address, rw=1)[0], sender="master",
                unit_label="address", display_label=self._address_display_labels(address, rw=1)[0],
            )
            for i, byte in enumerate(read_data):
                is_last = i == len(read_data) - 1
                label = read_labels[i] if read_labels else format_byte(byte)
                self._transfer_byte(
                    builder, byte, sender="slave", unit_label="data", display_label=label,
                    nack=(nack_last and is_last),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def read(
        self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes", nack_last: bool = True,
        labels: list[str] | None = None,
    ) -> FrameHandle:
        data = decode_payload(data, datatype)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._send_address_for_read(builder, address)
            for i, byte in enumerate(data):
                is_last = i == len(data) - 1
                label = labels[i] if labels else format_byte(byte)
                self._transfer_byte(
                    builder, byte, sender="slave", unit_label="data", display_label=label,
                    nack=(nack_last and is_last),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh
