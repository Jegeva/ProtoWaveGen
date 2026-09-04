# Wiegand

Back to [usage overview](../USAGE.md).

`type: "wiegand"` — `WiegandBus`, `protocols/wiegand.py`. Access-control
reader interface: two open-collector lines `d0`/`d1` (`SignalKind.TRISTATE`
— same "0 = driven low, 1 = pull-up released" semantics as I2C), idle
high, no clock — each bit is a brief pulse on exactly one line (a `0`
pulses `d0`, a `1` pulses `d1`). Both signals TRISTATE, `z`/`Z` auto-resolves.

## Constructor params

```json
"params": { "pulse_us": 50, "interval_us": 2000 }
```

- `pulse_us` (default `50`), `interval_us` (default `2000`) — representative
  defaults, not tied to any one reader's datasheet.

## Operations

- **`send_bits`** — `bits`, `datatype="bytes"`. `bits` is a plain
  `list[int]` (each element 0/1, default) or, with `datatype="bits"`, a
  flat `0`/`1`/`l/L`/`h/H`/`z/Z` string via `decode_bits_with_floating`
  (no byte-alignment needed — a card frame's bit count usually isn't a
  multiple of 8) — reachable from the CLI via `--data-bits` (e.g.
  `--data-bits "wg0:0:bits:0z1"`), which tags the `"bits"` datatype this
  field expects.
- **`send_card_26bit`** — `facility_code`, `card_number`. Builds the
  standard 26-bit format (leading even parity, 8-bit facility code, 16-bit
  card number, trailing odd parity). **No `datatype` on either field** —
  always plain ints, range-checked (facility_code 0-255, card_number
  0-65535).

## Example — `examples/wiegand_basic.json`

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "wg0",
      "type": "wiegand",
      "operations": [
        { "op": "send_card_26bit", "facility_code": 12, "card_number": 34567 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/wiegand_basic.svg" },
    { "type": "sigrok", "path": "output/wiegand_basic.sr" },
    { "type": "vcd", "path": "output/wiegand_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/wiegand_basic.json
```
