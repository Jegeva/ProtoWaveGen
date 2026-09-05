# IrDA — `type: "irda"`

`irda.py`. IrDA SIR (Serial InfraRed) physical layer carrying IrLAP frames:
one demodulated `ir` envelope line, active-low (idle/space = logic 1, a
light pulse = logic 0 — matching every real IR receiver module's
convention and this repo's `_ir_pulse.py`, even though SIR's own encoding
shape is unrelated to that module's mark-space/biphase primitives). Not the
consumer-remote protocols in `ir_rc5.py`/`ir_nec.py`/`ir_rc6.py` — IrDA is a
genuine serial data link (old phones/PDAs/laptops/printers, IrOBEX/IrCOMM
file transfer), standardized by the Infrared Data Association.

**v1 scope: SIR only** (up to 115.2kbit/s) — MIR/FIR (0.576/4+ Mbit/s) use
fundamentally different encodings and aren't implemented. sigrok itself has
no IrDA decoder at any layer, so this protocol is validated two ways
instead of the usual single sigrok round-trip (see "Validation" below).

## Physical layer (SIR)

Confirmed against IrDA's own physical-layer spec summaries: a logic 0 is a
brief light pulse (nominally 3/16 of the bit period, at the *start* of the
bit cell), a logic 1 is no pulse for the whole cell. That "0 is pulsed"
convention is exactly UART's own polarity (start bit = 0, idle/stop = 1),
so byte framing is UART-shaped: 1 start bit (always 0), 8 data bits
LSB-first, 1 stop bit (always 1), no parity.

## IrLAP framing

Every frame is Address (1 byte) + Control (1 byte) + optional Information
+ FCS (2 bytes, LSB first). There's no explicit end-of-frame delimiter at
the SIR byte-stream level (unlike synchronous HDLC's `0x7E` flags) — real
receivers infer frame boundaries from silence between frames, the same
idle-timeout shape this repo already uses for LIN/Modbus RTU. `IrdaBus`
enforces a minimum 16-bit-period gap before every `send_frame`/
`send_i_frame`/`send_xid` call for exactly this reason.

- **Address**: bit 0 = C/R (`command=True`/`False`), bits 1-7 = a 7-bit
  connection address (0x7F is the broadcast address used for discovery).
- **Control**: bit 0 = 0 selects an I-frame (bits 1-3 = N(S), bit 4 = P/F,
  bits 5-7 = N(R)); bits 0-1 = `11` selects a U-frame (bit 4 = P/F, the
  rest select the command/response type — XID's is `0x2F`).
- **FCS**: CRC-16/X-25 (`checksums.crc16_x25` — polynomial 0x1021/reflected
  0x8408, init 0xFFFF, final complement) over Address+Control+Information,
  transmitted LSB byte first. Verified against the Linux kernel's own
  (now-removed) IrDA stack's magic-residue self-check value
  (`GOOD_FCS = 0xf0b8`).

## Operations

- `send_frame(address, control, info=None, datatype="bytes", command=True,
  final=True, driver=None)`: the generic primitive — `control` must
  **not** set bit 4 (the P/F bit) directly; pass `final=` instead.
- `send_i_frame(address, ns, nr, info, datatype="bytes", command=True,
  final=True, driver=None)`: an I-frame (data-carrying), building the
  Control byte's N(S)/N(R)/frame-type bits automatically.
- `send_xid(address=0x7F, source_address, dest_address=0xFFFFFFFF,
  discovery_flags=0, slot=0xFF, version=0x00, command=True, final=True,
  driver=None)`: an XID (eXchange station IDentification) command frame —
  the U-frame IrLAP devices broadcast to discover each other. Chosen over
  SNRM/UA connection setup because its Information field (Format
  Identifier, Source/Destination Device Address, Discovery Flags, Slot
  Number, Version Number — all little-endian) is fully public and simple
  to get exactly right.

`info`/`datatype` follow this codebase's usual floating-marker convention
(`decode_payload_with_floating`, `tristate=False` — IrDA is a single-
transmitter-at-a-time link with no protocol-defined pull, same reasoning
as `dali.py`/the IR family).

## Validation

sigrok has no IrDA decoder at any layer (confirmed: listed as a
0%-complete future candidate on sigrok's own decoder wiki), so this
protocol is validated by two independent oracles instead of the usual
single sigrok round-trip:

1. **A custom sigrok decoder** written for this project,
   `tests/custom_decoders/irda/pd.py` — a real `sigrokdecode.Decoder`
   reassembling SIR bits into bytes and parsing Address/Control/Info/FCS,
   loaded via `SIGROKDECODE_DIR` (confirmed to *add* to, not replace,
   sigrok's system decoder search path). See
   `tests/test_sigrok_roundtrip.py::test_irda_roundtrips_through_custom_sigrok_decoder`.
2. **`tshark`'s real `irlap` dissector**, fed a synthetic classic-pcap file
   (`tests/_irda_pcap.py::build_irda_pcap`, link-layer type 144 —
   `LINKTYPE_LINUX_IRDA` — with a 16-byte Linux "cooked" SLL pseudo-header
   per frame). See
   `tests/test_sigrok_roundtrip.py::test_irda_cross_validates_against_wireshark_irlap_dissector`,
   which asserts both tools agree on the same address/control/payload
   values from the same encoded frame.

A real Linux IrDA capture only ever sees a frame *after* the driver has
already validated and stripped its trailing FCS (the same reason a
captured Ethernet frame usually has none either) — confirmed empirically
while building the pcap helper: feeding `tshark` the 2 real FCS bytes too
makes it misparse them as extra IrLMP payload content. So the pcap side
carries Address+Control+Information only; the `.sr` side (and the custom
decoder validating it) still carries a real FCS on the wire, since a real
transmitter always sends one.

**Needs 2+ back-to-back frame calls for a clean custom-decoder decode**,
same established shape as this repo's PS/2/LIN/DCF77/EM4100 round-trip
cases: the default 2% trailing idle margin (`pad_idle`) isn't long enough
for the custom decoder's own 12-bit-period idle timeout (needed to tell
"next byte, same frame" from "frame ended" — the timeout must exceed the
9-bit-period worst-case intra-frame silence bound, see `pd.py`'s module
docstring), but the protocol's own 16-bit-period inter-frame gap comfortably
clears it, so a second frame's mere presence (not its content) is what
flushes the first.

```json
{
  "samplerate": 10000000,
  "protocols": [
    {
      "id": "ir0", "type": "irda",
      "params": { "baudrate": 115200 },
      "operations": [
        { "op": "send_xid", "source_address": 305419896 },
        { "op": "send_i_frame", "address": 1, "ns": 0, "nr": 0, "info": "Hi", "datatype": "text" },
        { "op": "send_i_frame", "address": 1, "ns": 1, "nr": 0, "info": "Ho", "datatype": "text", "final": false }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/irda_basic.svg" },
    { "type": "sigrok", "path": "output/irda_basic.sr" },
    { "type": "vcd", "path": "output/irda_basic.vcd" }
  ]
}
```
