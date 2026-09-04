from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Annotation:
    """One piece of metadata attached to a sample range.

    This is the single flexible mechanism covering every metadata need the
    protocols raise: who's driving a shared line (`track="driver"`), MSB/LSB
    bit order (`track="bitorder"`), a decoded protocol field label
    (`track="field"`), a framing-unit boundary (`track="unit"`), or any new
    track name a protocol needs — nothing beyond `Protocol`/`StackedProtocol`
    has to change to introduce one. Output writers group annotations by
    `track` when deciding how to render or whether to drop them (e.g. the
    sigrok `.sr` writer drops all annotations — the format has no slot for
    them).

    `signals=None` means the annotation applies to the whole capture rather
    than a specific wire (e.g. a global bit-order declaration). `end=None`
    means open-ended: it applies from `start` to the end of the capture.
    """

    track: str
    label: str
    start: int
    end: int | None = None
    signals: tuple[str, ...] | None = None
    data: dict = field(default_factory=dict)

    def covers(self, sample_index: int) -> bool:
        if sample_index < self.start:
            return False
        return self.end is None or sample_index < self.end

    def applies_to(self, signal_name: str) -> bool:
        return self.signals is None or signal_name in self.signals
