from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle, Signal
from .base import DriverTracker, TransportProtocol, format_byte, register_protocol

_CRC15_POLY = 0x4599


def _crc15(bits: list[int]) -> list[int]:
    """Standard CAN CRC-15 (polynomial 0x4599), computed over the *logical*
    (pre-stuffing) bit sequence from SOF through the end of the data field —
    matches what real CAN controllers compute (stuff bits are inserted after
    CRC calculation, and removed again before a receiver checks it)."""

    crc = 0
    for bit in bits:
        bit_in = bit ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if bit_in:
            crc ^= _CRC15_POLY
    return [(crc >> i) & 1 for i in reversed(range(15))]


def _stuff(bits: list[int], roles: list[str]) -> tuple[list[int], list[str]]:
    """Insert one opposite-polarity bit after 5 consecutive identical bits
    (applies SOF through the CRC field). Returns bits and a parallel role
    list so a stuffed bit's origin (arbitration field, a specific data byte,
    CRC, ...) is still known after insertion — needed to still be able to
    bracket "field" annotations around whole logical bytes even though a
    stuff bit might land in the middle of one."""

    out_bits: list[int] = []
    out_roles: list[str] = []
    run_bit = None
    run_len = 0
    for bit, role in zip(bits, roles):
        out_bits.append(bit)
        out_roles.append(role)
        if bit == run_bit:
            run_len += 1
        else:
            run_bit, run_len = bit, 1
        if run_len == 5:
            stuff_bit = 1 - bit
            out_bits.append(stuff_bit)
            out_roles.append("stuff")
            run_bit, run_len = stuff_bit, 1
    return out_bits, out_roles


