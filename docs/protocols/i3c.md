# I3C

Back to [usage overview](../USAGE.md).

`type: "i3c"` — `I3CBus`, `protocols/i3c.py`. MIPI I3C, SDR (Single Data
Rate) mode only — HDR-DDR/BT/TSP are a genuinely separate signaling mode
and out of scope, same "don't build more than the scoped mode" precedent
`spi.py`'s JEDEC-CFI-on-SPI already establishes. `scl`/`sda` are the same
wires I2C uses (both `SignalKind.TRISTATE`), and only the *address phase*
of every transaction (START/repeated-START/STOP, the address+R/W byte,
the following ACK/NACK bit) is open-drain like I2C. Once addressing
completes, an I3C-native transfer (CCC code/defining bytes, ENTDAA's
dynamic-address-assignment byte, private read/write data) switches to
**push-pull**: both 0 and 1 are actively driven, never released to a
pull-up, and every such byte ends with a **T-bit** (odd-parity bit)
instead of an I2C-style ACK/NACK. `driver` annotations use I3C's own
vocabulary, `"controller"`/`"target"`, rather than I2C's `"master"`/
`"slave"`.

In scope for v1: ENTDAA (Dynamic Address Assignment, modeling exactly one
responding target — no multi-target arbitration contention, same
"don't simulate contention we can't win" precedent `can.py` already
establishes for its own uncontested frames), broadcast CCCs, direct CCCs,
and I3C-native private read/write. Out of scope: IBI, hot-join, HDR-DDR/
BT/TSP, and real multi-target arbitration.

No mainline sigrok decoder exists for I3C; this bus is instead validated
against a real, actively-maintained third-party one
([xyphro/Sigrok-I3C-decoder](https://github.com/xyphro/Sigrok-I3C-decoder),
GPL-3.0), vendored under `tests/custom_decoders/i3c/` and exercised by
`tests/test_sigrok_roundtrip.py`'s `test_i3c_roundtrips_through_vendored_i3c_decoder`.

## Constructor params

```json
"params": { "clock_hz": 100000 }
```

- `clock_hz` (required) — bus clock speed.

## Operations

- **`entdaa`** — `targets`: a list of exactly one
  `{"pid": <48-bit int>, "bcr": <0-255>, "dcr": <0-255>,
  "dynamic_address": <0-0x7F>}` (v1's single-target scope limit).
  Broadcasts CCC `0x07` to the reserved address `0x7E`, a repeated START,
  the target open-drain-clocking out its 48-bit Provisional ID + 8-bit
  BCR + 8-bit DCR (64 bits, no ACK/T-bit between bytes — matches real SDR
  ENTDAA timing), then the controller assigns the dynamic address as one
  push-pull byte + T-bit.
- **`broadcast_ccc`** — `code` (`0x00`-`0x7F`), `data=None`,
  `datatype="bytes"`. Sends the CCC code and any defining bytes (`data`)
  to the reserved broadcast address `0x7E`, controller-driven throughout.
- **`direct_ccc`** — `address`, `code` (`0x80`-`0xFE`), `data=None`,
  `datatype="bytes"`, `read=False`. The CCC code is still announced
  broadcast to `0x7E` first, then a repeated START switches to the
  specific target's own address (`read` selects direction) before the
  defining/data bytes.
- **`private_write`** — `address`, `data`, `datatype="bytes"`. I3C-native
  write: open-drain address phase, then every data byte push-pull + T-bit,
  controller-driven.
- **`private_read`** — `address`, `data`, `datatype="bytes"`. Same shape,
  but `data` is the synthesized target response (this tool generates
  diagrams, it doesn't sense a real device), target-driven throughout.

`address`/`code` are always plain ints — no `datatype` on them. `data`
has full floating-marker support (`decode_payload_with_floating(...,
tristate=True)`, since `sda` is `SignalKind.TRISTATE`) on every operation
above except `entdaa`.

## Example — `examples/i3c_basic.json`

```json
{
  "samplerate": 4000000,
  "protocols": [
    {
      "id": "i3c0",
      "type": "i3c",
      "params": { "clock_hz": 100000 },
      "operations": [
        {
          "op": "entdaa",
          "targets": [
            { "pid": 20015998343868, "bcr": 16, "dcr": 99, "dynamic_address": 8 }
          ]
        },
        { "op": "broadcast_ccc", "code": 12, "data": [1] },
        { "op": "direct_ccc", "address": 8, "code": 143, "read": true, "data": [0, 0, 0, 0, 0, 0] },
        { "op": "private_write", "address": 8, "data": [222, 173] },
        { "op": "private_read", "address": 8, "data": [190, 239] }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/i3c_basic.svg" },
    { "type": "sigrok", "path": "output/i3c_basic.sr" },
    { "type": "vcd", "path": "output/i3c_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/i3c_basic.json
```

Override the private write's payload from the CLI, marking one byte
floating-high:

```bash
.venv/bin/python -m protowavegen --config examples/i3c_basic.json \
    --data-hex "i3c0:3:data:deh0"
```

To decode a generated `.sr` file with the vendored third-party decoder
yourself (not part of the system `sigrok-cli` install):

```bash
SIGROKDECODE_DIR=tests/custom_decoders sigrok-cli \
    -i output/i3c_basic.sr -P "i3c:scl=i3c0.scl:sda=i3c0.sda" -A i3c
```

Note: that decoder only flushes a queued annotation when it sees a
*following* bus edge (see `tests/test_sigrok_roundtrip.py`'s I3C test
docstring) — so the very last STOP condition of a whole capture (here,
`private_read`'s own) never appears in its output. This is a decoder-side
limitation, not a sign the waveform is wrong; every byte value up to and
including that final STOP still decodes correctly.
