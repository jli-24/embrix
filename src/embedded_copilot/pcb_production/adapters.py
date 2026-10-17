from __future__ import annotations

import math
import re
from pathlib import Path


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


class KiCadFootprintAvailability:
    """Resolve only the standard KiCad ``Library:Footprint`` identity."""

    def __init__(self, footprint_root: Path) -> None:
        self._root = Path(footprint_root).resolve()
        self._definition_cache: dict[str, tuple[tuple[int, int], str]] = {}
        self._positions_cache: dict[
            str, tuple[str, dict[str, tuple[tuple[float, float], ...]]]
        ] = {}
        self._through_hole_cache: dict[str, tuple[str, frozenset[str]]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def is_available(self, footprint_binding: str) -> bool:
        try:
            _footprint_path(self._root, footprint_binding)
        except (OSError, ValueError):
            return False
        return True

    def read_definition(self, footprint_binding: str) -> str:
        path = _footprint_path(self._root, footprint_binding)
        stat = path.stat()
        state = (stat.st_mtime_ns, stat.st_size)
        cached = self._definition_cache.get(footprint_binding)
        if cached is not None and cached[0] == state:
            return cached[1]
        definition = _read_footprint_definition(self._root, footprint_binding)
        refreshed = _footprint_path(self._root, footprint_binding).stat()
        refreshed_state = (refreshed.st_mtime_ns, refreshed.st_size)
        self._definition_cache[footprint_binding] = (refreshed_state, definition)
        return definition

    def pad_local_positions(self, footprint_binding: str) -> dict[str, tuple[float, float]]:
        return {
            pad: positions[0]
            for pad, positions in self.pad_local_positions_all(footprint_binding).items()
        }

    def pad_local_positions_all(self, footprint_binding: str) -> dict[str, tuple[tuple[float, float], ...]]:
        definition = self.read_definition(footprint_binding)
        cached = self._positions_cache.get(footprint_binding)
        if cached is None or cached[0] is not definition:
            parsed = _parse_pad_local_positions_all(definition)
            self._positions_cache[footprint_binding] = (definition, parsed)
            cached = (definition, parsed)
        return dict(cached[1])

    def pad_world_positions(self, placement: object, footprint_binding: str, pad_number: str) -> tuple[tuple[float, float], ...]:
        rotation = math.radians(float(getattr(placement, "rotation_deg")))
        angle = -rotation
        return tuple(
            (
                float(getattr(placement, "x_mm")) + local_x * math.cos(angle) - local_y * math.sin(angle),
                float(getattr(placement, "y_mm")) + local_x * math.sin(angle) + local_y * math.cos(angle),
            )
            for local_x, local_y in self.pad_local_positions_all(footprint_binding)[pad_number]
        )

    def pad_is_through_hole(self, footprint_binding: str, pad_number: str) -> bool:
        definition = self.read_definition(footprint_binding)
        cached = self._through_hole_cache.get(footprint_binding)
        if cached is None or cached[0] is not definition:
            through_hole_pads = frozenset(
                match.group(1)
                for child in _direct_children(definition)
                if (match := re.match(r'\(pad\s+"([^"]+)"\s+thru_hole\b', child)) is not None
            )
            self._through_hole_cache[footprint_binding] = (definition, through_hole_pads)
            cached = (definition, through_hole_pads)
        return pad_number in cached[1]

    def pad_world_position(self, placement: object, footprint_binding: str, pad_number: str) -> tuple[float, float]:
        local_x, local_y = self.pad_local_positions(footprint_binding)[pad_number]
        rotation = math.radians(float(getattr(placement, "rotation_deg")))
        angle = -rotation
        return (
            float(getattr(placement, "x_mm")) + local_x * math.cos(angle) - local_y * math.sin(angle),
            float(getattr(placement, "y_mm")) + local_x * math.sin(angle) + local_y * math.cos(angle),
        )


def _footprint_path(root: Path, footprint_binding: str) -> Path:
    if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
        raise ValueError("KiCad footprint root is invalid")
    if not isinstance(footprint_binding, str) or footprint_binding.count(":") != 1:
        raise ValueError("KiCad footprint binding is invalid")
    library, name = footprint_binding.split(":", 1)
    if not library or not name or any(
        part in {"", ".", ".."} for part in (*library.replace("\\", "/").split("/"), *name.replace("\\", "/").split("/"))
    ):
        raise ValueError("KiCad footprint binding is invalid")
    resolved_root = root.resolve(strict=True)
    raw_candidate = resolved_root / f"{library}.pretty" / f"{name}.kicad_mod"
    if raw_candidate.is_symlink():
        raise ValueError("KiCad footprint is a symlink")
    candidate = raw_candidate.resolve(strict=False)
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        raise ValueError("KiCad footprint is unavailable")
    return candidate


def _read_footprint_definition(root: Path, footprint_binding: str) -> str:
    path = _footprint_path(root, footprint_binding)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("KiCad footprint is too large")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError("KiCad footprint cannot be read") from error


def _parse_pad_local_positions(source: str) -> dict[str, tuple[float, float]]:
    return {pad: positions[0] for pad, positions in _parse_pad_local_positions_all(source).items()}


def _parse_pad_local_positions_all(source: str) -> dict[str, tuple[tuple[float, float], ...]]:
    positions: dict[str, list[tuple[float, float]]] = {}
    for child in _direct_children(source):
        match = re.match(r'\(pad\s+"([^"\\]+)"\s+', child)
        if match is None:
            continue
        at = re.search(rf"\(at\s+({_NUMBER})\s+({_NUMBER})(?:\s+{_NUMBER})?\)", child)
        if at is None:
            raise ValueError("KiCad footprint pad position is missing")
        pad_number = match.group(1)
        positions.setdefault(pad_number, []).append((float(at.group(1)), float(at.group(2))))
    if not positions:
        raise ValueError("KiCad footprint has no pads")
    return {pad: tuple(values) for pad, values in positions.items()}


def _direct_children(source: str) -> tuple[str, ...]:
    children: list[str] = []
    depth = 0
    child_start: int | None = None
    in_quote = False
    escaped = False
    for index, character in enumerate(source):
        if in_quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_quote = False
            continue
        if character == '"':
            in_quote = True
            continue
        if character == "(":
            if depth == 1:
                child_start = index
            depth += 1
            continue
        if character != ")":
            continue
        depth -= 1
        if depth == 1 and child_start is not None:
            children.append(source[child_start : index + 1])
            child_start = None
    if depth != 0:
        raise ValueError("KiCad footprint expression is unbalanced")
    return tuple(children)


__all__ = (
    "KiCadFootprintAvailability",
    "_direct_children",
    "_parse_pad_local_positions",
    "_read_footprint_definition",
)
