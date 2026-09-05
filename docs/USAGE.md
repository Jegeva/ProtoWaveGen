# protowavegen usage

`protowavegen` *synthesizes* (does not decode) timing diagrams for embedded
protocols from a JSON scenario description, rendering the result to SVG
(documentation) and to sigrok-compatible capture files (`.sr` and `.vcd`,
importable into PulseView/sigrok-cli or GTKWave as if a real logic analyzer
had captured it).

This page covers install, the CLI, the JSON config shape, and the payload
datatype/floating-marker system shared by every protocol. Each protocol's
own operations and example configs are documented separately, one page per
transport family:

- [I2C](protocols/i2c.md) — plus LM75, 24xx EEPROM, DS1307, TCA6408A,
  MLX90614, Nunchuk, ADXL345
- [I3C](protocols/i3c.md) — ENTDAA, broadcast/direct CCCs, private
  read/write
- [SPI/QSPI/OctoSPI](protocols/spi.md) — plus JEDEC CFI, MAX7219,
  SD-card-SPI-mode, 7-segment shift register
- [UART](protocols/uart.md) — plus LIN, Modbus RTU, DMX512
- [1-Wire](protocols/onewire.md) — plus DS2408, DS243x, DS28EA00
- [Microwire](protocols/microwire.md) — plus 93xx-series EEPROM
- [CAN](protocols/can.md)
- [DALI](protocols/dali.md)
- [Wiegand](protocols/wiegand.md)
- [PS/2](protocols/ps2.md)
- [NES gamepad](protocols/nes_gamepad.md)

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This installs `protowavegen` in editable mode plus `pytest`. A
`protowavegen` console command becomes available too (via the package's
`[project.scripts]` entry point), but every example below uses
`.venv/bin/python -m protowavegen`, which works without activating the
virtualenv first.

## Quick start

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --format svg --format sigrok --format vcd
```

Writes `capture.svg`/`capture.sr`/`capture.vcd` to `./output/` (the default
when `--format` is given without `--output-dir`). Every example config under
`examples/` already declares its own `outputs[]`, so running it with just
`--config` (no `--format`) writes to the paths the config itself names:

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json
```

## The JSON config shape

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

