from timingdiagram.model import CaptureBuilder
from timingdiagram.protocols.checksums import crc16_modbus
from timingdiagram.protocols.modbus_rtu import ModbusRtu
from timingdiagram.protocols.uart import UartTransport


def _setup():
    uart = UartTransport("uart0", baudrate=9600)
    modbus = ModbusRtu("modbus0", uart)
    builder = CaptureBuilder(samplerate=96_000)  # bit_period_samples = 10
    uart.register_signals(builder)
    return modbus, uart, builder


def test_read_holding_registers_crc_and_silence():
    modbus, uart, builder = _setup()
    fh = modbus.read_holding_registers(builder, slave=1, start_addr=0, count=10)
    capture = builder.finish()

    frame = [1, 0x03, 0x00, 0x00, 0x00, 0x0A]
    expected_crc = crc16_modbus(frame)
    fields = [a for a in capture.annotations if a.track == "field"]
    labels = [f.label for f in fields]
    assert labels[:6] == ["SLAVE=1", "FN=READ_HOLDING", "ADDR=0x0000", "ADDR=0x0000", "COUNT=10", "COUNT=10"]
    assert labels[6:8] == [f"CRC=0x{expected_crc:04X}"] * 2

    # silence before AND after the frame: fh.start/.end come from UartTransport.send()'s own
    # frame, which only spans the byte stream, not the surrounding silence advances.
    assert fh.start > 0
    assert capture.duration_samples > fh.end


def test_write_single_register():
    modbus, uart, builder = _setup()
    modbus.write_single_register(builder, slave=2, addr=0x10, value=0x1234)
    labels = [a.label for a in builder.finish().annotations if a.track == "field"]
    assert labels[:6] == ["SLAVE=2", "FN=WRITE_SINGLE", "ADDR=0x0010", "ADDR=0x0010", "VALUE=0x1234", "VALUE=0x1234"]


def test_silence_is_roughly_3_5_char_times():
    modbus, uart, builder = _setup()
    modbus.read_holding_registers(builder, slave=1, start_addr=0, count=1)
    capture = builder.finish()

    fields = [a for a in capture.annotations if a.track == "field"]
    fh_start = min(a.start for a in fields)
    # 1 start + 8 data + 1 stop = 10 bit-times/char at 10 samples/bit = 100 samples/char
    expected_silence = round(10 * 10 * 3.5)
    assert fh_start == expected_silence
