from __future__ import annotations

import csv
import io
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from itertools import pairwise

from .exceptions import PCBParseError, PCBSourceDrift, PCBUnsupportedFormat
from .models import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBBOMArtifact,
    PCBBOMItem,
    PCBComponent,
    PCBConnection,
    PCBEvidenceClaim,
    PCBEvidenceReference,
    PCBEvidenceSource,
    PCBNet,
    PCBNetlistArtifact,
    PCBNetType,
    PCBPin,
    PCBPopulation,
    PCBSchematicArtifact,
    content_fingerprint,
    deterministic_id,
)

MAX_BYTES = 16_777_216
MAX_TOKENS = 300_000
MAX_DEPTH = 64
MAX_COMPONENTS = 20_000
MAX_NETS = 50_000
MAX_CONNECTIONS = 200_000
SUPPORTED_SCHEMATIC_VERSIONS = frozenset({"20231120"})
BOM_HEADER = (
    "References",
    "Quantity",
    "Value",
    "Footprint",
    "Manufacturer",
    "ManufacturerPartNumber",
    "Population",
)
CANONICAL_BOM_HEADER = ("Reference", "Value", "Symbol", "Description", "Source")


def _checked_content(artifact: PCBArtifactReference, content: bytes) -> bytes:
    if type(content) is not bytes or not content or len(content) > MAX_BYTES:
        raise PCBParseError("artifact content size is invalid")
    if content_fingerprint(content) != artifact.content_fingerprint:
        raise PCBSourceDrift("artifact content fingerprint changed")
    return content


def artifact_evidence(artifact: PCBArtifactReference) -> PCBEvidenceReference:
    return PCBEvidenceReference.create(
        evidence_id=deterministic_id("evidence", artifact.fingerprint),
        project_id=artifact.project_id,
        source_type=PCBEvidenceSource.ARTIFACT,
        source_id=artifact.artifact_id,
        source_fingerprint=artifact.content_fingerprint,
        claim=PCBEvidenceClaim.OBSERVED,
        locator=artifact.source_reference,
    )


def _decode_utf8(artifact: PCBArtifactReference, content: bytes) -> str:
    checked = _checked_content(artifact, content)
    try:
        return checked.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PCBParseError("artifact must be UTF-8") from exc


def parse_csv_bom(artifact: PCBArtifactReference, content: bytes) -> PCBBOMArtifact:
    if artifact.artifact_type is not PCBArtifactType.BOM:
        raise PCBUnsupportedFormat("CSV BOM parser received a non-BOM artifact")
    text = _decode_utf8(artifact, content)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        header = tuple(reader.fieldnames or ())
        if header not in {BOM_HEADER, CANONICAL_BOM_HEADER}:
            raise PCBParseError("BOM header does not match the approved schema")
        evidence = artifact_evidence(artifact)
        items: list[PCBBOMItem] = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise PCBParseError(f"BOM row {row_number} is malformed")
            if header == CANONICAL_BOM_HEADER:
                reference = row["Reference"].strip()
                value = row["Value"].strip()
                if not reference or not value or reference in seen:
                    raise PCBParseError("canonical BOM identity is missing or duplicated")
                refs = (reference,)
                quantity = 1
                population = PCBPopulation.POPULATED
                footprint = None
                manufacturer = None
                manufacturer_part_number = None
                symbol = row["Symbol"].strip()
                description = row["Description"].strip()
                source_reference = row["Source"].strip()
            else:
                refs = tuple(
                sorted(
                    part.strip()
                    for part in row["References"].split(";")
                    if part.strip()
                )
                )
                if not refs or seen.intersection(refs):
                    raise PCBParseError("BOM references are empty or duplicated")
                try:
                    quantity = int(row["Quantity"])
                    population = PCBPopulation(row["Population"].strip())
                except (TypeError, ValueError) as exc:
                    raise PCBParseError(
                        f"BOM row {row_number} has invalid quantity or population"
                    ) from exc
                value = row["Value"]
                footprint = row["Footprint"]
                manufacturer = row["Manufacturer"]
                manufacturer_part_number = row["ManufacturerPartNumber"]
                symbol = None
                description = None
                source_reference = None
            seen.update(refs)
            items.append(
                PCBBOMItem.create(
                    item_id=deterministic_id("bom", artifact.fingerprint, *refs),
                    references=refs,
                    quantity=quantity,
                    value=value,
                    footprint=footprint,
                    manufacturer=manufacturer,
                    manufacturer_part_number=manufacturer_part_number,
                    symbol=symbol,
                    description=description,
                    source_reference=source_reference,
                    population=population,
                    evidence_refs=(evidence,),
                )
            )
            if len(items) > MAX_COMPONENTS:
                raise PCBParseError("BOM component limit exceeded")
    except csv.Error as exc:
        raise PCBParseError("BOM CSV is malformed") from exc
    if not items:
        raise PCBParseError("BOM is empty")
    return PCBBOMArtifact.create(
        artifact=artifact, items=tuple(sorted(items, key=lambda item: item.item_id))
    )


