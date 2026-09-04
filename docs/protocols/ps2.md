# PS/2

Back to [usage overview](../USAGE.md).

`type: "ps2"` — `Ps2Bus`, `protocols/ps2.py`. Keyboard/mouse interface:
open-collector `clock`+`data` (`SignalKind.TRISTATE`). Device→host
(`send_from_device`): 11 bits, device-generated clock — start(0), 8 data
bits LSB-first, odd parity, stop(1). Host→device (`send_to_host`): host
holds clock low (inhibit), pulls data low as its own start bit, releases
clock; the device generates the remaining clock pulses and a final ACK
bit. Both signals TRISTATE, `z`/`Z` auto-resolves.

## Constructor params

```json
"params": { "clock_hz": 12500, "inhibit_us": 100 }
```

- `clock_hz` (default `12500`), `inhibit_us` (default `100`) —
  representative timing, not tied to one real device.

## Operations

- **`send_from_device`** — `byte`, `datatype="bytes"`. `byte` is a plain
  int (default) or, with a `"text"`/`"hex"`/`"bin"` `datatype`, a
  single-byte payload via `resolve_single_byte` — the floating-marker
  alphabet applies.
- **`send_to_host`** — same shape.

## Example — `examples/ps2_basic.json`

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "ps2_0",
      "type": "ps2",
      "operations": [
        { "op": "send_from_device", "byte": 28 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ps2_basic.svg" },
    { "type": "sigrok", "path": "output/ps2_basic.sr" },
    { "type": "vcd", "path": "output/ps2_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/ps2_basic.json
```

Override the byte with a fully floating value (`z` auto-resolves high —
both signals are TRISTATE):

```bash
.venv/bin/python -m protowavegen --config examples/ps2_basic.json \
    --data-hex "ps2_0:0:byte:zz"
```
