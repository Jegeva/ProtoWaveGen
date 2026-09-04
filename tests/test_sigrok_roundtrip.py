import shutil
import subprocess
from pathlib import Path

import pytest

from protowavegen.app import TimingDiagramApplication
from protowavegen.config import Config
from protowavegen.outputs.sigrok_writer import SigrokWriter

SIGROK_CLI = shutil.which("sigrok-cli")
pytestmark = pytest.mark.skipif(SIGROK_CLI is None, reason="sigrok-cli not installed")


def _write_sr(config: Config, path: Path) -> None:
    capture = TimingDiagramApplication(config).build_capture()
    SigrokWriter().write(capture, path)


def _decode(
    sr_path: Path, pd_spec: str, annotation_class: str | None, decoder_id: str | None = None
) -> list[str]:
    """Run sigrok-cli's own, independently-implemented decoder over one of
    our generated `.sr` files and return the decoded annotation values.

    This is the strongest correctness check available for the generator: if
    a real, separately-written decoder reconstructs exactly what we intended
    to encode, the waveform is electrically correct — not just internally
    self-consistent with our own hand-derived expected-edge assertions
    elsewhere in the suite.

    `decoder_id` names which decoder in `pd_spec` owns `annotation_class` —
    defaults to the first (leftmost) one, but a stacked spec like
    `"i2c:...,lm75"` needs the *last* decoder's id for its own classes.
    `annotation_class=None` shows every annotation from that decoder
    (`-A <decoder_id>` with no `=class` filter) — some decoders (e.g.
    `modbus`) spread one logical message across several classes with no
    single class covering all of it.
    """

    decoder_id = decoder_id or pd_spec.split(":")[0]
    filter_spec = decoder_id if annotation_class is None else f"{decoder_id}={annotation_class}"
    result = subprocess.run(
        [SIGROK_CLI, "-i", str(sr_path), "-P", pd_spec, "-A", filter_spec],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return [line.split(": ", 1)[1] for line in result.stdout.splitlines() if ": " in line]


def test_uart_roundtrips_through_sigroks_uart_decoder(tmp_path):
    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "uart0", "type": "uart",
                "params": {"baudrate": 9600},
                "operations": [{"op": "send", "data": [72, 101, 108, 108, 111]}],  # "Hello"
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "uart.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "uart:tx=uart0.tx:baudrate=9600:format=hex", "tx-data")
    assert decoded == ["48", "65", "6C", "6C", "6F"]


def test_i2c_roundtrips_through_sigroks_i2c_decoder(tmp_path):
    config = Config(
        samplerate=4_000_000,
        protocols=[
            {
                "id": "i2c0", "type": "i2c",
                "params": {"clock_hz": 100_000, "addr_bits": 7},
                "operations": [
                    {"op": "write", "address": 0x48, "data": [0x01, 0x2A]},
                    {"op": "read", "address": 0x48, "data": [0x00, 0x96]},
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "i2c.sr"
    _write_sr(config, sr_path)

    pd = "i2c:scl=i2c0.scl:sda=i2c0.sda"
    assert _decode(sr_path, pd, "address-write") == ["Write", "Address write: 48"]
    assert _decode(sr_path, pd, "data-write") == ["Data write: 01", "Data write: 2A"]
    assert _decode(sr_path, pd, "address-read") == ["Read", "Address read: 48"]
    assert _decode(sr_path, pd, "data-read") == ["Data read: 00", "Data read: 96"]
    assert _decode(sr_path, pd, "nack") == ["NACK"]  # only the last byte requested a nack
    assert len(_decode(sr_path, pd, "ack")) == 5  # every other transferred byte acked


def test_i2c_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """A payload byte using the l/h/z floating-bit sentinel alphabet (see
    `protocols/base.py`'s `decode_payload_with_floating`) still resolves to
    a concrete, correctly-encoded value on the wire — sigrok's independent
    I2C decoder has no notion of "floating", so this only proves the
    resolved bits themselves are right; the "floating" driver-annotation
    label is a diagram-only concern sigrok's `.sr` format can't carry
    (see this repo's CLAUDE.md on `SigrokWriter` dropping annotations)."""

    config = Config(
        samplerate=4_000_000,
        protocols=[
            {
                "id": "i2c0", "type": "i2c",
                "params": {"clock_hz": 100_000, "addr_bits": 7},
                "operations": [
                    # "hh" -> both nibbles floating-resolves-high -> 0xFF
                    {"op": "write", "address": 0x48, "data": "hh", "datatype": "hex"},
                    # "ll" -> both nibbles floating-resolves-low -> 0x00
                    {"op": "read", "address": 0x48, "data": "ll", "datatype": "hex"},
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "i2c_floating.sr"
    _write_sr(config, sr_path)

    pd = "i2c:scl=i2c0.scl:sda=i2c0.sda"
    assert _decode(sr_path, pd, "data-write") == ["Data write: FF"]
    assert _decode(sr_path, pd, "data-read") == ["Data read: 00"]


def test_lin_frame_bytes_roundtrip_through_sigroks_uart_decoder(tmp_path):
    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "lin_uart", "type": "uart",
                "params": {"baudrate": 19200, "duplex": "half"},
                "operations": [],
            },
            {
                "id": "lin0", "type": "lin", "stack_on": "lin_uart",
                "operations": [
                    {"op": "send_frame", "frame_id": 16, "data": [1, 2, 3], "checksum": "enhanced"}
                ],
            },
        ],
        outputs=[],
    )
    sr_path = tmp_path / "lin.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "uart:tx=lin_uart.data:baudrate=19200:format=hex", "tx-data")
    # sigrok's plain "uart" PD has no break-condition handling, so it reads
    # the break field as a spurious leading zero byte — the real frame
    # content (sync, PID, data, checksum) starts right after it. This still
    # independently confirms our protected-ID and checksum math: frame ID
    # 16 -> PID 0x50, and enhanced-checksum(0x50, [1,2,3]) -> 0xA9.
    assert decoded[1:] == ["55", "50", "01", "02", "03", "A9"]


def test_onewire_roundtrips_through_sigroks_onewire_link_decoder(tmp_path):
    """Regression test for a real bug this exact validation loop found: our
    slot/presence timing constants originally sat exactly on sigrok's
    onewire_link decoder's classification thresholds, and its live
    edge-vs-timeout race silently dropped every other bit (see onewire.py's
    docstring and the `_SLOT_US`/`_PRESENCE_DELAY_US`/`_RESET_RECOVERY_US`
    comments for the fix). This asserts full presence detection, zero
    decoder warnings, and every written/read bit reconstructed correctly."""

    config = Config(
        samplerate=2_000_000,
        protocols=[
            {
                "id": "ow0", "type": "onewire",
                "operations": [
                    {"op": "reset"},
                    {"op": "write", "data": [0xCC, 0x44]},
                    {"op": "read", "data": [0x28, 0x01, 0x4F]},
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "onewire.sr"
    _write_sr(config, sr_path)

    pd = "onewire_link:owr=ow0.dq"
    assert _decode(sr_path, pd, "presence") == ["Presence: true"]
    assert _decode(sr_path, pd, "warnings") == []

    bits = "".join(line.split(": ")[-1] for line in _decode(sr_path, pd, "bit"))
    expected_bits = "".join(
        str((byte >> i) & 1) for byte in (0xCC, 0x44, 0x28, 0x01, 0x4F) for i in range(8)
    )
    assert bits == expected_bits


def test_onewire_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as I2C/SPI
    earlier in this file, now for 1-Wire — "hl" -> 0xF0."""

    config = Config(
        samplerate=2_000_000,
        protocols=[
            {
                "id": "ow0", "type": "onewire",
                "operations": [
                    {"op": "reset"},
                    {"op": "write", "data": "hl", "datatype": "hex"},
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "onewire_floating.sr"
    _write_sr(config, sr_path)

    pd = "onewire_link:owr=ow0.dq"
    assert _decode(sr_path, pd, "warnings") == []
    bits = "".join(line.split(": ")[-1] for line in _decode(sr_path, pd, "bit"))
    expected_bits = "".join(str((0xF0 >> i) & 1) for i in range(8))  # LSB first
    assert bits == expected_bits


def test_can_roundtrips_through_sigroks_can_decoder(tmp_path):
    config = Config(
        samplerate=8_000_000,
        protocols=[
            {
                "id": "can0", "type": "can",
                "params": {"bitrate": 500_000},
                "operations": [{"op": "send", "identifier": 0x123, "data": [0xDE, 0xAD, 0xBE, 0xEF]}],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "can.sr"
    _write_sr(config, sr_path)

    pd = "can:can_rx=can0.can:nominal_bitrate=500000"
    assert _decode(sr_path, pd, "id") == ["Identifier: 291 (0x123)"]
    assert _decode(sr_path, pd, "dlc") == ["Data length code: 4"]
    assert _decode(sr_path, pd, "data") == [
        "Data byte 0: 0xde", "Data byte 1: 0xad", "Data byte 2: 0xbe", "Data byte 3: 0xef",
    ]
    assert _decode(sr_path, pd, "ack-slot") == ["ACK slot: ACK"]
    # sigrok's CAN decoder verifies the CRC itself and would warn on mismatch —
    # an empty warnings row is independent confirmation our CRC-15 and bit
    # stuffing are both correct, not just internally self-consistent.
    assert _decode(sr_path, pd, "warnings") == []


def test_can_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as I2C/SPI/
    1-Wire earlier in this file, now for CAN — "hl" -> 0xF0."""

    config = Config(
        samplerate=8_000_000,
        protocols=[
            {
                "id": "can0", "type": "can",
                "params": {"bitrate": 500_000},
                "operations": [{"op": "send", "identifier": 0x123, "data": "hl", "datatype": "hex"}],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "can_floating.sr"
    _write_sr(config, sr_path)

    pd = "can:can_rx=can0.can:nominal_bitrate=500000"
    assert _decode(sr_path, pd, "data") == ["Data byte 0: 0xf0"]
    assert _decode(sr_path, pd, "warnings") == []


# No sigrok round-trip test for LM75: sigrok's `lm75` decoder never resets
# its internal byte-pairing state (`self.databytes`) between I2C
# transactions, so it silently mispairs bytes across ANY two-transaction
# sequence (repeated-START combined, or two separate STOP-terminated
# transactions alike) — confirmed by reading its source at
# /usr/share/libsigrokdecode/decoders/lm75/pd.py. This isn't something a
# waveform can be shaped to work around; it's a decoder bug independent of
# what we generate. `Lm75._encode_temp`'s correctness is covered by
# tests/protocols/test_lm75.py's unit tests instead.

# No sigrok round-trip test for NesGamepad either, confirmed unfixable by
# reading sigrok's `spi`/`nes_gamepad` decoder sources (the latter, id
# 'nes_gamepad', stacks on the former) plus a hands-on probe: the generic
# `spi` decoder's `handle_bit()` only records a bit on a *qualifying clock
# edge* and only emits a word once exactly `wordsize` (8) such edges have
# been seen — there is no notion of a bit that's already valid before the
# first clock edge. Real NES hardware (and `NesGamepad.read_buttons()`,
# matching it) needs only 7 CLOCK pulses for 8 bits, since the first bit
# (button "A") is valid immediately once LATCH falls, before any CLOCK
# activity — confirmed by manually adding an 8th synthetic clock edge to a
# probe capture, at which point `spi`'s decoder immediately produced the
# correct byte. `spi`'s optional `cs` channel doesn't help either: LATCH
# is a momentary parallel-load strobe that's already back low by the time
# CLOCK activity starts, so mapping it to `cs` just makes the decoder treat
# the entire clocked region as CS-deasserted and ignore it outright.
# Shaping the waveform to add a bogus 8th CLOCK pulse would "fix" the
# decode but misrepresent real NES controller timing, which is exactly the
# kind of value-over-correctness trade this project doesn't make — so this
# is a genuine `spi`-decoder-model limitation (it assumes every shift
# register uses one clock edge per bit with none "free"), not a bug on our
# side. `NesGamepad.read_buttons`'s bit encoding/ordering is covered by
# `tests/protocols/test_nes_gamepad.py`'s unit tests instead.


def test_modbus_rtu_roundtrips_through_sigroks_modbus_decoder(tmp_path):
    config = Config(
        samplerate=1_000_000,
        protocols=[
            {"id": "uart0", "type": "uart", "params": {"baudrate": 19200}, "operations": []},
            {
                "id": "modbus0", "type": "modbus_rtu", "stack_on": "uart0",
                "operations": [
                    {"op": "read_holding_registers", "slave": 1, "start_addr": 0, "count": 10},
                    {"op": "write_single_register", "slave": 1, "addr": 0x10, "value": 0x1234},
                ],
            },
        ],
        outputs=[],
    )
    sr_path = tmp_path / "modbus.sr"
    _write_sr(config, sr_path)

    pd = "uart:tx=uart0.tx:rx=uart0.rx:baudrate=19200,modbus"
    decoded = _decode(sr_path, pd, None, decoder_id="modbus")
    assert decoded == [
        "Slave ID: 1",
        "Function 3: Read Holding Registers",
        "Start at address 0x0 / 30001",
        "Read 10 units of data",
        "CRC correct",
        "Slave ID: 1",
        "Function 6: Write Single Register",
        "Address 0x10 / 30016",
        "Register Value 0x1234 / 4660",
        "CRC correct",
    ]


def test_max7219_roundtrips_through_sigroks_max7219_decoder(tmp_path):
    """Regression test for a real bug this exact validation loop found:
    `SpiBus.transfer()` bracketed each call's own CS assert/deassert with
    zero samples between one call's deassert and the next call's assert,
    which is physically meaningless and left sigrok's `max7219` decoder
    unable to tell separate commands apart ("Overlong write"). Fixed by a
    mandatory minimum CS-deasserted recovery gap before every transfer
    (see `SpiBus.transfer`'s comment). This asserts all 5 commands across
    two separate stacked-protocol calls (`init()` + `set_digit()`) decode
    cleanly with no warnings."""

    config = Config(
        samplerate=10_000_000,
        protocols=[
            {"id": "spi0", "type": "spi", "params": {"clock_hz": 1_000_000, "width": 1, "mode": 0}, "operations": []},
            {
                "id": "disp0", "type": "max7219", "stack_on": "spi0",
                "operations": [
                    {"op": "init", "intensity": 8},
                    {"op": "set_digit", "position": 0, "value": 7},
                ],
            },
        ],
        outputs=[],
    )
    sr_path = tmp_path / "max7219.sr"
    _write_sr(config, sr_path)

    pd = "spi:clk=spi0.sclk:mosi=spi0.mosi:cs=spi0.cs,max7219"
    decoded = _decode(sr_path, pd, None, decoder_id="max7219")
    assert decoded == [
        "Shutdown: off",
        "Decode: 0b11111111",
        "Scan limit: 8",
        "Intensity: 8",
        "Digit 1: 07",
    ]


def test_wiegand_roundtrips_through_sigroks_wiegand_decoder(tmp_path):
    """sigrok's `wiegand` decoder only offers whole-millisecond bit widths
    (`bitwidth_ms`), so this uses a 1ms pulse (`pulse_us=1000`) rather than
    `WiegandBus`'s more realistic 50us default — a limitation of that
    decoder's own granularity, not something to change our default for.
    Also incidentally confirms the mandatory idle-margin padding
    (`pad_idle`) is functionally necessary here, not just cosmetic: without
    trailing idle samples after the last pulse, the decoder can't recognize
    the final bit's end and silently drops it (verified while writing this
    test) — `TimingDiagramApplication.build_capture()` always adds it.
    """

    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "wg0", "type": "wiegand", "params": {"pulse_us": 1000, "interval_us": 20000},
                "operations": [{"op": "send_card_26bit", "facility_code": 12, "card_number": 34567}],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "wiegand.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "wiegand:d0=wg0.d0:d1=wg0.d1:bitwidth_ms=1", "bits")
    bits = "".join(decoded)

    data_bits = [(12 >> i) & 1 for i in reversed(range(8))] + [(34567 >> i) & 1 for i in reversed(range(16))]
    leading_parity = sum(data_bits[:12]) % 2
    trailing_parity = 1 - (sum(data_bits[12:]) % 2)
    expected = "".join(str(b) for b in [leading_parity, *data_bits, trailing_parity])
    assert bits == expected


def test_wiegand_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as
    earlier in this file, now for Wiegand's `send_card_26bit` — facility
    code "0000110z" resolves via TRISTATE pull-high (z->1) to 13, same as
    the plain-int 13 would."""

    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "wg0", "type": "wiegand", "params": {"pulse_us": 1000, "interval_us": 20000},
                "operations": [
                    {
                        "op": "send_card_26bit",
                        "facility_code": "0000110z", "facility_code_datatype": "bits",
                        "card_number": 34567,
                    },
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "wiegand_floating.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "wiegand:d0=wg0.d0:d1=wg0.d1:bitwidth_ms=1", "bits")
    bits = "".join(decoded)

    facility_code = 13  # "0000110z" -> 0b0000110(1) since z resolves pull-high
    data_bits = [(facility_code >> i) & 1 for i in reversed(range(8))] + [(34567 >> i) & 1 for i in reversed(range(16))]
    leading_parity = sum(data_bits[:12]) % 2
    trailing_parity = 1 - (sum(data_bits[12:]) % 2)
    expected = "".join(str(b) for b in [leading_parity, *data_bits, trailing_parity])
    assert bits == expected


def test_dali_roundtrips_through_sigroks_dali_decoder(tmp_path):
    config = Config(
        samplerate=12_000,
        protocols=[
            {
                "id": "dali0", "type": "dali",
                "operations": [
                    {"op": "send_forward_frame", "DALI_ADDRESS": 0x01, "command": 0xFE},
                    {"op": "send_backward_frame", "answer": 0xFF},
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "dali.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "dali:dali=dali0.dali", None)
    assert "Raw data: 01" in decoded
    assert "Raw data: FE" in decoded
    assert "Command: 254 (Application Specific Command 254)" in decoded
    assert "Reply: FF" in decoded


def test_dali_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as
    earlier in this file, now for DALI — "2h" -> 0x2F."""

    config = Config(
        samplerate=12_000,
        protocols=[
            {
                "id": "dali0", "type": "dali",
                "operations": [
                    {
                        "op": "send_forward_frame",
                        "DALI_ADDRESS": "2h", "DALI_ADDRESS_datatype": "hex", "command": 0xFE,
                    },
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "dali_floating.sr"
    _write_sr(config, sr_path)

    decoded = _decode(sr_path, "dali:dali=dali0.dali", None)
    assert "Raw data: 2F" in decoded


def test_microwire_93xx_roundtrips_through_sigroks_eeprom93xx_decoder(tmp_path):
    """Regression test for two real bugs this exact validation loop found in
    `MicrowireBus.transfer()`: (1) CS dropped while `clk` was still high,
    so sigrok's `microwire` decoder silently dropped the last clocked bit
    of every transfer (address decoded fine, word data came up one bit
    short); (2) `do` (SO) changed on the same falling edge as `di` (SI),
    but the decoder samples SO on the *falling* edge expecting a real slave
    to have already changed it on the preceding *rising* edge — shifting
    every decoded SO bit by one position (confirmed: reading back 0xABCD
    decoded as a precisely 1-bit-shifted 0x579b). Both fixed in
    `MicrowireBus._clock_bit`/`transfer()` — see their comments."""

    config = Config(
        samplerate=10_000_000,
        protocols=[
            {"id": "mw0", "type": "microwire", "params": {"clock_hz": 1_000_000}, "operations": []},
            {
                "id": "ee0", "type": "microwire_93xx", "stack_on": "mw0", "params": {"addr_bits": 6},
                "operations": [
                    {"op": "write", "address": 1, "value": 0x1234},
                    {"op": "read", "address": 5, "value": 0xABCD},
                ],
            },
        ],
        outputs=[],
    )
    sr_path = tmp_path / "microwire_93xx.sr"
    _write_sr(config, sr_path)

    pd = "microwire:cs=mw0.cs:sk=mw0.clk:si=mw0.di:so=mw0.do,eeprom93xx:addresssize=6"
    decoded = _decode(sr_path, pd, None, decoder_id="eeprom93xx")
    assert decoded == [
        "Write enable",
        "Write word",
        "Address: 0x0001",
        "Data: 0x1234",
        "Read word",
        "Address: 0x0005",
        "Data: 0xabcd",
    ]


def test_microwire_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as
    earlier in this file, now for the bare Microwire transport's own
    `datatype="bits"` support — `microwire_93xx` itself has no datatype
    capability, so this exercises `MicrowireBus.transfer()` directly
    (same shape as `examples/microwire_basic.json`), not the stacked
    EEPROM device. Compares against a plain-digit config resolving to the
    exact same value ("1100hh10" and "11001110" both -> 0xCE) rather than
    hand-deriving the decoder's own per-bit annotation order/format —
    if floating resolution is correct, sigrok must decode both configs
    identically."""

    def _config(mosi_bits: str) -> Config:
        return Config(
            samplerate=10_000_000,
            protocols=[
                {
                    "id": "mw0", "type": "microwire", "params": {"clock_hz": 1_000_000},
                    "operations": [
                        {
                            "op": "transfer",
                            "mosi_bits": mosi_bits, "read_bits": "0101101001011010",
                            "datatype": "bits",
                        },
                    ],
                }
            ],
            outputs=[],
        )

    floating_path = tmp_path / "microwire_floating.sr"
    plain_path = tmp_path / "microwire_plain.sr"
    _write_sr(_config("1100hh10"), floating_path)
    _write_sr(_config("11001110"), plain_path)

    pd = "microwire:cs=mw0.cs:sk=mw0.clk:si=mw0.di:so=mw0.do"
    floating_decoded = _decode(floating_path, pd, None, decoder_id="microwire")
    plain_decoded = _decode(plain_path, pd, None, decoder_id="microwire")
    assert floating_decoded == plain_decoded
    assert floating_decoded  # sanity: decoder actually produced output


def test_ps2_roundtrips_through_sigroks_ps2_decoder(tmp_path):
    """sigrok's `ps2` decoder has an off-by-one in its bit-count flush
    check: it only emits a frame's decoded word once it sees a *12th*
    falling clock edge arrive — but a real PS/2 device->host frame is
    exactly 11 bits (start+8 data+parity+stop), so a single isolated frame
    never flushes. Sending a second frame right after lets its first
    falling edge act as the trigger that flushes the first frame (the
    second frame itself is never flushed, for the same reason — inherent
    to the decoder, not fixable from our side). Verified against
    `Ps2Bus.send_from_device`'s own odd-parity computation."""

    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "ps2_0", "type": "ps2",
                "operations": [
                    {"op": "send_from_device", "byte": 0x1C},
                    {"op": "send_from_device", "byte": 0x00},  # trailing frame only to flush the first
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "ps2.sr"
    _write_sr(config, sr_path)

    pd = "ps2:clk=ps2_0.clock:data=ps2_0.data"
    assert _decode(sr_path, pd, "start-bit") == ["Start bit"]
    assert _decode(sr_path, pd, "word") == ["Data: 1c"]
    assert _decode(sr_path, pd, "parity-ok") == ["Parity OK"]
    assert _decode(sr_path, pd, "parity-err") == []
    assert _decode(sr_path, pd, "stop-bit") == ["Stop bit"]


def test_ps2_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as
    earlier in this file, now for PS/2 — "zz" resolves via TRISTATE
    pull-high to 0xFF. Second frame only to flush the first, same
    decoder-off-by-one workaround as the baseline test above."""

    config = Config(
        samplerate=1_000_000,
        protocols=[
            {
                "id": "ps2_0", "type": "ps2",
                "operations": [
                    {"op": "send_from_device", "byte": "zz", "datatype": "hex"},
                    {"op": "send_from_device", "byte": 0x00},  # trailing frame only to flush the first
                ],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "ps2_floating.sr"
    _write_sr(config, sr_path)

    pd = "ps2:clk=ps2_0.clock:data=ps2_0.data"
    assert _decode(sr_path, pd, "word") == ["Data: ff"]
    assert _decode(sr_path, pd, "parity-ok") == ["Parity OK"]


def test_lin_roundtrips_through_sigroks_lin_decoder(tmp_path):
    """sigrok's dedicated `lin` decoder (stacked on `uart`) only flushes a
    frame's checksum once it either sees the *next* BREAK or two full
    UART-frame-durations of idle — our default 2% trailing idle margin
    isn't long enough for the latter. Sending a second LIN frame supplies
    the BREAK that flushes the first one's checksum (the second frame
    itself is left unflushed, same reasoning as the PS/2 case above)."""

    config = Config(
        samplerate=1_000_000,
        protocols=[
            {"id": "lin_uart", "type": "uart", "params": {"baudrate": 19200, "duplex": "half"}, "operations": []},
            {
                "id": "lin0", "type": "lin", "stack_on": "lin_uart",
                "operations": [
                    {"op": "send_frame", "frame_id": 16, "data": [1, 2, 3], "checksum": "enhanced"},
                    {"op": "send_frame", "frame_id": 0, "data": [], "checksum": "classic"},  # flushes the first
                ],
            },
        ],
        outputs=[],
    )
    sr_path = tmp_path / "lin.sr"
    _write_sr(config, sr_path)

    pd = "uart:tx=lin_uart.data:baudrate=19200:format=hex,lin"
    decoded = _decode(sr_path, pd, None, decoder_id="lin")
    assert decoded == [
        "Break condition",
        "Sync",
        "ID: 10 Parity: 1 (ok)",
        "Data: 0x01",
        "Data: 0x02",
        "Data: 0x03",
        "Checksum: 0xA9",
        "Break condition",
    ]


def test_spi_roundtrips_through_sigroks_spi_decoder(tmp_path):
    config = Config(
        samplerate=8_000_000,
        protocols=[
            {
                "id": "spi0", "type": "spi",
                "params": {"clock_hz": 1_000_000, "width": 1, "mode": 0, "bit_order": "msb"},
                "operations": [{"op": "transfer", "mosi": [0x9B, 0x01, 0x02], "miso": [0x00, 0x00, 0xC8]}],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "spi.sr"
    _write_sr(config, sr_path)

    pd = "spi:clk=spi0.sclk:mosi=spi0.mosi:miso=spi0.miso:cs=spi0.cs"
    assert _decode(sr_path, pd, "mosi-data") == ["9B", "01", "02"]
    assert _decode(sr_path, pd, "miso-data") == ["00", "00", "C8"]


def test_spi_with_floating_marker_still_roundtrips_through_sigrok(tmp_path):
    """Same floating-marker-resolves-to-concrete-bits guarantee as the I2C
    case earlier in this file, now for SPI's newly-added per-bit
    DriverTracker (this session's Phase B rollout) — "hl" -> 0xF0."""

    config = Config(
        samplerate=8_000_000,
        protocols=[
            {
                "id": "spi0", "type": "spi",
                "params": {"clock_hz": 1_000_000, "width": 1, "mode": 0, "bit_order": "msb"},
                "operations": [{"op": "transfer", "mosi": "hl", "miso": "l3", "datatype": "hex"}],
            }
        ],
        outputs=[],
    )
    sr_path = tmp_path / "spi_floating.sr"
    _write_sr(config, sr_path)

    pd = "spi:clk=spi0.sclk:mosi=spi0.mosi:miso=spi0.miso:cs=spi0.cs"
    assert _decode(sr_path, pd, "mosi-data") == ["F0"]
    assert _decode(sr_path, pd, "miso-data") == ["03"]