- **`samplerate`** (Hz) — shared by every signal in the capture. Must be
  high enough to represent every protocol node's own clock/baud rate
  (each node validates this at bind time and raises a clear error naming
  the minimum required Hz if it's too low).
- **`protocols`** — a list of nodes. Each has an `id` (referenced by
  `--data-target` and by `stack_on` — see below), a `type` (the registered
  protocol name — `"i2c"`, `"spi"`, `"lm75"`, ...), an optional `params`
  object (constructor kwargs — clock speed, addressing mode, etc.), and an
  `operations` list replayed in order. A stacked protocol (an
  application-layer device like `lm75` or `jedec_cfi`) also needs
  `"stack_on": "<transport id>"`, and the transport it stacks on must be
  declared earlier in the list.
- **`operations`** — each entry is `{"op": "<method name>", ...kwargs}`;
  the kwargs become that protocol class's operation method's arguments
  directly. Every protocol page linked above lists its exact operations.
- **`outputs`** — a list of `{"type": "svg"|"sigrok"|"vcd", "path": "..."}`.
  Overridden entirely by `--format` on the CLI, or relocated (same
  filenames, new directory) by `--output-dir` alone.

## CLI reference

```
usage: protowavegen [-h] --config CONFIG [--output-dir OUTPUT_DIR]
                    [--samplerate SAMPLERATE] [--format {svg,sigrok,vcd}]
                    [--unit-bits UNIT_BITS] [--svg-verbose]
                    [--data-hex [TARGET:]HEX] [--data-string [TARGET:]TEXT]
                    [--data-int [TARGET:]INTS] [--data-bin [TARGET:]BIN]
                    [--data-bits [TARGET:]BITS] [--data-file [TARGET:]PATH]
                    [--data-mask [TARGET:]PATH]
                    [--data-target DATA_TARGET] [--save-settings PATH] [-v]
```

| Flag | Meaning |
|---|---|
| `--config CONFIG` | JSON scenario config file (required). |
| `--output-dir DIR` | Directory outputs are written to (overrides/relocates JSON outputs). Defaults to `./output` when used with `--format`. |
| `--samplerate HZ` | Override the config's `samplerate`. |
| `--format {svg,sigrok,vcd}` | Replace the JSON `outputs` list with one `capture.<ext>` per given format. Repeatable (`--format svg --format vcd`). |
| `--unit-bits N` | Override the per-protocol SVG framing-unit grouping with a fixed N-bit width. |
| `--svg-verbose` | Render protocol field descriptions inline on any SVG output. |
| `--data-hex`, `--data-string`, `--data-int`, `--data-bin`, `--data-bits`, `--data-file` | Override one operation's payload from the command line — see below. Each is repeatable and chainable with the others. |
| `--data-mask [TARGET:]PATH` | Mark byte/bit positions of an already-resolved `"bytes"`-datatype payload as floating, from a companion mask file — see below. Applied after every `--data-*` value flag. |
| `--data-target TARGET` | Fallback `protocol_id:op_index[:field]` target for any `--data-*`/`--data-mask` occurrence with no inline target of its own. |
| `--save-settings PATH` | Write the fully resolved config (JSON + every CLI override applied) to `PATH` as JSON — directly reloadable via `--config`. |
| `-v`, `--verbose` | Print a one-line samplerate/protocol-count/output-count summary. |

There is no separate build or lint step; `pytest` is the whole check
(see the repository's own `CLAUDE.md` for the full test/validation
workflow if you're changing the tool itself, not just using it).

## Overriding payload data from the CLI

Every operation that carries a byte payload accepts a sibling `"datatype"`
key in its JSON (`"bytes"` — the default, a plain `list[int]` — `"text"`,
`"hex"`, or `"bin"`; some transports also accept `"bits"` for their own
flat bit-list fields — see each protocol's page). The same forms are
reachable straight from the CLI without editing the JSON, via
`--data-hex`/`--data-string`/`--data-int`/`--data-bin`/`--data-bits`/`--data-file`.
Each flag writes whichever datatype-selecting kwarg the target operation's
method actually uses — almost always a shared `"datatype"`, except DALI's
multi-field operations, which each get their own `<field>_datatype`
(resolved automatically from the real method signature, not something you
need to specify):

```bash
# override the single unambiguous data-carrying operation
.venv/bin/python -m protowavegen --config examples/uart_basic.json \
    --data-hex 48656c6c6f

# same thing, spelled with --data-string
.venv/bin/python -m protowavegen --config examples/uart_basic.json \
    --data-string Hello

# a config with more than one data-carrying operation needs a target
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-hex 012a --data-target i2c0:0
```

When a config has exactly one data-carrying operation, the target
auto-detects and `--data-target` can be omitted (as in the first two
examples). When it's ambiguous, the resulting error lists every valid
`protocol_id:op_index[:field]` candidate to copy from.

### Chaining multiple overrides in one invocation

Every `--data-*` flag is repeatable, and each occurrence can carry its own
inline `protocol_id:op_index[:field]:` target prefix instead of relying on
the single global `--data-target`:

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-bin  "i2c0:0:data:0b0000000100101010" \
    --data-string "i2c0:1:data:toto"
```

This sets the `write` operation's (`op_index` 0) payload from a binary
literal and the `read` operation's (`op_index` 1) payload from a text
string, in one command. Two overrides that resolve to the exact same
target raise a clear conflict error naming both flag occurrences — there's
no silent last-one-wins.

If a payload value itself needs to contain a literal `:` (only relevant to
`--data-string`), force auto-detection with a leading empty target:
`--data-string ':a:b:c'` sets the value to `a:b:c` instead of trying to
parse `a` as a target.

### `--data-file`

Loads raw bytes from a file instead of typing them on the command line —
useful for a captured firmware image or any binary blob:

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-file i2c0:0:data:./firmware_fragment.bin
```

The path is resolved relative to the current working directory (same as
`--config`'s own path). The loaded bytes are stored as `datatype: "bytes"`
— a raw file has no way to carry floating-bit markers of its own.

### `--data-mask`

A `--data-file`-loaded payload (or any other field currently resolved to
plain `datatype: "bytes"`) has no way to mark any of its bytes as
floating on its own — `--data-mask` is a companion file that does exactly
that, applied *after* every `--data-*` value flag has resolved:

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-file i2c0:0:data:./firmware_fragment.bin \
    --data-mask i2c0:0:data:./firmware_fragment.mask
```

The mask file is plain text, comma- or newline-separated entries, each
either `byte_index:resolution` (the whole byte, all 8 bits) or
`byte_index.bit_index:resolution` (one bit, `0`=MSB, same convention the
floating-marker alphabet uses everywhere else), `resolution` one of
`l`/`h`/`z`:

```
3:h,7:l,10.3:z
```

marks byte 3 fully floating-high, byte 7 fully floating-low, and just bit
3 of byte 10 floating-on-protocol-pull. The target's current datatype
must be `"bytes"` — a mask only applies to a concrete byte list, and
converts it to `datatype: "bin"` under the hood (the same channel a
hand-typed `--data-bin` marker already uses), so `z` resolves the same
way it would anywhere else: silently on a TRISTATE signal (I2C SDA/SCL,
1-Wire DQ, ...), a hard error otherwise. Two `--data-mask` flags
targeting the same field raise a conflict error, same as `--data-*`.

### `--save-settings`

Writes the fully resolved config — the JSON file with every `--data-*`
override already applied — back out as JSON:

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-hex 012a --data-target i2c0:0 --save-settings ./resolved.json
```

`resolved.json` is a complete, directly reloadable config
(`--config resolved.json` reproduces the exact same output) — useful for
turning a one-off CLI override into a saved, reviewable scenario file.

## The floating-bit marker system (`l`/`h`/`z`)

A payload byte doesn't have to be a fully-driven value. Real bus traffic
often has positions where a party genuinely isn't driving the line — an
undriven register bit, a tri-stated SPI MISO byte between transfers, an
open-drain line simply released to its pull-up. `protowavegen` lets you
mark those positions explicitly instead of picking an arbitrary concrete
value and hoping the diagram reads correctly:

| Marker | Meaning |
|---|---|
| `l` / `L` | Floating, resolves **low** (`0`). |
| `h` / `H` | Floating, resolves **high** (`1`). |
| `z` / `Z` | Floating, resolves via the signal's **protocol-defined pull** if it has one (I2C SDA/SCL, 1-Wire DQ, Wiegand D0/D1, PS/2 clock/data are all open-drain pull-high buses — `z` resolves to `1` there, silently). On a signal with **no** defined pull (SPI MOSI/MISO, Microwire DI/DO, CAN, DALI — all push-pull/single-transmitter buses), `z` is a **hard error**: guessing a resolution there risks being electrically wrong, so those buses require `l`/`h` explicitly. |

The marker only affects which label ends up on the SVG's `"driver"`
annotation track (rendered as `"floating"`, its own color in the legend) —
the resolved *value* is always baked in as a concrete bit, so checksums,
`format_byte()` display, and everything else downstream sees a completely
normal byte. Floating markers work on every direct transport operation
(I2C/1-Wire/SPI/CAN/Wiegand/PS2/Microwire/UART's own methods) and on any
stacked device's operation whose payload field reaches its transport
unmixed with other bytes — confirmed for `ds243x.read_memory`,
`jedec_cfi.read`, `sd_spi.read_block`, and `eeprom_24xx.read_sequential`.
**The gap is a stacked protocol whose payload bytes get folded *into* a
checksum alongside other bytes before transmission (LIN,
`ds243x.write_memory`)** — there, a floating placeholder can't survive the
checksum computation, so those operations still require fully concrete
bytes.

The alphabet works at three different granularities, same characters
everywhere:

- **`hex` datatype** — one character = one **nibble** (4 bits). `"2h"` is a
  byte whose high nibble is the literal `2` and whose low nibble floats
  high — `0x2F`.
- **`bin` datatype** — one character = one **bit**, no byte-alignment
  requirement (comma-separate multiple bytes: `"0b11010100,0b01010101"`).
  `"0b1101h010"` is one byte, bit 4 floating high.
- **`text` datatype** — a `\xNN` escape inside the string, where `NN` is
  either 2 hex digits (a literal raw byte, bypassing UTF-8 encoding for
  just that byte) or 2 characters from the same `l/L/h/H/z/Z` alphabet:
  `"\xhhtoto"` is a floating-high byte followed by the literal text
  `"toto"`.

Worked example — an I2C read where the first response byte is explicitly
marked as floating (the pull-up releasing the line, since SDA is
TRISTATE and `z` resolves automatically):

```bash
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
    --data-string "i2c0:1:data:\xzztoto"
```

A marker also works directly in the JSON config itself, no CLI override
needed — see `examples/i2c_7bit_floating.json`, whose `read` operation
sets `"data": "2hzz"` with `"datatype": "hex"` (byte 0 is `0x2` driven with
its low nibble floating high; byte 1 is fully floating, resolving via
SDA's pull-up).

## Output formats

- **`svg`** — one lane per signal, with the framing-unit color bars,
  per-byte field labels, and (on TRISTATE/floating-labeled signals) a
  per-span-colored driver waveform with a shared legend instead of a plain
  lane. `--svg-verbose` adds inline field-summary text.
- **`sigrok`** — a real `.sr` zip, openable in PulseView or decodable with
  `sigrok-cli -P <decoder>`. No slot for annotations (driver/field/unit
  labels) — only the raw sampled levels, exactly what a real logic
  analyzer would have captured.
- **`vcd`** — plain-text, GTKWave-compatible. Pass a config-level
  `"include_annotations": true` on a `vcd` output entry to add non-standard
  `$var string` pseudo-signals per annotation track (driver/field/unit),
  visible in GTKWave alongside the real signals but outside the VCD spec.