def parse_kicad_xml_netlist(
    artifact: PCBArtifactReference, content: bytes
) -> PCBNetlistArtifact:
    if artifact.artifact_type is not PCBArtifactType.NETLIST:
        raise PCBUnsupportedFormat("XML netlist parser received a non-netlist artifact")
    checked = _checked_content(artifact, content)
    upper = checked.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PCBParseError("DTD and entity declarations are forbidden")
    try:
        root = ET.fromstring(checked)
    except ET.ParseError as exc:
        raise PCBParseError("netlist XML is malformed") from exc
    if root.tag != "export":
        raise PCBUnsupportedFormat("netlist XML root must be export")
    evidence = artifact_evidence(artifact)
    component_data: dict[str, tuple[str, str]] = {}
    for element in root.findall("./components/comp"):
        reference = (element.get("ref") or "").strip()
        value = (element.findtext("value") or "").strip()
        footprint = (element.findtext("footprint") or "").strip()
        if not reference or not value or not footprint or reference in component_data:
            raise PCBParseError("netlist component identity is missing or duplicated")
        component_data[reference] = (value, footprint)
    if not component_data or len(component_data) > MAX_COMPONENTS:
        raise PCBParseError("netlist component count is invalid")

    nets: list[PCBNet] = []
    connections: list[PCBConnection] = []
    pins: dict[str, set[str]] = {reference: set() for reference in component_data}
    seen_net_ids: set[str] = set()
    seen_nodes: set[tuple[str, str]] = set()
    for net_element in root.findall("./nets/net"):
        code = (net_element.get("code") or "").strip()
        name = (net_element.get("name") or "").strip()
        net_id = name
        if not code or not name or net_id in seen_net_ids:
            raise PCBParseError("netlist net identity is missing or duplicated")
        seen_net_ids.add(net_id)
        net_type = classify_net(name)
        nets.append(
            PCBNet.create(
                net_id=net_id, name=name, net_type=net_type, evidence_refs=(evidence,)
            )
        )
        for node in net_element.findall("node"):
            reference = (node.get("ref") or "").strip()
            pin = (node.get("pin") or "").strip()
            identity = (reference, pin)
            if reference not in component_data or not pin or identity in seen_nodes:
                raise PCBParseError(
                    "netlist node is missing, duplicated, or references an unknown component"
                )
            seen_nodes.add(identity)
            pins[reference].add(pin)
            connections.append(
                PCBConnection.create(
                    component_reference=reference,
                    pin_number=pin,
                    net_id=net_id,
                    connection_kind="NETLIST_NODE",
                    artifact_fingerprints=(artifact.fingerprint,),
                    evidence_refs=(evidence,),
                )
            )
            if len(connections) > MAX_CONNECTIONS:
                raise PCBParseError("netlist connection limit exceeded")
    if not nets or len(nets) > MAX_NETS:
        raise PCBParseError("netlist net count is invalid")
    components = tuple(
        PCBComponent.create(
            reference=reference,
            value=value,
            footprint=footprint,
            population=PCBPopulation.POPULATED,
            pins=tuple(
                PCBPin.create(
                    component_reference=reference,
                    pin_number=pin,
                    evidence_refs=(evidence,),
                )
                for pin in sorted(pins[reference])
            ),
            evidence_refs=(evidence,),
        )
        for reference, (value, footprint) in sorted(component_data.items())
    )
    return PCBNetlistArtifact.create(
        artifact=artifact,
        components=components,
        nets=tuple(sorted(nets, key=lambda item: item.net_id)),
        connections=tuple(
            sorted(
                connections,
                key=lambda item: (
                    item.component_reference,
                    item.pin_number,
                    item.net_id,
                ),
            )
        ),
    )


