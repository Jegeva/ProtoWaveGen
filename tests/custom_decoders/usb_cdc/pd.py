##
## Custom sigrok protocol decoder for USB CDC/ACM.
##
## sigrok upstream has no USB CDC decoder at any layer (confirmed: only
## usb_packet/usb_request/usb_signalling/usb_power_delivery ship under
## /usr/share/libsigrokdecode/decoders/), so this is a from-scratch
## implementation, not a port of anything shipped with sigrok. It stacks
## on top of the mainline `usb_packet` decoder's own OUTPUT_PYTHON stream
## -- the identical PD-stacking mechanism `usb_request/pd.py` uses on top
## of `usb_packet` (`inputs = ['usb_packet']`), confirmed by reading that
## decoder's source in full during implementation.
##
## --- Input contract (read directly from usb_packet/pd.py's own
## handle_packet(), not just its module docstring, since the docstring
## undersells the real HANDSHAKE shape) ---
##
## Each `decode(self, ss, es, data)` call unpacks `ptype, pdata = data`;
## only `ptype == 'PACKET'` matters here, and then
## `pcategory, pname, pinfo = pdata`:
##  - pcategory: 'TOKEN', 'DATA', or 'HANDSHAKE' (others ignored).
##  - pname: 'SETUP'/'OUT'/'IN'/'SOF' (TOKEN), 'DATA0'/'DATA1' (DATA),
##    'ACK'/'NAK'/'STALL'/'NYET' (HANDSHAKE).
##  - pinfo, exactly as usb_packet's handle_packet() builds it:
##    - TOKEN (non-SOF): [sync_bitstr, pid_bitstr, addr_int, ep_int, crc5_int].
##    - DATA: [sync_bitstr, pid_bitstr, databytes_list, crc16_int] --
##      databytes_list is a plain list[int] in transmission order.
##    - HANDSHAKE: [sync_bitstr, pid_bitstr] only -- TWO elements, not the
##      three the module docstring's comment table implies (traced through
##      handle_packet()'s bare `pass` branch for ACK/NAK/STALL/NYET, which
##      appends nothing after the PID). Never index past pinfo[1] for a
##      HANDSHAKE.
##
## --- Decoding approach ---
##
## This project's own USB CDC generator (protowavegen.protocols.usb_cdc)
## only ever emits three fixed packet sequences, always ACKed on the
## first attempt (no retries/NAKs, same "don't simulate contention/
## retries we can't win" precedent already established for CanBus/I3C
## ENTDAA elsewhere in this project) -- so this decoder is a linear state
## machine over those three sequences rather than a general USB control/
## bulk transaction tracker like usb_request/pd.py's own. Any packet that
## doesn't match the current state's expectation resets to IDLE with a
## 'warnings' annotation instead of trying to recover -- same "flag and
## move on" shape usb_request/pd.py uses for its own ERR annotations.
##
##   SET_LINE_CODING:    SETUP+DATA0(8)+ACK, OUT+DATA1(7)+ACK, IN+DATA1(0)+ACK
##   SET_CONTROL_LINE_STATE: SETUP+DATA0(8)+ACK, IN+DATA1(0)+ACK
##   bulk OUT (send_data):   OUT+DATA0/1(N)+ACK
##
## The SETUP stage's own 8-byte payload (bmRequestType/bRequest/wValue/
## wIndex/wLength, little-endian per USB 2.0 spec 9.3) is parsed once,
## right after it arrives, and its bRequest+wLength decide which of the
## two CDC request paths above to expect next -- an unrecognized request
## warns and returns to IDLE rather than guessing.
##

import sigrokdecode as srd

_SET_LINE_CODING = 0x20
_SET_CONTROL_LINE_STATE = 0x22

_ANN_LINECODING = 0
_ANN_CONTROLLINE = 1
_ANN_DATA = 2
_ANN_WARNINGS = 3

_CHAR_FORMAT_LABELS = {0: '1', 1: '1.5', 2: '2'}
_PARITY_TYPE_LABELS = {0: 'None', 1: 'Odd', 2: 'Even', 3: 'Mark', 4: 'Space'}


def _format_byte(b):
    """Standalone hex+ASCII rendering, matching this project's own
    format_byte() convention (base.py) -- reimplemented here rather than
    imported, since a self-authored decoder must be standalone (same rule
    tests/custom_decoders/irda/pd.py's own module docstring states)."""

    if 32 <= b < 127:
        return "0x%02X '%s'" % (b, chr(b))
    return '0x%02X' % b


