from __future__ import annotations

import math
import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from embedded_copilot.pcb.parser.kicad import (
    _atom,
    _child,
    _children,
    _parse_s_expression,
)
from embedded_copilot.pcb.parser.models import PCBParserLimits, SExpression


@dataclass(frozen=True, slots=True)
class BoardFacts:
    layer_count: int
    copper_layers: tuple[str, ...]
    board_thickness_mm: float | None
    board_dimensions_mm: tuple[float, float]
    outline_valid: bool
    outline_reason: str
    used_layers: tuple[str, ...]
    expected_gerber_layers: tuple[str, ...]
    drill_classes: tuple[str, ...]
    component_refs: tuple[str, ...]
    component_values: tuple[tuple[str, str], ...]
    smd_components: int
    through_holes: int
    mechanical_only: int
    min_track_width_mm: float | None
    min_drill_mm: float | None
    drill_hole_count: int = 0
    drill_sizes_mm: tuple[float, ...] = ()
    drill_class_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class CopperToEdgeMeasurement:
    """Measured copper clearance from the current KiCad board geometry."""

    artifact_fingerprint: str
    geometry_source: str
    measurement_method: str
    affected_objects: tuple[str, ...]
    actual_minimum_mm: float
    minimum_object: str
    edge_segment: tuple[float, float, float, float]


def _number(expr: SExpression | None, index: int) -> float | None:
    value = _atom(expr, index)
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _point(expr: SExpression | None, head: str) -> tuple[float, float] | None:
    child = _child(expr, head)
    x, y = _number(child, 1), _number(child, 2)
    return None if x is None or y is None else (x, y)


def _layer_name(expr: SExpression | None, head: str = "layer") -> str | None:
    return _atom(_child(expr, head), 1)


def _walk(expr: SExpression):
    yield expr
    for item in expr[1:]:
        if isinstance(item, tuple):
            yield from _walk(item)


def _outline_edges(root: SExpression) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for expression in _children(root, "gr_rect"):
        if _layer_name(expression) != "Edge.Cuts":
            continue
        start = _point(expression, "start")
        end = _point(expression, "end")
        if start is None or end is None or start == end:
            return ()
        x0, y0 = start
        x1, y1 = end
        edges.extend((((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))))
    for expression in _children(root, "gr_line"):
        if _layer_name(expression) != "Edge.Cuts":
            continue
        start = _point(expression, "start")
        end = _point(expression, "end")
        if start is None or end is None or start == end:
            return ()
        edges.append((start, end))
    return tuple(edges)


def _key(point: tuple[float, float]) -> tuple[int, int]:
    return round(point[0] * 1_000_000), round(point[1] * 1_000_000)


def _validate_outline(edges: tuple[tuple[tuple[float, float], tuple[float, float]], ...]) -> tuple[bool, str, tuple[float, float]]:
    if not edges:
        return False, "Edge.Cuts outline is missing or unsupported", (0.0, 0.0)
    degree: dict[tuple[int, int], int] = {}
    points = [point for edge in edges for point in edge]
    for start, end in edges:
        degree[_key(start)] = degree.get(_key(start), 0) + 1
        degree[_key(end)] = degree.get(_key(end), 0) + 1
    if any(value != 2 for value in degree.values()):
        return False, "Edge.Cuts outline is open or has invalid junctions", (0.0, 0.0)
    graph: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for start, end in edges:
        graph.setdefault(_key(start), set()).add(_key(end))
        graph.setdefault(_key(end), set()).add(_key(start))
    seen: set[tuple[int, int]] = set()
    pending = [next(iter(graph))]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph[current] - seen)
    if len(seen) != len(graph):
        return False, "Edge.Cuts contains multiple disconnected outlines", (0.0, 0.0)
    width = max(point[0] for point in points) - min(point[0] for point in points)
    height = max(point[1] for point in points) - min(point[1] for point in points)
    if width <= 0 or height <= 0:
        return False, "Edge.Cuts outline has zero area", (0.0, 0.0)
    return True, "Edge.Cuts is one closed non-zero-area outline", (width, height)


def _distance_point_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, start)
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    closest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.dist(point, closest)


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (
        second[1] - first[1]
    ) * (third[0] - first[0])


