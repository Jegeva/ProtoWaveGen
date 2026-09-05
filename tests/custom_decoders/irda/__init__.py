'''
IrDA SIR (Serial InfraRed) + IrLAP: a custom decoder for this repo's own
IrDA generator (`protowavegen.protocols.irda`), since sigrok upstream has no
IrDA decoder at any layer (confirmed: listed as a 0%-complete future
candidate on sigrok's own decoder wiki). See `pd.py`'s module docstring for
the decoding approach.
'''

from .pd import Decoder
