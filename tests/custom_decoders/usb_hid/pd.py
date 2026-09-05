##
## Custom sigrok protocol decoder for USB HID, stacked on sigrok's own
## mainline `usb_packet` decoder (`inputs = ['usb_packet']`) -- the same
## PD-stacking mechanism `usb_request` uses on top of `usb_packet` (see
## /usr/share/libsigrokdecode/decoders/usb_request/pd.py) and this repo's
## own `rtc8564` uses on top of `i2c`.
##
## No mainline sigrok decoder exists for USB HID (confirmed: only
## usb_packet/usb_request/usb_signalling/usb_power_delivery ship under
## /usr/share/libsigrokdecode/decoders/), so this is a from-scratch
## implementation, not a port of anything. It is written as an independent
## decoder-first reading of this project's own narrow USB HID scope
## (protowavegen.protocols.usb_hid.UsbHid): a fixed 3-byte relative-mouse
## report, and GET_DESCRIPTOR(HID)/GET_DESCRIPTOR(REPORT).
##
## `usb_packet`'s own OUTPUT_PYTHON stream emits one
## ('PACKET', [pcategory, pname, pinfo]) event per fully-parsed packet
## (confirmed directly from usb_packet/pd.py's handle_packet()/putpp()
## calls -- NOT from its own top-of-file docstring, which lists 5/6-element
## pinfo tuples the actual code never produces):
##   TOKEN     pinfo = [sync, pid, addr, ep, crc5]      (5 elements)
##   DATA      pinfo = [sync, pid, databytes, crc16]    (4 elements)
##   HANDSHAKE pinfo = [sync, pid]                      (2 elements)
##
## Unlike this repo's other custom/self-authored decoders (IrDA), there is
## no idle-timeout flush concern here: usb_packet already closes each
## packet on its own EOP, so a full logical HID transaction (a control
## transfer's 9 packets, or an interrupt report's 3) is always fully
## buffered by the time its last packet arrives -- no extra trailing
## operation is needed to flush anything, unlike ps2/lin/dcf77/em4100/i3c's
## own decoder-side "needs a following event" limitations documented in
## this repo's CLAUDE.md.
##
## Reassembly strategy: buffer incoming packets and pattern-match from the
## front. A leading TOKEN SETUP starts a 9-packet control-transfer window
## (SETUP+DATA0+ACK, IN+DATA+ACK, OUT+DATA+ACK -- exactly what
## UsbBus.control_transfer always emits for a GET_DESCRIPTOR request, which
## always has an IN data stage and therefore an OUT status stage). A
## leading TOKEN IN starts a 3-packet interrupt-report window
## (IN+DATA+ACK). Any other leading packet is dropped (resync) rather than
## wedging the decoder.
##

import sigrokdecode as srd

_ANN_DESCRIPTOR = 0
_ANN_REPORT = 1
_ANN_WARNING = 2


class Decoder(srd.Decoder):
    api_version = 3
    id = 'usb_hid'
    name = 'USB HID'
    longname = 'USB Human Interface Device (relative-mouse subset)'
    desc = 'USB HID GET_DESCRIPTOR(HID/REPORT) and 3-byte mouse-style interrupt reports.'
    license = 'gplv2+'
    inputs = ['usb_packet']
    outputs = []
    tags = ['PC']
    annotations = (
        ('descriptor', 'Descriptor'),
        ('report', 'Report'),
        ('warning', 'Warning'),
    )
    annotation_rows = (
        ('descriptors', 'Descriptors', (0,)),
        ('reports', 'Reports', (1,)),
        ('warnings', 'Warnings', (2,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.buffer = []

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putg(self, ss, es, cls, text):
        self.put(ss, es, self.out_ann, [cls, [text]])

    def decode(self, ss, es, data):
        ptype, pdata = data
        if ptype != 'PACKET':
            return
        pcategory, pname, pinfo = pdata
        self.buffer.append((pcategory, pname, pinfo, ss, es))
        self._try_match()

    def _try_match(self):
        while self.buffer:
            head_cat, head_name = self.buffer[0][0], self.buffer[0][1]
            if head_cat == 'TOKEN' and head_name == 'SETUP':
                if len(self.buffer) < 9:
                    return
                self._match_control_transfer()
            elif head_cat == 'TOKEN' and head_name == 'IN':
                if len(self.buffer) < 3:
                    return
                self._match_interrupt_report()
            else:
                # Unrecognized leading packet (stray ACK/SOF/etc): drop and
                # resync rather than deadlocking the decoder.
                self.buffer.pop(0)

    def _match_control_transfer(self):
        entries = self.buffer[:9]
        cats = [e[0] for e in entries]
        names = [e[1] for e in entries]
        expected_cats = ['TOKEN', 'DATA', 'HANDSHAKE', 'TOKEN', 'DATA', 'HANDSHAKE', 'TOKEN', 'DATA', 'HANDSHAKE']
        expected_names = [None, 'DATA0', 'ACK', 'IN', None, 'ACK', 'OUT', None, 'ACK']
        ok = all(
            c == ec and (en is None or n == en)
            for c, n, ec, en in zip(cats, names, expected_cats, expected_names)
        )
        if not ok:
            self.buffer.pop(0)
            return

        setup_tok, setup_data, _setup_ack, _in_tok, in_data, _in_ack, _out_tok, _out_data, out_ack = entries
        setup_bytes = setup_data[2][2]   # DATA pinfo[2] = databytes
        descriptor_bytes = in_data[2][2]
        ss, es = setup_tok[3], out_ack[4]

        w_value = setup_bytes[2] | (setup_bytes[3] << 8)
        descriptor_type = (w_value >> 8) & 0xFF
        hexstr = ' '.join('%02X' % b for b in descriptor_bytes)
        if descriptor_type == 0x21:
            label = 'GET_DESCRIPTOR(HID): %s' % hexstr
        elif descriptor_type == 0x22:
            label = 'GET_DESCRIPTOR(REPORT): %s' % hexstr
        else:
            label = 'GET_DESCRIPTOR(0x%02X): %s' % (descriptor_type, hexstr)
        self.putg(ss, es, _ANN_DESCRIPTOR, label)
        del self.buffer[:9]

    def _match_interrupt_report(self):
        entries = self.buffer[:3]
        cats = [e[0] for e in entries]
        names = [e[1] for e in entries]
        if not (cats == ['TOKEN', 'DATA', 'HANDSHAKE'] and names[2] == 'ACK'):
            self.buffer.pop(0)
            return

        in_tok, data_pkt, ack_pkt = entries
        report_bytes = data_pkt[2][2]
        ss, es = in_tok[3], ack_pkt[4]
        if len(report_bytes) != 3:
            self.putg(ss, es, _ANN_WARNING, 'Unexpected report length: %d' % len(report_bytes))
        else:
            buttons, x_raw, y_raw = report_bytes
            x = x_raw - 256 if x_raw >= 128 else x_raw
            y = y_raw - 256 if y_raw >= 128 else y_raw
            self.putg(ss, es, _ANN_REPORT, 'buttons=0x%02X x=%d y=%d' % (buttons, x, y))
        del self.buffer[:3]
