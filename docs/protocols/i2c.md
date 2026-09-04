# I2C

Back to [usage overview](../USAGE.md).

`type: "i2c"` — `I2CBus`, `protocols/i2c.py`. Open-drain SCL/SDA
(`SignalKind.TRISTATE`): level 1 is always the pull-up holding the line
released, never a device driving high; level 0 is always some device
actively sinking it. Both signals are TRISTATE, so `z`/`Z` floating markers
(see [the datatype/floating-marker guide](../USAGE.md#the-floating-bit-marker-system-lhz))
resolve automatically.

## Constructor params

```json
"params": { "clock_hz": 100000, "addr_bits": 7 }
```

- `clock_hz` (required) — bus clock speed.
- `addr_bits` — `7` (default) or `10`. 10-bit addressing uses the standard
  two-header-byte encoding with a repeated START to switch direction on a
  read.

## Operations

- **`write`** — `address`, `data`, `datatype="bytes"`, `nack=False`,
  `labels=None`. Full floating-marker support on `data`.
- **`read`** — `address`, `data`, `datatype="bytes"`, `nack_last=True`,
  `labels=None`. `data` is the synthesized response the slave sends back
  (this tool generates diagrams, it doesn't sense a real device).
- **`write_then_read`** — `address`, `write_data`, `read_data`,
  `datatype="bytes"` (applies to both fields independently),
  `nack_last=True`, `write_labels=None`, `read_labels=None`. The common
  "set a register pointer, then read it back" idiom, as one continuous
  frame (write phase, repeated START, read phase) instead of two separate
  transactions.

`address` is always a plain int — no `datatype` on it.

## Example — `examples/i2c_7bit.json`

```json
{
  "samplerate": 4000000,
  "protocols": [
    {
      "id": "i2c0",
      "type": "i2c",
      "params": { "clock_hz": 100000, "addr_bits": 7 },
      "operations": [
        { "op": "write", "address": 72, "data": [1, 42] },
        { "op": "read", "address": 72, "data": [0, 150] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/i2c_7bit.svg" },
    { "type": "sigrok", "path": "output/i2c_7bit.sr" },
    { "type": "vcd", "path": "output/i2c_7bit.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json
```

Override the write's payload from the CLI, chained with a floating-marker
override on the read (SDA auto-resolves `z` since it's TRISTATE):

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-bin    "i2c0:0:data:0b0000000100101010" \
    --data-string "i2c0:1:data:\xzztoto"
```

---

## Stacked devices

Every device below needs `"stack_on": "<i2c node id>"` and must be
declared after that I2C node in the `protocols` list.

### LM75 — `type: "lm75"`

`lm75.py`. National LM75-style temperature sensor, 7-bit address
`0x48`-`0x4F`. Caches its own register pointer internally, so a second
`read_temperature()` in a row skips the redundant pointer-write — visible
in the example below (only the first call's frame includes the
pointer-set phase).

`params`: `address` (default `0x48`).

Operations: `read_temperature(celsius)`, `write_config(byte)`,
`write_threshold(register, celsius)`. None take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "lm75_0", "type": "lm75", "stack_on": "i2c0", "params": { "address": 72 },
      "operations": [
        { "op": "read_temperature", "celsius": 23.5 },
        { "op": "read_temperature", "celsius": 24.0 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/lm75_basic.svg" },
    { "type": "sigrok", "path": "output/lm75_basic.sr" },
    { "type": "vcd", "path": "output/lm75_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/lm75_basic.json
```

### 24xx EEPROM — `type: "eeprom_24xx"`

`eeprom_24xx.py`. Generic 24xx-series I2C EEPROM; `addr_width` (1 or 2)
picks byte- vs. word-addressing.

`params`: `address` (default `0x50`), `addr_width` (default `1`),
`page_size` (default `16`).

Operations: `write_page(word_addr, values)` (plain list, no `datatype`),
`write_byte(word_addr, value)`, `read_sequential(word_addr, values, datatype="bytes")`
(plain-decode only — `"bin"` and floating markers not yet supported here),
`read_byte(word_addr, value)`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "ee0", "type": "eeprom_24xx", "stack_on": "i2c0", "params": { "address": 80, "addr_width": 1 },
      "operations": [
        { "op": "write_byte", "word_addr": 16, "value": 171 },
        { "op": "read_sequential", "word_addr": 32, "values": [17, 34, 51] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/eeprom_24xx_basic.svg" },
    { "type": "sigrok", "path": "output/eeprom_24xx_basic.sr" },
    { "type": "vcd", "path": "output/eeprom_24xx_basic.vcd" }
  ]
}
```

### DS1307 — `type: "ds1307"`

`ds1307.py`. Dallas DS1307 realtime clock, fixed 7-bit address `0x68` (no
`address` param — it's not configurable on this part).

Operations: `read_datetime(dt)`, `write_datetime(dt)` — `dt` is an
ISO-8601 string (JSON has no native datetime type) or a Python
`datetime`. `read_nvram(addr, values)`, `write_nvram(addr, values)`. None
take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "rtc0", "type": "ds1307", "stack_on": "i2c0",
      "operations": [
        { "op": "read_datetime", "dt": "2026-03-05T14:30:45" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ds1307_basic.svg" },
    { "type": "sigrok", "path": "output/ds1307_basic.sr" },
    { "type": "vcd", "path": "output/ds1307_basic.vcd" }
  ]
}
```

### TCA6408A — `type: "tca6408a"`

`tca6408a.py`. TI TCA6408A 8-bit I2C GPIO expander, 7-bit address
`0x20`-`0x27`.

`params`: `address` (default `0x20`).

Operations: `configure(mask)`, `set_polarity(mask)`, `set_output(bits)`,
`read_inputs(value)`. None take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "gpio0", "type": "tca6408a", "stack_on": "i2c0",
      "operations": [
        { "op": "configure", "mask": 15 },
        { "op": "set_output", "bits": 170 },
        { "op": "read_inputs", "value": 85 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/tca6408a_basic.svg" },
    { "type": "sigrok", "path": "output/tca6408a_basic.sr" },
    { "type": "vcd", "path": "output/tca6408a_basic.vcd" }
  ]
}
```

### MLX90614 — `type: "mlx90614"`

`mlx90614.py`. Melexis MLX90614 infrared thermometer, default address
`0x5A`. Every read transaction ends with a real SMBus PEC-8 checksum byte
(`checksums.pec8_smbus`), computed over the actual bus bytes.

Operations: `read_ambient_temperature(celsius)`,
`read_object_temperature(celsius, source=1)`. None take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "mlx0", "type": "mlx90614", "stack_on": "i2c0",
      "operations": [
        { "op": "read_ambient_temperature", "celsius": 22.0 },
        { "op": "read_object_temperature", "celsius": 36.5, "source": 1 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/mlx90614_basic.svg" },
    { "type": "sigrok", "path": "output/mlx90614_basic.sr" },
    { "type": "vcd", "path": "output/mlx90614_basic.vcd" }
  ]
}
```

### Nunchuk — `type: "nunchuk"`

`nunchuk.py`. Nintendo Wii Nunchuk, fixed 7-bit address `0x52`.

Operations: `init()` (two fixed writes disabling the classic encryption
scheme), `poll(joystick, accel, button_z=False, button_c=False)`. None
take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "nc0", "type": "nunchuk", "stack_on": "i2c0",
      "operations": [
        { "op": "init" },
        { "op": "poll", "joystick": [128, 130], "accel": [300, 310, 320], "button_z": true, "button_c": false }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/nunchuk_basic.svg" },
    { "type": "sigrok", "path": "output/nunchuk_basic.sr" },
    { "type": "vcd", "path": "output/nunchuk_basic.vcd" }
  ]
}
```

### ADXL345 — `type: "adxl345"`

`adxl345.py`. Analog Devices ADXL345 3-axis accelerometer, I2C mode,
7-bit address `0x1D` (or `0x53`, depending on the `ALT ADDRESS` pin).

`params`: `address` (default `0x1D`).

Operations: `enable_measurement()`, `read_acceleration(x, y, z)`. None
take `datatype`.

```json
{
  "samplerate": 4000000,
  "protocols": [
    { "id": "i2c0", "type": "i2c", "params": { "clock_hz": 100000, "addr_bits": 7 }, "operations": [] },
    {
      "id": "acc0", "type": "adxl345", "stack_on": "i2c0",
      "operations": [
        { "op": "enable_measurement" },
        { "op": "read_acceleration", "x": 100, "y": -50, "z": 250 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/adxl345_basic.svg" },
    { "type": "sigrok", "path": "output/adxl345_basic.sr" },
    { "type": "vcd", "path": "output/adxl345_basic.vcd" }
  ]
}
```
