from dataclasses import dataclass
from enum import Enum


class SignalKind(Enum):
    """Electrical nature of a signal.

    TRISTATE marks an open-drain/open-collector wire (I2C SCL/SDA, 1-Wire
    DQ): a device only ever actively pulls it low, it never drives it high —
    level 1 means the line was *released* and a pullup resistor carries it
    high, not that anything drove it there. The sample stream still stores
    plain 0/1 levels (that's the voltage a real logic analyzer would sample),
    but a `TRISTATE` signal's `"driver"` annotations use `"pullup"` for every
    released/high span instead of naming a device, and output writers may
    render tristate signals distinctly (e.g. a lighter/dashed line while
    released) using that annotation. ANALOG is reserved for later.
    """

    DIGITAL = "digital"
    TRISTATE = "tristate"
    ANALOG = "analog"


@dataclass(frozen=True, slots=True)
class Signal:
    """Static declaration of one wire a protocol exposes.

    `initial_level` is the logic level the signal idles at before any protocol
    activity (e.g. 1 for UART TX/RX, I2C SCL/SDA; 0 for an active-low CS line).
    """

    name: str
    kind: SignalKind = SignalKind.DIGITAL
    initial_level: int = 1
