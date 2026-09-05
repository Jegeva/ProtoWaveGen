'''
Custom sigrok protocol decoder for USB CDC/ACM (line coding, control line
state, bulk data), stacked on the mainline `usb_packet` decoder's own
OUTPUT_PYTHON stream -- the same PD-stacking mechanism `usb_request/pd.py`
uses on top of `usb_packet` (confirmed by reading that file's own source:
`inputs = ['usb_packet']`, consuming its 'PACKET' events).

No mainline sigrok decoder for USB CDC exists (confirmed: only
usb_packet/usb_request/usb_signalling/usb_power_delivery ship under
/usr/share/libsigrokdecode/decoders/), so this is a from-scratch
implementation, not a port of anything shipped with sigrok. See pd.py's
module docstring for the decoding approach.
'''

from .pd import Decoder
