from __future__ import annotations

import math

from .contracts import PCBPlacementConstraint


def pad_local_position(index: int) -> tuple[float, float]:
    """Return the deterministic v1 local position used by the narrow serializer."""
    if index < 0:
        raise ValueError("pad index must be non-negative")
    return (-0.65 + (index // 2) * 1.3, -0.55 + (index % 2) * 1.1)


def pad_world_position(placement: PCBPlacementConstraint, pad_numbers: tuple[str, ...], pad_number: str) -> tuple[float, float]:
    try:
        index = pad_numbers.index(pad_number)
    except ValueError as error:
        raise ValueError(f"pad {pad_number!r} is not bound to the footprint") from error
    local_x, local_y = pad_local_position(index)
    # KiCad footprint rotation is clockwise in board coordinates; keep the
    # readiness geometry aligned with the serializer and real CLI output.
    angle = -math.radians(placement.rotation_deg)
    return (
        placement.x_mm + local_x * math.cos(angle) - local_y * math.sin(angle),
        placement.y_mm + local_x * math.sin(angle) + local_y * math.cos(angle),
    )


__all__ = ("pad_local_position", "pad_world_position")
