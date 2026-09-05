from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal, SignalKind
from .base import (
    DriverTracker,
    TransportProtocol,
    bind_clock_samples,
    bits_of_byte,
    format_byte,
    register_protocol,
)
from .payload import decode_payload_with_floating, group_floating_by_byte

_BROADCAST_ADDRESS = 0x7E


def _odd_parity_bit(byte: int) -> int:
    """The I3C SDR "T-bit": chosen so the total number of 1s across the
    byte plus this bit is odd (the spec's parity/transition-bit
    convention for every push-pull data byte — CCC code/defining bytes,
    private read/write data, and the ENTDAA dynamic-address-assignment
    byte all use it)."""

    return 0 if bin(byte).count("1") % 2 else 1


@register_protocol("i3c")
class I3CBus(TransportProtocol):
    """MIPI I3C bus, SDR (Single Data Rate) mode only — HDR-DDR/BT/TSP are a
    genuinely separate signaling mode and out of scope here, same "don't
    build more than the scoped mode" precedent `jedec_cfi.py` already
    establishes for SPI. IBI, hot-join, and real multi-target arbitration
    contention during ENTDAA are likewise out of scope: this synthesizes one
    intentional scenario with exactly one target responding, the same
    "don't model contention we can't win" precedent `can.py` already
    establishes for its own single-transmitter frames.

    `scl`/`sda` are the same wires I2C uses, and I3C is electrically
    backward-compatible with it — but only the *address phase* of every I3C
    transaction (START/repeated-START/STOP shapes, the 7-bit address + R/W
    byte, and the ACK/NACK bit that follows it) is open-drain like I2C: see
    `_clock_bit`/`_start_condition`/`_stop_condition`, structured the same
    way as `I2CBus`'s own (`_stop_condition` here fixes a real edge-shape
    bug I2C's own copy still has — see its docstring). Once addressing
    completes, an I3C-*native* transfer (CCC code/defining bytes, ENTDAA's
    dynamic-address-assignment byte, private read/write data) switches the
    bus to push-pull: `_clock_bit_pushpull` actively drives both 0 and 1 —
    no pullup release, unlike I2C's `DriverTracker.set("pullup")` pattern —
    and every such byte ends with a T-bit (`_odd_parity_bit`) instead of an
    I2C-style ACK/NACK. `DriverTracker` labels are `"controller"`/`"target"`
    (I3C's own vocabulary) rather than I2C's `"master"`/`"slave"`.

    ENTDAA (`entdaa()`) models exactly one responding target: broadcast CCC
    `0x07` to the reserved broadcast address `0x7E`, a repeated START, the
    target open-drain-clocking out its 48-bit Provisional ID + 8-bit BCR +
    8-bit DCR (64 bits, no ACK/T-bit between those 8 bytes — matches real
    SDR ENTDAA arbitration timing, verified against the vendored decoder's
    own state machine at `tests/custom_decoders/i3c/pd.py`), then the
    controller assigns a dynamic address as one more push-pull byte
    (7-bit address + a fixed low bit) + T-bit.

    Every payload (`data` on `private_write`/`private_read`/CCC operations)
    decodes via `decode_payload_with_floating(..., tristate=True)` since
    `sda` is declared `SignalKind.TRISTATE` — `z`/`Z` floating markers
    resolve pull-high the same way I2C's do, even on a push-pull byte
    (the marker describes authoring intent, not an actual per-bit pull
    network — push-pull always drives a concrete level either way).
    """

    def __init__(self, node_id: str, *, clock_hz: int, operations: list[dict] | None = None):
        super().__init__(node_id, operations)
        self.clock_hz = clock_hz
        self._samples_per_half_bit: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        self._samples_per_half_bit = bind_clock_samples(samplerate, self.clock_hz, hz_label="clock_hz")

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

    # -- bit/condition primitives -------------------------------------------

    def _clock_bit(self, builder: CaptureBuilder, level: int, owner: str) -> None:
        """Open-drain bit, identical shape to `I2CBus._clock_bit` (minus the
        `floating` param — only used for the address phase and the raw
        ENTDAA ID/BCR/DCR bits here, neither of which needs it in v1):
        SDA changes while SCL is low, both held stable while SCL is high
        (when a receiver samples it)."""

        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        builder.set_level(scl, 0)
        self._scl_driver.set("controller")
        builder.set_level(sda, level)
        self._sda_driver.set(owner if level == 0 else "pullup")
        builder.advance(shb)
        builder.set_level(scl, 1)
        self._scl_driver.set("pullup")
        builder.advance(shb)

    def _clock_bit_pushpull(
        self, builder: CaptureBuilder, level: int, owner: str, floating: bool = False
    ) -> None:
        """Push-pull bit: same SCL shape as `_clock_bit`, but SDA is
        *actively driven* to both 0 and 1 by `owner` — no pullup release,
        since I3C's native data phase never floats the line. `floating`
        (from `decode_payload_with_floating`) still labels the driver
        annotation `"floating"` when the config author explicitly marked
        this bit as "not really the point, just resolving a marker",
        matching `_clock_bit`'s own floating convention."""

        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        builder.set_level(scl, 0)
        self._scl_driver.set("controller")
        builder.set_level(sda, level)
        self._sda_driver.set("floating" if floating else owner)
        builder.advance(shb)
        builder.set_level(scl, 1)
        self._scl_driver.set("pullup")
        builder.advance(shb)

    def _start_condition(self, builder: CaptureBuilder) -> None:
        """Same real START shape as I2C's (SDA falls while SCL is high), but
        deliberately *more* unconditional than `I2CBus._start_condition`:
        every call here first brings SCL low (a no-op-but-for-the-advance
        when it's already low), *then* ensures SDA is idle-high, *then*
        raises SCL back up before presenting the actual START edge.

        I2C's own version only takes that "bring SCL low first" detour in
        one specific entry state (SDA already held low by an immediately
        preceding ACK). That's not enough here: the vendored third-party
        I3C decoder's SDR state machine (`tests/custom_decoders/i3c/pd.py`,
        `mStateSdrDecode_state == 2`) only finalizes a byte's 9th bit
        (ACK/NACK or T-bit) and returns to its "waiting for a condition"
        state on a *falling* SCL edge — it has no START/STOP handling at
        all while still parked in that state. Since every one of this
        bus's bit primitives (`_clock_bit`/`_clock_bit_pushpull`) leaves
        SCL held *high* afterward (so a receiver can sample it), a repeated
        START issued right after any 9th bit — regardless of whether that
        bit resolved to SDA 0 or 1 — needs an explicit SCL-low pulse first
        or the decoder never sees the START at all (confirmed empirically:
        omitting this collapsed 4 of 5 example-config transactions' START/
        STOP pairs into nothing, see this method's stop-condition sibling
        for the STOP-side half of the same fix). The very first START of a
        whole capture picks up one harmless extra SCL low->high wiggle from
        this too (the decoder ignores clock edges while idle/state 0) —
        an acceptable trade for correctness everywhere else, and arguably
        no less realistic than a controller idling its clock briefly before
        actually addressing the bus.
        """

        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        with builder.frame() as fh:
            if builder.level_of(scl) == 1:
                builder.set_level(scl, 0)
                self._scl_driver.set("controller")
                builder.advance(shb)
            if builder.level_of(sda) == 0:
                builder.set_level(sda, 1)
                self._sda_driver.set("pullup")
                builder.advance(shb)
            builder.set_level(scl, 1)
            self._scl_driver.set("pullup")
            builder.advance(shb)
            builder.set_level(sda, 0)  # SDA falls while SCL high == START
            self._sda_driver.set("controller")
            builder.advance(shb)
            builder.set_level(scl, 0)  # controller takes the clock low to begin
            self._scl_driver.set("controller")
            builder.advance(shb)
        builder.annotate("field", "start-condition", start=fh.start, end=fh.end, signals=(sda, scl))

    def _stop_condition(self, builder: CaptureBuilder) -> None:
        """Same real STOP shape as I2C's (SDA rises while SCL is high), but
        *not* copied from `I2CBus._stop_condition` verbatim, and — like
        `_start_condition` above — unconditional about bringing SCL low
        first rather than only in specific entry states.

        I2C's own `_stop_condition` has a known, documented-but-unfixed bug
        (see its own docstring/CLAUDE.md entry): called right after a
        NACK'd/high-ending bit (SCL already high, SDA already high), its
        "make sure SDA is idle-low first" step raises... no, *lowers* SDA
        while SCL is still high, which is itself a spurious START-shaped
        edge immediately before the real STOP. Simply guarding that step
        behind "bring SCL low first" (mirroring `_start_condition`'s fix)
        isn't sufficient here either, for the same reason explained there:
        the vendored I3C decoder needs an explicit SCL falling edge before
        *every* STOP, not just the ones where SDA happens to need moving,
        to close out the preceding byte's 9th-bit state. So every call
        here unconditionally lowers SCL first, then ensures SDA is
        idle-low, then raises SCL, then presents the real STOP edge.
        """

        scl, sda = self.sig("scl"), self.sig("sda")
        shb = self._samples_per_half_bit
        with builder.frame() as fh:
            if builder.level_of(scl) == 1:
                builder.set_level(scl, 0)
                self._scl_driver.set("controller")
                builder.advance(shb)
            if builder.level_of(sda) == 1:
                builder.set_level(sda, 0)
                self._sda_driver.set("controller")
                builder.advance(shb)
            builder.set_level(scl, 1)
            self._scl_driver.set("pullup")
            builder.advance(shb)
            builder.set_level(sda, 1)  # SDA rises while SCL high == STOP
            self._sda_driver.set("pullup")
            builder.advance(shb)
        builder.annotate("field", "stop-condition", start=fh.start, end=fh.end, signals=(sda, scl))

    def _transfer_address_byte(
        self, builder: CaptureBuilder, byte: int, *, display_label: str, ack: bool = True
    ) -> FrameHandle:
        """Open-drain address-phase byte (7-bit address/broadcast-address +
        R/W) followed by a plain open-drain ACK/NACK bit — electrically
        identical to I2C's own address byte, used for both a specific
        target's address and the `0x7E` broadcast address (CCC header,
        ENTDAA header)."""

        with builder.frame() as fh:
            for bit in bits_of_byte(byte):
                self._clock_bit(builder, bit, "controller")
            self._clock_bit(builder, 0 if ack else 1, "target")
        sda = self.sig("sda")
        builder.annotate("unit", "address", start=fh.start, end=fh.end, signals=(sda,))
        builder.annotate(
            "field", display_label, start=fh.start, end=fh.end, signals=(sda,), value=byte, ack=ack,
        )
        return fh

    def _transfer_byte_pushpull(
        self,
        builder: CaptureBuilder,
        byte: int,
        *,
        sender: str,
        unit_label: str,
        display_label: str,
        floating_bits: frozenset[int] = frozenset(),
    ) -> FrameHandle:
        """Push-pull data-phase byte + trailing T-bit — the I3C-native
        analog of `I2CBus._transfer_byte`, minus ACK/NACK: `sender` (always
        `"controller"` for a write-direction byte, `"target"` for a
        read-direction one) actively drives all 9 bits, no pullup release
        at any point."""

        t = _odd_parity_bit(byte)
        with builder.frame() as fh:
            for bit_index, bit in enumerate(bits_of_byte(byte)):
                self._clock_bit_pushpull(builder, bit, sender, floating=bit_index in floating_bits)
            self._clock_bit_pushpull(builder, t, sender)
        sda = self.sig("sda")
        builder.annotate("unit", unit_label, start=fh.start, end=fh.end, signals=(sda,))
        builder.annotate(
            "field", display_label, start=fh.start, end=fh.end, signals=(sda,), value=byte, tbit=t,
        )
        return fh

    def _transfer_byte_raw_od(
        self, builder: CaptureBuilder, byte: int, *, sender: str, unit_label: str, display_label: str
    ) -> FrameHandle:
        """Open-drain byte with *no* trailing 9th bit at all — only used for
        ENTDAA's 8 raw Provisional-ID/BCR/DCR bytes, which real SDR ENTDAA
        clocks as one unbroken 64-bit run (see the vendored decoder's
        `mentdaa_detector == 5` state, which reads exactly 8 rising clock
        edges per byte with no ACK/T-bit phase in between)."""

        with builder.frame() as fh:
            for bit in bits_of_byte(byte):
                self._clock_bit(builder, bit, sender)
        sda = self.sig("sda")
        builder.annotate("unit", unit_label, start=fh.start, end=fh.end, signals=(sda,))
        builder.annotate("field", display_label, start=fh.start, end=fh.end, signals=(sda,), value=byte)
        return fh

    # -- public operations (JSON `op` targets) ------------------------------

    def entdaa(self, builder: CaptureBuilder, *, targets: list[dict]) -> FrameHandle:
        """Dynamic Address Assignment: broadcast CCC `0x07` to `0x7E`, a
        repeated START, then exactly one responding target (v1's deliberate
        scope limit — no multi-target arbitration contention modeled, same
        precedent as `can.py` never modeling real bus-arbitration loss)
        open-drain-clocks its 48-bit Provisional ID + 8-bit BCR + 8-bit DCR
        (64 bits, no ACK/T-bit between bytes), and the controller assigns
        it a dynamic address as one push-pull byte + T-bit.

        `targets`: exactly one `{"pid": <48-bit int>, "bcr": <0-255>,
        "dcr": <0-255>, "dynamic_address": <0-0x7F>}`.
        """

        if len(targets) != 1:
            raise ValueError(
                f"entdaa: v1 models exactly one responding target, got {len(targets)}"
            )
        target = targets[0]
        pid, bcr, dcr, dynamic_address = target["pid"], target["bcr"], target["dcr"], target["dynamic_address"]
        if not (0 <= pid < (1 << 48)):
            raise ValueError(f"entdaa: pid {pid} does not fit in 48 bits")
        if not (0 <= bcr <= 0xFF):
            raise ValueError(f"entdaa: bcr {bcr} does not fit in a byte")
        if not (0 <= dcr <= 0xFF):
            raise ValueError(f"entdaa: dcr {dcr} does not fit in a byte")
        if not (0 <= dynamic_address < 0x80) or dynamic_address == _BROADCAST_ADDRESS:
            raise ValueError(f"entdaa: dynamic_address {dynamic_address} is not a valid 7-bit target address")

        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._transfer_address_byte(
                builder, (_BROADCAST_ADDRESS << 1) | 0, display_label="ENTDAA (0x7E W)",
            )
            self._transfer_byte_pushpull(
                builder, 0x07, sender="controller", unit_label="ccc", display_label="CCC=0x07 ENTDAA",
            )
            self._start_condition(builder)  # repeated START, switch to the ENTDAA read header
            self._transfer_address_byte(
                builder, (_BROADCAST_ADDRESS << 1) | 1, display_label="ENTDAA (0x7E R)",
            )
            pid_bytes = pid.to_bytes(6, "big")
            for i, byte in enumerate(pid_bytes):
                self._transfer_byte_raw_od(
                    builder, byte, sender="target", unit_label="entdaa",
                    display_label=f"PID[{i}]={format_byte(byte)}",
                )
            self._transfer_byte_raw_od(
                builder, bcr, sender="target", unit_label="entdaa", display_label=f"BCR={format_byte(bcr)}",
            )
            self._transfer_byte_raw_od(
                builder, dcr, sender="target", unit_label="entdaa", display_label=f"DCR={format_byte(dcr)}",
            )
            da_byte = (dynamic_address & 0x7F) << 1
            self._transfer_byte_pushpull(
                builder, da_byte, sender="controller", unit_label="entdaa-assign",
                display_label=f"Assign DA=0x{dynamic_address:02X}",
            )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def broadcast_ccc(
        self, builder: CaptureBuilder, *, code: int, data=None, datatype: str = "bytes",
    ) -> FrameHandle:
        """Broadcast CCC (code `0x00`-`0x7F`) sent to the reserved broadcast
        address `0x7E`: address byte (open-drain), then the CCC code and any
        defining bytes (`data`) as push-pull bytes + T-bit each, controller-
        driven throughout (a broadcast is always controller-to-targets)."""

        if not (0x00 <= code <= 0x7F):
            raise ValueError(f"broadcast_ccc: code 0x{code:02X} out of range (0x00-0x7F)")
        payload = decode_payload_with_floating(data if data is not None else [], datatype, tristate=True)
        values = payload.values
        floating_by_byte = group_floating_by_byte(payload.floating)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._transfer_address_byte(
                builder, (_BROADCAST_ADDRESS << 1) | 0, display_label="CCC (0x7E W)",
            )
            self._transfer_byte_pushpull(
                builder, code, sender="controller", unit_label="ccc", display_label=f"CCC=0x{code:02X}",
            )
            for i, byte in enumerate(values):
                self._transfer_byte_pushpull(
                    builder, byte, sender="controller", unit_label="data", display_label=format_byte(byte),
                    floating_bits=floating_by_byte.get(i, frozenset()),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def direct_ccc(
        self, builder: CaptureBuilder, *, address: int, code: int, data=None, datatype: str = "bytes",
        read: bool = False,
    ) -> FrameHandle:
        """Direct CCC (code `0x80`-`0xFE`): the CCC code is still announced
        broadcast to `0x7E` first, then a repeated START switches to the
        specific target's own address (`read` selects direction) before the
        defining/data bytes — the standard I3C direct-CCC framing. Data
        bytes are push-pull + T-bit, driven by whichever side matches
        `read` (`"target"` for a read reply, `"controller"` for a write)."""

        if not (0x80 <= code <= 0xFE):
            raise ValueError(f"direct_ccc: code 0x{code:02X} out of range (0x80-0xFE)")
        if not (0 <= address < 0x80) or address == _BROADCAST_ADDRESS:
            raise ValueError(f"direct_ccc: address {address} is not a valid 7-bit target address")
        payload = decode_payload_with_floating(data if data is not None else [], datatype, tristate=True)
        values = payload.values
        floating_by_byte = group_floating_by_byte(payload.floating)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._transfer_address_byte(
                builder, (_BROADCAST_ADDRESS << 1) | 0, display_label="CCC (0x7E W)",
            )
            self._transfer_byte_pushpull(
                builder, code, sender="controller", unit_label="ccc", display_label=f"CCC=0x{code:02X}",
            )
            self._start_condition(builder)  # repeated START, switch to the specific target
            direction = "R" if read else "W"
            self._transfer_address_byte(
                builder, (address << 1) | (1 if read else 0),
                display_label=f"ADDR=0x{address:02X} {direction}",
            )
            sender = "target" if read else "controller"
            for i, byte in enumerate(values):
                self._transfer_byte_pushpull(
                    builder, byte, sender=sender, unit_label="data", display_label=format_byte(byte),
                    floating_bits=floating_by_byte.get(i, frozenset()),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def private_write(
        self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes",
    ) -> FrameHandle:
        """I3C-native private write: open-drain address phase, then every
        data byte push-pull + T-bit, controller-driven throughout."""

        if not (0 <= address < 0x80) or address == _BROADCAST_ADDRESS:
            raise ValueError(f"private_write: address {address} is not a valid 7-bit target address")
        payload = decode_payload_with_floating(data, datatype, tristate=True)
        values = payload.values
        floating_by_byte = group_floating_by_byte(payload.floating)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._transfer_address_byte(builder, (address << 1) | 0, display_label=f"ADDR=0x{address:02X} W")
            for i, byte in enumerate(values):
                self._transfer_byte_pushpull(
                    builder, byte, sender="controller", unit_label="data", display_label=format_byte(byte),
                    floating_bits=floating_by_byte.get(i, frozenset()),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh

    def private_read(
        self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes",
    ) -> FrameHandle:
        """I3C-native private read: open-drain address phase (R/W=1), then
        every data byte push-pull + T-bit, target-driven throughout (`data`
        is the synthesized response, same convention as `I2CBus.read`)."""

        if not (0 <= address < 0x80) or address == _BROADCAST_ADDRESS:
            raise ValueError(f"private_read: address {address} is not a valid 7-bit target address")
        payload = decode_payload_with_floating(data, datatype, tristate=True)
        values = payload.values
        floating_by_byte = group_floating_by_byte(payload.floating)
        self._ensure_bound(builder)
        self._scl_driver = DriverTracker(builder, self.sig("scl"))
        self._sda_driver = DriverTracker(builder, self.sig("sda"))

        with builder.frame() as fh:
            self._start_condition(builder)
            self._transfer_address_byte(builder, (address << 1) | 1, display_label=f"ADDR=0x{address:02X} R")
            for i, byte in enumerate(values):
                self._transfer_byte_pushpull(
                    builder, byte, sender="target", unit_label="data", display_label=format_byte(byte),
                    floating_bits=floating_by_byte.get(i, frozenset()),
                )
            self._stop_condition(builder)

        self._scl_driver.close()
        self._sda_driver.close()
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(self.sig("sda"),))
        return fh
