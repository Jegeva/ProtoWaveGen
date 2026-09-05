# USB HID

Back to [usage overview](../USAGE.md).

`type: "usb_hid"` — `UsbHid`, `protocols/usb_hid.py`. A minimal USB Human
Interface Device, stacked on `UsbBus` (`type: "usb"`). Scope is
deliberately narrow, mirroring `spiflash.py`/`rtc8564.py`'s precedent of a
real-but-narrow device subset rather than full HID spec coverage: a fixed
3-byte relative-mouse report (buttons bitmap, signed X, signed Y, each
-127..127) plus `GET_DESCRIPTOR` requests for the HID and REPORT
descriptor types.

Out of scope: real report-descriptor *parsing* (the synthesized
`REPORT_DESCRIPTOR` bytes are realistic but fixed, not driven by any
user-supplied field layout), multiple report IDs, OUT reports,
SET_IDLE/SET_PROTOCOL/SET_REPORT, and boot-protocol negotiation.

No mainline sigrok decoder exists for USB HID (confirmed: only
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` ship
under `/usr/share/libsigrokdecode/decoders/`), so this is validated
against a self-authored decoder instead — single-oracle tier, per the
oracle-tier writeup in the repo's `CLAUDE.md` (a second/cross-checking
oracle was already researched and ruled out as not worth it for USB
app-layer protocols; see CLAUDE.md's USB HID/CDC/MSC/DFU discussion for
why). The decoder (`tests/custom_decoders/usb_hid/pd.py`) stacks on
sigrok's own mainline `usb_packet` decoder — the same
`inputs = ['usb_packet']` mechanism `usb_request` itself uses — so the
electrical (`usb_signalling`) and packet-framing (`usb_packet`) layers
underneath are still real, independently-implemented sigrok code; only
the HID-specific reassembly (control-transfer descriptor requests,
interrupt-IN reports) is self-authored.

## Constructor params

```json
"stack_on": "usb0"
```

No params of its own beyond `stack_on` (pointing at a `type: "usb"` node)
— all bit timing comes from the underlying `UsbBus`.

## Operations

- **`get_hid_descriptor`** — `address`, `endpoint=0`. A `GET_DESCRIPTOR`
  control transfer for the HID descriptor (`bmRequestType=0x81`
  device-to-host/standard/interface, `bRequest=0x06`,
  `wValue=(0x21<<8)`, `wIndex=<endpoint's interface>`, `wLength=9`). The
  synthesized 9-byte descriptor content: `bLength=9`,
  `bDescriptorType=0x21`, `bcdHID=0x0110` (LE), `bCountryCode=0`,
  `bNumDescriptors=1`, `bDescriptorType=0x22` (REPORT),
  `wDescriptorLength` (LE) = the length of the report descriptor below.
- **`get_report_descriptor`** — `address`, `endpoint=0`. Same shape,
  descriptor type `0x22`, returning a fixed, realistic report descriptor
  for a 3-button relative mouse (`UsbHid.REPORT_DESCRIPTOR` in
  `usb_hid.py` — Usage Page Generic Desktop/Mouse, a 3-bit button array
  padded to a byte, then signed 8-bit relative X/Y).
- **`send_report`** — `buttons`, `x`, `y` (all plain ints — `x`/`y` wrap
  two's-complement into a byte, e.g. `x=-5` encodes as `0xFB`), `address`,
  `endpoint=1`. **Not** a control transfer: a raw interrupt-IN transaction
  (`UsbBus.token(pid="IN")` + `data_packet` + `handshake(pid="ACK")`).
  Each `UsbHid` instance tracks its own DATA0/DATA1 toggle across
  successive calls (starts at DATA0, flips every call) — `UsbBus` itself
  has no notion of toggle state, the same division of responsibility
  `I2CDevice`/`OneWireDevice` already use for their own addressing state
  on top of a shared transport.

Every operation wraps its packet sequence in one summary `field`
annotation spanning the whole logical operation (`"GET_DESCRIPTOR(HID)"`,
`"GET_DESCRIPTOR(REPORT)"`, or `"HID report buttons=0x.. x=.. y=.."`), in
addition to `UsbBus`'s own per-packet/per-byte annotations.

## Example — `examples/usb_hid_basic.json`

```json
{
  "samplerate": 192000000,
  "protocols": [
    { "id": "usb0", "type": "usb", "operations": [] },
    {
      "id": "usb_hid0", "type": "usb_hid", "stack_on": "usb0",
      "operations": [
        { "op": "get_hid_descriptor", "address": 5, "endpoint": 0 },
        { "op": "get_report_descriptor", "address": 5, "endpoint": 0 },
        { "op": "send_report", "buttons": 1, "x": 10, "y": -5, "address": 5, "endpoint": 1 },
        { "op": "send_report", "buttons": 0, "x": -20, "y": 20, "address": 5, "endpoint": 1 },
        { "op": "send_report", "buttons": 2, "x": 0, "y": 0, "address": 5, "endpoint": 1 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/usb_hid_basic.svg" },
    { "type": "sigrok", "path": "output/usb_hid_basic.sr" },
    { "type": "vcd", "path": "output/usb_hid_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/usb_hid_basic.json
```

Decode the generated `.sr` through the custom decoder (not part of the
system `sigrok-cli` install), stacked on sigrok's own mainline
`usb_signalling`/`usb_packet` decoders:

```bash
SIGROKDECODE_DIR=tests/custom_decoders sigrok-cli -i output/usb_hid_basic.sr \
    -P "usb_signalling:dp=usb0.dp:dm=usb0.dm:signalling=full-speed,usb_packet,usb_hid" \
    -A usb_hid
```

```
usb_hid-1: GET_DESCRIPTOR(HID): 09 21 10 01 00 01 22 32 00
usb_hid-1: GET_DESCRIPTOR(REPORT): 05 01 09 02 A1 01 09 01 A1 00 05 09 19 01 29 03 15 00 25 01 95 03 75 01 81 02 95 01 75 05 81 01 05 01 09 30 09 31 15 81 25 7F 75 08 95 02 81 06 C0 C0
usb_hid-1: buttons=0x01 x=10 y=-5
usb_hid-1: buttons=0x00 x=-20 y=20
usb_hid-1: buttons=0x02 x=0 y=0
```

## A real bug this feature found in `UsbBus` itself

Writing this decoder's unit tests surfaced a genuine pre-existing bug in
`UsbBus._send_packet`'s role-tracking, unrelated to HID specifically:
when USB bit-stuffing inserts a stuffed bit *strictly inside* a single
payload byte's own bit-run (common — any byte whose LSB-first bits
contain an internal run of 6+ consecutive 1s, e.g. `0x7F` or `0xFF`), the
transient `"stuff"` role was treated as a real role transition, causing
that byte's `field`/`unit` annotations to be flushed **twice** with the
same value instead of once. This never corrupted the actual encoded bits
or a real decoder's output (sigrok/this project's own custom decoders
read real wire bits, not annotations), only the diagram-facing annotation
stream — but it was real and would have affected any protocol's payload
bytes hitting that bit pattern, not just HID's. Fixed in `usb.py`'s
`_send_packet` by treating `"stuff"` as transparent to role-transition
bookkeeping (see that method's comment for the full explanation); zero
regressions across the existing `usb`/`usb_hid` test suites, since no
existing test payload happened to contain such a bit pattern before this
feature's synthesized HID report descriptor did.