class Decoder(srd.Decoder):
    api_version = 3
    id = 'usb_cdc'
    name = 'USB CDC/ACM'
    longname = 'USB CDC/ACM (virtual serial port class)'
    desc = 'USB CDC/ACM class requests (line coding, control line state) and bulk data.'
    license = 'gplv2+'
    inputs = ['usb_packet']
    outputs = []
    tags = ['PC']
    annotations = (
        ('linecoding', 'Line coding'),
        ('controlline', 'Control line state'),
        ('data', 'Bulk data'),
        ('warnings', 'Warnings'),
    )
    annotation_rows = (
        ('linecoding-row', 'Line coding', (0,)),
        ('controlline-row', 'Control line', (1,)),
        ('data-row', 'Data', (2,)),
        ('warnings-row', 'Warnings', (3,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'IDLE'
        self.pending = {}

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putg(self, ss, es, cls, text):
        self.put(ss, es, self.out_ann, [cls, [text]])

    def _warn(self, ss, es, pcategory, pname):
        self.putg(ss, es, _ANN_WARNINGS, 'unexpected %s %s in state %s' % (pcategory, pname, self.state))
        self.state = 'IDLE'
        self.pending = {}

    def _emit_linecoding(self, ss, es, payload):
        dte_rate = payload[0] | (payload[1] << 8) | (payload[2] << 16) | (payload[3] << 24)
        char_format, parity_type, data_bits = payload[4], payload[5], payload[6]
        fmt_label = _CHAR_FORMAT_LABELS.get(char_format, str(char_format))
        parity_label = _PARITY_TYPE_LABELS.get(parity_type, str(parity_type))
        self.putg(ss, es, _ANN_LINECODING, 'baud=%d format=%s parity=%s bits=%d' % (
            dte_rate, fmt_label, parity_label, data_bits,
        ))

    def _emit_controlline(self, ss, es, w_value):
        dtr, rts = w_value & 1, (w_value >> 1) & 1
        self.putg(ss, es, _ANN_CONTROLLINE, 'DTR=%d RTS=%d' % (dtr, rts))

    def _emit_data(self, ss, es, databytes):
        self.putg(ss, es, _ANN_DATA, 'data: ' + ' '.join(_format_byte(b) for b in databytes))

    def decode(self, ss, es, data):
        ptype, pdata = data
        if ptype != 'PACKET':
            return
        pcategory, pname, pinfo = pdata

        state = self.state

        if state == 'IDLE':
            if pcategory == 'TOKEN' and pname == 'SETUP':
                self.pending = {'setup_ss': ss}
                self.state = 'AFTER_SETUP_TOKEN'
            elif pcategory == 'TOKEN' and pname == 'OUT':
                self.pending = {}
                self.state = 'AFTER_BULK_OUT_TOKEN'
            elif pcategory == 'TOKEN' and pname == 'SOF':
                pass  # not used by this project's generator; harmless if present
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_SETUP_TOKEN':
            if pcategory == 'DATA' and len(pinfo[2]) == 8:
                setup = pinfo[2]
                self.pending.update(
                    b_request=setup[1],
                    w_value=setup[2] | (setup[3] << 8),
                    w_index=setup[4] | (setup[5] << 8),
                    w_length=setup[6] | (setup[7] << 8),
                )
                self.state = 'AFTER_SETUP_DATA'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_SETUP_DATA':
            if pcategory == 'HANDSHAKE' and pname == 'ACK':
                b_request, w_length = self.pending.get('b_request'), self.pending.get('w_length')
                if b_request == _SET_LINE_CODING and w_length == 7:
                    self.state = 'AFTER_LC_SETUP_ACK'
                elif b_request == _SET_CONTROL_LINE_STATE and w_length == 0:
                    self.state = 'AFTER_CLS_SETUP_ACK'
                else:
                    self._warn(ss, es, pcategory, pname)
            else:
                self._warn(ss, es, pcategory, pname)

        # ---- SET_LINE_CODING data + status stages ----
        elif state == 'AFTER_LC_SETUP_ACK':
            if pcategory == 'TOKEN' and pname == 'OUT':
                self.state = 'AFTER_LC_OUT_TOKEN'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_LC_OUT_TOKEN':
            if pcategory == 'DATA' and len(pinfo[2]) == 7:
                self.pending['lc_payload'] = pinfo[2]
                self.pending['lc_ss'], self.pending['lc_es'] = ss, es
                self.state = 'AFTER_LC_OUT_DATA'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_LC_OUT_DATA':
            if pcategory == 'HANDSHAKE' and pname == 'ACK':
                self._emit_linecoding(self.pending['lc_ss'], self.pending['lc_es'], self.pending['lc_payload'])
                self.state = 'AFTER_LC_STATUS_TOKEN'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_LC_STATUS_TOKEN':
            if pcategory == 'TOKEN' and pname == 'IN':
                self.state = 'AFTER_LC_STATUS_DATA'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_LC_STATUS_DATA':
            if pcategory == 'DATA' and len(pinfo[2]) == 0:
                self.state = 'AFTER_LC_STATUS_ACK'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_LC_STATUS_ACK':
            if pcategory == 'HANDSHAKE' and pname == 'ACK':
                self.state, self.pending = 'IDLE', {}
            else:
                self._warn(ss, es, pcategory, pname)

        # ---- SET_CONTROL_LINE_STATE status stage ----
        elif state == 'AFTER_CLS_SETUP_ACK':
            if pcategory == 'TOKEN' and pname == 'IN':
                self.state = 'AFTER_CLS_STATUS_DATA'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_CLS_STATUS_DATA':
            if pcategory == 'DATA' and len(pinfo[2]) == 0:
                self._emit_controlline(self.pending['setup_ss'], es, self.pending['w_value'])
                self.state = 'AFTER_CLS_STATUS_ACK'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_CLS_STATUS_ACK':
            if pcategory == 'HANDSHAKE' and pname == 'ACK':
                self.state, self.pending = 'IDLE', {}
            else:
                self._warn(ss, es, pcategory, pname)

        # ---- bulk OUT (send_data) ----
        elif state == 'AFTER_BULK_OUT_TOKEN':
            if pcategory == 'DATA':
                self._emit_data(ss, es, pinfo[2])
                self.state = 'AFTER_BULK_OUT_DATA'
            else:
                self._warn(ss, es, pcategory, pname)

        elif state == 'AFTER_BULK_OUT_DATA':
            if pcategory == 'HANDSHAKE' and pname == 'ACK':
                self.state, self.pending = 'IDLE', {}
            else:
                self._warn(ss, es, pcategory, pname)

        else:
            self._warn(ss, es, pcategory, pname)