SExpr = str | list["SExpr"]


def _tokenize_sexpr(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == ";":
            end = text.find("\n", index)
            index = len(text) if end < 0 else end + 1
            continue
        if char in "()":
            tokens.append(char)
            index += 1
        elif char == '"':
            index += 1
            value: list[str] = []
            while index < len(text) and text[index] != '"':
                if text[index] == "\\":
                    index += 1
                    if index >= len(text):
                        raise PCBParseError("unterminated schematic escape")
                value.append(text[index])
                index += 1
            if index >= len(text):
                raise PCBParseError("unterminated schematic string")
            tokens.append("".join(value))
            index += 1
        else:
            end = index
            while end < len(text) and not text[end].isspace() and text[end] not in "()":
                end += 1
            tokens.append(text[index:end])
            index = end
        if len(tokens) > MAX_TOKENS:
            raise PCBParseError("schematic token limit exceeded")
    return tokens


def _parse_tokens(tokens: list[str]) -> list[SExpr]:
    stack: list[list[SExpr]] = []
    roots: list[SExpr] = []
    for token in tokens:
        if token == "(":
            if len(stack) >= MAX_DEPTH:
                raise PCBParseError("schematic nesting limit exceeded")
            stack.append([])
        elif token == ")":
            if not stack:
                raise PCBParseError("unexpected schematic closing parenthesis")
            completed = stack.pop()
            (stack[-1] if stack else roots).append(completed)
        else:
            (stack[-1] if stack else roots).append(token)
    if stack:
        raise PCBParseError("unterminated schematic expression")
    return roots


def _walk(expr: SExpr) -> Iterable[list[SExpr]]:
    if isinstance(expr, list):
        yield expr
        for item in expr:
            yield from _walk(item)


def _child_value(expr: list[SExpr], head: str) -> str | None:
    for item in expr[1:]:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and item[0] == head
            and isinstance(item[1], str)
        ):
            return item[1]
    return None


def _property(expr: list[SExpr], name: str) -> str | None:
    for item in expr[1:]:
        if (
            isinstance(item, list)
            and len(item) >= 3
            and item[0] == "property"
            and item[1] == name
            and isinstance(item[2], str)
        ):
            return item[2]
    return None


def _children(expr: list[SExpr], head: str) -> list[list[SExpr]]:
    return [
        item
        for item in expr[1:]
        if isinstance(item, list) and item and item[0] == head
    ]


def _number_value(expr: list[SExpr], index: int) -> float | None:
    if len(expr) <= index or not isinstance(expr[index], str):
        return None
    try:
        return float(expr[index])
    except ValueError:
        return None


def _at(expr: list[SExpr]) -> tuple[float, float, float] | None:
    value = next(iter(_children(expr, "at")), None)
    if value is None:
        return None
    x = _number_value(value, 1)
    y = _number_value(value, 2)
    angle = _number_value(value, 3) or 0.0
    if x is None or y is None:
        return None
    return x, y, angle


def _xy(expr: list[SExpr]) -> tuple[float, float] | None:
    x = _number_value(expr, 1)
    y = _number_value(expr, 2)
    return (x, y) if x is not None and y is not None else None


