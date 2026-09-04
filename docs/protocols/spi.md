# SPI / QSPI / OctoSPI

Back to [usage overview](../USAGE.md).

`type: "spi"` — `SpiBus`, `protocols/spi.py`. One class for classic SPI
(`width=1`), QSPI (`width=4`), or OctoSPI (`width=8`) — the only real
difference is how many parallel data lines a clock edge carries.
Push-pull bus (unlike I2C), so `mosi`/`miso`/`ioN` are plain `DIGITAL` —
`z`/`Z` floating markers always need `l`/`h` used explicitly instead,
since there's no protocol-defined pull to auto-resolve against.

## Constructor params

```json
"params": { "clock_hz": 1000000, "width": 1, "mode": 0, "bit_order": "msb", "cs_active_low": true }
```

- `clock_hz` (required).
- `width` — `1` (SPI, default), `4` (QSPI), or `8` (OctoSPI).
- `mode` — `0`-`3`, standard CPOL/CPHA convention.
- `bit_order` — `"msb"` (default) or `"lsb"`.
- `cs_active_low` — `true` (default) or `false`.

## Operations

- **`transfer`** — `width=1` only. `mosi=None`, `miso=None`,
  `datatype="bytes"` (shared by both fields), `labels=None`. Full
  floating-marker support on both `mosi` and `miso` independently.
- **`wide_transfer`** — `width>1` only. `data`, `direction="write"|"read"`,
  `datatype="bytes"`. Full floating-marker support.

A mandatory minimum CS-deasserted recovery gap precedes every `transfer`/
`wide_transfer` call — without it, two back-to-back calls are
electrically indistinguishable from one continuous, over-long transfer to
a real decoder.

## Example — `examples/spi_mode0.json`

```json
{
  "samplerate": 8000000,
  "protocols": [
    {
      "id": "spi0",
      "type": "spi",
      "params": { "clock_hz": 1000000, "width": 1, "mode": 0, "bit_order": "msb" },
      "operations": [
        { "op": "transfer", "mosi": [155, 1, 2], "miso": [0, 0, 200] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/spi_mode0.svg" },
    { "type": "sigrok", "path": "output/spi_mode0.sr" },
    { "type": "vcd", "path": "output/spi_mode0.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/spi_mode0.json
```

Mark part of a MOSI byte as floating (needs explicit `l`/`h` — SPI has no
defined pull). `mosi`/`miso` share one `datatype` and must stay the same
length, so overriding one while leaving the other at its original length
needs the other overridden to match too:

```bash
.venv/bin/python -m protowavegen --config examples/spi_mode0.json \
    --data-hex "spi0:0:mosi:9b010h" --data-hex "spi0:0:miso:0000c8"
```

---

## Stacked devices

Every device below needs `"stack_on": "<spi node id>"` (the SPI node must
be `width=1`) and must be declared after that SPI node in the `protocols`
list.

### JEDEC CFI — `type: "jedec_cfi"`

`jedec_cfi.py`. JEDEC manufacturer-ID query plus standard SPI-NOR flash
reads.

Operations: `read_jedec_id(manufacturer_id, memory_type, capacity)` (no
`datatype`), `read(address, data, datatype="bytes")` (plain-decode only).

```json
{
  "samplerate": 8000000,
  "protocols": [
    {
      "id": "spi0",
      "type": "spi",
      "params": { "clock_hz": 1000000, "width": 1, "mode": 0 },
      "operations": []
    },
    {
      "id": "cfi0",
      "type": "jedec_cfi",
      "stack_on": "spi0",
      "operations": [
        { "op": "read_jedec_id", "manufacturer_id": 239, "memory_type": 64, "capacity": 24 },
        { "op": "read", "address": 4660, "data": [222, 173, 190, 239] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/jedec_cfi_basic.svg" },
    { "type": "sigrok", "path": "output/jedec_cfi_basic.sr" },
    { "type": "vcd", "path": "output/jedec_cfi_basic.vcd" }
  ]
}
```

### MAX7219 — `type: "max7219"`

`max7219.py`. Maxim MAX7219 8-digit LED display driver. Each command is
its own `transfer()` call underneath (register byte + data byte).

Operations: `init(intensity=8)`, `set_digit(position, value)`. Neither
takes `datatype`.

```json
{
  "samplerate": 10000000,
  "protocols": [
    { "id": "spi0", "type": "spi", "params": { "clock_hz": 1000000, "width": 1, "mode": 0 }, "operations": [] },
    {
      "id": "disp0", "type": "max7219", "stack_on": "spi0",
      "operations": [
        { "op": "init", "intensity": 8 },
        { "op": "set_digit", "position": 0, "value": 7 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/max7219_basic.svg" },
    { "type": "sigrok", "path": "output/max7219_basic.sr" },
    { "type": "vcd", "path": "output/max7219_basic.vcd" }
  ]
}
```

### SD card, SPI mode — `type: "sd_spi"`

`sd_spi.py`. SD card in SPI mode (v1 command scope). Every command byte
gets a real CRC-7 (`checksums.crc7_sd`).

Operations: `init()` (no `datatype`), `read_block(address, data, datatype="bytes")`
(plain-decode). This is the one example in the repo already demonstrating
the `"hex"` datatype.

```json
{
  "samplerate": 10000000,
  "protocols": [
    { "id": "spi0", "type": "spi", "params": { "clock_hz": 1000000, "width": 1, "mode": 0 }, "operations": [] },
    {
      "id": "sd0", "type": "sd_spi", "stack_on": "spi0",
      "operations": [
        { "op": "init" },
        { "op": "read_block", "address": 4096, "data": "deadbeef", "datatype": "hex" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/sd_spi_basic.svg" },
    { "type": "sigrok", "path": "output/sd_spi_basic.sr" },
    { "type": "vcd", "path": "output/sd_spi_basic.vcd" }
  ]
}
```

### 7-segment shift register — `type: "seven_segment"`

`seven_segment.py`. Generic 74HC595-style serial-in/parallel-out shift
register driving 7-segment digits (one extra `latch` pin beyond plain SPI).

Operations: `set_digits(patterns)` (raw segment-pattern bytes),
`set_digit_values(values)` (decimal digit values, encoded internally).
Neither takes `datatype`.

```json
{
  "samplerate": 10000000,
  "protocols": [
    { "id": "spi0", "type": "spi", "params": { "clock_hz": 1000000, "width": 1, "mode": 0 }, "operations": [] },
    {
      "id": "seg0", "type": "seven_segment", "stack_on": "spi0",
      "operations": [
        { "op": "set_digit_values", "values": [4, 2] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/seven_segment_basic.svg" },
    { "type": "sigrok", "path": "output/seven_segment_basic.sr" },
    { "type": "vcd", "path": "output/seven_segment_basic.vcd" }
  ]
}
```
