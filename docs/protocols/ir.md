# IR remote-control family

Three standalone transports, one demodulated IR envelope line each
(`sig("ir")`, active-low: idle/space is logic 1, mark — carrier present —
is logic 0). All three sigrok decoders (`ir_rc5`, `ir_nec`, `ir_rc6`) are
envelope-only: none of them need the actual 36-38kHz sub-carrier modeled,
only this on/off envelope at the millisecond/microsecond timescale.

A small shared helper, `_ir_pulse.py`, holds the two genuinely common
primitives: `mark_space()` (one mark-then-space pulse — NEC's core
primitive) and `biphase_bit()` (one Manchester/biphase bit — RC-5/RC-6's
core primitive), plus `ensure_idle_gap()` (a mandatory small idle period
before a new frame, needed because two frames sent with literally zero
gap put a rise and a fall at the same sample, which sigrok's edge-based
decoders can silently misinterpret as no edge at all). Frame assembly
(start bits, mode bits, address/command layout, bit order) stays in each
protocol's own file — that's where the three genuinely diverge.

## RC-5 — `type: "ir_rc5"`

`ir_rc5.py`. Philips RC-5: 889us half-bit (1.78ms full bit), 14-bit
biphase frame — 2 start bits (always 1; the second is the complement of
command bit 6 in extended mode), 1 toggle bit, 5 address bits, 6 command
bits, all MSB-first.

Operations: `send(address, command, toggle=False, extended=False)`.

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "rc0", "type": "ir_rc5",
      "operations": [
        { "op": "send", "address": 5, "command": 12, "toggle": false }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ir_rc5_basic.svg" },
    { "type": "sigrok", "path": "output/ir_rc5_basic.sr" },
    { "type": "vcd", "path": "output/ir_rc5_basic.vcd" }
  ]
}
```

## NEC — `type: "ir_nec"`

`ir_nec.py`. Pulse-distance encoding: each bit is a fixed 562.5us mark
followed by a space whose width selects the value (562.5us = 0, 1687.5us
= 1) — sigrok's decoder measures mark+space edge-to-edge distance, so
mark width alone never carries the bit. Classic 8-bit form only: address,
its bitwise complement, command, its complement, all LSB-first, then a
stop-bit mark closing out the last bit's timing measurement. The decoder
hard-rejects a frame whose address doesn't complement-check against
`~address`, so extended 16-bit addressing isn't supported.

Operations: `send(address, command)`, `send_repeat()` (shorter leader,
no data bits — a real remote's held-button repeat frame).

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "nec0", "type": "ir_nec",
      "operations": [
        { "op": "send", "address": 0, "command": 12 },
        { "op": "send_repeat" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ir_nec_basic.svg" },
    { "type": "sigrok", "path": "output/ir_nec_basic.sr" },
    { "type": "vcd", "path": "output/ir_nec_basic.vcd" }
  ]
}
```

## RC-6 — `type: "ir_rc6"` (mode 0 only)

`ir_rc6.py`. Philips RC-6: 444.5us half-bit, a distinctive leader (a
6-half-bit mark followed by a 2-half-bit space, not a plain biphase bit),
then 1 start bit (always 1), 3 mode bits (mode 0 = standard frame shape),
1 **double-width** toggle bit, 8 address bits, 8 command bits, all
MSB-first. Modes 6A/6B (short/long addressing variants) aren't
implemented.

Every bit after the leader uses the *opposite* sense from
`biphase_bit()`'s RC-5 convention — confirmed empirically against
sigrok's decoder: a real start bit=1 must produce a falling edge exactly
at the leader's 2-half-bit mark, which only happens if its own first half
is low, not high. The decoder's `auto`-polarity mode then self-adapts
every later bit's recovered value consistently from whatever sense the
sync bit exhibited, so inverting every bit uniformly still decodes to the
correct logical values. A 20-half-bit trailing idle gap is also required
— the decoder only emits its address/command summary once it sees a
long-enough run with no further edge.

Operations: `send(mode=0, address=0, command=0, toggle=False)`.

```json
{
  "samplerate": 1000000,
  "protocols": [
    {
      "id": "rc60", "type": "ir_rc6",
      "operations": [
        { "op": "send", "address": 18, "command": 52, "toggle": true }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/ir_rc6_basic.svg" },
    { "type": "sigrok", "path": "output/ir_rc6_basic.sr" },
    { "type": "vcd", "path": "output/ir_rc6_basic.vcd" }
  ]
}
```