def _placed_symbol_pin_definitions(
    library_symbol: list[SExpr],
) -> tuple[tuple[str, str | None, str | None, float, float, float], ...]:
    """Return library pin endpoint definitions without treating lib symbols as instances."""

    pins: list[tuple[str, str | None, str | None, float, float, float]] = []
    for expr in _walk(library_symbol):
        if not expr or expr[0] != "pin":
            continue
        position = _at(expr)
        number_expr = next(iter(_children(expr, "number")), None)
        name_expr = next(iter(_children(expr, "name")), None)
        if position is None or number_expr is None or name_expr is None:
            continue
        number = number_expr[1] if len(number_expr) > 1 else None
        name = name_expr[1] if len(name_expr) > 1 else None
        electrical = expr[1] if len(expr) > 1 and isinstance(expr[1], str) else None
        if not isinstance(number, str) or not number.strip():
            continue
        pins.append((number, name if isinstance(name, str) else None, electrical, *position))
    return tuple(sorted(set(pins), key=lambda item: item[0]))


def _transform_pin(
    local_x: float,
    local_y: float,
    local_angle: float,
    origin_x: float,
    origin_y: float,
    rotation: float,
    mirror: str | None,
) -> tuple[float, float]:
    if mirror == "x":
        local_x = -local_x
    elif mirror == "y":
        local_y = -local_y
    radians = math.radians(rotation)
    x = local_x * math.cos(radians) - local_y * math.sin(radians)
    y = local_x * math.sin(radians) + local_y * math.cos(radians)
    # KiCad's schematic coordinates grow downwards, while symbol-library
    # pin coordinates use the conventional upward-positive Y axis.
    return origin_x + x, origin_y - y


def _point_key(point: tuple[float, float]) -> tuple[int, int]:
    return round(point[0] * 1_000_000), round(point[1] * 1_000_000)


def _point_on_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return abs(px - sx) <= 1e-6 and abs(py - sy) <= 1e-6
    cross = abs((px - sx) * dy - (py - sy) * dx)
    if cross > 1e-6:
        return False
    dot = (px - sx) * dx + (py - sy) * dy
    return -1e-6 <= dot <= length_squared + 1e-6


