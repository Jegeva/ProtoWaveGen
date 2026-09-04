from .base import (
    DriverTracker,
    Protocol,
    StackedProtocol,
    TransportProtocol,
    get_protocol_class,
    register_protocol,
)

# Importing each module registers its @register_protocol-decorated class.
from . import (  # noqa: F401,E402
    adxl345, can, dali, dmx512, ds1307, ds2408, ds28ea00, ds243x, eeprom_24xx, i2c, jedec_cfi, lin, lm75,
    max7219, microwire, microwire_93xx, mlx90614, modbus_rtu, nes_gamepad, nunchuk, onewire, ps2, sd_spi,
    seven_segment, spi, tca6408a, uart, wiegand,
)

__all__ = [
    "Protocol",
    "TransportProtocol",
    "StackedProtocol",
    "DriverTracker",
    "register_protocol",
    "get_protocol_class",
]
