# USB Mass Storage

Back to [usage overview](../USAGE.md).

`type: "usb_msc"` — `UsbMassStorage`, `protocols/usb_msc.py`, stacked on
`UsbBus` (`stack_on: "<usb node id>"`, see [usb.md](usb.md) for the
transport itself). USB Mass Storage's Bulk-Only Transport (BOT) plus a
narrow SCSI command subset — deliberately scoped down the same way
`spiflash.py`/`rtc8564.py` scope their own devices: no Bulk-Only Mass
Storage Reset or Get-Max-LUN control requests, no vendor commands, no
multi-LUN, and a single fixed 512-byte logical block size (not
configurable) used only to compute READ(10)/WRITE(10)'s
transfer-length-in-blocks CDB field. Everything here rides on raw bulk
`token`/`data_packet`/`handshake` calls — the BOT data path uses zero
control transfers.

Every operation is one full CBW (Command Block Wrapper, 31 bytes, bulk
OUT) → optional data stage (bulk IN or OUT, per the command) → CSW
(Command Status Wrapper, 13 bytes, bulk IN) transaction, wrapped in a
single `builder.frame()` for one summary annotation over the whole
logical command. `dCBWTag` is a per-instance incrementing counter
starting at 1; `bCSWStatus` is always 0 (Command Passed) — this tool
generates a happy-path capture, it doesn't model device error responses.
DATA0/DATA1 toggle state is tracked per `(address, endpoint)` pair inside
`UsbMassStorage` itself (`UsbBus` has no notion of it), persisting across
every call for the node's lifetime.

**Byte order**: CBW/CSW wrapper fields (`dCBWSignature`, `dCBWTag`,
`dCBWDataTransferLength`, `dCSWSignature`, `dCSWTag`, `dCSWDataResidue`)
are little-endian. SCSI CDB fields inside the CBW's `CBWCB` (LBA,
transfer length), and the READ_CAPACITY(10) response, are big-endian —
the opposite convention, and the easiest place in this protocol to
introduce a silent bug.

No mainline sigrok decoder exists for USB Mass Storage (confirmed: only
`usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` ship
under `/usr/share/libsigrokdecode/decoders/`), so this is validated
against a decoder this project wrote itself
(`tests/custom_decoders/usb_msc/pd.py`), stacked on the mainline
`usb_packet` decoder exactly the way the real `usb_request` decoder is
(`inputs = ['usb_packet']`) — single-oracle tier, deliberately, matching
CLAUDE.md's oracle-tier framing for USB application-layer protocols where
no cheaper mainline/vendored decoder exists and a second independent
oracle isn't worth building. Exercised by
`tests/test_sigrok_roundtrip.py`'s
`test_usb_msc_roundtrips_through_custom_sigrok_decoder`.

## Constructor params

None of its own — `usb_msc` only takes `stack_on` (pointing at a `usb`
node) and `operations`.

## Operations

- **`scsi_inquiry`** — `address`, `endpoint_out=1`, `endpoint_in=2`,
  `vendor`, `product`. INQUIRY (opcode `0x12`): synthesizes a 36-byte SCSI
  INQUIRY response — byte 0 = `0x00` (direct-access block device), byte 1
  = `0x80` (RMB/removable bit set, an arbitrary but explicit choice for
  this synthetic device), `vendor` padded/truncated to 8 ASCII bytes at
  offset 8, `product` padded/truncated to 16 ASCII bytes at offset 16,
  rest zero-filled. The CDB's allocation length is fixed at 36 to match.
- **`scsi_read_capacity10`** — `address`, `endpoint_out=1`,
  `endpoint_in=2`, `last_lba`, `block_size=512`. READ CAPACITY(10)
  (opcode `0x25`): 8-byte response, `last_lba` (4 bytes BE) + `block_size`
  (4 bytes BE).
- **`scsi_test_unit_ready`** — `address`, `endpoint_out=1`,
  `endpoint_in=2`. TEST UNIT READY (opcode `0x00`): no data stage at all
  (`dCBWDataTransferLength = 0`).
- **`scsi_read10`** — `address`, `endpoint_out=1`, `endpoint_in=2`, `lba`,
  `data`, `datatype="bytes"`. READ(10) (opcode `0x28`): `data` is the
  response payload (IN direction), decoded via the project's normal
  datatype convention (`decode_payload_with_floating`, same alphabet as
  every other protocol's byte-array fields). `data`'s length must be a
  positive multiple of the fixed 512-byte block size — used to compute
  the CDB's transfer-length-in-blocks field.
- **`scsi_write10`** — same shape as `scsi_read10`, but `data` is the
  OUT-direction payload the host writes, and the CDB opcode is `0x2A`.

`vendor`/`product` are plain ASCII strings, not datatype-controlled byte
arrays. `data` on `scsi_read10`/`scsi_write10` already has full
floating-marker support via the shared `_PAYLOAD_FIELDS` mechanism (the
field name `data` is already registered there).

## Example — `examples/usb_msc_basic.json`

```json
{
  "samplerate": 96000000,
  "protocols": [
    { "id": "usb0", "type": "usb", "operations": [] },
    {
      "id": "msc0", "type": "usb_msc", "stack_on": "usb0",
      "operations": [
        { "op": "scsi_inquiry", "address": 5, "vendor": "PWGEN", "product": "SyntheticDisk" },
        { "op": "scsi_read_capacity10", "address": 5, "last_lba": 2047, "block_size": 512 },
        { "op": "scsi_test_unit_ready", "address": 5 },
        { "op": "scsi_read10", "address": 5, "lba": 0, "data": "<1024 hex chars, 512 bytes>", "datatype": "hex" },
        { "op": "scsi_write10", "address": 5, "lba": 1, "data": "<1024 hex chars, 512 bytes>", "datatype": "hex" }
      ]
    }
  ],
  "outputs": [
    { "type": "svg", "path": "output/usb_msc_basic.svg" },
    { "type": "sigrok", "path": "output/usb_msc_basic.sr" },
    { "type": "vcd", "path": "output/usb_msc_basic.vcd" }
  ]
}
```

(The real file uses one full 512-byte block per `read10`/`write10` call —
a repeated 4-byte pattern encoded as a 1024-character hex string — rather
than a hand-typed 512-entry int array, since `scsi_read10`/`scsi_write10`
both require the payload length to be a positive multiple of 512 bytes.)

```bash
.venv/bin/python -m protowavegen --config examples/usb_msc_basic.json
```

To decode a generated `.sr` file with the custom decoder yourself (not
part of the system `sigrok-cli` install):

```bash
SIGROKDECODE_DIR=tests/custom_decoders sigrok-cli \
    -i output/usb_msc_basic.sr \
    -P "usb_signalling:dp=usb0.dp:dm=usb0.dm:signalling=full-speed,usb_packet,usb_msc" \
    -A usb_msc
```

`usb_msc` is the last decoder in the stack, so to filter by one specific
annotation class instead of all of them, pass `-A usb_msc=<class>` (e.g.
`-A usb_msc=inquiry`) — the annotation classes are `cbw`, `csw`,
`inquiry`, `read-capacity`, `read10`, `write10`, `test-unit-ready`, and
`warning`.
