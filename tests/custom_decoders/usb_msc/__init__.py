'''
USB Mass Storage (Bulk-Only Transport + SCSI subset): a custom decoder for
this repo's own usb_msc generator (`protowavegen.protocols.usb_msc`), since
no mainline sigrok decoder for USB Mass Storage exists (confirmed: only
usb_packet/usb_request/usb_signalling/usb_power_delivery ship under
/usr/share/libsigrokdecode/decoders/). See pd.py's module docstring for the
decoding approach.
'''

from .pd import Decoder
