from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from ..model import Capture

_REGISTRY: dict[str, type["OutputWriter"]] = {}


def register_output(name: str) -> Callable[[type], type]:
    """Class decorator registering an output writer under `name` for use in
    JSON scenario files' `outputs[].type` field and the CLI `--format` flag.
    New output formats plug in without touching the app/core — this is the
    "make it easy to add other output formats" interface."""

    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"output {name!r} already registered")
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_output_class(name: str) -> type["OutputWriter"]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown output type {name!r}; available: {sorted(_REGISTRY)}") from None


class OutputWriter(ABC):
    """A pluggable rendering backend consuming a frozen `Capture`."""

    @abstractmethod
    def write(self, capture: Capture, path: Path, **options) -> None:
        """Render `capture` to `path`. `path`'s parent directory is
        guaranteed to already exist (the app creates it once up front)."""
