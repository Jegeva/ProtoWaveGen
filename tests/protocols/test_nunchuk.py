from protowavegen.model import CaptureBuilder
from protowavegen.protocols.i2c import I2CBus
from protowavegen.protocols.nunchuk import Nunchuk


def _setup():
    i2c = I2CBus("i2c0", clock_hz=100_000, addr_bits=7)
    nc = Nunchuk("nc0", i2c)
    builder = CaptureBuilder(samplerate=4_000_000)
    i2c.register_signals(builder)
    return nc, builder


def test_pack_buttons_are_active_low():
    unpressed = Nunchuk._pack((0, 0), (0, 0, 0), button_z=False, button_c=False)
    pressed = Nunchuk._pack((0, 0), (0, 0, 0), button_z=True, button_c=True)
    assert unpressed[5] & 0x03 == 0x03  # both bits set = not pressed
    assert pressed[5] & 0x03 == 0x00  # both bits clear = pressed


def test_pack_splits_10bit_accel_correctly():
    data = Nunchuk._pack((0, 0), (0x2AB, 0x155, 0x000), False, False)
    assert data[2] == (0x2AB >> 2) & 0xFF
    assert data[3] == (0x155 >> 2) & 0xFF
    assert (data[5] >> 2) & 0x3 == 0x2AB & 0x3
    assert (data[5] >> 4) & 0x3 == 0x155 & 0x3


def test_init_sends_two_writes():
    nc, builder = _setup()
    nc.init(builder)
    starts = [a for a in builder.finish().annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2


def test_poll_uses_repeated_start_and_labels_report():
    nc, builder = _setup()
    fh = nc.poll(builder, joystick=(128, 130), accel=(300, 310, 320), button_z=True, button_c=False)
    capture = builder.finish()

    starts = [a for a in capture.annotations if a.track == "field" and a.label == "start-condition"]
    assert len(starts) == 2

    fields = [a for a in capture.annotations if a.track == "field"]
    labels = {f.label for f in fields}
    assert any("JOY=(128,130)" in label for label in labels)
    assert any("Z=True C=False" in label for label in labels)
    assert fh.start == 0 and fh.end == capture.duration_samples
