from protowavegen.protocols.checksums import crc7_sd, crc8_1wire, crc16_modbus, pec8_smbus


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


def test_pec8_smbus_self_check_is_zero():
    data = [0xB4, 0x07, 0xB5, 0x12, 0x34]
    pec = pec8_smbus(data)
    assert 0 <= pec <= 0xFF
    assert pec8_smbus(data + [pec]) == 0x00


def test_pec8_smbus_sensitive_to_input():
    assert pec8_smbus([0x01, 0x02]) != pec8_smbus([0x01, 0x03])


def test_crc7_sd_known_value_cmd0():
    # CMD0 with argument 0 is a well-known, widely-published SD command CRC
    # example: command bytes 40 00 00 00 00 -> CRC byte 0x95.
    assert crc7_sd([0x40, 0x00, 0x00, 0x00, 0x00]) == 0x95


def test_crc7_sd_known_value_cmd8():
    # CMD8 with argument 0x1AA: command bytes 48 00 00 01 AA -> CRC byte 0x87.
    assert crc7_sd([0x48, 0x00, 0x00, 0x01, 0xAA]) == 0x87
