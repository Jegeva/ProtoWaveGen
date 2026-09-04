# NES gamepad

Back to [usage overview](../USAGE.md).

`type: "nes_gamepad"` — `NesGamepad`, `protocols/nes_gamepad.py`. NES
controller's 4021 shift-register interface: the host pulses `latch` to
snapshot button states in parallel, then clocks them out serially — one
bit per `clock` pulse on `data`, active-low (`0` = pressed). The first bit
is valid immediately after `latch` falls, before any clock pulse. Order:
A, B, Select, Start, Up, Down, Left, Right. Single controller only (no
Four Score multitap). Plain `DIGITAL` signals, no driver tracking at all
(there's no shared/contested line here to track).

## Constructor params

```json
"params": { "latch_us": 12, "clock_us": 6 }
```

## Operations

- **`read_buttons`** — `buttons: dict[str, bool]` (any subset of
  `A`/`B`/`Select`/`Start`/`Up`/`Down`/`Left`/`Right`; omitted keys default
  to not-pressed). **No `datatype`/floating-marker support at all** — a
  fixed named-boolean structure, deliberately excluded from the
  floating-marker system since "not driven" has no natural meaning for a
  button-pressed boolean the way it does for a data byte.

## Example — `examples/nes_gamepad_basic.json`

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "nes0",
      "type": "nes_gamepad",
      "operations": [
        { "op": "read_buttons", "buttons": { "A": true, "Start": true } }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/nes_gamepad_basic.svg" },
    { "type": "sigrok", "path": "output/nes_gamepad_basic.sr" },
    { "type": "vcd", "path": "output/nes_gamepad_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/nes_gamepad_basic.json
```
