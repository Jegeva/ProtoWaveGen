# CAN

Back to [usage overview](../USAGE.md).

`type: "can"` — `CanBus`, `protocols/can.py`. Classic CAN (2.0A 11-bit or
2.0B 29-bit extended). Single logical `can` signal (dominant=0/recessive=1
— the differential CAN_H/CAN_L pair collapses to one logical line, same
as a logic analyzer's CAN decoder treats it). Real CRC-15 and bit-stuffing.
Synthesizes one node transmitting a frame uncontested — real multi-node
bus arbitration isn't modeled. Plain `DIGITAL` — `z`/`Z` on `data` needs
`l`/`h` used explicitly.

## Constructor params

```json
"params": { "bitrate": 500000, "extended": false }
```

- `bitrate` (required).
- `extended` — `false` (default, 11-bit ID) or `true` (29-bit ID).

## Operations

- **`send`** — `identifier`, `data=None`, `datatype="bytes"`,
  `rtr=False`. Only `data` (0-8 bytes) is floating-marker capable;
  `identifier` and `rtr` are always plain values, no `datatype`.

## Example — `examples/can_basic.json`

```json
{
  "samplerate": 8000000,
  "protocols": [
    {
      "id": "can0",
      "type": "can",
      "params": { "bitrate": 500000 },
      "operations": [
        { "op": "send", "identifier": 291, "data": [222, 173, 190, 239] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/can_basic.svg" },
    { "type": "sigrok", "path": "output/can_basic.sr" },
    { "type": "vcd", "path": "output/can_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/can_basic.json
```

Override the data payload from the CLI, marking one byte floating-high:

```bash
.venv/bin/python -m protowavegen --config examples/can_basic.json \
    --data-hex "can0:0:data:deh0"
```
