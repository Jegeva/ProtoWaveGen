##
## Custom sigrok protocol decoder for USB DFU (Device Firmware Upgrade,
## USB DFU 1.1) class requests.
##
## sigrok upstream has no DFU decoder at any layer (confirmed:
## `usb_packet`/`usb_request`/`usb_signalling`/`usb_power_delivery` are the
## only USB-family decoders shipped by libsigrokdecode). This decoder
## stacks on sigrok's own real `usb_packet` decoder exactly the way
## `usb_request/pd.py` does (`inputs = ['usb_packet']`), consuming its
## `OUTPUT_PYTHON` stream rather than re-deriving electrical/bit-level
## framing itself.
##
## `usb_packet`'s own source (`handle_packet()`) was read to confirm the
## exact shape of what it puts on that stream: a per-packet aggregate
## output `['PACKET', [pcategory, pname, pinfo]]`, where `pinfo` is
## `[sync, pid, addr, ep, crc5]` for a TOKEN, `[sync, pid, databytes,
## crc16]` for a DATA0/DATA1 packet (`pinfo[2]` is the byte list), and
## `[sync, pid]` for a HANDSHAKE -- matching exactly how `usb_request/pd.py`
## itself unpacks the same stream (`sync, pid, addr, ep, crc5 = pinfo`,
## `self.transaction_data = pinfo[2]`). `usb_packet` also emits several
## other, per-field Python outputs on the same stream (`['SYNC', ...]`,
## `['PID', ...]`, etc.) that must be ignored here.
##
## Deliberately simpler than `usb_request`'s own general bulk/control
## transaction-reassembly state machine (which tracks per-(addr,ep)
## request objects across an entire transfer): this decoder only needs to
## name each DFU-layer request from its SETUP stage (bRequest/wValue/
## wIndex/wLength are all present in that one 8-byte payload already) and,
## for GETSTATUS specifically, parse the 6-byte response that follows on
## the next IN transaction. It does not track the OUT/IN data-stage or
## Status-stage packets for DNLOAD/UPLOAD at all -- nothing about those
## carries information this decoder needs to add beyond what the SETUP
## stage already gave it. DFU control transfers in this project's model
## are always one at a time on endpoint 0 (no interleaving), so no
## per-(addr,ep) bookkeeping is needed either.
##

import sigrokdecode as srd

_DNLOAD, _UPLOAD, _GETSTATUS, _ABORT = 1, 2, 3, 6

_REQUEST_NAMES = {
    _DNLOAD: 'DNLOAD',
    _UPLOAD: 'UPLOAD',
    _GETSTATUS: 'GETSTATUS',
    _ABORT: 'ABORT',
}

# bRequest -> annotation class index (matches the `annotations` tuple below).
_ANN_CLASS = {_DNLOAD: 0, _UPLOAD: 1, _GETSTATUS: 2, _ABORT: 3}

_STATE_NAMES = {
    0: 'appIDLE',
    1: 'appDETACH',
    2: 'dfuIDLE',
    3: 'dfuDNLOAD-SYNC',
    4: 'dfuDNBUSY',
    5: 'dfuDNLOAD-IDLE',
    6: 'dfuMANIFEST-SYNC',
    7: 'dfuMANIFEST',
    8: 'dfuMANIFEST-WAIT-RESET',
    9: 'dfuUPLOAD-IDLE',
    10: 'dfuERROR',
}


class Decoder(srd.Decoder):
    api_version = 3
    id = 'usb_dfu'
    name = 'USB DFU'
    longname = 'USB Device Firmware Upgrade (DFU 1.1)'
    desc = 'USB DFU class requests: DNLOAD/UPLOAD/GETSTATUS/ABORT.'
    license = 'gplv2+'
    inputs = ['usb_packet']
    outputs = []
    tags = ['PC']
    annotations = (
        ('dnload', 'DNLOAD'),
        ('upload', 'UPLOAD'),
        ('getstatus', 'GETSTATUS'),
        ('abort', 'ABORT'),
        ('status-response', 'GETSTATUS response'),
        ('warnings', 'Warnings'),
    )
    annotation_rows = (
        ('requests', 'Requests', (0, 1, 2, 3)),
        ('responses', 'Responses', (4,)),
        ('warnings-row', 'Warnings', (5,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'WAIT_SETUP'
        self.setup_ss = None

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putg(self, ss, es, cls, text):
        self.put(ss, es, self.out_ann, [cls, [text]])

    def decode(self, ss, es, data):
        ptype, pdata = data
        if ptype != 'PACKET':
            return
        pcategory, pname, pinfo = pdata

        if pcategory == 'TOKEN':
            if pname == 'SETUP':
                self.setup_ss = ss
                self.state = 'WAIT_SETUP_DATA'
            # Any other token (IN/OUT) needs no action from this decoder;
            # the DATA packet that follows is what actually carries payload.
            return

        if pcategory == 'DATA':
            databytes = pinfo[2]
            if self.state == 'WAIT_SETUP_DATA':
                self._handle_setup(ss, es, databytes)
            elif self.state == 'WAIT_GETSTATUS_RESPONSE' and pname == 'DATA1':
                self._handle_status_response(ss, es, databytes)
                self.state = 'WAIT_SETUP'
            return

        # HANDSHAKE packets: no action needed for this decoder's scope.

    def _handle_setup(self, ss, es, databytes):
        if len(databytes) != 8:
            self.putg(self.setup_ss, es, 5, 'Malformed SETUP payload (expected 8 bytes, got %d)' % len(databytes))
            self.state = 'WAIT_SETUP'
            return

        b_request = databytes[1]
        w_value = databytes[2] | (databytes[3] << 8)
        w_index = databytes[4] | (databytes[5] << 8)
        w_length = databytes[6] | (databytes[7] << 8)

        name = _REQUEST_NAMES.get(b_request)
        if name is None:
            self.putg(self.setup_ss, es, 5, 'Unknown DFU request: bRequest=%d' % b_request)
            self.state = 'WAIT_SETUP'
            return

        cls = _ANN_CLASS[b_request]
        if b_request in (_DNLOAD, _UPLOAD):
            text = '%s: block=%d len=%d' % (name, w_value, w_length)
        elif b_request == _GETSTATUS:
            text = 'GETSTATUS (interface=%d)' % w_index
        else:
            text = 'ABORT (interface=%d)' % w_index
        self.putg(self.setup_ss, es, cls, text)

        self.state = 'WAIT_GETSTATUS_RESPONSE' if b_request == _GETSTATUS else 'WAIT_SETUP'

    def _handle_status_response(self, ss, es, databytes):
        if len(databytes) != 6:
            self.putg(ss, es, 5, 'Malformed GETSTATUS response (expected 6 bytes, got %d)' % len(databytes))
            return
        b_status = databytes[0]
        b_state = databytes[4]
        state_name = _STATE_NAMES.get(b_state, 'unknown(%d)' % b_state)
        self.putg(ss, es, 4, 'Status: %d State: %s' % (b_status, state_name))
