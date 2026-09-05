"""Shared CRC helpers used by several stacked protocols (1-Wire devices,
Modbus RTU, MLX90614's SMBus PEC, SD-card-SPI-mode's command CRC-7) — kept
generic/standalone rather than duplicated per protocol module or tied to
one transport."""

from __future__ import annotations


def crc8_1wire(data: list[int]) -> int:
    """1-Wire family CRC-8 (polynomial 0x31, reflected form 0x8C), as used
    by DS18B20/DS2408/DS243x/etc. scratchpad and ROM checks."""

    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc & 0xFF


def crc16_modbus(data: list[int]) -> int:
    """Modbus RTU CRC-16 (polynomial 0xA001, the reflected form of 0x8005;
    init 0xFFFF; no final XOR). Returned as a 16-bit int — Modbus puts the
    low byte on the wire first."""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def pec8_smbus(data: list[int]) -> int:
    """SMBus Packet Error Code: CRC-8-CCITT, polynomial 0x07, MSB-first,
    not reflected — a different CRC-8 variant from 1-Wire's
    (`crc8_1wire`). Used by the MLX90614 (and any other PEC-checked SMBus
    device)."""

    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc16_x25(data: list[int]) -> int:
    """CRC-16/X-25 (a.k.a. CRC-16/IBM-SDLC, CRC-CCITT reflected): polynomial
    0x1021 (reflected form 0x8408), init 0xFFFF, reflected in/out, final
    complement (XOR 0xFFFF). This is IrLAP's FCS — verified two ways while
    building `irda.py`: (1) it reproduces the Linux kernel's own
    `net/irda/crc.h` `GOOD_FCS` magic-residue value (0xF0B8: running this
    same algorithm's *un-complemented* running register over a real frame's
    bytes *including* its own trailing FCS bytes lands exactly there), and
    (2) a frame built with it round-trips through `tshark`'s real,
    independently-implemented `irlap` dissector (see
    `tests/test_sigrok_roundtrip.py`'s IrDA Wireshark cross-validation
    test). Distinct from `crc16_modbus` above, which uses a different
    polynomial (0xA001 reflected / 0x8005 normal) and no final XOR — the two
    are not interchangeable despite both being reflected 16-bit CRCs."""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


def crc7_sd(data: list[int]) -> int:
    """SD command CRC-7 (polynomial 0x09, MSB-first, not reflected), over
    the 5 command+argument bytes, returned as the final on-the-wire byte
    already including the fixed stop bit (`(crc7 << 1) | 1`).

    Bit-serial reference form (each bit of each byte combined with the
    running register's own top bit) — verified against the two
    universally-published SD command CRC constants: CMD0/arg 0 -> `0x95`,
    CMD8/arg `0x1AA` -> `0x87` (see the tests). The byte-XOR-then-shift
    structure used by `crc8_1wire`/`crc16_modbus`/`pec8_smbus` above does
    *not* reproduce these — this CRC-7 needs the bit-conditioned form."""

    crc = 0
    for byte in data:
        d = byte
        for _ in range(8):
            crc = (crc << 1) & 0xFF
            if (d & 0x80) ^ (crc & 0x80):
                crc = (crc ^ 0x09) & 0xFF
            d = (d << 1) & 0xFF
    return ((crc << 1) | 1) & 0xFF
