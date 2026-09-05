# USB CDC/ACM

Back to [usage overview](../USAGE.md).

`type: "usb_cdc"` — `UsbCdcAcm`, `protocols/usb_cdc.py`. USB CDC/ACM (the
virtual-serial-port class), stacked on `UsbBus` (`type: "usb"`, Full-Speed
only — see `docs/protocols/usb.md`). Scope is deliberately narrow, same
"real but narrow" precedent as `spiflash.py`/`rtc8564.py`: the two class
requests a host uses to configure a virtual serial port
(`SET_LINE_CODING`, `SET_CONTROL_LINE_STATE`) and outbound bulk data on
the data interface. Out of scope: the notification endpoint (interrupt IN
`SERIAL_STATE` from device to host), `GET_LINE_CODING`, `SEND_BREAK`, and
multi-interface modeling — CDC's usual two-interface split (control +
data) is collapsed to a single `interface` number reused for both, since
this tool has no USB configuration-descriptor concept to assign real
interface numbers from.

No mainline sigrok decoder exists for USB CDC (confirmed: only
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` ship
under `/usr/share/libsigrokdecode/decoders/`), so this bus is validated
against a custom, self-authored decoder (single-oracle tier, per
CLAUDE.md's oracle-tier writeup — a second, independent oracle for a
narrow USB application-layer protocol like this one isn't worth building)
vendored under `tests/custom_decoders/usb_cdc/` and exercised by
`tests/test_sigrok_roundtrip.py`'s
`test_usb_cdc_roundtrips_through_custom_sigrok_decoder`. It stacks on top
of the mainline `usb_packet` decoder's own `OUTPUT_PYTHON` stream — the
same PD-stacking mechanism sigrok's own `usb_request` uses on top of
`usb_packet`.

Every operation's underlying `UsbBus` packets (SETUP/OUT/IN tokens,
DATA0/DATA1, ACK handshakes) already carry their own detailed `"field"`
annotations (PID name, ADDR/EP, each data byte). `UsbCdcAcm` adds a
human-readable, whole-operation summary on its own `"cdc"` annotation
track instead of layering a second `"field"` annotation over the same
span — see `usb_cdc.py`'s own module docstring for why (avoiding a
same-track overlapping-range annotation, which paints over itself in the
SVG).

## Constructor params

None beyond `stack_on` — `UsbCdcAcm` has no configuration of its own; it
reuses whatever `UsbBus` node it's stacked on.

## Operations

- **`set_line_coding`** — `address`, `endpoint=0`, `interface=0`, `baud`
  (required), `data_bits=8`, `stop_bits=0`, `parity=0`. CDC class request
  `SET_LINE_CODING` (`bRequest=0x20`, `bmRequestType=0x21`): a 7-byte Line
  Coding structure (`dwDTERate` little-endian, `bCharFormat`,
  `bParityType`, `bDataBits`) sent as the control transfer's OUT data
  stage. `stop_bits`/`parity` are the raw CDC codes, passed through
  unvalidated (stop: 0=1, 1=1.5, 2=2 bits; parity: 0=none, 1=odd, 2=even,
  3=mark, 4=space) — same "carry the bits, don't behaviorally simulate
  them" precedent `rtc8564.py` uses for its VL/century flag bits.
- **`set_control_line_state`** — `address`, `endpoint=0`, `interface=0`,
  `dtr=True`, `rts=True`. CDC class request `SET_CONTROL_LINE_STATE`
  (`bRequest=0x22`): `wValue` bit 0 = DTR, bit 1 = RTS, zero-length data
  stage.
- **`send_data`** — `address`, `endpoint=2`, `data`, `datatype="bytes"`.
  Bulk OUT data transfer — not a control transfer: a bare OUT token + one
  DATA packet + ACK, using `UsbBus.token`/`data_packet`/`handshake`
  directly. `UsbCdcAcm` tracks its own per-instance DATA0/DATA1 toggle,
  starting at DATA0 and flipping on every call (`UsbBus` itself has no
  notion of toggle state). `data` has full `datatype` support (`"bytes"`
  the default int-array form, `"text"`, `"hex"`) via the same
  `decode_payload()`/`--data-hex`/`--data-string`/`--data-int` machinery
  every other protocol's payload fields use.

## Example — `examples/usb_cdc_basic.json`

```json
{
  "samplerate": 192000000,
  "protocols": [
    { "id": "usb0", "type": "usb", "operations": [] },
    {
      "id": "usb_cdc0", "type": "usb_cdc", "stack_on": "usb0",
      "operations": [
        { "op": "set_line_coding", "address": 5, "baud": 115200, "data_bits": 8, "stop_bits": 0, "parity": 0 },
        { "op": "set_control_line_state", "address": 5, "dtr": true, "rts": true },
        { "op": "send_data", "address": 5, "data": "Hello", "datatype": "text" },
        { "op": "send_data", "address": 5, "data": "World", "datatype": "text" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/usb_cdc_basic.svg" },
    { "type": "sigrok", "path": "output/usb_cdc_basic.sr" },
    { "type": "vcd", "path": "output/usb_cdc_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/usb_cdc_basic.json
```

To decode a generated `.sr` file with the custom decoder yourself (not
part of the system `sigrok-cli` install):

```bash
SIGROKDECODE_DIR=tests/custom_decoders sigrok-cli \
    -i output/usb_cdc_basic.sr \
    -P "usb_signalling:dp=usb0.dp:dm=usb0.dm:signalling=full-speed,usb_packet,usb_cdc" \
    -A usb_cdc
```

Expected output (annotation text, one line per class the decoder
recognized — verified by hand against real `sigrok-cli` output during
implementation, not just asserted in the test suite):

```
usb_cdc-1: baud=115200 format=1 parity=None bits=8
usb_cdc-1: DTR=1 RTS=1
usb_cdc-1: data: 0x48 'H' 0x65 'e' 0x6C 'l' 0x6C 'l' 0x6F 'o'
usb_cdc-1: data: 0x57 'W' 0x6F 'o' 0x72 'r' 0x6C 'l' 0x64 'd'
```
