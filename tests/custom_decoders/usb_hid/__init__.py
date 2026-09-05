'''
USB HID: a custom sigrok decoder for this repo's own USB HID generator
(`protowavegen.protocols.usb_hid`). No mainline sigrok decoder exists for
USB HID (confirmed: only usb_packet/usb_request/usb_signalling/
usb_power_delivery ship under /usr/share/libsigrokdecode/decoders/). See
`pd.py`'s module docstring for the decoding approach.
'''

from .pd import Decoder
