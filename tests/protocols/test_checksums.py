from timingdiagram.protocols.checksums import crc8_1wire, crc16_modbus


def test_crc16_modbus_self_check_is_zero():
    # Appending a linear CRC's own value (low byte first, matching how
    # Modbus puts it on the wire) to the message and recomputing must yield
    # zero — this holds regardless of the exact reference byte string, so it
    # doesn't depend on a possibly-misremembered external test vector.
    message = [0x01, 0x03, 0x00, 0x00, 0x00, 0x0A]
    crc = crc16_modbus(message)
    assert 0 <= crc <= 0xFFFF
    full = message + [crc & 0xFF, (crc >> 8) & 0xFF]
    assert crc16_modbus(full) == 0x0000


def test_crc16_modbus_sensitive_to_input():
    assert crc16_modbus([0x01, 0x03]) != crc16_modbus([0x01, 0x04])
    assert crc16_modbus([]) == 0xFFFF  # init value, unmodified


def test_crc8_1wire_self_check_is_zero():
    data = [0x02, 0x1C, 0xB8, 0x01, 0x00, 0x00, 0x00]
    crc = crc8_1wire(data)
    assert 0 <= crc <= 0xFF
    assert crc8_1wire(data + [crc]) == 0x00


def test_crc8_1wire_sensitive_to_input():
    assert crc8_1wire([0x01, 0x02]) != crc8_1wire([0x01, 0x03])
    assert crc8_1wire([]) == 0x00
