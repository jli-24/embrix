"""Bounded, read-only reducer for the documented EasyEDA Pro V3 log envelope.

This is a bounded record snapshot and internal schematic observation, not a
project import, public IR, or native validation result.
The legacy envelope remains synthetic-only. Ordering follows the correction in
easyeda/easyeda-format-skill commit 9e42727cc4cf13a8e442140e8e7794ae94d5f41b:
document/object ID, then larger ticket and lexicographically larger client.
This does not qualify that envelope as the current native document dialect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

MAX_BYTES = 8 * 1024 * 1024
MAX_LINES = 20_000
MAX_LINE_BYTES = 64 * 1024
MAX_DEPTH = 32


class JLCEDAV3FormatError(ValueError):
    """The input is malformed, ambiguous, or outside this reader's limits."""


@dataclass(frozen=True, slots=True)
class JLCEDAV3Record:
    record_type: str
    native_id: str | None
    ticket: int
    client: str
    payload_json: str
    deleted: bool
    source_line: int


@dataclass(frozen=True, slots=True)
class JLCEDAV3Document:
    doc_type: str
    uuid: str
    deleted: bool
    records: tuple[JLCEDAV3Record, ...]
    current_records: tuple[JLCEDAV3Record, ...]


@dataclass(frozen=True, slots=True)
class JLCEDAV3Snapshot:
    source_sha256: str
    documents: tuple[JLCEDAV3Document, ...]
    unsupported_types: tuple[str, ...]
    unsupported_document_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JLCEDAV3AttributeObservation:
    native_id: str
    parent_id: str
    key: str
    value: str
    source_line: int


@dataclass(frozen=True, slots=True)
class JLCEDAV3ComponentObservation:
    document_uuid: str
    native_id: str
    source_line: int
    part_id: str
    position_x: int | float
    position_y: int | float
    rotation: int | float
    is_mirror: bool
    coordinate_unit: str
    attributes: tuple[JLCEDAV3AttributeObservation, ...]


@dataclass(frozen=True, slots=True)
class JLCEDAV3ObservationIssue:
    kind: str
    code: str
    document_uuid: str
    native_id: str | None
    source_line: int | None
    blocks_mapping: bool


@dataclass(frozen=True, slots=True)
class JLCEDAV3SchematicObservation:
    source_sha256: str
    components: tuple[JLCEDAV3ComponentObservation, ...]
    issues: tuple[JLCEDAV3ObservationIssue, ...]


@dataclass(frozen=True, slots=True)
class JLCEDAEntryIssue:
    kind: str
    code: str
    blocks_mapping: bool


@dataclass(frozen=True, slots=True)
class JLCEDAEntryObservation:
    entry_name: str
    source_sha256: str
    native_format_version: str
    components: tuple[JLCEDAV3ComponentObservation, ...]
    issues: tuple[JLCEDAEntryIssue, ...]


def observe_jlceda_entry(entry_name: str, payload: bytes) -> JLCEDAEntryObservation:
    """Inventory an entry and diagnose bounded .eprj3 index JSON syntax only.

    The legacy V3 log reader below remains available for internal synthetic
    investigation. Its permissive envelope match is not evidence that an
    arbitrary .eprj3 has the same native version or update semantics.
    """
    if not isinstance(entry_name, str) or not entry_name:
        raise ValueError("entry name is invalid")
    if not isinstance(payload, bytes):
        raise TypeError("entry payload must be bytes")
    is_index = entry_name.casefold().endswith(".eprj3")
    code = "NATIVE_FORMAT_UNCONFIRMED" if is_index else "UNSUPPORTED_ENTRY_EXTENSION"
    issues = [JLCEDAEntryIssue(kind="UNKNOWN", code=code, blocks_mapping=True)]
    if is_index:
        syntax_code = _index_syntax_diagnostic(payload)
        if syntax_code is not None:
            issues.append(
                JLCEDAEntryIssue(kind="UNKNOWN", code=syntax_code, blocks_mapping=True)
            )
    return JLCEDAEntryObservation(
        entry_name=entry_name,
        source_sha256=sha256(payload).hexdigest(),
        native_format_version="UNKNOWN",
        components=(),
        issues=tuple(issues),
    )