@register_protocol("can")
class CanBus(TransportProtocol):
    """Classic CAN (2.0A 11-bit or 2.0B 29-bit extended): single logical
    `can` signal (dominant=0/recessive=1 — the differential CAN_H/CAN_L pair
    collapses to one logical line, same as a logic analyzer's CAN decoder
    treats it).

    Synthesizes one node transmitting a frame uncontested: real multi-node
    bus arbitration (dominant wins when two nodes transmit simultaneously)
    isn't modeled, since there's only ever one transmitter's worth of data
    to generate here. `driver` is `"master"` for the whole frame except the
    ACK slot, which is annotated `"slave"` (a receiving node pulling it
    dominant) — the rest of the bus doesn't get its own open-drain "pullup"
    concept the way I2C does, since a real CAN transceiver actively drives
    recessive too; this single-transmitter model doesn't need to represent
    that distinction beyond marking who's acknowledging.

    Full frame: SOF, arbitration (+SRR/IDE/r1 for extended), control
    (RTR/IDE/r0 + 4-bit DLC), 0-8 data bytes, 15-bit CRC (`_crc15`), CRC
    delimiter, ACK slot + delimiter, 7-bit EOF, 3-bit intermission. Bit
    stuffing (`_stuff`) applies from SOF through the end of the CRC field.
    No CAN FD (no separate arbitration/data-phase bit rates).
    """

    def __init__(
        self, node_id: str, *, bitrate: int, extended: bool = False, operations: list[dict] | None = None
    ):
        super().__init__(node_id, operations)
        self.bitrate = bitrate
        self.extended = extended
        self._bit_samples: int | None = None

    def bind_samplerate(self, samplerate: int) -> None:
        spb = round(samplerate / self.bitrate)
        if spb < 1:
            raise ValueError(
                f"samplerate {samplerate} too low for bitrate {self.bitrate} "
                f"(need at least {self.bitrate} Hz)"
            )
        self._bit_samples = spb

    def _ensure_bound(self, builder: CaptureBuilder) -> None:
        if self._bit_samples is None:
            self.bind_samplerate(builder.samplerate)

    @property
    def bit_period_samples(self) -> int | None:
        return self._bit_samples

    def get_signals(self) -> list[Signal]:
        return [Signal(self.sig("can"), initial_level=1)]  # 1 = recessive idle

    @staticmethod
    def _split_extended_id(identifier: int) -> tuple[int, int]:
        return (identifier >> 18) & 0x7FF, identifier & 0x3FFFF

    def _build_logical_bits(
        self, identifier: int, data: list[int], rtr: bool
    ) -> tuple[list[int], list[str]]:
        bits: list[int] = []
        roles: list[str] = []

        def add(bit: int, role: str) -> None:
            bits.append(bit)
            roles.append(role)

        add(0, "sof")
        if self.extended:
            if not (0 <= identifier < (1 << 29)):
                raise ValueError(f"29-bit identifier {identifier} out of range")
            base_id, ext_id = self._split_extended_id(identifier)
            for i in reversed(range(11)):
                add((base_id >> i) & 1, "id")
            add(1, "srr")
            add(1, "ide")
            for i in reversed(range(18)):
                add((ext_id >> i) & 1, "id")
            add(1 if rtr else 0, "rtr")
            add(0, "r1")
            add(0, "r0")
        else:
            if not (0 <= identifier < 0x800):
                raise ValueError(f"11-bit identifier {identifier} out of range")
            for i in reversed(range(11)):
                add((identifier >> i) & 1, "id")
            add(1 if rtr else 0, "rtr")
            add(0, "ide")
            add(0, "r0")
        dlc = len(data)
        for i in reversed(range(4)):
            add((dlc >> i) & 1, "dlc")
        if not rtr:
            for byte_index, byte in enumerate(data):
                for i in reversed(range(8)):
                    add((byte >> i) & 1, f"data{byte_index}")
        return bits, roles

    def send(
        self, builder: CaptureBuilder, *, identifier: int, data: list[int] | None = None, rtr: bool = False
    ) -> FrameHandle:
        self._ensure_bound(builder)
        data = data or []
        if not (0 <= len(data) <= 8):
            raise ValueError(f"CAN data field is 0-8 bytes, got {len(data)}")

        logical_bits, logical_roles = self._build_logical_bits(identifier, data, rtr)
        crc_bits = _crc15(logical_bits)
        stuffed_bits, stuffed_roles = _stuff(logical_bits + crc_bits, logical_roles + ["crc"] * 15)

        can = self.sig("can")
        tracker = DriverTracker(builder, can)
        id_width = 8 if self.extended else 3
        id_summary = f"ID=0x{identifier:0{id_width}X}" + (" RTR" if rtr else "")
        frame_meta = {"identifier": identifier, "dlc": len(data), "rtr": rtr, "extended": self.extended}

        with builder.frame() as fh:
            current_role = None
            role_start = builder.cursor
            for bit, role in zip(stuffed_bits, stuffed_roles):
                if role != current_role:
                    self._annotate_role(
                        builder, can, current_role, role_start, builder.cursor, id_summary, data, frame_meta
                    )
                    current_role, role_start = role, builder.cursor
                tracker.set("master")
                builder.set_level(can, bit)
                builder.advance(self._bit_samples)
            self._annotate_role(
                builder, can, current_role, role_start, builder.cursor, id_summary, data, frame_meta
            )

            # fixed-form fields after the (possibly stuffed) CRC field: none of these are stuffed.
            tracker.set("master")
            builder.set_level(can, 1)  # CRC delimiter (recessive)
            builder.advance(self._bit_samples)
            tracker.set("slave")
            builder.set_level(can, 0)  # ACK slot: receiver pulls dominant
            builder.advance(self._bit_samples)
            tracker.set("master")
            builder.set_level(can, 1)  # ACK delimiter (recessive)
            builder.advance(self._bit_samples)
            for _ in range(7):  # EOF
                builder.set_level(can, 1)
                builder.advance(self._bit_samples)
            for _ in range(3):  # intermission
                builder.set_level(can, 1)
                builder.advance(self._bit_samples)
        tracker.close()

        # Note: no whole-frame "field"/"unit" annotation here — it would
        # overlap and overpaint the precise per-role ones below (same lesson
        # as SPI's dropped whole-transfer annotation): the identifier's own
        # metadata (dlc/rtr/extended) rides on the "id" role's annotation
        # instead of a separate frame-spanning one.
        builder.annotate("bitorder", "msb", start=fh.start, end=fh.end, signals=(can,))
        return fh

    def _annotate_role(
        self,
        builder: CaptureBuilder,
        can: str,
        role: str | None,
        start: int,
        end: int,
        id_summary: str,
        data: list[int],
        frame_meta: dict,
    ) -> None:
        """Bracket a `field` annotation around one logical role's span (the
        identifier, or a whole data byte) even though a stuff bit may have
        been inserted inside it — the span is still physically contiguous,
        so this just needs the role's start/end in samples."""

        if role is None or start == end:
            return
        if role == "id":
            builder.annotate("field", id_summary, start=start, end=end, signals=(can,), **frame_meta)
        elif role.startswith("data"):
            byte = data[int(role[len("data") :])]
            builder.annotate("unit", "byte", start=start, end=end, signals=(can,))
            builder.annotate("field", format_byte(byte), start=start, end=end, signals=(can,), value=byte)
