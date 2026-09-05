##
## Custom sigrok protocol decoder for USB Mass Storage (Bulk-Only
## Transport + a SCSI subset), stacked on the mainline `usb_packet`
## decoder exactly the way the real `usb_request` decoder is (see
## /usr/share/libsigrokdecode/decoders/usb_request/pd.py's own
## `inputs = ['usb_packet']` and its `decode(self, ss, es, data)`
## signature, read during design). No mainline sigrok decoder for USB
## Mass Storage exists (confirmed: only usb_packet/usb_request/
## usb_signalling/usb_power_delivery ship).
##
## This is a from-scratch, decoder-first reading of the BOT wire format,
## not this project's own encoder (`protowavegen.protocols.usb_msc.
## UsbMassStorage`) run in reverse: it reasons from "is this OUT packet
## 31 bytes starting with the CBW signature" / "is this IN packet 13
## bytes starting with the CSW signature", not from replaying the
## encoder's own call sequence.
##
## Recognizes one full CBW -> [data] -> CSW Bulk-Only transaction from the
## individual OUT/IN DATA packets `usb_packet` reports on its
## OUTPUT_PYTHON stream, using signature + direction + length alone -- no
## multi-packet reassembly is needed here (unlike `usb_request`'s own
## BULK IN/OUT accumulation across many packets), because this project's
## generator always sends a whole CBW, a whole CSW, and a whole
## data-stage payload as a single DATA0/DATA1 packet each (matching
## `UsbBus.data_packet`'s own "no real max-packet-size chunking"
## simplification, already established for `control_transfer`).
##
## Byte order: CBW/CSW wrapper fields (dCBWSignature, dCBWTag,
## dCBWDataTransferLength, dCSWSignature, dCSWTag, dCSWDataResidue) are
## little-endian; SCSI CDB fields (LBA, transfer length) inside the CBW's
## CBWCB, and the READ_CAPACITY(10) response, are big-endian --
## cross-checked by hand-constructing the exact byte sequences against
## the encoder side (usb_msc.py) during design, not assumed.
##

import sigrokdecode as srd

_CBW_SIGNATURE = 0x43425355  # "USBC"
_CSW_SIGNATURE = 0x53425355  # "USBS"

(ANN_CBW, ANN_CSW, ANN_INQUIRY, ANN_READ_CAPACITY, ANN_READ10, ANN_WRITE10,
    ANN_TEST_UNIT_READY, ANN_WARNING) = range(8)


def _le32(b):
    return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)


def _be32(b):
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


