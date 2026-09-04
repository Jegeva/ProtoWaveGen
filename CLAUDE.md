# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`timingdiagram` *synthesizes* (does not decode) timing diagrams for embedded
protocols from a JSON scenario description, rendering the result to SVG
(documentation) and to sigrok-compatible capture files (`.sr` and `.vcd`,
importable into PulseView/sigrok-cli or GTKWave as if a real logic analyzer
captured it).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # install (editable + pytest)
.venv/bin/pytest -q                                            # run the full suite
.venv/bin/pytest tests/protocols/test_i2c.py::test_start_condition_exact_edges  # single test
.venv/bin/python -m timingdiagram --config examples/i2c_7bit.json \
    --format svg --format sigrok --format vcd  # run the CLI
```

There is no separate build or lint step; `pytest` is the whole check.

Other CLI flags worth knowing: `--unit-bits N` overrides the SVG unit-bar
grouping (global or per-protocol-node via a JSON `unit_bits` key); `--svg-verbose`
turns on inline field-summary text on SVG outputs; `--samplerate` overrides
the JSON config's samplerate. `main.py --help` lists all of them.

The default output directory is `./output` (used whenever `--format` is
given without an explicit `--output-dir`; see `config.py::resolve_config`).
Write generated captures there, or wherever a JSON config's own `outputs[]`
paths point — **do not** send output to `/tmp` when running or validating
this tool.

### Validating a change

**Passing `pytest` is necessary but not sufficient.** Before considering any
change to a protocol, the model, or an output writer done, run the CLI
against every example config in `examples/` (one per supported protocol —
27 of them) and confirm each one actually writes its SVG, `.sr`, and `.vcd`
output:

```bash
for f in examples/*.json; do
  .venv/bin/python -m timingdiagram --config "$f" || echo "FAILED $f"
done
```

(Each example config already writes into `output/` itself; no `--output-dir`
needed. Never redirect these to `/tmp`.)

The unit tests exercise protocol bit-timing and output-writer internals in
isolation; they do not prove the full pipeline (config -> app -> protocol
stack -> capture -> every output writer) still runs end to end for each
protocol. A change isn't verified until all currently-supported protocols
have generated real output through the CLI, not just green tests.

`sigrok-cli` is installed in this environment and is looped into the suite:
`tests/test_sigrok_roundtrip.py` decodes our generated `.sr` files with
sigrok's own, independently-implemented decoders (UART, I2C, SPI, CAN,
1-Wire, Modbus, Wiegand, DALI, Microwire/93xx EEPROM, PS/2, and LIN — 13
round-trip tests total) and asserts the decoded values match what was
encoded. This is the strongest correctness check available — a real
separately-written decoder reconstructing exactly what we intended to
encode proves the waveform is electrically correct, not just internally
self-consistent with our own hand-derived expected-edge assertions
elsewhere in the suite. It's a normal part of `pytest`, not a separate
step, but auto-skips if `sigrok-cli` isn't on `PATH` — so a green suite
elsewhere doesn't guarantee this ran; check for skips.

It found three classes of real bug, all worth knowing before touching
timing code:
- `OneWireBus`'s slot/presence timing constants originally sat *exactly* on
  sigrok's `onewire_link` decoder's classification thresholds, and a live
  edge-vs-timeout race in that decoder silently dropped every other bit —
  see the `_SLOT_US`/`_PRESENCE_DELAY_US`/`_RESET_RECOVERY_US` comments in
  `onewire.py`. Lesson: a value sitting exactly on a real decoder's
  threshold is one sample-quantization error from misclassifying — always
  add margin.
- `SpiBus.transfer`/`wide_transfer` and `MicrowireBus.transfer` originally
  bracketed each call's own CS assert/deassert with **zero samples**
  between one call's deassert and the next call's assert whenever two
  separate transfers ran back-to-back — physically meaningless (real CS
  needs nonzero setup time) and left sigrok's `max7219`/`microwire`
  decoders unable to tell separate commands apart. Fixed by a mandatory
  minimum CS-deasserted recovery gap (`builder.advance(self._shc)`) before
  every transfer — see `SpiBus.transfer`'s comment. Any *new* transport
  with its own per-transaction chip-select-like bracket should build this
  in from the start rather than rediscovering the bug.
- `MicrowireBus._clock_bit` had two bugs of its own, found in the same
  follow-up pass that fixed the CS-gap issue above: (1) CS was dropped
  while `clk` was still high — sigrok's `microwire` decoder only flushes
  the final clocked bit when CS falls *with `sk` also low*, so it silently
  dropped the last bit of every transfer (address decoded fine; word data
  came up one bit short). (2) `di` and `do` both changed on the same
  falling edge, but the decoder samples `so` on the *falling* edge
  expecting a real slave to have already changed it on the preceding
  *rising* edge — this shifted every decoded `so` bit by one position
  (reading back `0xABCD` decoded as a precisely 1-bit-shifted `0x579b`).
  Fixed by bringing `clk` back to idle-low before dropping CS, and moving
  `do`'s change to the rising edge while `di`'s stays on the falling edge.

Some of sigrok's own decoders have quirks/limitations confirmed unrelated
to us — found by reading their source under
`/usr/share/libsigrokdecode/decoders/<name>/pd.py` rather than assuming a
mismatch is our bug, and in two cases (`ps2`, `lin`) worked around by
changing the *test*, not the protocol:
- `lm75` never updates which register it thinks is selected (hardcoded at
  its `__init__` default for its entire lifetime) and never resets its
  internal byte-pairing state between I2C transactions or on a repeated
  START — mispairs bytes across any real access pattern that includes a
  register-pointer write. No waveform shape avoids this; genuinely
  unfixable from our side.
- `dmx512` never resets its per-byte oversampling accumulator (`self
  .aggreg`) at a byte boundary, so each byte's decode is contaminated by
  the *previous* byte's trailing sample — reproduced identically across 5
  samplerates (1-16 MHz), ruling out a rounding/samplerate cause. No
  content-independent fix exists.
- `ps2` has an off-by-one in its flush check: it only emits a frame's
  decoded word on a *12th* falling clock edge, but a real PS/2 frame is
  exactly 11 bits — a single isolated frame never flushes. **Worked
  around**: send two frames back-to-back and assert only on the first
  (its sigrok round-trip test does this).
- `lin` (stacked on `uart`) only flushes a frame's checksum when it sees
  either the *next* BREAK or two full UART-frame-durations of idle — our
  default trailing idle margin isn't long enough for the latter. **Worked
  around** the same way: a second frame's BREAK flushes the first (its
  sigrok round-trip test does this).

When adding a new protocol, still try a matching round-trip case first
(`sigrok-cli --show -P <name>` prints required channels/options/annotation
classes); if it doesn't decode cleanly, read the decoder's own source
before concluding it's unfixable — two of the "limitations" above turned
out to be real bugs on our side once actually investigated, not sigrok's.

## Architecture

Everything flows through one pipeline, orchestrated by
`TimingDiagramApplication` (`app.py`):

1. `config.py` merges a JSON scenario file with CLI overrides (JSON <
   CLI args in precedence) into a `Config`.
2. `app.build_nodes()` instantiates every `protocols[]` entry from the
   config. A node's `stack_on: <id>` field resolves to an already-built
   sibling instance passed into its constructor — this is how protocols
   *stack* (e.g. `LinBus` on `UartTransport`, `JedecCfi` on `SpiBus`, both
   implemented — see `examples/lin_basic.json`/`jedec_cfi_basic.json`): a
   node used as a transport must be declared earlier in the list than
   whatever stacks on it.
3. `app.build_capture()` runs generation in two phases against one shared
   `CaptureBuilder`: every node's `register_signals()` first, *then* every
   node's `generate()`. This ordering is load-bearing — a stacked protocol
   calls its transport's methods directly (not through `generate()`), so the
   transport's signals must already exist regardless of declaration order.
   It then applies any `unit_bits` override and always adds an idle margin
   before and after the real activity (`model/capture.py::pad_idle`,
   `Config.idle_margin_fraction`, default 2%) — every generated capture gets
   this, not just ones written to disk.
4. The finished, frozen `Capture` is handed to each requested output writer.

### Protocol layer (`protocols/`)

- `Protocol` (`base.py`) is the ABC: `get_signals()` + `generate(builder)`.
  `TransportProtocol` (link-layer: UART/I2C/SPI) and `StackedProtocol`
  (application-layer, wraps a transport instance) both replay a JSON
  `operations: [{"op": name, ...kwargs}]` list by dispatching to a
  same-named method — there's no generic dispatch machinery beyond that one
  mixin, so adding an operation is just adding a method.
- New protocol classes register via `@register_protocol("name")` and must be
  imported once in `protocols/__init__.py` to land in the registry that
  `app.build_nodes()` looks up by the JSON `type` field.
- Every signal name is node-id-prefixed (`Protocol.sig()`, e.g.
  `"uart0.tx"`) so two instances of the same protocol never collide.
- Open-drain buses (I2C, 1-Wire) never report a device "driving high" —
  level 1 always means the pullup released the line. This is tracked via
  `DriverTracker`, which coalesces same-driver spans into one annotation
  instead of one per bit/half-cycle.
- `format_byte()` (`base.py`) renders hex plus the printable ASCII char when
  there is one (`"0x41 'A'"`); UART/I2C/SPI/1-Wire/CAN use it directly as
  the `field` annotation's label — the full byte value is always shown, not
  gated behind verbose mode. `UartTransport.send()` and `SpiBus.transfer()`
  both take an optional `labels` param (a string per byte) so a stacked
  protocol can override that default display with something more meaningful
  (a LIN sync/PID byte, a JEDEC command/address byte) *instead of* adding a
  second annotation over the same byte — two annotations on the same track
  covering the same range paint over each other in the SVG, so this pattern
  (also used for CAN's per-role, per-data-byte annotations covering a
  post-bit-stuffing span) is how every stacked/composite protocol here
  avoids that.
- 27 protocols are implemented (one file each under `protocols/`, one
  `<name>_basic.json` each under `examples/`). Six are `TransportProtocol`s
  with their own physical-layer bit timing: **UART** (`uart.py`), **I2C**
  (`i2c.py`, 7/10-bit addressing, `write_then_read()` for the
  set-pointer-then-repeated-START-read idiom nearly every I2C device uses),
  **SPI/QSPI/OctoSPI** (`spi.py`, `width` param selects which), **1-Wire**
  (`onewire.py`), **CAN** (`can.py`, real CRC-15 + bit-stuffing), **DALI**
  (`dali.py`, Manchester-encoded), plus three more standalone transports for
  buses that don't fit any of those: **Wiegand** (`wiegand.py`, two
  open-collector pulse lines, no clock), **NES gamepad** (`nes_gamepad.py`,
  independently-timed latch/clock, deliberately *not* built on `SpiBus`),
  and **Microwire** (`microwire.py`, active-high CS, no CPOL/CPHA modes).
  Everything else is a `StackedProtocol` wrapping one of those — an
  application-layer device/protocol adding no new bit-timing of its own:
  **LIN** and **Modbus RTU** (real CRC16, `checksums.py`) on `UartTransport`;
  **DMX512** on `UartTransport` too (break+bytes, same shape as LIN);
  **LM75, 24xx EEPROM, DS1307, TCA6408A, MLX90614 (real SMBus PEC), Nunchuk,
  ADXL345** on `I2CBus`; **JEDEC CFI, MAX7219, SD-card-SPI-mode (real
  CRC-7), 7-segment (`seven_segment.py`)** on `SpiBus`; **DS2408, DS243x,
  DS28EA00** (1-Wire CRC-8, `checksums.py` + `onewire_rom.py`'s shared
  Skip-ROM/Match-ROM prelude) on `OneWireBus`; and **93xx EEPROM**
  (`microwire_93xx.py`) on `MicrowireBus`. `protocols/base.py`'s module
  docstring-level pattern (real signals + a docstring describing the
  intended algorithm, `generate()`/methods raising `NotImplementedError`
  until implemented) is the template for scaffolding a new one before it's
  actually built.

### Data model (`model/`)

- `CaptureBuilder`: protocols write into this. A single global sample
  `cursor` shared by every signal; `set_level()` only records an edge when
  the level actually changes (no-op otherwise); `annotate()` for metadata.
  `builder.frame()` is a context manager yielding a `FrameHandle` whose
  `.end` is filled in at block exit, so a stacked protocol can annotate the
  exact range its transport just produced.
- `Annotation` is the single, deliberately generic metadata mechanism —
  `track` (a string namespace: `"driver"`, `"field"`, `"bitorder"`, `"unit"`,
  `"error"`, or a new one) + `label` + a sample range + arbitrary `data`.
  There's no per-concern subclassing; every requirement (who's driving a
  shared line, MSB/LSB order, a decoded field, a framing-unit boundary) is
  just another track.
- `Capture` is the frozen, immutable result every output writer consumes:
  ordered signals, per-signal edge (transition) lists — not dense sample
  arrays — and the flat annotation list.

### Output layer (`outputs/`)

- `OutputWriter` (`base.py`): `write(capture, path, **options)`, registered
  via `@register_output("name")`, looked up by the JSON `outputs[].type` /
  CLI `--format` value.
- `SVGWriter` does the most: one lane per signal; a `driver`-tagged signal
  gets its waveform drawn in per-span color instead of a lane row (with a
  shared legend), since a lane row of tiny blocks is unreadable for
  something that changes every bit (I2C SDA/SCL); a track whose value is
  constant across the whole capture collapses into a one-line note instead
  of a repeated lane; `unit`-track annotations render as background bands
  behind the signal lanes; any lane text that would overflow into its
  neighbor falls back to a color-only block (same shared legend); `verbose`
  option prefers a `summary` key in an annotation's `data` when present.
- `SigrokWriter` writes a real `.sr` zip (bit-packed `logic-1-N` binary,
  chunked for large captures) — it has no slot for annotations, so they're
  dropped there by design; SVG is the path that preserves them.
- `VCDWriter` is stdlib-only; `include_annotations=True` adds non-standard
  GTKWave `$var string` pseudo-signals per annotation track.
