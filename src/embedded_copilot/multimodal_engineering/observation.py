from __future__ import annotations

from .models import (
    ComponentObservationContent,
    DebugObservationContent,
    DocumentObservationContent,
    ImageObservationContent,
    PCBObservationContent,
    SchematicObservationContent,
    WaveformObservationContent,
)


def content_values(content: object) -> tuple[tuple[str, str], ...]:
    """Return stable descriptive claims without creating verified engineering facts."""

    values: list[tuple[str, str]] = []
    for name, value in content.model_dump(
        mode="python", exclude={"content_type", "fingerprint"}
    ).items():
        if value is None:
            continue
        entries = value if isinstance(value, tuple) else (value,)
        values.extend((name, str(item)) for item in entries if str(item))
    return tuple(values)


__all__ = (
    "ComponentObservationContent",
    "DebugObservationContent",
    "DocumentObservationContent",
    "ImageObservationContent",
    "PCBObservationContent",
    "SchematicObservationContent",
    "WaveformObservationContent",
    "content_values",
)
