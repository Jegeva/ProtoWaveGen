# 1-Wire

Back to [usage overview](../USAGE.md).

`type: "onewire"` — `OneWireBus`, `protocols/onewire.py`. Dallas/Maxim
1-Wire, standard speed: single open-drain `dq` line (`SignalKind.TRISTATE`)
— same "0 = driven low, 1 = pull-up released" semantics as I2C. `z`/`Z`
floating markers auto-resolve.

## Constructor params

No timing params — standard-speed slot/reset timing is fixed.

```json
"operations": [ ... ]
```

## Operations

- **`reset`** — `presence=True`. No payload.
- **`write`** — `data`, `datatype="bytes"`, `labels=None`. Full
  floating-marker support, LSB-first (matching the 1-Wire spec's
  ROM/function-command bit order).
- **`read`** — `data`, `datatype="bytes"`, `labels=None`. `data` is the
  synthesized response the target sends back.

## Example — `examples/onewire_basic.json`

```json
{
  "samplerate": 2000000,
  "protocols": [
    {
      "id": "ow0",
      "type": "onewire",
      "operations": [
        { "op": "reset" },
        { "op": "write", "data": [204, 68] },
        { "op": "read", "data": [40, 1, 79] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/onewire_basic.svg" },
    { "type": "sigrok", "path": "output/onewire_basic.sr" },
    { "type": "vcd", "path": "output/onewire_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/onewire_basic.json
```

---

## Stacked devices

Every device below needs `"stack_on": "<onewire node id>"`, must be
declared after that 1-Wire node, and accepts a common
`"rom_id": [byte, ...]` param (Match-ROM addressing for a multi-drop bus —
omit it for Skip-ROM, the default, single-device-bus addressing).

### DS2408 — `type: "ds2408"`

`ds2408.py`. 1-Wire 8-channel addressable switch. Reads include a real
1-Wire CRC-8 (`checksums.crc8_1wire`).

Operations: `read_pio(state)`, `write_pio(bits)`. **Neither takes
`datatype`** — both plain ints.

```json
{
  "samplerate": 2000000,
  "protocols": [
    { "id": "ow0", "type": "onewire", "operations": [] },
    {
      "id": "ds0", "type": "ds2408", "stack_on": "ow0",
      "operations": [
        { "op": "write_pio", "bits": 240 },
        { "op": "read_pio", "state": 170 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ds2408_basic.svg" },
    { "type": "sigrok", "path": "output/ds2408_basic.sr" },
    { "type": "vcd", "path": "output/ds2408_basic.vcd" }
  ]
}
```

### DS243x — `type: "ds243x"`

`ds243x.py`. 1-Wire EEPROM (e.g. DS2433). `write_memory` does the real
3-transaction sequence (write scratchpad, read-back-to-verify with a
CRC16, copy scratchpad to commit).

Operations: `write_memory(address, data, datatype="bytes")`,
`read_memory(address, data, datatype="bytes")` — plain-decode only.

```json
{
  "samplerate": 2000000,
  "protocols": [
    { "id": "ow0", "type": "onewire", "operations": [] },
    {
      "id": "ee0", "type": "ds243x", "stack_on": "ow0",
      "operations": [
        { "op": "write_memory", "address": 16, "data": [170, 187] },
        { "op": "read_memory", "address": 32, "data": [1, 2, 3] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ds243x_basic.svg" },
    { "type": "sigrok", "path": "output/ds243x_basic.sr" },
    { "type": "vcd", "path": "output/ds243x_basic.vcd" }
  ]
}
```

### DS28EA00 — `type: "ds28ea00"`

`ds28ea00.py`. DS18B20-family digital thermometer (only the temperature
path is modeled, not the sequence-detect/PIO features).

`params`: `conversion_delay_us` (default `750000`, i.e. 750ms — the
example below shortens it to keep the capture small).

Operations: `read_temperature(celsius, th=0, tl=0, config=0x7F)`,
`write_scratchpad(th, tl, config)`. Neither takes `datatype`.

```json
{
  "samplerate": 2000000,
  "protocols": [
    { "id": "ow0", "type": "onewire", "operations": [] },
    {
      "id": "temp0", "type": "ds28ea00", "stack_on": "ow0", "params": { "conversion_delay_us": 1000 },
      "operations": [
        { "op": "read_temperature", "celsius": 23.5 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ds28ea00_basic.svg" },
    { "type": "sigrok", "path": "output/ds28ea00_basic.sr" },
    { "type": "vcd", "path": "output/ds28ea00_basic.vcd" }
  ]
}
```