def _schematic_connections(
    root: list[SExpr],
    placed_pins: tuple[tuple[str, str, str, tuple[float, float]], ...],
    semantic_anchors: tuple[tuple[str, tuple[float, float]], ...],
    evidence: PCBEvidenceReference,
    artifact: PCBArtifactReference,
) -> tuple[PCBConnection, ...]:
    wires: list[tuple[tuple[float, float], tuple[float, float]]] = []
    points: set[tuple[int, int]] = set()
    semantic_points: set[tuple[int, int]] = set()
    labels: list[tuple[str, tuple[float, float]]] = []
    for expr in _children(root, "wire"):
        pts = next(iter(_children(expr, "pts")), None)
        if pts is None:
            continue
        xy = [item for item in _children(pts, "xy") if _xy(item) is not None]
        if len(xy) != 2:
            continue
        first = _xy(xy[0])
        second = _xy(xy[1])
        assert first is not None and second is not None
        start = first
        end = second
        wires.append((start, end))
        points.add(_point_key(start))
        points.add(_point_key(end))
    for expr in _children(root, "label"):
        name = expr[1] if len(expr) > 1 and isinstance(expr[1], str) else None
        position = _at(expr)
        if not name or position is None:
            continue
        point = (position[0], position[1])
        labels.append((name, point))
        points.add(_point_key(point))
        semantic_points.add(_point_key(point))
    for head in ("global_label",):
        for expr in _children(root, head):
            name = expr[1] if len(expr) > 1 and isinstance(expr[1], str) else None
            position = _at(expr)
            if not name or position is None:
                continue
            point = (position[0], position[1])
            labels.append((name, point))
            points.add(_point_key(point))
            semantic_points.add(_point_key(point))
    for name, point in semantic_anchors:
        labels.append((name, point))
        points.add(_point_key(point))
        semantic_points.add(_point_key(point))
    for expr in _children(root, "junction"):
        position = _at(expr)
        if position is None:
            continue
        point = (position[0], position[1])
        points.add(_point_key(point))
        semantic_points.add(_point_key(point))
    for _, _, _, point in placed_pins:
        points.add(_point_key(point))
        semantic_points.add(_point_key(point))

    parent = {point: point for point in points}

    def find(point: tuple[int, int]) -> tuple[int, int]:
        while parent[point] != point:
            parent[point] = parent[parent[point]]
            point = parent[point]
        return point

    def union(left: tuple[int, int], right: tuple[int, int]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    point_values = {
        key: (key[0] / 1_000_000, key[1] / 1_000_000) for key in points
    }
    semantic_owner: dict[tuple[int, int], int | None] = {}
    for key in semantic_points:
        owners = [
            index
            for index, (start, end) in enumerate(wires)
            if _point_on_segment(point_values[key], start, end)
        ]
        semantic_owner[key] = owners[0] if len(owners) == 1 else None
    for wire_index, (start, end) in enumerate(wires):
        endpoint_keys = {_point_key(start), _point_key(end)}
        on_wire = [
            key
            for key, point in point_values.items()
            if key in endpoint_keys
            or (
                key in semantic_points
                and semantic_owner.get(key) == wire_index
                and _point_on_segment(point, start, end)
            )
        ]
        on_wire.sort(
            key=lambda key: (point_values[key][0] - start[0]) ** 2
            + (point_values[key][1] - start[1]) ** 2
        )
        for left, right in pairwise(on_wire):
            union(left, right)

    names_by_root: dict[tuple[int, int], set[str]] = {}
    for name, point in labels:
        key = _point_key(point)
        names_by_root.setdefault(find(key), set()).add(name)
    connections: list[PCBConnection] = []
    for reference, pin_number, _, point in placed_pins:
        root_key = find(_point_key(point))
        names = sorted(names_by_root.get(root_key, set()))
        if not names:
            continue
        connections.append(
            PCBConnection.create(
                component_reference=reference,
                pin_number=pin_number,
                net_id="|".join(names),
                connection_kind="SCHEMATIC_LABEL",
                artifact_fingerprints=(artifact.fingerprint,),
                evidence_refs=(evidence,),
            )
        )
    return tuple(
        sorted(
            connections,
            key=lambda item: (item.component_reference, item.pin_number, item.net_id),
        )
    )


def parse_kicad_schematic(
    artifact: PCBArtifactReference, content: bytes
) -> PCBSchematicArtifact:
    if artifact.artifact_type is not PCBArtifactType.SCHEMATIC:
        raise PCBUnsupportedFormat("schematic parser received a non-schematic artifact")
    expressions = _parse_tokens(_tokenize_sexpr(_decode_utf8(artifact, content)))
    if (
        len(expressions) != 1
        or not isinstance(expressions[0], list)
        or not expressions[0]
        or expressions[0][0] != "kicad_sch"
    ):
        raise PCBUnsupportedFormat("schematic root must be kicad_sch")
    root = expressions[0]
    if _child_value(root, "version") not in SUPPORTED_SCHEMATIC_VERSIONS:
        raise PCBUnsupportedFormat("schematic version is not supported")
    unsupported = {"sheet", "bus", "bus_entry", "hierarchical_label"}
    if any(expr and expr[0] in unsupported for expr in _walk(root)):
        raise PCBUnsupportedFormat(
            "schematic hierarchy or bus connectivity is not supported"
        )
    evidence = artifact_evidence(artifact)
    library_symbols: dict[str, list[SExpr]] = {}
    library_block = next(iter(_children(root, "lib_symbols")), None)
    if library_block is not None:
        for library_symbol in _children(library_block, "symbol"):
            if len(library_symbol) > 1 and isinstance(library_symbol[1], str):
                library_symbols[library_symbol[1]] = library_symbol
    components: list[PCBComponent] = []
    placed_pins: list[tuple[str, str, str, tuple[float, float]]] = []
    semantic_anchors: list[tuple[str, tuple[float, float]]] = []
    seen: set[str] = set()
    for expr in _walk(root):
        if not expr or expr[0] != "symbol":
            continue
        # Library definitions also use the `symbol` head.  Only a symbol with
        # a direct lib_id is a placed instance in the schematic body.
        lib_id = _child_value(expr, "lib_id")
        uuid = _child_value(expr, "uuid")
        legacy_instance = lib_id is None and uuid is not None and not _children(expr, "symbol")
        if lib_id is None and not legacy_instance:
            continue
        reference = _property(expr, "Reference")
        if reference is None:
            continue
        value = _property(expr, "Value")
        # KiCad power symbols are placed symbols, but they are net-name
        # anchors rather than BOM components.  Their references (for example
        # #PWR0101) are intentionally not component identities.
        if lib_id is not None and lib_id.startswith("power:"):
            placement = _at(expr)
            library_symbol = library_symbols.get(lib_id)
            if placement is not None and library_symbol is not None and value:
                mirror_expr = next(iter(_children(expr, "mirror")), None)
                mirror = (
                    mirror_expr[1]
                    if mirror_expr is not None
                    and len(mirror_expr) > 1
                    and isinstance(mirror_expr[1], str)
                    else None
                )
                pin_definitions = _placed_symbol_pin_definitions(library_symbol)
                for _, _, _, x, y, pin_angle in pin_definitions:
                    semantic_anchors.append(
                        (
                            value,
                            _transform_pin(
                                x,
                                y,
                                pin_angle,
                                placement[0],
                                placement[1],
                                placement[2],
                                mirror,
                            ),
                        )
                    )
            continue
        footprint_value = _property(expr, "Footprint")
        footprint = footprint_value.strip() or None if footprint_value is not None else None
        uuid = _child_value(expr, "uuid")
        placement = _at(expr)
        if not value or not uuid or reference in seen or (not legacy_instance and placement is None):
            raise PCBParseError("schematic symbol identity is missing or duplicated")
        seen.add(reference)
        library_symbol = library_symbols.get(lib_id) if lib_id is not None else None
        if not legacy_instance and library_symbol is None:
            raise PCBParseError("schematic symbol library binding is missing")
        mirror_expr = next(iter(_children(expr, "mirror")), None)
        mirror = (
            mirror_expr[1]
            if mirror_expr is not None
            and len(mirror_expr) > 1
            and isinstance(mirror_expr[1], str)
            else None
        )
        pins: list[PCBPin] = []
        pin_definitions = (
            _placed_symbol_pin_definitions(library_symbol)
            if library_symbol is not None and placement is not None
            else ()
        )
        for number, name, electrical, x, y, pin_angle in pin_definitions:
            point = _transform_pin(
                x,
                y,
                pin_angle,
                placement[0],
                placement[1],
                placement[2],
                mirror,
            )
            # KiCad library symbols may legally omit a display name while
            # retaining a stable pin number. Keep the parser boundary typed
            # by projecting that case to a deterministic observation label;
            # connectivity continues to use the original number/name pair.
            pins.append(
                PCBPin.create(
                    component_reference=reference,
                    pin_number=number,
                    pin_name=name or f"PIN_{number}",
                    electrical_type=electrical,
                    evidence_refs=(evidence,),
                )
            )
            placed_pins.append((reference, number, name or "", point))
        components.append(
            PCBComponent.create(
                reference=reference,
                value=value,
                footprint=footprint,
                population=PCBPopulation.POPULATED,
                pins=tuple(pins),
                evidence_refs=(evidence,),
            )
        )
        if len(components) > MAX_COMPONENTS:
            raise PCBParseError("schematic component limit exceeded")
    if not components:
        raise PCBParseError("schematic contains no placed components")
    connections = _schematic_connections(
        root, tuple(placed_pins), tuple(semantic_anchors), evidence, artifact
    )
    return PCBSchematicArtifact.create(
        artifact=artifact,
        components=tuple(sorted(components, key=lambda item: item.reference)),
        connections=connections,
    )


def classify_net(name: str) -> PCBNetType:
    normalized = name.upper()
    if normalized in {"GND", "DGND", "AGND", "PGND", "VSS"} or normalized.endswith(
        "_GND"
    ):
        return PCBNetType.GROUND
    if normalized.startswith(("VCC", "VDD", "+", "VBAT", "VIN", "VOUT")):
        return PCBNetType.POWER
    if "CLK" in normalized or "CLOCK" in normalized:
        return PCBNetType.CLOCK
    return PCBNetType.SIGNAL


__all__ = (
    "BOM_HEADER",
    "CANONICAL_BOM_HEADER",
    "artifact_evidence",
    "classify_net",
    "parse_csv_bom",
    "parse_kicad_schematic",
    "parse_kicad_xml_netlist",
)
