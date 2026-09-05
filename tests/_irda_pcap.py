"""Minimal classic-pcap writer for feeding synthetic IrDA/IrLAP frames to
`tshark`'s real, independently-implemented `irlap` dissector — test
infrastructure for `tests/test_sigrok_roundtrip.py`'s IrDA Wireshark
cross-validation case, not a user-facing feature.

Format confirmed empirically against this environment's real `tshark`
(4.6.6) before relying on it: `tshark -G protocols | grep -i irda` lists
`irlap`/`irlmp`, and a hand-built frame using exactly this shape decoded
with `Encapsulation type: IrDA (44)` and correct `irlap.a.*`/`irlap.c.*`
field values (see the shell history this session used to check it).

Two things this confirmed empirically that aren't obvious from guessing at
the format:

- The `pcap` (classic, not pcapng) global header's link-layer type must be
  **144** (`LINKTYPE_LINUX_IRDA`), and each packet must be prefixed with a
  16-byte Linux "cooked" SLL-style pseudo-header
  (`struct sll_header { u16 pkttype; u16 hatype; u16 halen; u8 addr[8];
  u16 protocol; }`) with `hatype = 783` (confirmed against
  `/usr/include/net/if_arp.h`'s `ARPHRD_...` constants — Linux has no
  `ARPHRD_IRDA` macro by that literal name in the installed header, but
  783 is the well-known real value used by every other reference for this
  purpose) and `protocol = 0x0017` (confirmed against
  `/usr/include/linux/if_ether.h`'s `ETH_P_IRDA`).
- A real Linux IrDA capture point only ever sees a frame *after* the
  receiving hardware/driver has already validated and stripped its
  trailing 2-byte FCS (the same reason a captured Ethernet frame usually
  has no trailing FCS either). Confirmed by feeding `tshark` a frame that
  *did* include trailing FCS bytes: it misparsed them as extra IrLMP
  payload content instead of recognizing end-of-frame. So a frame handed
  to `build_irda_pcap` should be Address+Control+Information only, no
  FCS — matching what a real capture would contain, even though this
  project's own SIR waveform (and the custom sigrok decoder validating it
  in this same test file) always carries a real FCS on the wire.
"""

from __future__ import annotations

import struct

_ETH_P_IRDA = 0x0017
_ARPHRD_IRDA = 783
_LINKTYPE_LINUX_IRDA = 144


def _sll_header() -> bytes:
    # struct sll_header { u16 pkttype; u16 hatype; u16 halen; u8 addr[8]; u16 protocol; } (big-endian on the wire)
    pkttype = 0  # LINUX_SLL_HOST
    halen = 0
    addr = bytes(8)
    return struct.pack(">HHH8sH", pkttype, _ARPHRD_IRDA, halen, addr, _ETH_P_IRDA)


def build_irda_pcap(frames: list[bytes]) -> bytes:
    """A classic-pcap byte string (global header + one record per frame),
    link-layer type `LINKTYPE_LINUX_IRDA`, each frame prefixed with the SLL
    pseudo-header `tshark`'s `irda`/`irlap` dissector chain expects. Each
    entry in `frames` should be raw Address+Control+Information bytes
    (no FCS — see the module docstring)."""

    header = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, _LINKTYPE_LINUX_IRDA)
    records = []
    for frame in frames:
        packet = _sll_header() + frame
        records.append(struct.pack("<IIII", 0, 0, len(packet), len(packet)) + packet)
    return header + b"".join(records)
