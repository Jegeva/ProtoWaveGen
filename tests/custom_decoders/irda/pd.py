##
## Custom sigrok protocol decoder for IrDA SIR + IrLAP.
##
## sigrok upstream has no IrDA decoder at any layer, so this is a from-
## scratch implementation, not a port of anything shipped with sigrok.
## It is intentionally written as an independent decoder-first reading of
## the two protocol layers, not as this repo's own encoder
## (`protowavegen.protocols.irda.IrdaBus`) run in reverse: the bit
## classification below reasons from "is the line pulsed (low) at the
## moment a 0-bit's pulse would be present", the byte/frame reassembly
## reasons from "reconstruct a UART-style byte, then group bytes into a
## frame by silence", and the FCS check reimplements CRC-16/X-25 from the
## public algorithm rather than importing this project's own
## `checksums.crc16_x25`.
##
## --- SIR physical layer -----------------------------------------------
##
## One `ir` channel, active-low (idle/space = logic 1, a light pulse =
## logic 0 — matching every real IR receiver module's convention, and this
## repo's own `_ir_pulse.py`). Each bit occupies one fixed-width cell
## (`bit_width = round(samplerate / baudrate)` samples, computed in whole
## samples rather than as a float so no rounding drift builds up across
## one byte's 10 cells); a logic 0 is a brief pulse (nominally 3/16 of the
## cell) at the *start* of the cell, a logic 1 is no pulse for the whole
## cell. Byte framing is plain UART: 1 start bit (always 0, i.e. always
## pulsed — this is how a byte's start is *found*, by waiting for a
## falling edge), 8 data bits LSB-first, 1 stop bit (always 1, never
## pulsed), no parity.
##
## Classifying a data/stop bit does *not* edge-hunt within its cell —
## a first attempt at that (racing a falling-edge wait against a timeout
## per bit cell) hit exactly the kind of same-sample fencepost bug this
## project has repeatedly found the hard way (see this repo's CLAUDE.md,
## `onewire_link`'s classification-threshold bug): whenever one cell's
## pulse starts *exactly* on the previous cell's boundary sample (which it
## always does here, since pulses sit at each cell's start with no slack),
## a wait() that lands exactly on that shared boundary sample "consumes"
## the edge, and the next cell's own edge-search (which only looks
## strictly *after* the current sample) misses the very pulse that
## defines it. Fixed by not edge-hunting for data/stop bits at all: skip
## straight to the nominal pulse's midpoint sample within each cell and
## read the line's *level* there directly (sigrok's `wait()` always
## returns the current pin values at wherever it stops, skip-driven or
## not) — a pulse can only be missed this way if its real width differs
## wildly from the 3/16 nominal, which is a decoder-tolerance question a
## real receiver faces too, not a boundary-arithmetic bug. Byte *framing*
## (finding each byte's start bit, and the idle timeout that closes a
## frame) still legitimately edge-hunts, since those really are "did an
## edge happen within this longer window" questions with no fixed offset
## to sample at.
##
## --- Frame reassembly ---------------------------------------------------
##
## IrLAP has no explicit end-of-frame delimiter at the SIR byte-stream
## level (unlike synchronous HDLC's 0x7E flags) — real receivers infer
## frame boundaries from silence, the same idle-timeout shape this repo
## already uses for LIN/Modbus RTU framing. This is sound here because a
## SIR byte's start bit is *always* a pulse: the longest possible silent
## run *within* one frame is bounded at 9 bit cells (8 data bits + 1 stop
## bit all silent, immediately followed by the next byte's start-bit
## pulse) no matter the frame's length or content. `_IDLE_TIMEOUT_BITS`
## (12) sits comfortably above that 9-cell bound, so "no falling edge for
## 12 bit cells after a byte completes" reliably means "frame ended", not
## "next byte, same frame".
##
## --- IrLAP framing -------------------------------------------------------
##
## Address byte: bit 0 = C/R (Command=1/Response=0), bits 1-7 = a 7-bit
## connection address. Control byte: bit 0 = 0 selects an I-frame (bits
## 1-3 = N(S), bit 4 = P/F, bits 5-7 = N(R)); bits 0-1 = 11 selects a
## U-frame (bit 4 = P/F); bits 0-1 = 10 selects an S-frame. FCS: the final
## 2 bytes, LSB first, CRC-16/X-25 (polynomial 0x1021, reflected 0x8408;
## init 0xFFFF; reflected in/out; final complement) over every byte before
## it.
##

