from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, format_byte, register_protocol
from .payload import decode_payload
from .checksums import crc7_sd
from .spi import SpiBus

_DATA_START_TOKEN = 0xFE


@register_protocol("sd_spi")
class SdCardSpi(StackedProtocol):
    """SD card, SPI mode, stacked on `SpiBus` (`width=1`). The hardest of
    the SPI-stacked protocols here: a real command/response state machine
    rather than a fixed register poke.

    v1 scope only: `init()` (`CMD0` GO_IDLE_STATE -> R1, `CMD8`
    SEND_IF_COND -> R7, one `CMD55`+`ACMD41` SD_SEND_OP_COND round -> R1)
    and `read_block()` (`CMD17` READ_SINGLE_BLOCK -> R1, then a `0xFE` start
    token + data + a 2-byte CRC16 placeholder — real CRC16-CCITT on the
    data block isn't computed, since it isn't the interesting part to
    model here and the command CRC-7 already demonstrates the same "real
    checksum" pattern). Every command byte gets a real CRC-7. No CMD9/CMD10
    (CSD/CID), no write path (`CMD24`), no SDHC-vs-SDSC addressing
    distinction (byte addresses assumed).
    """

    def __init__(self, node_id: str, transport: SpiBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    @staticmethod
    def _command_bytes(cmd: int, arg: int) -> list[int]:
        body = [0x40 | cmd, (arg >> 24) & 0xFF, (arg >> 16) & 0xFF, (arg >> 8) & 0xFF, arg & 0xFF]
        return [*body, crc7_sd(body)]

    def _send_command(
        self, builder: CaptureBuilder, *, cmd: int, arg: int, response: list[int], response_label: str
    ) -> FrameHandle:
        cmd_bytes = self._command_bytes(cmd, arg)
        cmd_labels = [f"CMD{cmd}", *[f"ARG=0x{arg:08X}"] * 4, "CRC7"]
        # one NCR (response-delay) byte before the response is available
        mosi = [*cmd_bytes, 0xFF, *([0x00] * len(response))]
        miso = [*([0x00] * len(cmd_bytes)), 0xFF, *response]
        labels = [*cmd_labels, "NCR", *([response_label] * len(response))]
        return self.transport.transfer(builder, mosi=mosi, miso=miso, labels=labels)

    def init(self, builder: CaptureBuilder) -> FrameHandle:
        self._send_command(builder, cmd=0, arg=0, response=[0x01], response_label="R1=IDLE")
        self._send_command(builder, cmd=8, arg=0x1AA, response=[0x01, 0x00, 0x00, 0x01, 0xAA], response_label="R7")
        self._send_command(builder, cmd=55, arg=0, response=[0x01], response_label="R1")
        return self._send_command(builder, cmd=41, arg=0x40000000, response=[0x00], response_label="R1=READY")

    def read_block(self, builder: CaptureBuilder, *, address: int, data, datatype: str = "bytes") -> FrameHandle:
        """`data` is the synthesized block contents (512 bytes for a real
        card, any length here) — this tool generates rather than senses
        real flash contents."""

        data = decode_payload(data, datatype)
        self._send_command(builder, cmd=17, arg=address, response=[0x00], response_label="R1=OK")
        mosi = [0xFF] * (1 + len(data) + 2)
        miso = [_DATA_START_TOKEN, *data, 0x00, 0x00]  # CRC16 placeholder, not computed
        labels = ["TOKEN=0xFE", *(format_byte(b) for b in data), "CRC16", "CRC16"]
        return self.transport.transfer(builder, mosi=mosi, miso=miso, labels=labels)
