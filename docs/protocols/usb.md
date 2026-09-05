# USB (Full-Speed)

Back to [usage overview](../USAGE.md).

`type: "usb"` — `UsbBus`, `protocols/usb.py`. Raw USB Full-Speed
(12 Mbit/s) electrical/packet layer only: SYNC field, PID(+complement),
NRZI encoding, 6-consecutive-1s bit-stuffing, CRC5 (tokens)/CRC16 (data),
EOP. Two plain `DIGITAL` signals, `dp`/`dm` (D+/D-) — push-pull, not
open-drain, so `z`/`Z` on a payload field needs `l`/`h` used explicitly
(same convention as CAN/SPI/Microwire).

v1 scope is control transfers only: SETUP/IN/OUT tokens, DATA0/DATA1,
ACK/NAK/STALL handshakes. No application-layer device modeling
(HID/CDC/Mass-Storage/DFU are a separate `StackedProtocol` layer, meant to
be built on top of this the way LIN stacks on `UartTransport`). No SPLIT
packets, no High-Speed (480 Mbit/s) signaling — Full-Speed only.

Validated against sigrok's real 3-decoder stack (`usb_signalling` ->
`usb_packet` -> `usb_request`) — no custom decoder needed for this layer.

## Constructor params

```json
"params": {}
```

No constructor params — Full-Speed's 12 Mbit/s bit rate is fixed, not
configurable in this v1.

## Operations

- **`token`** — `pid` (`"OUT"`/`"IN"`/`"SETUP"`), `address` (0-127),
  `endpoint` (0-15), `driver="host"` (tokens are always host-originated).
  SYNC + PID + 7-bit ADDR + 4-bit EP + CRC5 + EOP.
- **`data_packet`** — `pid` (`"DATA0"`/`"DATA1"`), `data=None`,
  `datatype="bytes"`, `driver` (required — device drives an IN transfer's
  data, host drives OUT/SETUP's). SYNC + PID + 0-1024 payload bytes +
  CRC16 + EOP. Floating-marker capable (`l`/`h`, not `z`).
- **`handshake`** — `pid` (`"ACK"`/`"NAK"`/`"STALL"`), `driver` (required).
  SYNC + PID + EOP only — no CRC field at all.
- **`control_transfer`** — the full 3-stage control transfer: `address`,
  `endpoint`, `setup_data` (exactly 8 bytes: bmRequestType, bRequest,
  wValue, wIndex, wLength) with `setup_data_datatype="bytes"`, then at
  most one of `in_data`/`out_data` (with their own
  `in_data_datatype`/`out_data_datatype`) for an optional Data stage, then
  an automatic Status stage (a zero-length DATA1 in the *opposite*
  direction from the Data stage — or `IN` when neither `in_data` nor
  `out_data` was given, matching every real zero-length-data-stage
  request such as SET_ADDRESS/SET_CONFIGURATION). Every stage's handshake
  is a plain ACK — this generates the single-attempt, everything-ACKs
  happy path, the same convention `CanBus.send` already uses for not
  modeling bus contention.

`token`/`data_packet`/`handshake` stay independently callable (as their
own JSON `op` entries, or from a future stacked protocol's Python code)
for bulk/interrupt transactions, which have no Setup/Status stages and
sit outside `control_transfer`'s scope.

## Example — `examples/usb_basic.json`

A GET_DESCRIPTOR-shaped control transfer: SETUP requests the first 8
bytes of the device descriptor, the IN data stage returns them, and the
Status stage (OUT direction, since the Data stage was IN) closes the
transfer.

```json
{
  "samplerate": 192000000,
  "protocols": [
    {
      "id": "usb0",
      "type": "usb",
      "operations": [
        {
          "op": "control_transfer",
          "address": 5,
          "endpoint": 0,
          "setup_data": [128, 6, 0, 1, 0, 0, 8, 0],
          "in_data": [18, 1, 16, 1, 0, 0, 0, 64]
        }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/usb_basic.svg" },
    { "type": "sigrok", "path": "output/usb_basic.sr" },
    { "type": "vcd", "path": "output/usb_basic.vcd" }
  ]
}
```

```bash
.venv/bin/python -m protowavegen --config examples/usb_basic.json
```

Decode the generated `.sr` through sigrok's real USB decoder stack
(channel names/options come from `sigrok-cli --show -P usb_signalling` —
Full-Speed must be passed explicitly, since that decoder's own default is
`automatic`):

```bash
sigrok-cli -i output/usb_basic.sr \
    -P usb_signalling:dp=usb0.dp:dm=usb0.dm:signalling=full-speed,usb_packet,usb_request \
    -A usb_request
```

```
usb_request-1: SETUP in: [ 80 06 00 01 00 00 08 00 ][ 12 01 10 01 00 00 00 40 ] : ACK
```