import sigrokdecode as srd

class SamplerateError(Exception):
    pass

_IDLE_TIMEOUT_BITS = 12
_PULSE_FRACTION = 3 / 16  # nominal SIR pulse width, as a fraction of one bit cell


def _crc16_x25(data):
    """CRC-16/X-25 (a.k.a. CRC-16/IBM-SDLC), the public algorithm IrLAP
    uses for its FCS: polynomial 0x1021 (reflected form 0x8408), init
    0xFFFF, reflected in/out, final complement. Independently reimplemented
    here from the public spec, not imported from the generator side."""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return (~crc) & 0xFFFF


class Decoder(srd.Decoder):
    api_version = 3
    id = 'irda'
    name = 'IrDA'
    longname = 'IrDA SIR + IrLAP'
    desc = 'IrDA SIR physical layer and IrLAP link-access framing.'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = []
    tags = ['Embedded/industrial', 'IR']
    channels = (
        {'id': 'ir', 'name': 'IR', 'desc': 'Demodulated IR envelope (active-low)'},
    )
    options = (
        {'id': 'baudrate', 'desc': 'SIR baud rate', 'default': 115200},
    )
    annotations = (
        ('bit', 'Bit'),
        ('byte', 'Byte'),
        ('address', 'Address'),
        ('frametype', 'Frame type'),
        ('ns', 'N(S)'),
        ('nr', 'N(R)'),
        ('pf', 'P/F'),
        ('info', 'Info byte'),
        ('fcs', 'FCS'),
        ('warnings', 'Warnings'),
    )
    annotation_rows = (
        ('bits', 'Bits', (0,)),
        ('bytes', 'Bytes', (1,)),
        ('fields', 'Fields', (2, 3, 4, 5, 6)),
        ('info-bytes', 'Info', (7,)),
        ('fcs-row', 'FCS', (8,)),
        ('warnings-row', 'Warnings', (9,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.samplerate = None
        self.bit_width = None
        self.sample_offset = None
        self.frame_bytes = []   # [(value, ss, es), ...]

    def metadata(self, key, value):
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putg(self, ss, es, cls, text):
        self.put(ss, es, self.out_ann, [cls, [text]])

    def _read_byte(self, start_bit_ss):
        """Reconstruct one UART-shaped byte (start+8 data LSB-first+stop)
        given the sample number of its already-detected start-bit pulse.
        Returns (value, ss, es, framing_ok)."""

        bw = self.bit_width
        bits = []
        for i in range(1, 10):  # data bits 1..8, stop bit 9
            window_start = start_bit_ss + i * bw
            sample_at = window_start + self.sample_offset
            skip = max(0, sample_at - self.samplenum)
            (pin,) = self.wait({'skip': skip})
            bit = 0 if pin == 0 else 1
            bits.append(bit)
            self.putg(window_start, window_start + bw, 0, str(bit))

        stop_bit = bits.pop()
        value = 0
        for k, b in enumerate(bits):
            value |= b << k
        es = start_bit_ss + 10 * bw
        framing_ok = (stop_bit == 1)
        return value, start_bit_ss, es, framing_ok

    def _finalize_frame(self):
        if len(self.frame_bytes) < 4:
            # Too short to be Address+Control+FCS(2) — spurious noise.
            if self.frame_bytes:
                ss = self.frame_bytes[0][1]
                es = self.frame_bytes[-1][2]
                self.putg(ss, es, 9, 'Frame too short (< 4 bytes)')
            self.frame_bytes = []
            return

        values = [v for v, _, _ in self.frame_bytes]
        addr_byte, control_byte = values[0], values[1]
        fcs_lo, fcs_hi = values[-2], values[-1]
        received_fcs = fcs_lo | (fcs_hi << 8)
        computed_fcs = _crc16_x25(values[:-2])

        addr_ss, addr_es = self.frame_bytes[0][1], self.frame_bytes[0][2]
        ctrl_ss, ctrl_es = self.frame_bytes[1][1], self.frame_bytes[1][2]

        command = addr_byte & 0x01
        address7 = addr_byte >> 1
        self.putg(addr_ss, addr_es, 2, 'Address: 0x%02X' % address7)
        self.putg(addr_ss, addr_es, 2, 'C/R: %s' % ('Command' if command else 'Response'))

        pf = (control_byte >> 4) & 0x01
        pf_label = ('P' if command else 'F') + (': 1' if pf else ': 0')
        if (control_byte & 0x01) == 0:
            ns = (control_byte >> 1) & 0x07
            nr = (control_byte >> 5) & 0x07
            self.putg(ctrl_ss, ctrl_es, 3, 'I-frame')
            self.putg(ctrl_ss, ctrl_es, 4, 'N(S): %d' % ns)
            self.putg(ctrl_ss, ctrl_es, 5, 'N(R): %d' % nr)
            self.putg(ctrl_ss, ctrl_es, 6, pf_label)
        elif (control_byte & 0x03) == 0x01:
            nr = (control_byte >> 5) & 0x07
            self.putg(ctrl_ss, ctrl_es, 3, 'S-frame: 0x%02X' % control_byte)
            self.putg(ctrl_ss, ctrl_es, 5, 'N(R): %d' % nr)
            self.putg(ctrl_ss, ctrl_es, 6, pf_label)
        else:
            self.putg(ctrl_ss, ctrl_es, 3, 'U-frame: 0x%02X' % control_byte)
            self.putg(ctrl_ss, ctrl_es, 6, pf_label)

        for i, (value, ss, es) in enumerate(self.frame_bytes[2:-2]):
            self.putg(ss, es, 7, 'Info[%d]: 0x%02X' % (i, value))

        fcs_ss, fcs_es = self.frame_bytes[-2][1], self.frame_bytes[-1][2]
        if received_fcs == computed_fcs:
            self.putg(fcs_ss, fcs_es, 8, 'FCS: OK')
        else:
            self.putg(fcs_ss, fcs_es, 8, 'FCS: BAD (expected 0x%04X, got 0x%04X)' % (computed_fcs, received_fcs))

        for value, ss, es in self.frame_bytes:
            self.putg(ss, es, 1, '0x%02X' % value)

        self.frame_bytes = []

    def decode(self):
        if not self.samplerate:
            raise SamplerateError('Cannot decode without samplerate.')
        self.bit_width = round(self.samplerate / self.options['baudrate'])
        pulse_width = max(round(self.bit_width * _PULSE_FRACTION), 1)
        self.sample_offset = pulse_width // 2
        idle_gap_samples = _IDLE_TIMEOUT_BITS * self.bit_width

        # Wait for the very first byte's start-bit pulse of the capture.
        self.wait({0: 'f'})
        have_edge = True

        while True:
            if not have_edge:
                self.wait({0: 'f'})
            have_edge = False

            start_bit_ss = self.samplenum
            value, ss, es, framing_ok = self._read_byte(start_bit_ss)
            if not framing_ok:
                self.putg(ss, es, 9, 'Framing error (stop bit not idle)')
            self.frame_bytes.append((value, ss, es))

            # Idle-timeout race: either the next byte's start-bit pulse
            # arrives before the frame is considered over, or it doesn't.
            self.wait([{0: 'f'}, {'skip': idle_gap_samples}])
            if self.matched[0]:
                have_edge = True
                continue
            self._finalize_frame()