class Decoder(srd.Decoder):
    api_version = 3
    id = 'usb_msc'
    name = 'USB MSC'
    longname = 'USB Mass Storage (Bulk-Only Transport + SCSI)'
    desc = 'USB Mass Storage Bulk-Only Transport CBW/CSW framing and a SCSI command subset.'
    license = 'gplv2+'
    inputs = ['usb_packet']
    outputs = []
    tags = ['PC']
    annotations = (
        ('cbw', 'CBW'),
        ('csw', 'CSW'),
        ('inquiry', 'INQUIRY'),
        ('read-capacity', 'READ CAPACITY'),
        ('read10', 'READ10'),
        ('write10', 'WRITE10'),
        ('test-unit-ready', 'TEST UNIT READY'),
        ('warning', 'Warnings'),
    )
    annotation_rows = (
        ('wrappers', 'CBW/CSW', (ANN_CBW, ANN_CSW)),
        ('commands', 'SCSI commands',
         (ANN_INQUIRY, ANN_READ_CAPACITY, ANN_READ10, ANN_WRITE10, ANN_TEST_UNIT_READY)),
        ('warnings-row', 'Warnings', (ANN_WARNING,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.samplerate = None
        self.token_type = None   # 'OUT' / 'IN', set on TOKEN, cleared on HANDSHAKE
        self.token_ss = None
        self.pending_cmd = None  # dict set at CBW time for commands with a data stage

    def metadata(self, key, value):
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putg(self, ss, es, cls, text):
        self.put(ss, es, self.out_ann, [cls, [text]])

    def _handle_cbw(self, b, ss, es):
        tag = _le32(b[4:8])
        dlen = _le32(b[8:12])
        flags = b[12]
        lun = b[13] & 0x0F
        cblen = b[14] & 0x1F
        cdb = b[15:15 + cblen]
        direction = 'IN' if flags & 0x80 else 'OUT'
        self.putg(ss, es, ANN_CBW, 'CBW tag=%d dir=%s lun=%d len=%d' % (tag, direction, lun, dlen))

        opcode = cdb[0] if cdb else None
        if opcode == 0x12:
            alloc_len = cdb[4]
            self.pending_cmd = {'ann': ANN_INQUIRY, 'ss': ss, 'alloc_len': alloc_len, 'needs_data': dlen > 0}
        elif opcode == 0x25:
            self.pending_cmd = {'ann': ANN_READ_CAPACITY, 'ss': ss, 'needs_data': dlen > 0}
        elif opcode == 0x28:
            lba = _be32(cdb[2:6])
            blocks = (cdb[7] << 8) | cdb[8]
            self.pending_cmd = {'ann': ANN_READ10, 'ss': ss, 'lba': lba, 'blocks': blocks, 'needs_data': dlen > 0}
        elif opcode == 0x2A:
            lba = _be32(cdb[2:6])
            blocks = (cdb[7] << 8) | cdb[8]
            self.pending_cmd = {'ann': ANN_WRITE10, 'ss': ss, 'lba': lba, 'blocks': blocks, 'needs_data': dlen > 0}
        elif opcode == 0x00:
            self.putg(ss, es, ANN_TEST_UNIT_READY, 'TEST UNIT READY')
            self.pending_cmd = None
        else:
            self.putg(ss, es, ANN_WARNING, 'Unknown SCSI opcode 0x%02X' % (opcode or 0))
            self.pending_cmd = None

    def _handle_csw(self, b, ss, es):
        tag = _le32(b[4:8])
        residue = _le32(b[8:12])
        status = b[12]
        status_text = 'PASS' if status == 0 else ('FAIL' if status == 1 else 'PHASE ERROR')
        self.putg(ss, es, ANN_CSW, 'CSW tag=%d status=%s residue=%d' % (tag, status_text, residue))
        self.pending_cmd = None

    def _handle_data_stage(self, b, ss, es):
        cmd = self.pending_cmd
        if cmd is None or not cmd['needs_data']:
            return
        if cmd['ann'] == ANN_INQUIRY:
            vendor = bytes(b[8:16]).decode('ascii', errors='replace').rstrip()
            product = bytes(b[16:32]).decode('ascii', errors='replace').rstrip()
            text = "INQUIRY alloc_len=%d vendor=%r product=%r" % (cmd['alloc_len'], vendor, product)
        elif cmd['ann'] == ANN_READ_CAPACITY:
            last_lba = _be32(b[0:4])
            block_size = _be32(b[4:8])
            text = 'READ CAPACITY(10) last_lba=%d block_size=%d' % (last_lba, block_size)
        elif cmd['ann'] == ANN_READ10:
            text = 'READ10 lba=%d blocks=%d bytes=%d' % (cmd['lba'], cmd['blocks'], len(b))
        elif cmd['ann'] == ANN_WRITE10:
            text = 'WRITE10 lba=%d blocks=%d bytes=%d' % (cmd['lba'], cmd['blocks'], len(b))
        else:
            return
        self.putg(cmd['ss'], es, cmd['ann'], text)
        self.pending_cmd = None

    def decode(self, ss, es, data):
        ptype, pdata = data
        if ptype != 'PACKET':
            return
        pcategory, pname, pinfo = pdata

        if pcategory == 'TOKEN':
            if pname in ('OUT', 'IN'):
                self.token_type = pname
                self.token_ss = ss
            return

        if pcategory == 'DATA':
            if self.token_type is None:
                return
            databytes = pinfo[2]
            direction = self.token_type
            n = len(databytes)
            if direction == 'OUT' and n == 31 and _le32(databytes[0:4]) == _CBW_SIGNATURE:
                self._handle_cbw(databytes, self.token_ss, es)
            elif direction == 'IN' and n == 13 and _le32(databytes[0:4]) == _CSW_SIGNATURE:
                self._handle_csw(databytes, self.token_ss, es)
            else:
                self._handle_data_stage(databytes, self.token_ss, es)
            return

        if pcategory == 'HANDSHAKE':
            self.token_type = None
            return
