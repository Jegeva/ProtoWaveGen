"""Shared bit-stuffing + NRZI-encoding primitives for USB Full-Speed
signalling (`protocols/usb.py`). Kept in a standalone module, mirroring
how `_ir_pulse.py` factors out shared IR-family primitives -- these two
building blocks are generic logical-bit-sequence transforms with no
`CaptureBuilder`/`Signal` dependency, unlike `usb.py`'s line-driving code.

Ordering (confirmed both from the USB 2.0 spec's own description and
independently via web search this session -- see `usb.py`'s module
docstring for the full citation): bit-stuffing runs over the *raw logical*
bitstream first, and NRZI encoding is applied to the *stuffed* result --
not the other way around. Getting this backwards produces a bitstream
that looks superficially plausible but that no real USB receiver (or
sigrok's `usb_signalling` decoder) can lock onto.
"""

from __future__ import annotations

_STUFF_RUN_LENGTH = 6


def stuff_bits(
    bits: list[int], roles: list[str], floating: list[bool]
) -> tuple[list[int], list[str], list[bool]]:
    """USB bit-stuffing: after 6 consecutive logical '1' bits (pre-NRZI),
    insert a stuffed logical '0' to force a guaranteed transition for clock
    recovery (USB 2.0 spec 7.1.9). Unlike CAN's 5-consecutive-*either*-
    polarity rule (see `can.py`'s `_stuff`), USB only stuffs runs of 1s,
    never runs of 0s, and the threshold is 6, not 5 -- confirmed against
    sigrok's `usb_signalling` decoder (`/usr/share/libsigrokdecode/
    decoders/usb_signalling/pd.py`'s `handle_bit()`, which tracks
    `consecutive_ones` and expects a literal '0' immediately after the 6th
    consecutive '1', counted *continuously* from the SYNC field's own
    first bit through the end of the CRC field -- there's no field-
    boundary reset, so this function must run over the whole logical
    packet (SYNC+PID+payload+CRC) in one pass, not per-field).

    Returns bits with parallel role/floating lists (see `can.py`'s
    `_stuff` for why -- lets a stuffed bit's origin, and whether a real
    payload bit was explicitly marked not-driven, survive insertion). A
    stuff bit itself is never floating: it's a real, always-driven
    protocol-mandated insertion, not payload content.
    """

    out_bits: list[int] = []
    out_roles: list[str] = []
    out_floating: list[bool] = []
    run = 0
    for bit, role, is_floating in zip(bits, roles, floating):
        out_bits.append(bit)
        out_roles.append(role)
        out_floating.append(is_floating)
        run = run + 1 if bit == 1 else 0
        if run == _STUFF_RUN_LENGTH:
            out_bits.append(0)
            out_roles.append("stuff")
            out_floating.append(False)
            run = 0
    return out_bits, out_roles, out_floating


def nrzi_encode(bits: list[int], initial_state: int = 1) -> list[int]:
    """NRZI-encode an already-stuffed logical bitstream: a logical '0'
    toggles the line state, a logical '1' holds it (USB 2.0 spec 7.1.8).
    `initial_state` is the line state in effect immediately before the
    first bit (1 = J, USB Full-Speed idle -- see `usb.py`'s
    `_STATE_TO_DPDM` for the J/K -> dp/dm mapping, and its docstring for
    why Full-Speed's J/K polarity must not be reused for Low-Speed).
    Returns one entry per input bit: the line state *during* that bit's
    interval, ready to map straight onto dp/dm.
    """

    state = initial_state
    out = []
    for bit in bits:
        if bit == 0:
            state ^= 1
        out.append(state)
    return out
