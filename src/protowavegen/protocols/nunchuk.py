from __future__ import annotations

from ..model import CaptureBuilder, FrameHandle
from .base import StackedProtocol, register_protocol
from .i2c import I2CBus

_ADDRESS = 0x52


@register_protocol("nunchuk")
class Nunchuk(StackedProtocol):
    """Nintendo Wii Nunchuk (joystick + accelerometer + Z/C buttons),
    stacked on `I2CBus`. Fixed 7-bit address `0x52`.

    `init()` sends the two writes (`0xF0,0x55` then `0xFB,0x00`) that
    disable the Nunchuk's (mostly decorative) encryption — what essentially
    every third-party clone and driver expects; the real encrypted-data
    mode isn't modeled. `poll()` synthesizes one 6-byte report: joystick X/Y
    (8-bit each), accelerometer X/Y/Z (10-bit each, split 8 high bits in
    their own byte + 2 low bits packed into the report's last byte), and
    the Z/C buttons (active-low, also packed into that last byte).
    """

    def __init__(self, node_id: str, transport: I2CBus, *, operations: list[dict] | None = None):
        super().__init__(node_id, transport, operations)

    def init(self, builder: CaptureBuilder) -> FrameHandle:
        self.transport.write(builder, address=_ADDRESS, data=[0xF0, 0x55], labels=["INIT", "INIT"])
        return self.transport.write(builder, address=_ADDRESS, data=[0xFB, 0x00], labels=["INIT", "INIT"])

    @staticmethod
    def _pack(
        joystick: tuple[int, int], accel: tuple[int, int, int], button_z: bool, button_c: bool
    ) -> list[int]:
        jx, jy = joystick
        ax, ay, az = accel
        last = ((ax & 0x3) << 2) | ((ay & 0x3) << 4) | ((az & 0x3) << 6)
        last |= 0x00 if button_z else 0x01  # active-low
        last |= 0x00 if button_c else 0x02
        return [jx & 0xFF, jy & 0xFF, (ax >> 2) & 0xFF, (ay >> 2) & 0xFF, (az >> 2) & 0xFF, last]

    def poll(
        self, builder: CaptureBuilder, *, joystick: tuple[int, int], accel: tuple[int, int, int],
        button_z: bool = False, button_c: bool = False,
    ) -> FrameHandle:
        data = self._pack(joystick, accel, button_z, button_c)
        label = (
            f"JOY=({joystick[0]},{joystick[1]}) ACC=({accel[0]},{accel[1]},{accel[2]}) "
            f"Z={button_z} C={button_c}"
        )
        return self.transport.write_then_read(
            builder, address=_ADDRESS, write_data=[0x00], read_data=data,
            write_labels=["PTR=0x00"], read_labels=[label] * len(data),
        )