def _index_syntax_diagnostic(payload: bytes) -> str | None:
    if len(payload) > MAX_BYTES:
        return "INDEX_SIZE_LIMIT_EXCEEDED"
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return "INDEX_ENCODING_UNCONFIRMED"
    if not text.strip():
        return "INDEX_EMPTY"
    try:
        _check_nesting(text, MAX_DEPTH)
    except JLCEDAV3FormatError as exc:
        if str(exc) == "JSON nesting limit exceeded":
            return "INDEX_DEPTH_LIMIT_EXCEEDED"
        return "INDEX_JSON_INVALID"
    try:
        _DECODER.decode(text)
    except JLCEDAV3FormatError as exc:
        if str(exc) == "duplicate JSON key":
            return "INDEX_JSON_AMBIGUOUS"
        return "INDEX_JSON_INVALID"
    except (ValueError, RecursionError):
        return "INDEX_JSON_INVALID"
    return None


_DOCUMENT_TYPES = frozenset(
    {
        "PROJECT_CONFIG",
        "BOARD",
        "SCH",
        "SCH_PAGE",
        "PCB",
        "PANEL",
        "SYMBOL",
        "FOOTPRINT",
        "DEVICE",
        "BLOB",
        "INSTANCE",
    }
)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JLCEDAV3FormatError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise JLCEDAV3FormatError(f"invalid JSON constant: {value}")


_DECODER = json.JSONDecoder(
    object_pairs_hook=_unique_pairs,
    parse_constant=_reject_constant,
)


def _check_nesting(line: str, limit: int) -> None:
    depth = 0
    quoted = False
    escaped = False
    for char in line:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            depth += 1
            if depth > limit:
                raise JLCEDAV3FormatError("JSON nesting limit exceeded")
        elif char in "}]":
            depth -= 1
            if depth < 0:
                raise JLCEDAV3FormatError("malformed JSON nesting")


def _parse_line(line: str, depth_limit: int) -> tuple[dict[str, Any], Any, str]:
    _check_nesting(line, depth_limit)
    try:
        outer, end = _DECODER.raw_decode(line)
        if line[end : end + 2] != "||":
            raise JLCEDAV3FormatError("missing V3 layer delimiter")
        inner_start = end + 2
        inner, inner_end = _DECODER.raw_decode(line, inner_start)
    except (ValueError, RecursionError) as exc:
        raise JLCEDAV3FormatError("malformed V3 JSON record") from exc
    if line[inner_end:] not in ("", "|"):
        raise JLCEDAV3FormatError("unexpected data after V3 record")
    if not isinstance(outer, dict):
        raise JLCEDAV3FormatError("outer layer must be an object")
    return outer, inner, line[inner_start:inner_end]


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise JLCEDAV3FormatError(f"invalid {field}")
    return value


def _client_key(value: Any) -> str:
    client = _nonempty_string(value, "client")
    # Keep this legacy synthetic envelope's decimal-only admission. Compare
    # raw strings lexicographically; normalization would merge distinct clients.
    if not client.isascii() or not client.isdecimal():
        raise JLCEDAV3FormatError("unsupported client identifier")
    return client


