"""Guard against the recurring bug class this project has hit three times
already (DALI's datatype-kwarg mismatch, a missing --data-bits flag,
seven_segment's `patterns` field): a protocol operation method grows full
datatype/floating-marker support in its Python signature, but the field
never gets added to `config.py`'s hand-maintained `_PAYLOAD_FIELDS` set,
making it silently unreachable via `--data-target`/CLI auto-detect.

Two tiers, per the field-naming convention `_datatype_kwarg_name`
(config.py) already resolves against:

- `{field}_datatype`-suffixed parameters (DALI, Wiegand, and now I2C's
  `write_then_read`) are unambiguous by signature alone — always checked.
- A bare shared `datatype` parameter is ambiguous when a method has more
  than one non-metadata parameter (e.g. `CanBus.send`'s `identifier`/
  `data`/`rtr` all coexist with one `datatype`, but only `data` is a real
  payload field) — checked only when filtering leaves exactly one
  candidate; methods left with more than one require an explicit entry in
  `_KNOWN_AMBIGUOUS` below, so a genuinely new ambiguous method fails loud
  instead of silently passing this test.
"""

import inspect

from protowavegen.config import _PAYLOAD_FIELDS
from protowavegen.protocols import registered_protocols

_METADATA_PARAMS = {
    "self", "builder", "datatype", "labels", "write_labels", "read_labels",
    "nack", "nack_last", "address", "identifier", "rtr", "channel", "direction",
    "checksum", "frame_id", "word_addr", "inter_byte_gap_bits", "pre_delay_bits", "driver",
    "pid", "endpoint",
}

# (class name, method name) pairs already vetted by hand as genuinely
# ambiguous under one shared `datatype` — more than one real payload field
# shares it, so this test can't safely guess which one to check.
_KNOWN_AMBIGUOUS = {
    ("CanBus", "send"),
    ("UartTransport", "send"),
    ("SpiBus", "wide_transfer"),
    ("I2CBus", "write_then_read"),
    ("IrdaBus", "send_frame"),      # info coexists with control/command/final
    ("IrdaBus", "send_i_frame"),    # info coexists with ns/nr/command/final
}


def test_every_field_datatype_suffixed_param_is_a_known_payload_field():
    for cls in registered_protocols().values():
        for meth_name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            params = inspect.signature(method).parameters
            for param in params:
                if param == "datatype" or not param.endswith("_datatype"):
                    continue
                field = param[: -len("_datatype")]
                assert field in _PAYLOAD_FIELDS, (
                    f"{cls.__name__}.{meth_name}: parameter {param!r} implies payload field "
                    f"{field!r}, which is missing from config.py's _PAYLOAD_FIELDS"
                )


def test_every_unambiguous_bare_datatype_field_is_a_known_payload_field():
    for cls in registered_protocols().values():
        for meth_name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            params = inspect.signature(method).parameters
            if "datatype" not in params:
                continue
            if (cls.__name__, meth_name) in _KNOWN_AMBIGUOUS:
                continue
            candidates = [p for p in params if p not in _METADATA_PARAMS and not p.endswith("_datatype")]
            if len(candidates) != 1:
                continue
            field = candidates[0]
            assert field in _PAYLOAD_FIELDS, (
                f"{cls.__name__}.{meth_name}: unambiguous datatype-controlled parameter "
                f"{field!r} is missing from config.py's _PAYLOAD_FIELDS (or, if this method "
                f"actually has more than one payload field sharing 'datatype', add "
                f"('{cls.__name__}', {meth_name!r}) to _KNOWN_AMBIGUOUS instead)"
            )
