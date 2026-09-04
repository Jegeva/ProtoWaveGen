# DALI

Back to [usage overview](../USAGE.md).

`type: "dali"` — `DaliBus`, `protocols/dali.py`. Digital Addressable
Lighting Interface, Manchester encoded (G.E. Thomas convention): bit `1`
= low→high transition at bit-center, bit `0` = high→low. Single logical
`dali` line (the differential current-loop pair collapses to one signal,
same simplification `CanBus` uses) — single transmitter per frame like
CAN, so no open-drain/pull-up concept either. Plain `DIGITAL` — `z`/`Z`
needs `l`/`h` used explicitly.

## Constructor params

```json
"params": { "baudrate": 1200 }
```

- `baudrate` — default `1200` (the DALI spec's ~1200bps, bit period
  ~833us), exposed for flexibility.

## Operations

- **`send_forward_frame`** (controller→ballast) — `DALI_ADDRESS`,
  `command`, `DALI_ADDRESS_datatype="bytes"`, `command_datatype="bytes"`.
  **Note the field is `DALI_ADDRESS`, not `address`** — deliberately
  namespaced so it can be `--data-target`-ed without colliding with
  I2C's own unrelated `address` field (the CLI's target-field set is
  shared across every protocol type). Both fields independently
  floating-marker capable.
- **`send_backward_frame`** (ballast→controller reply) — `answer`,
  `answer_datatype="bytes"`. Floating-marker capable.

## Example — `examples/dali_basic.json`

```json
{
  "samplerate": 12000,
  "protocols": [
    {
      "id": "dali0",
      "type": "dali",
      "operations": [
        { "op": "send_forward_frame", "DALI_ADDRESS": 1, "command": 254 },
        { "op": "send_backward_frame", "answer": 255 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/dali_basic.svg" },
    { "type": "sigrok", "path": "output/dali_basic.sr" },
    { "type": "vcd", "path": "output/dali_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/dali_basic.json
```

Override the forward frame's address with a floating-marked byte — the
CLI automatically writes `DALI_ADDRESS_datatype` (not a bare `datatype`)
here, since DALI's per-field-prefixed naming is resolved from the
operation's real signature:

```bash
.venv/bin/python -m protowavegen --config examples/dali_basic.json \
    --data-hex "dali0:0:DALI_ADDRESS:2h"
```
