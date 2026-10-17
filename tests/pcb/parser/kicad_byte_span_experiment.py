"""C2-P test-only byte-span experiment, not a product adapter or native writer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from embedded_copilot.pcb.parser.kicad import DEFAULT_MAX_PCB_SIZE_BYTES
from embedded_copilot.pcb.parser.models import PCBParserLimits

_NUMBER = re.compile(rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_WHITESPACE = b" \t\r\n"
_ESCAPES = b'\\"nrt'
_VERSION = b"20240108"


class SpanExperimentRejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SpanEdit:
    start: int
    end: int
    before: bytes
    after: bytes


@dataclass(frozen=True, slots=True)
class PatchResult:
    patched: bytes
    edits: tuple[SpanEdit, SpanEdit]


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Node:
    parts: tuple[_Token | _Node, ...]
    start: int
    end: int


def _tokens(payload: bytes) -> list[_Token]:
    limits = PCBParserLimits()
    if not payload or len(payload) > DEFAULT_MAX_PCB_SIZE_BYTES:
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX") from error
    if b"\x00" in payload:
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")

    result: list[_Token] = []
    i = 0
    while i < len(payload):
        character = payload[i]
        if character in _WHITESPACE:
            i += 1
            continue
        start = i
        if character == 40:  # (
            result.append(_Token("open", start, start + 1))
            i += 1
        elif character == 41:  # )
            result.append(_Token("close", start, start + 1))
            i += 1
        elif character == 34:  # quoted string
            i += 1
            while i < len(payload):
                current = payload[i]
                if current == 34:
                    i += 1
                    result.append(_Token("string", start, i))
                    break
                if current == 92:
                    i += 1
                    if i >= len(payload) or payload[i] not in _ESCAPES:
                        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
                elif current < 32 and current not in (9, 10, 13):
                    raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
                i += 1
            else:
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
        else:
            while i < len(payload):
                current = payload[i]
                if current in _WHITESPACE or current in (40, 41):
                    break
                if current < 33 or current > 126 or current in (34, 35, 59, 92):
                    raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
                i += 1
            if i == start:
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
            result.append(_Token("atom", start, i))
        if len(result) > limits.max_tokens:
            raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    return result


def _parse(payload: bytes) -> _Node:
    stack: list[tuple[int, list[_Token | _Node]]] = []
    roots: list[_Node] = []
    for token in _tokens(payload):
        if token.kind == "open":
            if len(stack) >= PCBParserLimits().max_depth:
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
            stack.append((token.start, []))
        elif token.kind == "close":
            if not stack:
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
            start, parts = stack.pop()
            if not parts or not isinstance(parts[0], _Token) or parts[0].kind != "atom":
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
            node = _Node(tuple(parts), start, token.end)
            if stack:
                stack[-1][1].append(node)
            else:
                roots.append(node)
        else:
            if not stack:
                raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
            stack[-1][1].append(token)
    if stack or len(roots) != 1:
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    return roots[0]


def _head(payload: bytes, node: _Node) -> bytes:
    token = node.parts[0]
    assert isinstance(token, _Token)
    return payload[token.start : token.end]


def _children(payload: bytes, node: _Node, name: bytes) -> list[_Node]:
    return [
        part
        for part in node.parts[1:]
        if isinstance(part, _Node) and _head(payload, part) == name
    ]


def _ascii_value(payload: bytes, token: _Token) -> bytes:
    data = payload[token.start : token.end]
    if token.kind == "string":
        data = data[1:-1]
        if b"\\" in data:
            raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    elif token.kind != "atom":
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    if not data.isascii():
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    return data


def _direct_value(payload: bytes, node: _Node, name: bytes) -> bytes | None:
    if (
        _head(payload, node) != name
        or len(node.parts) != 2
        or not isinstance(node.parts[1], _Token)
    ):
        return None
    return _ascii_value(payload, node.parts[1])


def _target_uuid(value: str) -> bytes:
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise SpanExperimentRejected("INVALID_TARGET_UUID") from error
    if value != canonical:
        raise SpanExperimentRejected("INVALID_TARGET_UUID")
    return value.encode("ascii")


def _coordinate(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise SpanExperimentRejected("INVALID_COORDINATE")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise SpanExperimentRejected("INVALID_COORDINATE") from error
    if not number.is_finite() or abs(number) > 10_000:
        raise SpanExperimentRejected("INVALID_COORDINATE")
    return number


def _position_atom(payload: bytes, token: _Token) -> Decimal:
    raw = payload[token.start : token.end]
    if token.kind != "atom" or _NUMBER.fullmatch(raw) is None:
        raise SpanExperimentRejected("INVALID_COORDINATE")
    return _coordinate(Decimal(raw.decode("ascii")))


def patch_footprint_xy(
    payload: bytes,
    native_uuid: str,
    *,
    expected_old: tuple[object, object],
    new: tuple[object, object],
) -> PatchResult:
    """Return an in-memory two-token patch for a locked KiCad PCB syntax subset."""
    if not isinstance(payload, bytes) or len(expected_old) != 2 or len(new) != 2:
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    target = _target_uuid(native_uuid)
    old_values = tuple(_coordinate(value) for value in expected_old)
    new_values = tuple(_coordinate(value) for value in new)
    root = _parse(payload)
    if _head(payload, root) != b"kicad_pcb":
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    versions = _children(payload, root, b"version")
    if (
        len(versions) != 1
        or _direct_value(payload, versions[0], b"version") != _VERSION
    ):
        raise SpanExperimentRejected("UNSUPPORTED_FORMAT_VERSION")

    occurrences = 0
    todo = [root]
    while todo:
        node = todo.pop()
        if _direct_value(payload, node, b"uuid") == target:
            occurrences += 1
        todo.extend(part for part in node.parts if isinstance(part, _Node))
    if occurrences == 0:
        raise SpanExperimentRejected("TARGET_NOT_FOUND")
    if occurrences > 1:
        raise SpanExperimentRejected("DUPLICATE_TARGET")

    matches = [
        footprint
        for footprint in _children(payload, root, b"footprint")
        if any(
            _direct_value(payload, item, b"uuid") == target
            for item in _children(payload, footprint, b"uuid")
        )
    ]
    if len(matches) != 1:
        raise SpanExperimentRejected("TARGET_HIERARCHY_INVALID")
    footprint = matches[0]
    at_nodes = _children(payload, footprint, b"at")
    if len(at_nodes) != 1:
        raise SpanExperimentRejected("AT_CARDINALITY")
    at = at_nodes[0]
    if len(at.parts) not in (3, 4) or not all(
        isinstance(item, _Token) for item in at.parts[1:]
    ):
        raise SpanExperimentRejected("UNSUPPORTED_SYNTAX")
    coordinate_tokens = (at.parts[1], at.parts[2])
    assert all(isinstance(item, _Token) for item in coordinate_tokens)
    actual = tuple(_position_atom(payload, item) for item in coordinate_tokens)
    if actual != old_values:
        raise SpanExperimentRejected("OLD_COORDINATE_MISMATCH")
    if len(at.parts) == 4:
        _position_atom(payload, at.parts[3])

    edits = tuple(
        SpanEdit(
            token.start,
            token.end,
            payload[token.start : token.end],
            format(value, "f").encode("ascii"),
        )
        for token, value in zip(coordinate_tokens, new_values)
    )
    first, second = edits
    patched = (
        payload[: first.start]
        + first.after
        + payload[first.end : second.start]
        + second.after
        + payload[second.end :]
    )
    return PatchResult(patched, (first, second))
