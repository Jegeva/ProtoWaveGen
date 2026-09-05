'''
USB DFU (Device Firmware Upgrade, USB DFU 1.1) class requests: a custom
decoder for this repo's own USB DFU generator
(`protowavegen.protocols.usb_dfu`), since no mainline sigrok decoder exists
for DFU at any layer (confirmed: `usb_packet`/`usb_request`/`usb_signalling`/
`usb_power_delivery` are the only USB-family decoders shipped by
libsigrokdecode). Stacks on sigrok's own real `usb_packet` decoder exactly
like `usb_request` does (`inputs = ['usb_packet']`), consuming its
`OUTPUT_PYTHON` stream directly rather than re-decoding electrical/framing
levels itself. See `pd.py`'s module docstring for the exact request/
response reconstruction approach.
'''

from .pd import Decoder
