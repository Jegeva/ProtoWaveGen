from __future__ import annotations

from pathlib import Path

from .config import Config
from .model import Capture, CaptureBuilder, pad_idle
from .outputs import get_output_class
from .protocols import Protocol, get_protocol_class


class TimingDiagramApplication:
    """Base orchestration class: build the protocol stack from config,
    generate one shared `Capture`, and hand it to every requested output
    writer. Everything protocol- or output-format-specific lives in a
    registered plugin class (see `protocols/base.py` and `outputs/base.py`);
    this class only manages the sequencing.
    """

    def __init__(self, config: Config):
        self.config = config

    def build_nodes(self) -> dict[str, Protocol]:
        """Instantiate every `protocols[]` entry in declaration order,
        resolving `stack_on` references to already-built node instances.
        A node used as a transport must therefore be declared before
        whatever stacks on it."""

        nodes: dict[str, Protocol] = {}
        for spec in self.config.protocols:
            node_id = spec["id"]
            cls = get_protocol_class(spec["type"])
            params = dict(spec.get("params", {}))
            operations = spec.get("operations")
            if "stack_on" in spec:
                transport = nodes[spec["stack_on"]]
                node = cls(node_id, transport, operations=operations, **params)
            else:
                node = cls(node_id, operations=operations, **params)
            nodes[node_id] = node
        return nodes

    def _apply_unit_bits_overrides(self, nodes: dict[str, Protocol], builder: CaptureBuilder) -> None:
        """A `unit_bits` override (global config default, or per-node in its
        `protocols[]` spec) forces a fixed N-bit grouping for the SVG unit
        bar instead of whatever the protocol emits on its own — translated
        from "N bits" to raw samples via the node's `bit_period_samples`.
        Nodes with no such property (stacked protocols, stubs) or that never
        bound a bit period (no operations ran) are silently skipped."""

        for spec in self.config.protocols:
            unit_bits = spec.get("unit_bits", self.config.unit_bits)
            if not unit_bits:
                continue
            node = nodes[spec["id"]]
            period = getattr(node, "bit_period_samples", None)
            if not period:
                continue
            unit_samples = unit_bits * period
            signal_names = tuple(s.name for s in node.get_signals()) or None

            builder.clear_annotations("unit", signals=signal_names)
            t = 0
            while t < builder.cursor:
                end = min(t + unit_samples, builder.cursor)
                builder.annotate("unit", f"{unit_bits}b", start=t, end=end, signals=signal_names)
                t = end

    def build_capture(self) -> Capture:
        """Run generation end to end: build the node graph, produce the raw
        `Capture`, and add the mandatory idle margin — every generated
        capture gets this, not just ones written through `run()`, since it's
        a property of the generated signal stream itself."""

        nodes = self.build_nodes()
        builder = CaptureBuilder(samplerate=self.config.samplerate)

        # Two-phase: every node's signals exist before any node's operations
        # run, so a stacked protocol can call its transport's methods
        # regardless of which node was declared first.
        for node in nodes.values():
            node.register_signals(builder)
        for node in nodes.values():
            node.generate(builder)

        self._apply_unit_bits_overrides(nodes, builder)
        capture = builder.finish()
        return pad_idle(capture, self.config.idle_margin_fraction)

    def run(self) -> None:
        capture = self.build_capture()

        for output_spec in self.config.outputs:
            writer_cls = get_output_class(output_spec["type"])
            path = Path(output_spec["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            options = {k: v for k, v in output_spec.items() if k not in ("type", "path")}
            writer_cls().write(capture, path, **options)
