# USB DFU

Back to [usage overview](../USAGE.md).

`type: "usb_dfu"` — `UsbDfu`, `protocols/usb_dfu.py`. USB DFU (Device
Firmware Upgrade) class 1.1 requests, stacked on `UsbBus` (`type: "usb"`,
`stack_on: "<usb node id>"`). DFU is entirely control-transfer-based — no
bulk/interrupt endpoints at all, the simplest of this project's USB
stacked protocols in that respect — so every operation here goes through
`UsbBus.control_transfer` exclusively.

Scope is deliberately narrow (mirrors `spiflash.py`/`jedec_cfi.py`'s own
"don't build more than the scoped mode" precedent): no vendor DFU
extensions (ST's DfuSe, etc.), no runtime-vs-DFU-mode descriptor switching
modeled, and every transfer is the single-attempt, everything-ACKs happy
path `control_transfer` already provides. Implements `DFU_DNLOAD`,
`DFU_UPLOAD`, `DFU_GETSTATUS`, `DFU_ABORT` — not `DFU_DETACH`/
`DFU_CLRSTATUS`/`DFU_GETSTATE` (rare in a synthesized-timing-diagram use
case; trivial to add later following the same pattern).

No mainline sigrok decoder exists for DFU at any layer (confirmed:
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` are the
only USB-family decoders libsigrokdecode ships), so this bus is validated
against a custom decoder written for this project
(`tests/custom_decoders/usb_dfu/pd.py`), stacked on sigrok's own real
`usb_packet` decoder exactly the way `usb_request` is — single-oracle
tier, exercised by
`tests/test_sigrok_roundtrip.py::test_usb_dfu_roundtrips_through_custom_sigrok_decoder`.

## Constructor params

```json
"params": {}
```

No constructor params of its own — just `stack_on` naming a `usb` node.

## Operations

- **`dnload`** — `address`, `interface=0`, `block_num`, `data`,
  `datatype="bytes"`. `DFU_DNLOAD` (bRequest=1): host sends one firmware
  block. `wValue=block_num`, `wIndex=interface`, `wLength=len(data)`,
  `out_data=data`. **`block_num=0, data=[]`** (an empty list, not omitted)
  is the real DFU wire signal for "download complete" (USB DFU 1.1 section
  6.1.3) — it still produces a genuine zero-length OUT DATA1 packet (a real
  OUT token + empty-payload DATA1 + ACK), not a skipped Data stage.
- **`upload`** — `address`, `interface=0`, `block_num`, `data`,
  `datatype="bytes"`. `DFU_UPLOAD` (bRequest=2): device sends one firmware
  block back. `wValue=block_num`, `wIndex=interface`, `wLength=len(data)`,
  `in_data=data` (the synthesized block content — this tool generates
  diagrams, it doesn't sense real firmware).
- **`get_status`** — `address`, `interface=0`, `status`,
  `poll_timeout_ms=0`, `state`. `DFU_GETSTATUS` (bRequest=3): device
  reports its 6-byte status block (`bStatus`, `bwPollTimeout` 3 bytes LE,
  `bState`, `iString=0`). `state` is one of the 11 real DFU 1.1 state
  values, exposed as module constants:

  | Constant                  | Value | Real state name          |
  |----------------------------|-------|---------------------------|
  | `DFU_APP_IDLE`             | 0     | appIDLE                   |
  | `DFU_APP_DETACH`           | 1     | appDETACH                 |
  | `DFU_IDLE`                 | 2     | dfuIDLE                   |
  | `DFU_DNLOAD_SYNC`          | 3     | dfuDNLOAD-SYNC            |
  | `DFU_DNBUSY`               | 4     | dfuDNBUSY                 |
  | `DFU_DNLOAD_IDLE`          | 5     | dfuDNLOAD-IDLE            |
  | `DFU_MANIFEST_SYNC`        | 6     | dfuMANIFEST-SYNC          |
  | `DFU_MANIFEST`             | 7     | dfuMANIFEST               |
  | `DFU_MANIFEST_WAIT_RESET`  | 8     | dfuMANIFEST-WAIT-RESET    |
  | `DFU_UPLOAD_IDLE`          | 9     | dfuUPLOAD-IDLE            |
  | `DFU_ERROR`                | 10    | dfuERROR                  |

- **`abort`** — `address`, `interface=0`. `DFU_ABORT` (bRequest=6):
  zero-length-data-stage request (no Data stage at all) that returns the
  device to `dfuIDLE`.

`address`/`interface`/`block_num`/`status`/`poll_timeout_ms`/`state` are
always plain ints — no `datatype` on them. `data` (on `dnload`/`upload`)
uses the plain `bytes`/`text`/`hex` datatype convention
(`decode_payload()`), no floating-marker support — DFU firmware bytes are
always concretely driven, the same convention `lin.py`/`modbus_rtu.py` use
for their own byte-array fields.

Each operation wraps its `control_transfer` call in `builder.frame()` and
adds one summary `field` annotation naming the request and its key fields
(e.g. `"DFU DNLOAD block=0 len=4"`, `"DFU GETSTATUS status=0
state=dfuDNBUSY"`) on top of `UsbBus`'s own per-packet field annotations.

## Example — `examples/usb_dfu_basic.json`

A real-ish DFU download flow: send one firmware block, poll status through
busy/idle, send the empty "download complete" DNLOAD, then poll status
through manifest/idle.

```json
{
  "samplerate": 192000000,
  "protocols": [
    { "id": "usb0", "type": "usb", "operations": [] },
    {
      "id": "usb_dfu0",
      "type": "usb_dfu",
      "stack_on": "usb0",
      "operations": [
        { "op": "dnload", "address": 5, "block_num": 0, "data": [222, 173, 190, 239] },
        { "op": "get_status", "address": 5, "status": 0, "state": 4 },
        { "op": "get_status", "address": 5, "status": 0, "state": 5 },
        { "op": "dnload", "address": 5, "block_num": 0, "data": [] },
        { "op": "get_status", "address": 5, "status": 0, "state": 7 },
        { "op": "get_status", "address": 5, "status": 0, "state": 2 }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/usb_dfu_basic.svg" },
    { "type": "sigrok", "path": "output/usb_dfu_basic.sr" },
    { "type": "vcd", "path": "output/usb_dfu_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/usb_dfu_basic.json
```

Decode the generated `.sr` through the custom decoder (not part of the
system `sigrok-cli` install, so `SIGROKDECODE_DIR` must point at this
repo's `tests/custom_decoders/`), stacked on sigrok's real `usb_signalling`
+ `usb_packet`:

```bash
SIGROKDECODE_DIR=tests/custom_decoders sigrok-cli -i output/usb_dfu_basic.sr \
    -P usb_signalling:dp=usb0.dp:dm=usb0.dm:signalling=full-speed,usb_packet,usb_dfu \
    -A usb_dfu
```

```
usb_dfu-1: DNLOAD: block=0 len=4
usb_dfu-1: GETSTATUS (interface=0)
usb_dfu-1: Status: 0 State: dfuDNBUSY
usb_dfu-1: GETSTATUS (interface=0)
usb_dfu-1: Status: 0 State: dfuDNLOAD-IDLE
usb_dfu-1: DNLOAD: block=0 len=0
usb_dfu-1: GETSTATUS (interface=0)
usb_dfu-1: Status: 0 State: dfuMANIFEST
usb_dfu-1: GETSTATUS (interface=0)
usb_dfu-1: Status: 0 State: dfuIDLE
```

(Output above was captured directly from a real `sigrok-cli` run against
this example, not hand-written.)
