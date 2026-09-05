# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`protowavegen` *synthesizes* (does not decode) timing diagrams for embedded
protocols from a JSON scenario description, rendering the result to SVG
(documentation) and to sigrok-compatible capture files (`.sr` and `.vcd`,
importable into PulseView/sigrok-cli or GTKWave as if a real logic analyzer
captured it).

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # install (editable + pytest)
.venv/bin/pytest -q                                            # run the full suite
.venv/bin/pytest tests/protocols/test_i2c.py::test_start_condition_exact_edges  # single test
.venv/bin/python -m protowavegen --config examples/i2c_7bit.json \
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
against every example config in `examples/` (45 protocols, 46 example
configs — I2C has two) and confirm each one actually writes its SVG, `.sr`,
and `.vcd` output:

```bash
for f in examples/*.json; do
  .venv/bin/python -m protowavegen --config "$f" || echo "FAILED $f"
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
`tests/test_sigrok_roundtrip.py` decodes our generated `.sr` files with a
real, independently-implemented decoder and asserts the decoded values
match what was encoded — the strongest correctness check available, since a
separately-written decoder reconstructing exactly what we intended to
encode proves the waveform is electrically correct, not just internally
self-consistent with our own hand-derived expected-edge assertions
elsewhere in the suite. It's a normal part of `pytest` (39 round-trip tests
total as of USB HID/CDC/MSC/DFU), not a separate step, but auto-skips if
`sigrok-cli` (and, for IrDA's cross-check, `tshark`) isn't on `PATH` — so a
green suite elsewhere doesn't guarantee this ran; check for skips.

**The independent decoder isn't always the same kind of thing**, and which
tier a protocol gets is a deliberate choice, not an oversight — a future
protocol addition should pick the cheapest tier that's still a genuine
second, independently-authored implementation, not default to the most
expensive one:
- **Mainline sigrok decoder** (the default, used by every protocol through
  the original 38): a real decoder already ships in `libsigrokdecode` (UART,
  I2C, SPI, CAN, 1-Wire, Modbus, Wiegand, DALI, Microwire/93xx EEPROM, PS/2,
  LIN, and more) — zero authoring risk, just point `sigrok-cli -P` at it.
- **Third-party vendored decoder** (I3C): no mainline decoder exists, but a
  real, actively-maintained third-party one does
  (`xyphro/Sigrok-I3C-decoder`, GPL-3.0) — vendored into
  `tests/custom_decoders/i3c/` with its license and an attribution header,
  loaded via `SIGROKDECODE_DIR` (see below). Still a genuinely independent
  author; zero decoder-authoring risk on our side.
- **Self-authored decoder, single-oracle** (used by several existing
  stacked protocols with no dedicated decoder, e.g. DALI's ballast replies):
  when neither of the above exists, we write our own `Decoder` class as the
  test's oracle. This reintroduces *some* of the same-author risk the whole
  round-trip methodology exists to avoid, so it's only acceptable when a
  second, independent oracle would cost more to build than it's worth (see
  USB HID/CDC/MSC/DFU — Wireshark's class-dissector activation there needs
  a full, undocumented enumeration-sequence state machine correlating
  usbmon event pairs, estimated 300-500+ fragile lines for a weaker
  guarantee than the custom decoder alone already gives, so all four
  shipped single-oracle instead).
- **Self-authored decoder, dual-oracle** (IrDA): sigrok has no IrDA decoder
  at any layer, and a cheap, genuinely independent second oracle exists
  (Wireshark's real `irlap`/`irlmp` dissector, driven via a hand-built
  synthetic pcap — see `tests/_irda_pcap.py`), so both are used and
  cross-checked against each other on the same semantic fields
  (address/control/payload), not diffed as raw text.

Custom decoders (self-authored or vendored) live under
`tests/custom_decoders/<id>/` in sigrok's own `__init__.py`+`pd.py` layout,
and are loaded by pointing `sigrok-cli` at `tests/custom_decoders/` via the
`SIGROKDECODE_DIR` environment variable — confirmed empirically
(`sigrok-cli -l 4`'s verbose log lists both paths) that this variable *adds*
to sigrok's system decoder search path rather than replacing it, so a
custom decoder stacking on a system one in the same `-P` spec (as USB's
HID/CDC/MSC/DFU decoders do, on top of sigrok's own `usb_packet`)
still works. `tests/test_sigrok_roundtrip.py`'s `_decode_custom()` helper
sets this env var; `_decode()` (no env override) is for mainline/vendored
decoders that don't need it.

USB's own transport core needs no custom decoder at all: sigrok's existing
3-decoder stack (`usb_signalling` electrical→symbols, `usb_packet`
SYNC/PID/CRC framing, `usb_request` SETUP/transaction tracking) already
validates it end to end, same "mainline decoder" tier as everything else.

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
- `I2CBus._start_condition()` produced a spurious STOP-shaped edge right
  before a *repeated* START (`write_then_read()`'s switch from write to
  read, and 10-bit addressing's own repeated-START prelude): called right
  after an ACK'd byte, SCL is already high and SDA is already held low —
  the code's "make sure SDA is idle-high first" step then raised SDA
  *while SCL was still high*, which is itself a real STOP condition,
  before immediately doing the intended START edge. Found via sigrok's
  `rtc8564` decoder (adopted this session), which tracks real START vs.
  repeated-START vs. STOP and never reached its read-phase state because
  of the phantom STOP in between. Fixed by detecting that specific entry
  state (SCL high, SDA low) and bringing SCL low first so SDA can go
  idle-high safely, matching real I2C repeated-START electrical timing.
  Verified zero regressions across the full suite (the buggy path was
  already exercised by every existing `write_then_read()` caller —
  `ds1307`, `tca6408a`, `lm75`, `eeprom_24xx` — none of which happened to
  assert exact absolute sample counts sensitive to it).
- `I2CBus._stop_condition()` had the same class of bug in the opposite
  direction, since fixed: called right after a *NACK'd* byte (SDA already
  high, the default ending for any read via `nack_last=True`), its own
  "make sure SDA is idle-low first" step produced a spurious START-shaped
  edge immediately before the real STOP edge. Confirmed via the same
  `rtc8564` round-trip test: the base `i2c` decoder's event stream ended
  in a bogus "Start repeat" instead of "Stop", so any decoder gating a
  summary annotation on a real STOP (`rtc8564` does; most per-byte-
  annotating decoders like `pca9571` don't care and were unaffected)
  never fired it. Fixed the same way as the START-side case: bring SCL
  low first so SDA can drop safely before the real STOP edge. Verified
  zero regressions (every affected test only checks relative frame
  positions/decoded values, none assert exact absolute sample counts —
  confirmed by grepping every test touching `read()`/`write_then_read()`
  for exact-edge/exact-duration assertions before applying the fix, not
  just assuming). `rtc8564`'s round-trip test now also asserts the
  previously-unreachable "Read date/time: ..." summary line.
  Searched the rest of the codebase for the same bug shape
  (`grep -rn "level_of" src/protowavegen/protocols/`) and found it used
  nowhere else — `spi.py`'s own back-to-back-CS fix (a different bug,
  same session) uses an unconditional idle-time gap instead of a
  runtime-level-conditional guard, and `onewire.py`'s slots always force
  a fresh falling edge unconditionally by design — neither is vulnerable
  to this specific "conditional guard forces an unintended semantic edge"
  shape.
- `I3CBus`'s own START/STOP condition primitives (`i3c.py`) are a *separate*
  implementation from `I2CBus`'s, deliberately not a reuse of the
  already-fixed I2C versions above — the vendored `xyphro/Sigrok-I3C-decoder`
  only finalizes a transfer's 9th bit (T-bit/ACK) on a falling SCL edge, and
  does no START/STOP handling at all while it's still waiting for one.
  Naively porting I2C's conditional "only raise SDA if it's not already
  high" guard shape into I3C silently dropped 4 of 5 STOP/START pairs on
  first attempt. Fixed by having I3C's condition primitives unconditionally
  pulse SCL low before every real START/STOP edge, rather than trying to
  detect and special-case the entry state the way I2C's fix does — a
  stricter decoder can require a stricter encoder-side shape even when the
  electrical intent is identical.
- The IrDA custom decoder (`tests/custom_decoders/irda/pd.py`)'s first
  draft had the same class of edge-vs-timeout race documented above for
  `onewire_link`, but self-inflicted this time: it tried to classify each
  SIR bit by hunting for the pulse's edges rather than sampling the bit
  cell at its nominal midpoint, and IrDA's own encoding always places a
  pulse's edge exactly on the previous bit cell's boundary sample — a
  textbook case of the same "boundary-exact timing is one quantization
  error from misclassifying" lesson, just found in a decoder we wrote
  ourselves instead of one sigrok ships. Fixed the same way as the general
  lesson prescribes: sample at the midpoint, don't edge-hunt.

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
- `nes_gamepad` (stacked on the generic `spi` decoder) only samples a bit
  on a qualifying CLOCK edge and only emits a word after exactly
  `wordsize` (8) such edges — it has no notion of a bit that's already
  valid *before* the first clock edge. Real NES hardware (and
  `NesGamepad.read_buttons()`) needs only 7 CLOCK pulses for 8 bits, since
  the first bit is valid immediately once LATCH falls. Confirmed by
  manually adding an 8th synthetic clock edge to a probe capture, which
  immediately produced the correct decoded byte — but a real waveform
  shaped that way would misrepresent actual NES controller timing, so
  this is a genuine `spi`-decoder-model limitation, not a bug on our side.

When adding a new protocol, still try a matching round-trip case first
(`sigrok-cli --show -P <name>` prints required channels/options/annotation
classes); if it doesn't decode cleanly, read the decoder's own source
before concluding it's unfixable — two of the "limitations" above turned
out to be real bugs on our side once actually investigated, not sigrok's.

### Other lessons worth knowing

- **The Python API and the CLI/JSON surface can silently drift apart.** A
  protocol operation method can grow full `datatype`/floating-marker
  support in its Python signature while `config.py`'s hand-maintained
  `_PAYLOAD_FIELDS` set (which the CLI's `--data-target`/auto-detect walks
  to find payload fields) never gets told about the new field — nothing
  raises at definition time, it just silently can't be reached from the
  CLI until someone happens to try it. This has happened three separate
  times (DALI's per-field `datatype` kwarg naming, a missing `--data-bits`
  flag, `seven_segment.set_digits`'s `patterns` field). `tests/
  test_payload_field_coverage.py` guards against recurrence: it walks
  every registered protocol's operation methods via `inspect.signature`
  and asserts every unambiguous datatype-controlled parameter is in
  `_PAYLOAD_FIELDS` — keep it green rather than special-casing around it
  when adding a new payload field.
- **`vcd_writer.py`'s per-timestamp annotation ordering matters.** Two
  same-track annotations that are adjacent (one's `end` == another's
  `start`) get their value-change events bucketed by sample time, but
  VCD applies last-write-wins *within* a timestamp based on emission
  order, not chronological order — if `capture.annotations` happens to
  list the later span before the earlier one, an end-clear can land after
  the next span's start-value and silently blank a label that should be
  showing. Fixed by sorting each bucket so clears sort before real values
  before emitting (see the `clear_first` sort key). Any future change to
  how `changes[t]` is built or emitted needs to preserve that ordering
  guarantee, not just get the *set* of events right.
- **A biphase/Manchester bit's "1 vs 0" polarity convention isn't
  universal — verify per decoder, don't assume a working convention
  transfers.** `dali.py`'s `_manchester_bit` and RC-5's own biphase
  encoding agree (`bit=1` = high-to-low at the bit's midpoint), but
  RC-6's leader is a distinctive 6-half-bit mark + 2-half-bit space, not a
  plain biphase bit — its own start bit must produce a falling edge
  exactly at the leader's 2-half-bit mark to be recognized at all, which
  only happens with the *opposite* sense from RC-5's convention. Found by
  empirically probing a generated capture against `sigrok-cli` and
  reading the exact edge/delta classification logic in `ir_rc6/pd.py`
  when the obvious first attempt (reusing RC-5's convention verbatim)
  produced zero annotations — see `ir_rc6.py`'s docstring for the fix.
  Also needed a 20-half-bit trailing idle gap before the decoder would
  emit its address/command summary at all (it only closes out a field
  once it sees a long enough gap with no further edge). Two back-to-back
  IR frames with zero gap between them also need a small mandatory idle
  period (`_ir_pulse.ensure_idle_gap`) — a rise and fall landing on the
  *same* sample is silently misread by edge-based decoders as no edge
  having happened at all.
- **A Manchester decoder's bit-pairing state can bootstrap out of phase,
  and only settle after a real frame boundary — send several frames, not
  one, before trusting a round-trip decode.** `em4100.py`'s decoder
  (`em4100/pd.py`) computes each bit from `oldpin ^ polarity` using
  whatever edge it happens to see *first*, with no concept of "waiting
  for a clean sync point" the way `ir_rc5`'s state machine does — so a
  single isolated frame, or even two back-to-back, can decode with every
  row shifted out of phase (confirmed by porting the decoder's exact
  edge-pairing algorithm into a standalone Python script and comparing
  its output against the exact bits generated, byte for byte). Three or
  more `transmit()` calls back-to-back let its pairing state settle by
  the first frame boundary, after which every subsequent frame decodes
  cleanly. Also needed `polarity=active-low` explicitly on the decoder
  invocation — its own default is `active-high`, decoding every bit
  inverted despite using the same Manchester convention `dali.py`
  already established. Lesson: when a decoder's own state machine has no
  explicit re-sync/bootstrap guarantee, don't assume a single frame is
  enough to validate a round-trip — and when a convention that worked for
  one Manchester-based decoder produces garbage on another, re-derive the
  polarity/bootstrap assumptions from that decoder's own source rather
  than assuming the earlier convention transfers.

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
- Operation payload fields (UART `send`'s `data`, I2C `write`/`read`'s
  `data`, SPI `transfer`'s `mosi`/`miso`, and similar `list[int]` byte
  parameters across the stacked protocols) accept an optional sibling
  `"datatype"` field: `"bytes"` (default, the original JSON int-array
  form), `"text"` (a JSON string, UTF-8-encoded), or `"hex"` (a hex-digit
  string decoded via `bytes.fromhex`). Normalization happens via
  `decode_payload()` (`payload.py`), called at the top of each JSON-facing
  operation method — see `examples/uart_basic.json` (`"datatype": "text"`)
  and `examples/sd_spi_basic.json` (`"datatype": "hex"`) for the two forms
  in use; every other example still uses the plain int-array default. The
  same three forms are also reachable from the CLI: `--data-hex`,
  `--data-string`, `--data-int` (comma-separated, e.g. `72,101,108,108,111`)
  override one operation's payload without editing the JSON, via
  `apply_data_override()` (`config.py`). Since a real config can have
  several data-carrying operations (e.g. `i2c_7bit.json`'s separate
  `write`/`read`) or several payload fields on one operation (SPI
  `transfer`'s `mosi`+`miso`), these flags only auto-detect the target when
  it's unambiguous; otherwise `--data-target protocol_id:op_index[:field]`
  is required, and the resulting `ValueError` lists every candidate in that
  exact syntax so it can be copied straight into `--data-target`.
- 45 protocols are implemented (one file each under `protocols/`, one
  `<name>_basic.json` each under `examples/`). Twenty are
  `TransportProtocol`s with their own physical-layer bit timing:
  **UART** (`uart.py`), **I2C** (`i2c.py`, 7/10-bit addressing,
  `write_then_read()` for the set-pointer-then-repeated-START-read idiom
  nearly every I2C device uses), **SPI/QSPI/OctoSPI** (`spi.py`, `width`
  param selects which), **1-Wire** (`onewire.py`), **CAN** (`can.py`, real
  CRC-15 + bit-stuffing), **DALI** (`dali.py`, Manchester-encoded),
  **PS/2** (`ps2.py`), plus three more standalone transports for buses
  that don't fit any of those: **Wiegand** (`wiegand.py`, two
  open-collector pulse lines, no clock), **NES gamepad**
  (`nes_gamepad.py`, independently-timed latch/clock, deliberately *not*
  built on `SpiBus`), and **Microwire** (`microwire.py`, active-high CS,
  no CPOL/CPHA modes); plus seven newer standalone transports: **IR RC-5,
  IR NEC, IR RC-6** (`ir_rc5.py`/`ir_nec.py`/`ir_rc6.py`, sharing the
  small `_ir_pulse.py` helper — biphase and pulse-distance IR
  remote-control encodings), **TLC5620** (`tlc5620.py`, a shift-register
  quad DAC), **EM4100** (`em4100.py`, Manchester-encoded 125kHz RFID),
  **AM230x** (`am230x.py`, a DHTxx-family humidity/temperature sensor,
  pulse-width-timed like 1-Wire but with no ROM addressing), and **DCF77**
  (`dcf77.py`, a 1-bit-per-second longwave time signal); plus three most
  recently added: **I3C** (`i3c.py`, SDR mode — reuses I2C's open-drain
  START/STOP/address-phase electrical behavior, adds a push-pull data
  phase, a T-bit/parity in place of I2C's plain ACK, and Dynamic Address
  Assignment via ENTDAA), **IrDA** (`irda.py`, SIR physical encoding —
  pulse-per-bit-cell — under IrLAP link-layer framing with a real CRC-16/
  X-25 FCS), and **USB** (`usb.py`, Full-Speed only — NRZI + bit-stuffing,
  SYNC/PID framing, CRC5 token / CRC16 data, control transfers). Everything
  else is a `StackedProtocol` wrapping one of those — an application-layer
  device/protocol adding no new bit-timing of its own: **LIN** and
  **Modbus RTU** (real CRC16, `checksums.py`) on `UartTransport`;
  **DMX512** on `UartTransport` too (break+bytes, same shape as LIN);
  **LM75, 24xx EEPROM, DS1307, TCA6408A, MLX90614 (real SMBus PEC),
  Nunchuk, ADXL345, PCA9571, RTC-8564** on `I2CBus`; **JEDEC CFI, MAX7219,
  SD-card-SPI-mode (real CRC-7), 7-segment (`seven_segment.py`), SPI
  flash (`spiflash.py`)** on `SpiBus`; **DS2408, DS243x, DS28EA00**
  (1-Wire CRC-8, `checksums.py` + `onewire_rom.py`'s shared Skip-ROM/
  Match-ROM prelude) on `OneWireBus`; **93xx EEPROM**
  (`microwire_93xx.py`) on `MicrowireBus`; and, most recently, **USB HID,
  USB CDC/ACM, USB Mass Storage, USB DFU** (`usb_hid.py`/`usb_cdc.py`/
  `usb_msc.py`/`usb_dfu.py`) on `UsbBus` — each a deliberately narrow,
  real-but-scoped device subset (mouse HID reports, virtual-serial
  line-coding + bulk data, Bulk-Only-Transport SCSI READ10/WRITE10, and
  DFU download/upload/status respectively), validated via self-authored
  custom sigrok decoders vendored under `tests/custom_decoders/usb_*/`
  since no mainline decoder exists at that layer (see
  `docs/protocols/usb_hid.md`/`usb_cdc.md`/`usb_msc.md`/`usb_dfu.md`).
  `protocols/base.py`'s module
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
  `track` (a string namespace: `"driver"`, `"field"`, `"bitorder"`,
  `"unit"`, or a new one) + `label` + a sample range + arbitrary `data`.
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
