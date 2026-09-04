"""Shared CRC helpers used by several stacked protocols (1-Wire devices,
Modbus RTU) — kept generic/standalone rather than duplicated per protocol
module or tied to one transport."""

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
