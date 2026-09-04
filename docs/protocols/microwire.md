# Microwire

Back to [usage overview](../USAGE.md).

`type: "microwire"` — `MicrowireBus`, `protocols/microwire.py`. 3-wire
half-duplex synchronous bus: `clk`, `cs` (**active-high** — the opposite
of SPI's usual convention), `di`, `do`. No CPOL/CPHA modes: clock idles
low, data changes on the falling edge, sampled on the rising edge — one
fixed timing. `di`/`do` are plain `DIGITAL` — `z`/`Z` floating markers
need `l`/`h` used explicitly.

## Constructor params

```json
"params": { "clock_hz": 1000000 }
```

- `clock_hz` (required).

## Operations

- **`transfer`** — `mosi_bits`, `read_bits=None`, `datatype="bytes"`,
  `labels=None`. `mosi_bits`/`read_bits` are a plain `list[int]` (each
  element 0/1, default) or, with `datatype="bits"`, a flat
  `0`/`1`/`l/L`/`h/H`/`z/Z` string — no byte-alignment requirement, since
  Microwire's opcode+address bit strings aren't byte-multiples. Clocks
  `len(mosi_bits)` bits out on `di` (MSB-first), then `read_bits` more
  cycles reading back whatever's supplied on `do`.

## Example — `examples/microwire_basic.json`

```json
{
  "samplerate": 10000000,
  "protocols": [
    {
      "id": "mw0",
      "type": "microwire",
      "params": { "clock_hz": 1000000 },
      "operations": [
        {
          "op": "transfer",
          "mosi_bits": "11000010",
          "read_bits": "0101101001011010",
          "datatype": "bits",
          "labels": ["CMD=0xC2"]
        }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/microwire_basic.svg" },
    { "type": "sigrok", "path": "output/microwire_basic.sr" },
    { "type": "vcd", "path": "output/microwire_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/microwire_basic.json
```

Mark part of the command bits as floating (needs explicit `l`/`h` —
Microwire has no defined pull), via the `--data-bits` flag, which tags
the `"bits"` datatype `transfer()` expects for its bit-list fields:

```bash
.venv/bin/python -m protowavegen --config examples/microwire_basic.json \
    --data-bits "mw0:0:mosi_bits:1100hh10"
```

---

## Stacked devices

### 93xx-series EEPROM — `type: "microwire_93xx"`

`microwire_93xx.py`. 93xx-series Microwire EEPROM (93C46-style).
Needs `"stack_on": "<microwire node id>"`.

`params`: `addr_bits` (`6`, `8`, or `9`, default `6` — fixed at
construction, matches the specific part's address width),
`busy_delay_us` (default `5000`).

Operations: `ewen()` (erase/write enable), `ewds()` (erase/write disable),
`read(address, value)`, `write(address, value)` (auto-issues `ewen` first
if not already enabled). None take `datatype` — all plain ints.

```json
{
  "samplerate": 10000000,
  "protocols": [
    { "id": "mw0", "type": "microwire", "params": { "clock_hz": 1000000 }, "operations": [] },
    {
      "id": "ee0", "type": "microwire_93xx", "stack_on": "mw0", "params": { "addr_bits": 6 },
      "operations": [
        { "op": "write", "address": 1, "value": 4660 },
        { "op": "read", "address": 5, "value": 43981 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/microwire_93xx_basic.svg" },
    { "type": "sigrok", "path": "output/microwire_93xx_basic.sr" },
    { "type": "vcd", "path": "output/microwire_93xx_basic.vcd" }
  ]
}
```
