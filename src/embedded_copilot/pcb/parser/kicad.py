from __future__ import annotations

import math
import os
import re
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.exceptions import PCBParseError
from embedded_copilot.pcb.models import (
    PCBComponent,
    PCBLayer,
    PCBNet,
    PCBNetNode,
    PCBNetType,
    PCBPin,
    PCBPosition,
    PCBTrack,
    PCBVia,
    PCBZone,
    UnifiedPCBModel,
)
from embedded_copilot.pcb.parser.base import PCBSourceResolver
from embedded_copilot.pcb.parser.models import PCBParserLimits, SExpression

DEFAULT_MAX_PCB_SIZE_BYTES = 25 * 1024 * 1024
_POWER_NET = re.compile(
    r"^(?:\+?\d+(?:V\d+)?|VCC|VDD|VBAT|VBUS|VIN|VOUT|PWR)(?:[_-].*)?$",
    re.IGNORECASE,
)
_GROUND_NET = re.compile(r"^(?:GND|AGND|DGND|PGND|VSS)(?:[_-].*)?$", re.IGNORECASE)
_CLOCK_NET = re.compile(r"(?:CLK|CLOCK|XTAL|OSC)", re.IGNORECASE)


class KiCadPCBParser:
    """Parse a bounded KiCad board subset into one immutable exchange model."""

    def __init__(
        self,
        resolver: PCBSourceResolver,
        *,
        max_size_bytes: int = DEFAULT_MAX_PCB_SIZE_BYTES,
    ) -> None:
        if not isinstance(resolver, PCBSourceResolver):
            raise PCBParseError("PCB source resolver is invalid")
        if (
            isinstance(max_size_bytes, bool)
            or not isinstance(max_size_bytes, int)
            or max_size_bytes <= 0
        ):
            raise PCBParseError("PCB parser size limit is invalid")
        self._resolver = resolver
        self._max_size_bytes = max_size_bytes
        self._limits = PCBParserLimits()

    def parse(self, attachment: UserAttachment) -> UnifiedPCBModel:
        try:
            raw = self._read_source(attachment)
            text = raw.decode("utf-8", errors="strict")
            if "\x00" in text:
                raise ValueError("NUL is forbidden")
            root = _parse_s_expression(text, self._limits)
            return _extract_board(root, attachment.filename)
        except PCBParseError:
            raise
        except (UnicodeError, ValueError, TypeError, ValidationError, OSError):
            raise PCBParseError("KiCad PCB parsing failed") from None

    def _read_source(self, attachment: UserAttachment) -> bytes:
        if (
            not isinstance(attachment, UserAttachment)
            or attachment.media_type is not AttachmentType.EDA
            or Path(attachment.filename).suffix.casefold() != ".kicad_pcb"
            or attachment.content_type != "application/x-kicad-pcb"
            or attachment.metadata.get("format") != "kicad_pcb"
        ):
            raise PCBParseError("PCB attachment is unsupported")
        try:
            root = Path(self._resolver.root)
            if root.is_symlink() or not root.is_dir():
                raise ValueError("invalid root")
            trusted_root = root.resolve(strict=True)
            candidate = Path(self._resolver.resolve(attachment))
            if not candidate.is_absolute() or candidate.name != attachment.filename:
                raise ValueError("invalid target")
            if not candidate.is_relative_to(trusted_root):
                raise ValueError("target escaped root")

            cursor = trusted_root
            relative = candidate.relative_to(trusted_root)
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("symlink is forbidden")
                cursor.lstat()

            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(trusted_root):
                raise ValueError("target escaped root")
            file_stat = resolved.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size != attachment.size_bytes
                or file_stat.st_size <= 0
                or file_stat.st_size > self._max_size_bytes
            ):
                raise ValueError("invalid source size")

            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(resolved, flags)
            try:
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or opened_stat.st_size != file_stat.st_size
                    or opened_stat.st_dev != file_stat.st_dev
                    or opened_stat.st_ino != file_stat.st_ino
                    or opened_stat.st_mtime_ns != file_stat.st_mtime_ns
                    or opened_stat.st_ctime_ns != file_stat.st_ctime_ns
                ):
                    raise ValueError("source changed")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(64 * 1024, self._max_size_bytes + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_size_bytes:
                        raise ValueError("source is too large")
                    chunks.append(chunk)
                finished_stat = os.fstat(descriptor)
                if (
                    total != file_stat.st_size
                    or finished_stat.st_size != opened_stat.st_size
                    or finished_stat.st_dev != opened_stat.st_dev
                    or finished_stat.st_ino != opened_stat.st_ino
                    or finished_stat.st_mtime_ns != opened_stat.st_mtime_ns
                    or finished_stat.st_ctime_ns != opened_stat.st_ctime_ns
                ):
                    raise ValueError("source changed")
                return b"".join(chunks)
            finally:
                os.close(descriptor)
        except PCBParseError:
            raise
        except Exception:
            raise PCBParseError("PCB source validation failed") from None


@dataclass(frozen=True, slots=True)
class KiCadPCBObservationIssue:
    kind: str
    code: str
    locator: str
    blocks_mapping: bool


@dataclass(frozen=True, slots=True)
class KiCadPCBFootprintObservation:
    native_id: str
    locator: str
    library_id: str
    reference: str | None
    position_x_mm: float
    position_y_mm: float
    rotation_degrees: float


@dataclass(frozen=True, slots=True)
class KiCadPCBSourceObservation:
    entry_name: str
    source_sha256: str
    native_format_version: str
    footprints: tuple[KiCadPCBFootprintObservation, ...]
    issues: tuple[KiCadPCBObservationIssue, ...]


_OBSERVED_PCB_VERSION = "20240108"


def observe_kicad_pcb_bytes(
    entry_name: str, payload: bytes
) -> KiCadPCBSourceObservation:
    """Observe a bounded KiCad board footprint subset from already trusted bytes.

    Native UUID, location, library link and placement are retained. Pads, nets,
    electrical relationships and every other file version are outside this
    subset. This pure function does not read or modify the source workspace.
    """
    if not isinstance(entry_name, str) or not entry_name:
        raise ValueError("entry name is invalid")
    if not isinstance(payload, bytes):
        raise TypeError("entry payload must be bytes")

    digest = sha256(payload).hexdigest()

    def result(
        version: str, footprints: tuple[KiCadPCBFootprintObservation, ...],
        issues: tuple[KiCadPCBObservationIssue, ...],
    ) -> KiCadPCBSourceObservation:
        return KiCadPCBSourceObservation(entry_name, digest, version, footprints, issues)

    def issue(kind: str, code: str, locator: str = "/kicad_pcb") -> KiCadPCBObservationIssue:
        return KiCadPCBObservationIssue(kind, code, locator, True)

    if not entry_name.casefold().endswith(".kicad_pcb"):
        return result("UNKNOWN", (), (issue("UNKNOWN", "UNSUPPORTED_ENTRY_EXTENSION"),))
    if len(payload) > DEFAULT_MAX_PCB_SIZE_BYTES:
        return result("UNKNOWN", (), (issue("LOSS", "SOURCE_SIZE_LIMIT"),))
    try:
        text = payload.decode("utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError("NUL")
        root = _parse_s_expression(text, PCBParserLimits())
    except (UnicodeError, ValueError, TypeError):
        return result("UNKNOWN", (), (issue("LOSS", "INVALID_BOARD_SYNTAX"),))
    if _head(root) != "kicad_pcb":
        return result("UNKNOWN", (), (issue("UNKNOWN", "UNSUPPORTED_BOARD_ROOT"),))
    version_nodes = _children(root, "version")
    if len(version_nodes) != 1 or _atom(version_nodes[0], 1) != _OBSERVED_PCB_VERSION:
        return result("UNKNOWN", (), (issue("UNKNOWN", "UNSUPPORTED_FORMAT_VERSION"),))

    footprints: list[KiCadPCBFootprintObservation] = []
    issues: list[KiCadPCBObservationIssue] = []
    seen_ids: set[str] = set()
    for index, footprint in enumerate(_children(root, "footprint")):
        locator = f"/kicad_pcb/footprint[{index}]"
        uuid_nodes = _children(footprint, "uuid")
        native_id = _atom(uuid_nodes[0], 1) if len(uuid_nodes) == 1 else None
        if native_id is None:
            issues.append(issue("LOSS", "FOOTPRINT_UUID_MISSING", locator))
            continue
        try:
            normalized_id = str(UUID(native_id))
            if normalized_id != native_id.lower():
                raise ValueError("non-canonical uuid")
        except ValueError:
            issues.append(issue("LOSS", "FOOTPRINT_UUID_INVALID", locator))
            continue
        if normalized_id in seen_ids:
            issues.append(issue("CONFLICT", "DUPLICATE_FOOTPRINT_UUID", locator))
            continue
        seen_ids.add(normalized_id)
        placement_nodes = _children(footprint, "at")
        reference_nodes = [
            item
            for kind in ("property", "fp_text")
            for item in _children(footprint, kind)
            if (_atom(item, 1) or "").casefold() == "reference"
        ]
        if len(placement_nodes) > 1 or len(reference_nodes) > 1:
            issues.append(issue("CONFLICT", "AMBIGUOUS_FOOTPRINT_FIELDS", locator))
            continue
        at = placement_nodes[0] if placement_nodes else None
        try:
            library_id = _required_atom(footprint, 1)
            x = _number(at, 1)
            y = _number(at, 2)
            rotation = float(_atom(at, 3) or 0)
            if not all(math.isfinite(value) for value in (x, y, rotation)):
                raise ValueError("non-finite placement")
        except (ValueError, TypeError):
            issues.append(issue("LOSS", "INVALID_FOOTPRINT_PLACEMENT", locator))
            continue
        footprints.append(
            KiCadPCBFootprintObservation(
                native_id=native_id,
                locator=locator,
                library_id=library_id,
                reference=_property(footprint, "Reference"),
                position_x_mm=x,
                position_y_mm=y,
                rotation_degrees=rotation,
            )
        )
    # A duplicate native identity makes the projected object set ambiguous.
    if any(item.code == "DUPLICATE_FOOTPRINT_UUID" for item in issues):
        footprints.clear()
    return result(_OBSERVED_PCB_VERSION, tuple(footprints), tuple(issues))


def _tokenize(text: str, limits: PCBParserLimits) -> list[str]:
    tokens: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character in "()":
            tokens.append(character)
            index += 1
        elif character == '"':
            index += 1
            value: list[str] = []
            while index < length:
                character = text[index]
                if character == '"':
                    index += 1
                    break
                if character == "\\":
                    index += 1
                    if index >= length:
                        raise ValueError("unterminated escape")
                    escaped = text[index]
                    value.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
                    index += 1
                    continue
                value.append(character)
                index += 1
            else:
                raise ValueError("unterminated string")
            tokens.append("".join(value))
        else:
            start = index
            while index < length and not text[index].isspace() and text[index] not in "()":
                index += 1
            tokens.append(text[start:index])
        if len(tokens) > limits.max_tokens:
            raise ValueError("token limit exceeded")
    return tokens


def _parse_s_expression(text: str, limits: PCBParserLimits) -> SExpression:
    stack: list[list[object]] = []
    roots: list[object] = []
    for token in _tokenize(text, limits):
        if token == "(":
            if len(stack) >= limits.max_depth:
                raise ValueError("nesting limit exceeded")
            stack.append([])
        elif token == ")":
            if not stack:
                raise ValueError("unexpected close")
            expression = tuple(stack.pop())
            if stack:
                stack[-1].append(expression)
            else:
                roots.append(expression)
        else:
            if not stack:
                raise ValueError("atom outside expression")
            stack[-1].append(token)
    if stack or len(roots) != 1 or not isinstance(roots[0], tuple):
        raise ValueError("invalid root expression")
    return roots[0]


def _head(expression: SExpression) -> str | None:
    return expression[0] if expression and isinstance(expression[0], str) else None


def _children(expression: SExpression, name: str) -> list[SExpression]:
    return [
        item
        for item in expression[1:]
        if isinstance(item, tuple) and _head(item) == name
    ]


def _child(expression: SExpression, name: str) -> SExpression | None:
    values = _children(expression, name)
    return values[0] if values else None


def _atom(expression: SExpression | None, index: int) -> str | None:
    if expression is None or len(expression) <= index:
        return None
    value = expression[index]
    return value if isinstance(value, str) else None


def _required_atom(expression: SExpression | None, index: int) -> str:
    value = _atom(expression, index)
    if value is None or not value.strip():
        raise ValueError("required atom is missing")
    return value.strip()


def _number(expression: SExpression | None, index: int) -> float:
    return float(_required_atom(expression, index))


def _integer(expression: SExpression | None, index: int) -> int:
    return int(_required_atom(expression, index))


def _property(expression: SExpression, name: str) -> str | None:
    for item in _children(expression, "property"):
        if (_atom(item, 1) or "").casefold() == name.casefold():
            return _atom(item, 2)
    legacy_name = name.casefold()
    for item in _children(expression, "fp_text"):
        if (_atom(item, 1) or "").casefold() == legacy_name:
            return _atom(item, 2)
    return None


def _net_name(expression: SExpression | None, nets: dict[int, str]) -> str | None:
    if expression is None:
        return None
    net_id = _integer(expression, 1)
    explicit = _atom(expression, 2)
    if net_id == 0:
        if explicit is not None and explicit.strip():
            raise ValueError("unconnected net has a name")
        return None
    declared = nets.get(net_id)
    if declared is None or not declared.strip():
        raise ValueError("net reference is missing")
    if explicit is not None and explicit.strip() != declared.strip():
        raise ValueError("net reference does not match")
    return declared.strip()


def _net_type(name: str) -> PCBNetType:
    if _GROUND_NET.search(name):
        return PCBNetType.GROUND
    if _POWER_NET.search(name):
        return PCBNetType.POWER
    if _CLOCK_NET.search(name):
        return PCBNetType.CLOCK
    return PCBNetType.SIGNAL if name.strip() else PCBNetType.UNKNOWN


def _extract_layers(root: SExpression) -> tuple[PCBLayer, ...]:
    container = _child(root, "layers")
    if container is None:
        raise ValueError("layers are missing")
    layers: list[PCBLayer] = []
    for item in container[1:]:
        if not isinstance(item, tuple):
            raise ValueError("layer is malformed")
        layers.append(
            PCBLayer(
                index=_integer(item, 0),
                name=_required_atom(item, 1),
                type=_required_atom(item, 2),
            )
        )
    if not layers:
        raise ValueError("layers are empty")
    return tuple(layers)


def _extract_net_map(root: SExpression) -> tuple[dict[int, str], list[int]]:
    result: dict[int, str] = {}
    order: list[int] = []
    for item in _children(root, "net"):
        net_id = _integer(item, 1)
        name = _atom(item, 2) or ""
        if net_id in result:
            raise ValueError("duplicate net id")
        result[net_id] = name
        if name.strip():
            order.append(net_id)
    return result, order


def _extract_components(
    root: SExpression,
    nets: dict[int, str],
) -> tuple[PCBComponent, ...]:
    components: list[PCBComponent] = []
    expressions = [*_children(root, "footprint"), *_children(root, "module")]
    for expression in expressions:
        identifier = _required_atom(expression, 1)
        library, separator, footprint = identifier.partition(":")
        if not separator:
            footprint = library
            library_value: str | None = None
        else:
            library_value = library
        at = _child(expression, "at")
        component_x = _number(at, 1)
        component_y = _number(at, 2)
        component_rotation = float(_atom(at, 3) or 0)
        pins: list[PCBPin] = []
        seen_pins: dict[str, PCBPin] = {}
        for pad in _children(expression, "pad"):
            pad_at = _child(pad, "at")
            pad_x = _number(pad_at, 1) if pad_at is not None else None
            pad_y = _number(pad_at, 2) if pad_at is not None else None
            if pad_x is None or pad_y is None:
                pad_position = None
            else:
                angle = math.radians(component_rotation)
                pad_position = PCBPosition(
                    x_mm=component_x + pad_x * math.cos(angle) - pad_y * math.sin(angle),
                    y_mm=component_y + pad_x * math.sin(angle) + pad_y * math.cos(angle),
                )
            pin = PCBPin(
                number=_required_atom(pad, 1),
                pad_type=_required_atom(pad, 2),
                net_name=_net_name(_child(pad, "net"), nets),
                position=pad_position,
            )
            key = pin.number.casefold()
            previous = seen_pins.get(key)
            if previous is not None:
                # Stacked/thermal pads may share one electrical pin number
                # while having different physical centers.  The exchange
                # model is pin-oriented, so retain the first deterministic
                # observation when electrical identity agrees; reject only
                # an actual electrical conflict.
                if previous.pad_type != pin.pad_type or previous.net_name != pin.net_name:
                    raise ValueError("ambiguous duplicate PCB component pin")
                continue
            seen_pins[key] = pin
            pins.append(pin)
        components.append(
            PCBComponent(
                reference=_property(expression, "Reference") or "",
                value=_property(expression, "Value") or None,
                footprint=footprint,
                library=library_value,
                position=PCBPosition(x_mm=component_x, y_mm=component_y),
                rotation=component_rotation,
                layer=_required_atom(_child(expression, "layer"), 1),
                pins=tuple(pins),
            )
        )
    return tuple(components)


def _extract_tracks(root: SExpression, nets: dict[int, str]) -> tuple[PCBTrack, ...]:
    tracks: list[PCBTrack] = []
    for item in _children(root, "segment"):
        start = _child(item, "start")
        end = _child(item, "end")
        tracks.append(
            PCBTrack(
                start=PCBPosition(x_mm=_number(start, 1), y_mm=_number(start, 2)),
                end=PCBPosition(x_mm=_number(end, 1), y_mm=_number(end, 2)),
                width_mm=_number(_child(item, "width"), 1),
                layer=_required_atom(_child(item, "layer"), 1),
                net_name=_net_name(_child(item, "net"), nets),
            )
        )
    return tuple(tracks)


def _extract_vias(root: SExpression, nets: dict[int, str]) -> tuple[PCBVia, ...]:
    vias: list[PCBVia] = []
    for item in _children(root, "via"):
        at = _child(item, "at")
        layers = _child(item, "layers")
        if layers is None:
            raise ValueError("via layers are missing")
        vias.append(
            PCBVia(
                position=PCBPosition(x_mm=_number(at, 1), y_mm=_number(at, 2)),
                diameter_mm=_number(_child(item, "size"), 1),
                drill_mm=_number(_child(item, "drill"), 1),
                layers=tuple(
                    value for value in layers[1:] if isinstance(value, str) and value
                ),
                net_name=_net_name(_child(item, "net"), nets),
            )
        )
    return tuple(vias)


def _extract_zones(root: SExpression, nets: dict[int, str]) -> tuple[PCBZone, ...]:
    def polygon_points(item: SExpression) -> tuple[tuple[float, float], ...]:
        polygon = _child(item, "polygon")
        points = None if polygon is None else _child(polygon, "pts")
        if points is None:
            return ()
        result = []
        for point in _children(points, "xy"):
            result.append((_number(point, 1), _number(point, 2)))
        return tuple(result)

    zones: list[PCBZone] = []
    for item in _children(root, "zone"):
        layer = _child(item, "layer")
        layers = _child(item, "layers")
        if layer is not None:
            layer_names: Iterable[str] = (_required_atom(layer, 1),)
        elif layers is not None:
            layer_names = (
                value for value in layers[1:] if isinstance(value, str) and value
            )
        else:
            raise ValueError("zone layers are missing")
        zones.append(
            PCBZone(
                name=_atom(_child(item, "name"), 1),
                net_name=(
                    _atom(_child(item, "net_name"), 1)
                    or _net_name(_child(item, "net"), nets)
                ),
                layers=tuple(layer_names),
                polygon_points=polygon_points(item),
            )
        )
    return tuple(zones)


def _metadata(root: SExpression) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    version = _atom(_child(root, "version"), 1)
    generator = _atom(_child(root, "generator"), 1)
    if version is not None:
        result["format_version"] = int(version)
    if generator is not None and generator.strip():
        result["generator"] = generator.strip()
    return result


def _extract_board(root: SExpression, filename: str) -> UnifiedPCBModel:
    if _head(root) != "kicad_pcb":
        raise ValueError("unsupported root")
    nets, net_order = _extract_net_map(root)
    components = _extract_components(root, nets)
    layers = _extract_layers(root)
    tracks = _extract_tracks(root, nets)
    vias = _extract_vias(root, nets)
    zones = _extract_zones(root, nets)
    _validate_layer_references(components, layers, tracks, vias, zones)
    nodes_by_net: dict[str, list[PCBNetNode]] = {}
    for component in components:
        for pin in component.pins:
            if pin.net_name is not None:
                nodes_by_net.setdefault(pin.net_name, []).append(
                    PCBNetNode(reference=component.reference, pin=pin.number)
                )
    net_models = tuple(
        PCBNet(
            name=nets[net_id],
            net_type=_net_type(nets[net_id]),
            nodes=tuple(nodes_by_net.get(nets[net_id], [])),
        )
        for net_id in net_order
    )
    if not any((components, net_models, tracks, vias, zones)):
        raise ValueError("board is empty")
    return UnifiedPCBModel(
        board_name=Path(filename).stem,
        source_format="kicad_pcb",
        components=components,
        nets=net_models,
        layers=layers,
        tracks=tracks,
        vias=vias,
        zones=zones,
        metadata=_metadata(root),
    )


def _validate_layer_references(
    components: tuple[PCBComponent, ...],
    layers: tuple[PCBLayer, ...],
    tracks: tuple[PCBTrack, ...],
    vias: tuple[PCBVia, ...],
    zones: tuple[PCBZone, ...],
) -> None:
    declared = {layer.name for layer in layers}
    referenced = [
        *(component.layer for component in components),
        *(track.layer for track in tracks),
        *(layer for via in vias for layer in via.layers),
        *(layer for zone in zones for layer in zone.layers),
    ]
    if any(layer not in declared for layer in referenced):
        raise ValueError("PCB layer reference is missing")


# C2-I only: a byte-preserving, in-memory position stage for the observed
# 20240108 subset. This intentionally does not use the semantic S-expression
# parser above: that parser discards the original byte offsets.
_STAGE_NUMBER = re.compile(
    rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)
_STAGE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STAGE_WHITESPACE = b" \t\r\n"
_STAGE_ESCAPES = b'\\"nrt'


class KiCadByteSpanRejected(ValueError):
    """Fixed, path-free reason for a rejected bounded in-memory stage."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class KiCadByteSpanEdit:
    start: int
    end: int
    before: bytes
    after: bytes


@dataclass(frozen=True, slots=True)
class KiCadByteSpanStage:
    staged_bytes: bytes
    edits: tuple[KiCadByteSpanEdit, KiCadByteSpanEdit]


@dataclass(frozen=True, slots=True)
class _StageToken:
    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _StageNode:
    parts: tuple[_StageToken | _StageNode, ...]


def _stage_tokens(payload: bytes) -> list[_StageToken]:
    limits = PCBParserLimits()
    if not payload or len(payload) > DEFAULT_MAX_PCB_SIZE_BYTES or b"\x00" in payload:
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX") from error

    tokens: list[_StageToken] = []
    index = 0
    while index < len(payload):
        current = payload[index]
        if current in _STAGE_WHITESPACE:
            index += 1
            continue
        start = index
        if current == 40:  # (
            tokens.append(_StageToken("open", start, start + 1))
            index += 1
        elif current == 41:  # )
            tokens.append(_StageToken("close", start, start + 1))
            index += 1
        elif current == 34:  # quoted string
            index += 1
            while index < len(payload):
                current = payload[index]
                if current == 34:
                    index += 1
                    tokens.append(_StageToken("string", start, index))
                    break
                if current == 92:
                    index += 1
                    if index >= len(payload) or payload[index] not in _STAGE_ESCAPES:
                        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
                elif current < 32 and current not in (9, 10, 13):
                    raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
                index += 1
            else:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
        else:
            while index < len(payload):
                current = payload[index]
                if current in _STAGE_WHITESPACE or current in (40, 41):
                    break
                if current < 33 or current > 126 or current in (34, 35, 59, 92):
                    raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
                index += 1
            if index == start:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            tokens.append(_StageToken("atom", start, index))
        if len(tokens) > limits.max_tokens:
            raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    return tokens


def _stage_parse(payload: bytes) -> _StageNode:
    stack: list[list[_StageToken | _StageNode]] = []
    roots: list[_StageNode] = []
    for token in _stage_tokens(payload):
        if token.kind == "open":
            if len(stack) >= PCBParserLimits().max_depth:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            stack.append([])
        elif token.kind == "close":
            if not stack:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            parts = stack.pop()
            if (
                not parts
                or not isinstance(parts[0], _StageToken)
                or parts[0].kind != "atom"
            ):
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            node = _StageNode(tuple(parts))
            if stack:
                stack[-1].append(node)
            else:
                roots.append(node)
        else:
            if not stack:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            stack[-1].append(token)
    if stack or len(roots) != 1:
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    return roots[0]


def _stage_head(payload: bytes, node: _StageNode) -> bytes:
    first = node.parts[0]
    assert isinstance(first, _StageToken)
    return payload[first.start : first.end]


def _stage_children(payload: bytes, node: _StageNode, name: bytes) -> list[_StageNode]:
    return [
        part
        for part in node.parts[1:]
        if isinstance(part, _StageNode) and _stage_head(payload, part) == name
    ]


def _stage_direct_value(payload: bytes, node: _StageNode, name: bytes) -> bytes | None:
    if _stage_head(payload, node) != name or len(node.parts) != 2:
        return None
    token = node.parts[1]
    if not isinstance(token, _StageToken):
        return None
    raw = payload[token.start : token.end]
    if token.kind == "string":
        raw = raw[1:-1]
        if b"\\" in raw:
            raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    elif token.kind != "atom":
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    if not raw.isascii():
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    return raw


def _stage_coordinate(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise KiCadByteSpanRejected("INVALID_COORDINATE")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError) as error:
        raise KiCadByteSpanRejected("INVALID_COORDINATE") from error
    if (
        not number.is_finite()
        or abs(number) > 10_000
        or len(number.as_tuple().digits) > 64
        or number.as_tuple().exponent < -64
    ):
        raise KiCadByteSpanRejected("INVALID_COORDINATE")
    return number


def _stage_position_atom(payload: bytes, token: _StageToken) -> Decimal:
    raw = payload[token.start : token.end]
    if token.kind != "atom" or len(raw) > 64 or _STAGE_NUMBER.fullmatch(raw) is None:
        raise KiCadByteSpanRejected("INVALID_COORDINATE")
    return _stage_coordinate(Decimal(raw.decode("ascii")))


def stage_kicad_footprint_xy_bytes(
    payload: bytes,
    native_uuid: str,
    *,
    expected_old: tuple[object, object],
    new: tuple[object, object],
    expected_source_sha256: str,
) -> KiCadByteSpanStage:
    """Stage only a unique footprint's top-level x/y bytes; never read or write files."""
    if (
        not isinstance(payload, bytes)
        or not isinstance(expected_source_sha256, str)
        or _STAGE_SHA256.fullmatch(expected_source_sha256) is None
        or not isinstance(expected_old, tuple)
        or len(expected_old) != 2
        or not isinstance(new, tuple)
        or len(new) != 2
    ):
        raise KiCadByteSpanRejected("INVALID_STAGE_INPUT")
    if sha256(payload).hexdigest() != expected_source_sha256:
        raise KiCadByteSpanRejected("SOURCE_CHANGED")
    try:
        canonical_uuid = str(UUID(native_uuid))
    except (TypeError, ValueError, AttributeError) as error:
        raise KiCadByteSpanRejected("INVALID_TARGET_UUID") from error
    if native_uuid != canonical_uuid:
        raise KiCadByteSpanRejected("INVALID_TARGET_UUID")
    old_values = tuple(_stage_coordinate(value) for value in expected_old)
    new_values = tuple(_stage_coordinate(value) for value in new)

    root = _stage_parse(payload)
    if _stage_head(payload, root) != b"kicad_pcb":
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    versions = _stage_children(payload, root, b"version")
    if (
        len(versions) != 1
        or _stage_direct_value(payload, versions[0], b"version") != b"20240108"
    ):
        raise KiCadByteSpanRejected("UNSUPPORTED_FORMAT_VERSION")

    target = canonical_uuid.encode("ascii")
    occurrences = 0
    pending = [root]
    while pending:
        node = pending.pop()
        if _stage_head(payload, node) == b"uuid":
            candidate = _stage_direct_value(payload, node, b"uuid")
            if candidate is None:
                raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
            if candidate == target:
                occurrences += 1
        pending.extend(part for part in node.parts if isinstance(part, _StageNode))
    if occurrences == 0:
        raise KiCadByteSpanRejected("TARGET_NOT_FOUND")
    if occurrences > 1:
        raise KiCadByteSpanRejected("DUPLICATE_TARGET")

    matches = [
        footprint
        for footprint in _stage_children(payload, root, b"footprint")
        if any(
            _stage_direct_value(payload, uuid_node, b"uuid") == target
            for uuid_node in _stage_children(payload, footprint, b"uuid")
        )
    ]
    if len(matches) != 1:
        raise KiCadByteSpanRejected("TARGET_HIERARCHY_INVALID")
    at_nodes = _stage_children(payload, matches[0], b"at")
    if len(at_nodes) != 1:
        raise KiCadByteSpanRejected("AT_CARDINALITY")
    at_parts = at_nodes[0].parts
    if len(at_parts) not in (3, 4) or not all(
        isinstance(part, _StageToken) for part in at_parts[1:]
    ):
        raise KiCadByteSpanRejected("UNSUPPORTED_SYNTAX")
    x_token, y_token = at_parts[1:3]
    assert isinstance(x_token, _StageToken) and isinstance(y_token, _StageToken)
    if (
        _stage_position_atom(payload, x_token),
        _stage_position_atom(payload, y_token),
    ) != old_values:
        raise KiCadByteSpanRejected("OLD_COORDINATE_MISMATCH")
    if len(at_parts) == 4:
        rotation = at_parts[3]
        assert isinstance(rotation, _StageToken)
        _stage_position_atom(payload, rotation)

    edits: list[KiCadByteSpanEdit] = []
    for token, value in zip((x_token, y_token), new_values):
        replacement = format(value, "f").encode("ascii")
        if len(replacement) > 64:
            raise KiCadByteSpanRejected("INVALID_COORDINATE")
        edits.append(
            KiCadByteSpanEdit(
                token.start, token.end, payload[token.start : token.end], replacement
            )
        )
    first, second = edits
    staged = (
        payload[: first.start]
        + first.after
        + payload[first.end : second.start]
        + second.after
        + payload[second.end :]
    )
    return KiCadByteSpanStage(staged, (first, second))
