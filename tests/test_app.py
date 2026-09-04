from protowavegen.app import TimingDiagramApplication
from protowavegen.config import Config
from protowavegen.model import CaptureBuilder
from protowavegen.protocols import get_protocol_class


def _config(protocols, outputs=None, unit_bits=None):
    return Config(samplerate=4_000_000, protocols=protocols, outputs=outputs or [], unit_bits=unit_bits)


def _build_and_generate(app):
    nodes = app.build_nodes()
    builder = CaptureBuilder(samplerate=app.config.samplerate)
    for node in nodes.values():
        node.register_signals(builder)
    for node in nodes.values():
        node.generate(builder)
    return nodes, builder


def test_build_capture_always_pads_idle_margin():
    protocols = [
        {
            "id": "uart0", "type": "uart",
            "params": {"baudrate": 9600},
            "operations": [{"op": "send", "data": [0x41]}],
        }
    ]
    app = TimingDiagramApplication(_config(protocols))  # idle_margin_fraction defaults to 0.02
    raw_nodes, raw_builder = _build_and_generate(app)
    raw_duration = raw_builder.cursor

    capture = app.build_capture()
    pad = max(round(raw_duration * 0.02), 1)
    assert capture.duration_samples == raw_duration + 2 * pad
    # no annotation or edge starts before the leading pad
    assert all(a.start >= pad for a in capture.annotations)
    assert all(edges[0] == (0, edges[0][1]) for edges in capture.edges.values())


def test_build_nodes_resolves_stack_on_to_an_instance():
    protocols = [{"id": "uart0", "type": "uart", "params": {"baudrate": 9600}, "operations": []}]
    app = TimingDiagramApplication(_config(protocols))
    nodes = app.build_nodes()
    assert isinstance(nodes["uart0"], get_protocol_class("uart"))


def test_unit_bits_global_override_replaces_native_units():
    protocols = [
        {
            "id": "i2c0", "type": "i2c",
            "params": {"clock_hz": 100_000, "addr_bits": 7},
            "operations": [{"op": "write", "address": 0x48, "data": [1, 2]}],
        }
    ]
    app = TimingDiagramApplication(_config(protocols, unit_bits=4))
    nodes, builder = _build_and_generate(app)

    native_units = [a for a in builder.finish().annotations if a.track == "unit"]
    assert len(native_units) == 3  # address + 2 data bytes, before the override runs

    app._apply_unit_bits_overrides(nodes, builder)
    capture = builder.finish()
    overridden_units = [a for a in capture.annotations if a.track == "unit"]
    assert overridden_units  # override actually produced something
    assert all(a.label == "4b" for a in overridden_units)

    period = nodes["i2c0"].bit_period_samples
    for a in overridden_units[:-1]:  # last chunk may be a short remainder
        assert a.end - a.start == 4 * period


def test_per_node_unit_bits_override_does_not_touch_other_nodes():
    protocols = [
        {
            "id": "uart0", "type": "uart",
            "params": {"baudrate": 9600},
            "operations": [{"op": "send", "data": [0x41]}],
            "unit_bits": 2,
        },
        {
            "id": "i2c0", "type": "i2c",
            "params": {"clock_hz": 100_000},
            "operations": [{"op": "write", "address": 1, "data": [2]}],
        },
    ]
    app = TimingDiagramApplication(_config(protocols))
    nodes, builder = _build_and_generate(app)
    app._apply_unit_bits_overrides(nodes, builder)
    capture = builder.finish()

    uart_units = [a for a in capture.annotations if a.track == "unit" and a.signals and "uart0.tx" in a.signals]
    i2c_units = [a for a in capture.annotations if a.track == "unit" and a.signals and "i2c0.sda" in a.signals]
    assert uart_units and all(a.label == "2b" for a in uart_units)
    assert i2c_units and all(a.label != "2b" for a in i2c_units)  # i2c kept its own native unit labels
