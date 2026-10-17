from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .adapters import KiCadFootprintAvailability
from .contracts import PCBArtifactDiff, PCBArtifactDiffStatus, PCBProposal
from .geometry import pad_local_position


def _number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def _uuid(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


def serialize_kicad_pcb(
    proposal: PCBProposal,
    *,
    footprint_root: Path | None = None,
    plane_nets: frozenset[str] = frozenset(),
    plane_layers: dict[str, str] | None = None,
) -> bytes:
    """Serialize the narrow, deterministic v1 PCB proposal to KiCad s-expression."""
    checked = PCBProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
    boundary = inspect_serializer_boundary(
        checked,
        footprint_root=footprint_root,
        plane_nets=plane_nets,
        plane_layers=plane_layers,
    )
    if boundary.status is PCBArtifactDiffStatus.BLOCKED:
        raise ValueError(
            "PCB serializer boundary is blocked by unapproved generated geometry: "
            + boundary.fingerprint
        )
    phase5_bound = checked.pcb_state_fingerprint is not None
    library = None if footprint_root is None else KiCadFootprintAvailability(footprint_root)
    if not isinstance(plane_nets, frozenset) or not all(isinstance(item, str) and item for item in plane_nets):
        raise TypeError("plane_nets must be a frozenset of non-empty net names")
    resolved_plane_layers = dict(plane_layers or {})
    if set(resolved_plane_layers) != set(plane_nets) or any(layer not in {"F.Cu", "B.Cu"} for layer in resolved_plane_layers.values()):
        raise ValueError("plane_layers must bind every plane net to F.Cu or B.Cu")
    if not plane_nets.issubset({item.net_name for item in checked.routing}):
        raise ValueError("plane net must have an approved physical route")
    if phase5_bound and library is not None and _implicit_library_geometry(checked, library):
        raise ValueError("Phase5 PCB serialization would add implicit via or stub geometry")
    board = checked.board
    x0, y0 = board.origin_x_mm, board.origin_y_mm
    x1, y1 = x0 + board.width_mm, y0 + board.height_mm
    net_ids = {name: index for index, name in enumerate(sorted(item.net_name for item in checked.net_assignments), start=1)}
    endpoint_nets = {
        (endpoint.reference, endpoint.pin_number): assignment.net_name
        for assignment in checked.net_assignments
        for endpoint in assignment.endpoints
    }
    lines = [
        '(kicad_pcb (version 20240108) (generator pcbnew)',
        '  (general' + (f' (thickness {_number(board.board_thickness_mm)})' if board.board_thickness_mm is not None else '') + ')',
        '  (paper "A4")',
        '  (layers',
        '    (0 "F.Cu" signal)',
        '    (31 "B.Cu" signal)',
        '    (36 "B.SilkS" user "b.Silkscreen")',
        '    (37 "F.SilkS" user "f.Silkscreen")',
        '    (44 "Edge.Cuts" user "Edge.Cuts")',
        '  )',
        '  (setup (pad_to_mask_clearance 0))',
        '  (net 0 "")',
    ]
    for name, number in sorted(net_ids.items(), key=lambda item: item[1]):
        lines.append(f'  (net {number} "{name}")')
    lines.extend(
        (
            f'  (gr_rect (start {_number(x0)} {_number(y0)}) (end {_number(x1)} {_number(y1)}) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{_uuid(checked.proposal_id, "outline")}"))',
        )
    )
    for footprint in checked.footprints:
        placement = footprint.placement
        if library is not None:
            lines.extend(
                _render_library_footprint(
                    library,
                    footprint_binding=footprint.footprint_binding,
                    component_ref=footprint.component_ref,
                    placement=placement,
                    stub_width_mm=checked.routing[0].width_mm if checked.routing else 0.1,
                    pad_nets={
                        pad: endpoint_nets.get((footprint.component_ref, pad))
                        for pad in footprint.pad_numbers
                    },
                    net_ids=net_ids,
                    footprint_uuid=_uuid(checked.proposal_id, "footprint", footprint.component_ref),
                ).splitlines()
            )
            continue
        lines.extend(
            (
                f'  (footprint "{footprint.footprint_binding}" (layer "{placement.side}")',
                f'    (uuid "{_uuid(checked.proposal_id, "footprint", footprint.component_ref)}")',
                f'    (at {_number(placement.x_mm)} {_number(placement.y_mm)} {_number(placement.rotation_deg)})',
                f'    (property "Reference" "{footprint.component_ref}" (at 0 -1.5 0) (layer "F.SilkS") (uuid "{_uuid(checked.proposal_id, "reference", footprint.component_ref)}") (effects (font (size 1.27 1.27))))',
                f'    (fp_rect (start -1 -1) (end 1 1) (stroke (width 0.12) (type default)) (fill none) (layer "F.SilkS") (uuid "{_uuid(checked.proposal_id, "body", footprint.component_ref)}"))',
            )
        )
        for index, pad in enumerate(footprint.pad_numbers):
            local_x, local_y = pad_local_position(index)
            net_name = endpoint_nets.get((footprint.component_ref, pad))
            net_suffix = "" if net_name is None else f' (net {net_ids[net_name]} "{net_name}")'
            lines.append(
                f'    (pad "{pad}" smd roundrect (at {_number(local_x)} {_number(local_y)}) (size 0.45 0.65) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.2){net_suffix} (uuid "{_uuid(checked.proposal_id, "pad", footprint.component_ref, pad)}"))'
            )
        lines.append("  )")
    stub_pad_points: set[tuple[float, float]] = set()
    f_cu_stubs: set[tuple[str, tuple[float, float], tuple[float, float]]] = set()
    via_anchors: set[tuple[str, str, tuple[float, float]]] = set()
    stub_width_mm = checked.routing[0].width_mm if checked.routing else 0.1
    if library is not None:
        endpoints_by_net = {
            assignment.net_name: assignment.endpoints
            for assignment in checked.net_assignments
        }
        for route in checked.routing:
            if route.layer != "B.Cu":
                for endpoint in endpoints_by_net[route.net_name]:
                    footprint = next(item for item in checked.footprints if item.component_ref == endpoint.reference)
                    pad_positions = library.pad_world_positions(
                        footprint.placement,
                        footprint.footprint_binding,
                        endpoint.pin_number,
                    )
                    primary = pad_positions[0]
                    for duplicate in pad_positions[1:]:
                        f_cu_stubs.add((route.net_name, duplicate, primary))
                continue
            for endpoint in endpoints_by_net[route.net_name]:
                footprint = next(item for item in checked.footprints if item.component_ref == endpoint.reference)
                pad_positions = library.pad_world_positions(
                    footprint.placement,
                    footprint.footprint_binding,
                    endpoint.pin_number,
                )
                primary = pad_positions[0]
                point_index = next(
                    (index for index, point in enumerate(route.points) if math.dist(point, primary) <= 1e-6),
                    None,
                )
                if point_index is None or library.pad_is_through_hole(footprint.footprint_binding, endpoint.pin_number):
                    continue
                stub_pad_points.add(primary)
                neighbors = (
                    route.points[point_index + 1] if route.net_name in plane_nets and point_index + 1 < len(route.points) else None,
                    route.points[point_index - 1] if route.net_name not in plane_nets and point_index > 0 else None,
                    route.points[point_index + 1] if route.net_name not in plane_nets and point_index + 1 < len(route.points) else None,
                    route.points[point_index - 1] if route.net_name in plane_nets and point_index > 0 else None,
                )
                anchor = next((candidate for candidate in neighbors if candidate is not None and math.dist(candidate, primary) > 1e-6), None)
                if anchor is None:
                    continue
                f_cu_stubs.add((route.net_name, primary, anchor))
                via_anchors.add((route.net_name, endpoint.reference, anchor))
                for duplicate in pad_positions[1:]:
                    f_cu_stubs.add((route.net_name, duplicate, primary))
        for net_name, reference, anchor in sorted(via_anchors):
            lines.append(
                f'  (via (at {_number(anchor[0])} {_number(anchor[1])}) (size 0.5) (drill 0.3) (layers "F.Cu" "B.Cu") (net {net_ids[net_name]}) (uuid "{_uuid(checked.proposal_id, "via", reference, str(anchor))}"))'
            )
        for index, (net_name, start, end) in enumerate(sorted(f_cu_stubs)):
            lines.append(
                f'  (segment (start {_number(start[0])} {_number(start[1])}) (end {_number(end[0])} {_number(end[1])}) (width {_number(stub_width_mm)}) (layer "F.Cu") (net {net_ids[net_name]}) (uuid "{_uuid(checked.proposal_id, "stub", net_name, str(index), str(start), str(end))}"))'
            )
    for route in checked.routing:
        if route.net_name in plane_nets and route.layer == resolved_plane_layers[route.net_name]:
            continue
        net_id = net_ids[route.net_name]
        for start, end in zip(route.points, route.points[1:]):
            if route.layer == "B.Cu" and (start in stub_pad_points or end in stub_pad_points):
                continue
            lines.append(
                f'  (segment (start {_number(start[0])} {_number(start[1])}) (end {_number(end[0])} {_number(end[1])}) (width {_number(route.width_mm)}) (layer "{route.layer}") (net {net_id}) (uuid "{_uuid(checked.proposal_id, "segment", route.net_name, str(start), str(end))}"))'
            )
    for net_name in sorted(plane_nets):
        layer = resolved_plane_layers[net_name]
        net_id = net_ids[net_name]
        lines.extend(
            (
                f'  (zone (net {net_id}) (net_name "{net_name}") (layer "{layer}") (hatch edge 0.5)',
                '    (connect_pads yes (clearance 0))',
                '    (min_thickness 0.25)',
                '    (fill yes (thermal_gap 0) (thermal_bridge_width 0))',
                f'    (polygon (pts (xy {_number(x0 + 1.0)} {_number(y0 + 1.0)}) (xy {_number(x1 - 1.0)} {_number(y0 + 1.0)}) (xy {_number(x1 - 1.0)} {_number(y1 - 1.0)}) (xy {_number(x0 + 1.0)} {_number(y1 - 1.0)})))',
                '  )',
            )
        )
    lines.append(")")
    return ("\n".join(lines) + "\n").encode("utf-8")


def inspect_serializer_boundary(
    proposal: PCBProposal,
    *,
    footprint_root: Path | None = None,
    plane_nets: frozenset[str] = frozenset(),
    plane_layers: dict[str, str] | None = None,
) -> PCBArtifactDiff:
    """Report any geometry the serializer would add outside approved input."""
    checked = PCBProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
    phase5_bound = checked.pcb_state_fingerprint is not None
    if not phase5_bound:
        return PCBArtifactDiff.create(
            proposal_fingerprint=checked.fingerprint,
            status=PCBArtifactDiffStatus.NO_UNEXPECTED_GEOMETRY,
            unexpected_objects=(),
            source_refs=checked.source_refs,
            reason="Phase5 serializer boundary is not applicable to a legacy proposal.",
        )
    unexpected: list[str] = []
    if footprint_root is None:
        unexpected.append("UNAPPROVED_FOOTPRINT_DEFAULT")
    if plane_nets or plane_layers:
        unexpected.append("EXTERNAL_PLANE_CONFIGURATION")
    if checked.vias or checked.zones or checked.keepouts:
        unexpected.append("UNSUPPORTED_EXPLICIT_GEOMETRY")
    if footprint_root is not None and not unexpected:
        library = KiCadFootprintAvailability(footprint_root)
        if _implicit_library_geometry(checked, library):
            unexpected.append("IMPLICIT_VIA_OR_STUB_GEOMETRY")
    status = PCBArtifactDiffStatus.BLOCKED if unexpected else PCBArtifactDiffStatus.NO_UNEXPECTED_GEOMETRY
    return PCBArtifactDiff.create(
        proposal_fingerprint=checked.fingerprint,
        status=status,
        unexpected_objects=tuple(unexpected),
        source_refs=checked.source_refs,
        reason=(
            "Serializer output is fully sourced from the approved proposal."
            if not unexpected
            else "Serializer would add or accept geometry not represented by the approved proposal."
        ),
    )


def _implicit_library_geometry(proposal: PCBProposal, library: KiCadFootprintAvailability) -> bool:
    """Detect geometry the legacy renderer would add from library inspection.

    Phase 5 requires every via/stub/zone to originate in the approved physical
    proposal.  The legacy serializer may derive a via or a duplicate-pad stub
    while rendering a footprint; that path is therefore rejected before bytes
    are created instead of being silently promoted into the artifact.
    """
    endpoints_by_net = {
        assignment.net_name: assignment.endpoints
        for assignment in proposal.net_assignments
    }
    footprints = {item.component_ref: item for item in proposal.footprints}
    for route in proposal.routing:
        for endpoint in endpoints_by_net[route.net_name]:
            footprint = footprints[endpoint.reference]
            pad_positions = library.pad_world_positions(
                footprint.placement,
                footprint.footprint_binding,
                endpoint.pin_number,
            )
            if len(pad_positions) > 1:
                return True
            primary = pad_positions[0]
            if route.layer == "B.Cu" and not library.pad_is_through_hole(
                footprint.footprint_binding,
                endpoint.pin_number,
            ) and any(math.dist(point, primary) <= 1e-6 for point in route.points):
                return True
    return False


def _render_library_footprint(
    library: KiCadFootprintAvailability,
    *,
    footprint_binding: str,
    component_ref: str,
    placement: object,
    stub_width_mm: float,
    pad_nets: dict[str, str | None],
    net_ids: dict[str, int],
    footprint_uuid: str,
) -> str:
    source = library.read_definition(footprint_binding).strip()
    pad_positions = library.pad_local_positions(footprint_binding)
    if not set(pad_nets).issubset(pad_positions):
        raise ValueError(
            f"approved footprint pad mapping does not match the KiCad library: {footprint_binding} "
            f"library={tuple(pad_positions)} mapped={tuple(pad_nets)}"
        )
    root = re.match(r'\(footprint\s+"(?:[^"\\]|\\.)*"', source)
    if root is None:
        raise ValueError("KiCad footprint definition is malformed")
    source = re.sub(
        r'(\(property\s+"Reference"\s+)"(?:[^"\\]|\\.)*"',
        rf'\1"{component_ref}"',
        source,
        count=1,
    )

    children = _direct_child_spans(source)
    replacements: list[tuple[int, int, str]] = []
    for start, end in children:
        child = source[start:end]
        match = re.match(r'\(pad\s+"([^"\\]+)"\s+', child)
        if match is None:
            continue
        pad_number = match.group(1)
        net_name = pad_nets.get(pad_number)
        if net_name is None:
            continue
        net_id = net_ids[net_name]
        body = child.rstrip()
        replacements.append(
            (
                start,
                end,
                body[:-1] + f'\n\t\t(net {net_id} "{net_name}")\n\t)',
            )
        )
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]

    children = _direct_child_spans(source)
    layer_end = next(
        (end for start, end in children if source[start:end].lstrip().startswith("(layer ")),
        None,
    )
    if layer_end is None:
        raise ValueError("KiCad footprint layer is missing")
    insertion = (
        f'\n\t(uuid "{footprint_uuid}")'
        f'\n\t(at {_number(float(placement.x_mm))} '
        f'{_number(float(placement.y_mm))} '
        f'{_number(float(placement.rotation_deg))})'
    )
    return source[:layer_end] + insertion + source[layer_end:]


def _direct_child_spans(source: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
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
        if character == ")":
            depth -= 1
            if depth == 1 and child_start is not None:
                spans.append((child_start, index + 1))
                child_start = None
    if depth != 0:
        raise ValueError("KiCad footprint expression is unbalanced")
    return tuple(spans)


__all__ = ("inspect_serializer_boundary", "serialize_kicad_pcb")