def _on_segment(
    start: tuple[float, float],
    point: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    return (
        min(start[0], end[0]) - 1e-9 <= point[0] <= max(start[0], end[0]) + 1e-9
        and min(start[1], end[1]) - 1e-9 <= point[1] <= max(start[1], end[1]) + 1e-9
    )


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    values = (
        _orientation(first_start, first_end, second_start),
        _orientation(first_start, first_end, second_end),
        _orientation(second_start, second_end, first_start),
        _orientation(second_start, second_end, first_end),
    )
    if (
        ((values[0] > 1e-9 and values[1] < -1e-9) or (values[0] < -1e-9 and values[1] > 1e-9))
        and ((values[2] > 1e-9 and values[3] < -1e-9) or (values[2] < -1e-9 and values[3] > 1e-9))
    ):
        return True
    return any(
        abs(value) <= 1e-9 and _on_segment(start, point, end)
        for value, start, point, end in (
            (values[0], first_start, second_start, first_end),
            (values[1], first_start, second_end, first_end),
            (values[2], second_start, first_start, second_end),
            (values[3], second_start, first_end, second_end),
        )
    )


def _distance_between_segments(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> float:
    if _segments_intersect(first_start, first_end, second_start, second_end):
        return 0.0
    return min(
        _distance_point_to_segment(first_start, second_start, second_end),
        _distance_point_to_segment(first_end, second_start, second_end),
        _distance_point_to_segment(second_start, first_start, first_end),
        _distance_point_to_segment(second_end, first_start, first_end),
    )


def _atom_list(expression: SExpression | None) -> tuple[str, ...]:
    if expression is None:
        return ()
    return tuple(item for item in expression[1:] if isinstance(item, str))


def _uuid_or_index(expression: SExpression, prefix: str, index: int) -> str:
    uuid = _atom(_child(expression, "uuid"), 1)
    return f"{prefix}:{uuid or index}"


def _footprint_reference(expression: SExpression, index: int) -> str:
    for property_expression in (*_children(expression, "property"), *_children(expression, "fp_text")):
        if (_atom(property_expression, 1) or "").casefold() == "reference":
            return _atom(property_expression, 2) or f"footprint-{index}"
    return f"footprint-{index}"


def _rotated_point(
    origin: tuple[float, float],
    local: tuple[float, float],
    rotation_degrees: float,
) -> tuple[float, float]:
    angle = math.radians(rotation_degrees)
    return (
        origin[0] + local[0] * math.cos(angle) - local[1] * math.sin(angle),
        origin[1] + local[0] * math.sin(angle) + local[1] * math.cos(angle),
    )


def _polygon_edges(points: Iterable[tuple[float, float]]) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    checked = tuple(points)
    if len(checked) < 2:
        return ()
    return tuple((checked[index], checked[(index + 1) % len(checked)]) for index in range(len(checked)))


def _clearance_to_outline(
    primitive_edges: tuple[tuple[tuple[float, float], tuple[float, float]], ...],
    outline_edges: tuple[tuple[tuple[float, float], tuple[float, float]], ...],
    radius: float = 0.0,
) -> tuple[float, tuple[tuple[float, float], tuple[float, float]]]:
    best = min(
        (
            _distance_between_segments(first[0], first[1], second[0], second[1])
            for first in primitive_edges
            for second in outline_edges
        ),
        default=math.inf,
    )
    edge = min(
        outline_edges,
        key=lambda candidate: min(
            _distance_between_segments(first[0], first[1], candidate[0], candidate[1])
            for first in primitive_edges
        ),
    )
    return max(0.0, best - radius), edge


def measure_copper_to_edge_text(
    text: str,
    *,
    artifact_fingerprint: str,
) -> CopperToEdgeMeasurement:
    """Measure copper clearance from KiCad text without using a manufacturing default."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("PCB source is unavailable")
    if not isinstance(artifact_fingerprint, str) or not artifact_fingerprint.startswith("sha256:"):
        raise ValueError("artifact fingerprint is invalid")
    root = _parse_s_expression(text, PCBParserLimits())
    if _atom(root, 0) != "kicad_pcb":
        raise ValueError("PCB source root is invalid")
    outline_edges = _outline_edges(root)
    valid, reason, _ = _validate_outline(outline_edges)
    if not valid:
        raise ValueError(f"copper-to-edge measurement unavailable: {reason}")
    layers_expr = _child(root, "layers")
    copper_layers = {
        _atom(item, 1)
        for item in (() if layers_expr is None else layers_expr[1:])
        if isinstance(item, tuple) and _atom(item, 2) == "signal" and _atom(item, 1) is not None
    }
    copper_layers.discard(None)
    candidates: list[tuple[float, str, tuple[tuple[float, float], tuple[float, float]]]] = []
    affected: list[str] = []

    def add_candidate(
        object_id: str,
        primitive_edges: tuple[tuple[tuple[float, float], tuple[float, float]], ...],
        radius: float = 0.0,
    ) -> None:
        if not primitive_edges:
            return
        clearance, edge = _clearance_to_outline(primitive_edges, outline_edges, radius)
        candidates.append((clearance, object_id, edge))
        affected.append(object_id)

    for index, segment in enumerate(_children(root, "segment")):
        layer = _atom(_child(segment, "layer"), 1)
        start = _point(segment, "start")
        end = _point(segment, "end")
        width = _number(_child(segment, "width"), 1)
        if layer not in copper_layers or start is None or end is None or width is None or width <= 0:
            continue
        add_candidate(_uuid_or_index(segment, "segment", index), ((start, end),), width / 2.0)

    for index, via in enumerate(_children(root, "via")):
        point = _point(via, "at")
        size = _number(_child(via, "size"), 1)
        layers = set(_atom_list(_child(via, "layers")))
        if point is None or size is None or size <= 0 or not layers.intersection(copper_layers):
            continue
        add_candidate(
            _uuid_or_index(via, "via", index),
            _polygon_edges(
                (
                    (point[0] - size / 2.0, point[1]),
                    (point[0], point[1] + size / 2.0),
                    (point[0] + size / 2.0, point[1]),
                    (point[0], point[1] - size / 2.0),
                )
            ),
        )

    for footprint_index, footprint in enumerate(_children(root, "footprint")):
        origin = _point(footprint, "at")
        rotation = _number(_child(footprint, "at"), 3) or 0.0
        if origin is None:
            continue
        reference = _footprint_reference(footprint, footprint_index)
        for pad_index, pad in enumerate(_children(footprint, "pad")):
            layers = set(_atom_list(_child(pad, "layers")))
            if not layers.intersection(copper_layers):
                continue
            local = _point(pad, "at") or (0.0, 0.0)
            size = _point(pad, "size")
            if size is None or size[0] <= 0 or size[1] <= 0:
                raise ValueError("copper-to-edge measurement unavailable: pad size is missing")
            center = _rotated_point(origin, local, rotation)
            shape = (_atom(pad, 3) or "").casefold()
            object_id = f"pad:{reference}:{_atom(pad, 1) or pad_index}"
            if shape == "circle":
                add_candidate(object_id, _polygon_edges(((center[0] - size[0] / 2, center[1]), (center[0] + size[0] / 2, center[1]))), max(size) / 2.0)
                continue
            corners = tuple(
                _rotated_point(origin, (local[0] + x, local[1] + y), rotation)
                for x, y in (
                    (-size[0] / 2, -size[1] / 2),
                    (size[0] / 2, -size[1] / 2),
                    (size[0] / 2, size[1] / 2),
                    (-size[0] / 2, size[1] / 2),
                )
            )
            add_candidate(object_id, _polygon_edges(corners))

    for zone_index, zone in enumerate(_children(root, "zone")):
        layers = set(_atom_list(_child(zone, "layers")))
        if not layers.intersection(copper_layers):
            continue
        polygon = _child(zone, "polygon")
        points = tuple(
            point
            for item in (() if polygon is None else _children(_child(polygon, "pts") or (), "xy"))
            if (point := _point(item, "xy")) is not None
        )
        if len(points) < 3:
            raise ValueError("copper-to-edge measurement unavailable: zone geometry is missing")
        add_candidate(_uuid_or_index(zone, "zone", zone_index), _polygon_edges(points))

    if not candidates:
        raise ValueError("copper-to-edge measurement unavailable: no copper primitive was found")
    actual, object_id, edge = min(candidates, key=lambda item: (item[0], item[1]))
    return CopperToEdgeMeasurement(
        artifact_fingerprint=artifact_fingerprint,
        geometry_source="current KiCad PCB artifact parsed by manufacturing.board",
        measurement_method=(
            "minimum Euclidean distance between Edge.Cuts segments and all current "
            "copper tracks, pads, vias, and zone polygons, minus copper radius"
        ),
        affected_objects=tuple(sorted(set(affected))),
        actual_minimum_mm=round(actual, 9),
        minimum_object=object_id,
        edge_segment=(edge[0][0], edge[0][1], edge[1][0], edge[1][1]),
    )


def measure_copper_to_edge(
    path: Path,
    *,
    artifact_fingerprint: str | None = None,
) -> CopperToEdgeMeasurement:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("PCB source is unavailable")
    content = candidate.read_bytes()
    actual_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
    if artifact_fingerprint is not None and artifact_fingerprint != actual_fingerprint:
        raise ValueError("PCB artifact fingerprint is stale")
    return measure_copper_to_edge_text(content.decode("utf-8"), artifact_fingerprint=actual_fingerprint)


def min_trace_spacing_from_routes(routes: Iterable[object]) -> float | None:
    """Measure minimum clearance between distinct-net routed segments."""

    segments: list[tuple[str, str, tuple[float, float], tuple[float, float], float]] = []
    for route in routes:
        layer = getattr(route, "layer", None)
        net_id = getattr(route, "net_id", None)
        start = getattr(route, "start", None)
        end = getattr(route, "end", None)
        width = getattr(route, "width_mm", None)
        if not isinstance(layer, str) or not isinstance(net_id, str) or not isinstance(width, (int, float)):
            continue
        if not isinstance(start, (tuple, list)) or not isinstance(end, (tuple, list)) or len(start) != 2 or len(end) != 2:
            continue
        first = (float(start[0]), float(start[1]))
        second = (float(end[0]), float(end[1]))
        if not all(math.isfinite(item) for item in (*first, *second, float(width))) or width <= 0:
            continue
        segments.append((layer, net_id, first, second, float(width)))
    values = [
        max(
            0.0,
            _distance_between_segments(left[2], left[3], right[2], right[3])
            - left[4] / 2.0
            - right[4] / 2.0,
        )
        for index, left in enumerate(segments)
        for right in segments[index + 1 :]
        if left[0] == right[0] and left[1] != right[1]
    ]
    return None if not values else round(min(values), 9)


def _used_layers(root: SExpression) -> tuple[str, ...]:
    values: set[str] = set()

    def visit(expr: SExpression, *, skip_layer_table: bool = False) -> None:
        if not expr:
            return
        if _atom(expr, 0) == "layers":
            return
        if _atom(expr, 0) == "layer":
            value = _atom(expr, 1)
            if value:
                values.add(value)
        for item in expr[1:]:
            if isinstance(item, tuple):
                visit(item)

    visit(root)
    return tuple(sorted(values))


def _drill_classes(root: SExpression) -> tuple[str, ...]:
    classes: set[str] = set()
    for expression in _children(root, "footprint"):
        for pad in _children(expression, "pad"):
            kind = (_atom(pad, 2) or "").casefold()
            if "np_thru_hole" in kind:
                classes.add("NPTH")
            elif "thru_hole" in kind:
                classes.add("PTH")
            elif _child(pad, "drill") is not None and "smd" not in kind:
                # Older KiCad files can omit the explicit pad type. A pad
                # carrying a drill is then conservatively treated as plated;
                # the source still remains authoritative for the export.
                classes.add("NPTH")
    if _children(root, "via"):
        classes.add("PTH")
    return tuple(sorted(classes))


def _drill_inventory(root: SExpression) -> tuple[int, tuple[float, ...], tuple[tuple[str, int], ...]]:
    sizes: list[float] = []
    class_counts: dict[str, int] = {}

    def add(kind: str, expression: SExpression | None) -> None:
        value = _number(expression, 1)
        if value is None or value <= 0:
            return
        normalized = kind.upper()
        sizes.append(value)
        class_counts[normalized] = class_counts.get(normalized, 0) + 1

    for footprint in _children(root, "footprint"):
        for pad in _children(footprint, "pad"):
            drill = _child(pad, "drill")
            if drill is None:
                continue
            pad_kind = (_atom(pad, 2) or "").casefold()
            if "np_thru_hole" in pad_kind:
                add("NPTH", drill)
            elif "thru_hole" in pad_kind:
                add("PTH", drill)
            else:
                add("NPTH", drill)
    for via in _children(root, "via"):
        add("PTH", _child(via, "drill"))
    return (
        len(sizes),
        tuple(sorted({round(value, 9) for value in sizes})),
        tuple(sorted(class_counts.items())),
    )


def _components(root: SExpression) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for expression in _children(root, "footprint"):
        reference = None
        value = None
        for prop in (*_children(expression, "property"), *_children(expression, "fp_text")):
            kind = (_atom(prop, 1) or "").casefold()
            if kind == "reference":
                reference = _atom(prop, 2)
            elif kind == "value":
                value = _atom(prop, 2)
        if reference:
            values.append((reference, value or ""))
    return tuple(sorted(values))


def _footprint_classes(root: SExpression) -> tuple[int, int, int]:
    smd = 0
    through_hole = 0
    mechanical = 0
    for footprint in _children(root, "footprint"):
        pads = _children(footprint, "pad")
        pad_kinds = {(_atom(pad, 2) or "").casefold() for pad in pads}
        if "smd" in pad_kinds:
            smd += 1
        elif pad_kinds.intersection({"thru_hole", "np_thru_hole"}) or any(
            _child(pad, "drill") is not None for pad in pads
        ):
            through_hole += 1
        else:
            mechanical += 1
    return smd, through_hole, mechanical


def read_board_facts(path: Path) -> BoardFacts:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("PCB source is unavailable")
    metadata = candidate.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > 25 * 1024 * 1024:
        raise ValueError("PCB source size is invalid")
    try:
        text = candidate.read_text(encoding="utf-8")
        root = _parse_s_expression(text, PCBParserLimits())
    except (OSError, UnicodeError, ValueError):
        raise ValueError("PCB source is not a valid KiCad board") from None
    if _atom(root, 0) != "kicad_pcb":
        raise ValueError("PCB source root is invalid")
    layers_expr = _child(root, "layers")
    if layers_expr is None:
        raise ValueError("PCB layer table is missing")
    copper = tuple(
        _atom(item, 1)
        for item in layers_expr[1:]
        if isinstance(item, tuple) and _atom(item, 2) == "signal" and (_atom(item, 1) or "").endswith(".Cu")
    )
    copper = tuple(item for item in copper if item is not None)
    used = _used_layers(root)
    expected = tuple(sorted((*copper, *(item for item in used if item not in copper and item.endswith((".SilkS", "Edge.Cuts"))))))
    outline_ok, outline_reason, dimensions = _validate_outline(_outline_edges(root))
    general = _child(root, "general")
    thickness = _number(general, 0)
    thickness_child = _child(general, "thickness")
    thickness = _number(thickness_child, 1) if thickness_child is not None else thickness
    widths = [_number(_child(item, "width"), 1) for item in _children(root, "segment")]
    widths = [item for item in widths if item is not None and item > 0]
    drill_hole_count, drill_sizes_mm, drill_class_counts = _drill_inventory(root)
    components = _components(root)
    smd_components, through_holes, mechanical_only = _footprint_classes(root)
    return BoardFacts(
        layer_count=len(copper),
        copper_layers=copper,
        board_thickness_mm=thickness,
        board_dimensions_mm=dimensions,
        outline_valid=outline_ok,
        outline_reason=outline_reason,
        used_layers=used,
        expected_gerber_layers=expected,
        drill_classes=_drill_classes(root),
        component_refs=tuple(reference for reference, _ in components),
        component_values=components,
        smd_components=smd_components,
        through_holes=through_holes,
        mechanical_only=mechanical_only,
        min_track_width_mm=min(widths) if widths else None,
        min_drill_mm=min(drill_sizes_mm) if drill_sizes_mm else None,
        drill_hole_count=drill_hole_count,
        drill_sizes_mm=drill_sizes_mm,
        drill_class_counts=drill_class_counts,
    )


def render_board_facts(facts: BoardFacts) -> str:
    return "\n".join(
        (
            "EMBRIX_CANONICAL_BOARD_FACTS",
            f"layer_count={facts.layer_count}",
            f"outline={'VALID' if facts.outline_valid else 'INVALID'}",
            f"size={facts.board_dimensions_mm[0]}x{facts.board_dimensions_mm[1]}mm",
            f"smd_components={facts.smd_components}",
            f"through_holes={facts.through_holes}",
            f"mechanical_only={facts.mechanical_only}",
        )
    )


__all__ = (
    "BoardFacts",
    "CopperToEdgeMeasurement",
    "measure_copper_to_edge",
    "measure_copper_to_edge_text",
    "min_trace_spacing_from_routes",
    "read_board_facts",
    "render_board_facts",
)