def read_jlceda_pro_v3(
    source: bytes,
    *,
    max_bytes: int = MAX_BYTES,
    max_lines: int = MAX_LINES,
    max_line_bytes: int = MAX_LINE_BYTES,
    max_depth: int = MAX_DEPTH,
) -> JLCEDAV3Snapshot:
    """Reduce V3 log bytes without I/O, semantic inference, or input mutation."""
    if not isinstance(source, bytes):
        raise TypeError("V3 source must be bytes")
    if min(max_bytes, max_lines, max_line_bytes, max_depth) < 1:
        raise ValueError("limits must be positive")
    if len(source) > max_bytes:
        raise JLCEDAV3FormatError("source size limit exceeded")
    lines = source.splitlines()
    if len(lines) > max_lines:
        raise JLCEDAV3FormatError("line count limit exceeded")
    if not lines:
        raise JLCEDAV3FormatError("empty V3 log")

    documents: dict[str, dict[str, Any]] = {}
    current_uuid: str | None = None
    current_client: str | None = None
    unsupported_types: set[str] = set()
    unsupported_document_types: set[str] = set()
    for line_no, raw_line in enumerate(lines, 1):
        if len(raw_line) > max_line_bytes:
            raise JLCEDAV3FormatError("line length limit exceeded")
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JLCEDAV3FormatError("V3 log is not UTF-8") from exc
        if not line:
            raise JLCEDAV3FormatError("empty V3 record")
        outer, inner, payload_json = _parse_line(line, max_depth)
        record_type = _nonempty_string(outer.get("type"), "record type")
        if record_type == "DOCHEAD":
            if (
                set(outer) != {"type"}
                or not isinstance(inner, dict)
                or set(inner) != {"docType", "uuid", "client"}
            ):
                raise JLCEDAV3FormatError("invalid document header")
            doc_type = _nonempty_string(inner.get("docType"), "document type")
            uuid = _nonempty_string(inner.get("uuid"), "document uuid")
            client = _nonempty_string(inner.get("client"), "client")
            _client_key(client)
            if uuid not in documents:
                documents[uuid] = {
                    "doc_type": doc_type,
                    "records": [],
                    "selected": {},
                    "ordering_records": {},
                    "id_presence": {},
                }
            elif documents[uuid]["doc_type"] != doc_type:
                raise JLCEDAV3FormatError("conflicting document type for uuid")
            if doc_type not in _DOCUMENT_TYPES:
                unsupported_document_types.add(doc_type)
            current_uuid, current_client = uuid, client
            continue

        if current_uuid is None or current_client is None:
            raise JLCEDAV3FormatError("record before document header")
        if (
            isinstance(outer.get("ticket"), bool)
            or not isinstance(outer.get("ticket"), int)
            or outer["ticket"] < 0
        ):
            raise JLCEDAV3FormatError("invalid ticket")
        native_id = outer.get("id")
        if native_id is not None:
            native_id = _nonempty_string(native_id, "native object id")
        if set(outer) != (
            {"type", "ticket", "id"} if native_id is not None else {"type", "ticket"}
        ):
            raise JLCEDAV3FormatError("invalid outer layer fields")
        if record_type == "DELETE_DOC":
            if (
                native_id is not None
                or not isinstance(inner, dict)
                or set(inner) != {"isDelete"}
                or type(inner["isDelete"]) is not bool
            ):
                raise JLCEDAV3FormatError("invalid document deletion marker")
        elif not isinstance(inner, dict) and inner != "":
            raise JLCEDAV3FormatError("invalid atomic payload")
        if record_type != "DELETE_DOC":
            unsupported_types.add(record_type)

        document = documents[current_uuid]
        id_presence = document["id_presence"]
        has_id = native_id is not None
        if record_type in id_presence and id_presence[record_type] != has_id:
            raise JLCEDAV3FormatError("conflicting object identity form")
        id_presence[record_type] = has_id
        entry = JLCEDAV3Record(
            record_type=record_type,
            native_id=native_id,
            ticket=outer["ticket"],
            client=current_client,
            payload_json=payload_json,
            deleted=inner == "" if record_type != "DELETE_DOC" else inner["isDelete"],
            source_line=line_no,
        )
        document["records"].append(entry)
        # Object identity is independent of its changing record type. Keep
        # no-ID singletons separate from ordinary IDs with the same spelling.
        key = ("singleton", record_type) if native_id is None else ("object", native_id)
        selected = document["selected"]
        previous = selected.get(key)
        ordering_key = (key, entry.ticket, entry.client)
        ordering_records = document["ordering_records"]
        ordered_previous = ordering_records.get(ordering_key)
        if ordered_previous is not None and (
            ordered_previous.record_type != entry.record_type
            or ordered_previous.payload_json != entry.payload_json
        ):
            raise JLCEDAV3FormatError("conflicting records at the same ordering key")
        ordering_records[ordering_key] = entry
        if previous is None or (
            entry.ticket > previous.ticket
            or (
                entry.ticket == previous.ticket
                and _client_key(entry.client) > _client_key(previous.client)
            )
        ):
            selected[key] = entry

    snapshots: list[JLCEDAV3Document] = []
    for uuid, document in documents.items():
        selected = document["selected"]
        deletion = selected.get(("singleton", "DELETE_DOC"))
        snapshots.append(
            JLCEDAV3Document(
                doc_type=document["doc_type"],
                uuid=uuid,
                deleted=deletion.deleted if deletion is not None else False,
                records=tuple(document["records"]),
                current_records=tuple(selected.values()),
            )
        )
    return JLCEDAV3Snapshot(
        source_sha256=sha256(source).hexdigest(),
        documents=tuple(snapshots),
        unsupported_types=tuple(sorted(unsupported_types)),
        unsupported_document_types=tuple(sorted(unsupported_document_types)),
    )


_COMPONENT_ATTR_KEYS = frozenset({"Designator", "Device", "Symbol", "Footprint"})


def _native_number(value: Any) -> bool:
    return (
        type(value) in (int, float) and value != float("inf") and value != float("-inf")
    )


