# protowavegen

Synthesizes (does not decode) timing diagrams for embedded protocols from a
JSON scenario description, rendering to SVG (documentation) and to
sigrok-compatible capture files (`.sr`/`.vcd`, importable into
PulseView/sigrok-cli or GTKWave as if a real logic analyzer had captured
them).

28 protocols: UART, I2C, SPI/QSPI/OctoSPI, 1-Wire, CAN, DALI, Wiegand,
PS/2, Microwire, NES gamepad, plus 18 application-layer devices stacked on
those (LIN, Modbus RTU, DMX512, LM75, 24xx EEPROM, DS1307, TCA6408A,
MLX90614, Nunchuk, ADXL345, JEDEC CFI, MAX7219, SD-card-SPI-mode,
7-segment, DS2408, DS243x, DS28EA00, 93xx EEPROM).

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json
```

**[Full usage guide, CLI reference, and per-protocol docs →](docs/USAGE.md)**
