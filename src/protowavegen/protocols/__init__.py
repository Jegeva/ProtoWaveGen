from .base import (
    DriverTracker,
    Protocol,
    StackedProtocol,
    TransportProtocol,
    get_protocol_class,
    register_protocol,
    registered_protocols,
)

# Importing each module registers its @register_protocol-decorated class.
from . import (  # noqa: F401,E402
    adxl345, am230x, can, dali, dcf77, dmx512, ds1307, ds2408, ds28ea00, ds243x, eeprom_24xx, em4100, i2c,
    ir_nec, ir_rc5, ir_rc6, jedec_cfi, lin, lm75, max7219, microwire, microwire_93xx, mlx90614, modbus_rtu,
    nes_gamepad, nunchuk, onewire, pca9571, ps2, rtc8564, sd_spi, seven_segment, spi, spiflash, tca6408a,
    tlc5620, uart, usb, wiegand,
)

__all__ = [
    "Protocol",
    "TransportProtocol",
    "StackedProtocol",
    "DriverTracker",
    "register_protocol",
    "get_protocol_class",
    "registered_protocols",
]