def observe_jlceda_schematic_components(
    snapshot: JLCEDAV3Snapshot,
) -> JLCEDAV3SchematicObservation:
    """Observe only unambiguous SCH_PAGE components from a previously bounded log.

    Native units, libraries, pin/net connectivity, and winning-record semantics
    are intentionally unresolved. Every issue remains visible to the caller.
    """
    components: list[JLCEDAV3ComponentObservation] = []
    issues: list[JLCEDAV3ObservationIssue] = []

    for document in snapshot.documents:
        if document.doc_type != "SCH_PAGE":
            continue
        records = document.records
        clients = {record.client for record in records}
        seen_ids: set[str] = set()

        def issue(
            kind: str,
            code: str,
            record: JLCEDAV3Record | None,
            *,
            blocks: bool,
            document_uuid: str = document.uuid,
        ) -> None:
            entry = JLCEDAV3ObservationIssue(
                kind=kind,
                code=code,
                document_uuid=document_uuid,
                native_id=record.native_id if record is not None else None,
                source_line=record.source_line if record is not None else None,
                blocks_mapping=blocks,
            )
            issues.append(entry)

        if document.deleted:
            issue("CONFLICT", "DOCUMENT_DELETED", None, blocks=True)
        if len(clients) > 1:
            issue("CONFLICT", "MULTIPLE_CLIENTS", None, blocks=True)
        component_records: dict[str, JLCEDAV3Record] = {}
        attribute_records: list[JLCEDAV3Record] = []
        for record in records:
            if record.record_type == "DELETE_DOC" or record.deleted:
                issue("CONFLICT", "TOMBSTONE_PRESENT", record, blocks=True)
                continue
            if record.native_id is not None:
                if record.native_id in seen_ids:
                    issue("CONFLICT", "REPEATED_NATIVE_ID", record, blocks=True)
                seen_ids.add(record.native_id)
            if record.record_type == "COMPONENT":
                if record.native_id is None:
                    issue("LOSS", "COMPONENT_ID_MISSING", record, blocks=True)
                else:
                    component_records[record.native_id] = record
            elif record.record_type == "ATTR":
                attribute_records.append(record)
            else:
                issue("UNKNOWN", "UNSUPPORTED_RECORD_TYPE", record, blocks=False)

        attributes: dict[str, list[JLCEDAV3AttributeObservation]] = {
            native_id: [] for native_id in component_records
        }
        for record in attribute_records:
            payload = _DECODER.decode(record.payload_json)
            if not isinstance(payload, dict):
                issue("LOSS", "INVALID_ATTRIBUTE", record, blocks=True)
                continue
            parent_id, key, value = (
                payload.get(name) for name in ("parentId", "key", "value")
            )
            if not isinstance(key, str) or key not in _COMPONENT_ATTR_KEYS:
                issue("UNKNOWN", "UNMAPPED_ATTRIBUTE", record, blocks=False)
                continue
            if record.native_id is None or not isinstance(value, str) or not value:
                issue("LOSS", "INVALID_ATTRIBUTE", record, blocks=True)
            elif not isinstance(parent_id, str) or parent_id not in component_records:
                issue("CONFLICT", "ORPHAN_ATTRIBUTE", record, blocks=True)
            else:
                attributes[parent_id].append(
                    JLCEDAV3AttributeObservation(
                        native_id=record.native_id,
                        parent_id=parent_id,
                        key=key,
                        value=value,
                        source_line=record.source_line,
                    )
                )

        candidate_components: list[JLCEDAV3ComponentObservation] = []
        for native_id, record in component_records.items():
            payload = _DECODER.decode(record.payload_json)
            if not isinstance(payload, dict) or not (
                isinstance(payload.get("partId"), str)
                and _native_number(payload.get("positionX"))
                and _native_number(payload.get("positionY"))
                and _native_number(payload.get("rotation"))
                and type(payload.get("isMirror")) is bool
            ):
                issue("LOSS", "INVALID_COMPONENT", record, blocks=True)
                continue
            candidate_components.append(
                JLCEDAV3ComponentObservation(
                    document_uuid=document.uuid,
                    native_id=native_id,
                    source_line=record.source_line,
                    part_id=payload["partId"],
                    position_x=payload["positionX"],
                    position_y=payload["positionY"],
                    rotation=payload["rotation"],
                    is_mirror=payload["isMirror"],
                    coordinate_unit="UNVERIFIED_NATIVE_UNIT",
                    attributes=tuple(attributes[native_id]),
                )
            )
        if not any(
            item.document_uuid == document.uuid and item.blocks_mapping
            for item in issues
        ):
            components.extend(candidate_components)

    return JLCEDAV3SchematicObservation(
        source_sha256=snapshot.source_sha256,
        components=tuple(components),
        issues=tuple(issues),
    )
