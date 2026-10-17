"""Finite, read-only project mappings for reviewed SHTC3 private originals."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import fitz

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.desktop_copilot_chat.contracts import identifier
from embedded_copilot.engineering_copilot.schematic_evidence import ParameterLocator
from embedded_copilot.engineering_copilot.schematic_library_observation import (
    _footprint_pads,
    _symbol_pins,
)
from embedded_copilot.engineering_copilot.schematic_parameter_readers import (
    _DIGEST,
    _pairs,
    _reparse,
    _safe_read,
    _sha,
)
from embedded_copilot.engineering_copilot.schematic_parameter_readers import (
    _mapping as _parameter_mapping,
)
from embedded_copilot.engineering_copilot.schematic_parameter_readers import (
    _original as _parameter_original,
)

_LIBRARY_MAP = ".embrix/library-documents.json"
_PDF_URI = (
    "https://sensirion.com/en/media/documents/643F9C8E/63A5A436/Datasheet_SHTC3.pdf"
)
_PDF_REVISION = "Version 4 - December 2022"
_PDF_SHA = "sha256:dbe979c975fe520663606b5c278d2b9130f4e402e6d9d943ca06c4e4f38e63d9"
_SYMBOL_ID = "SHTC3_EP:SHTC3_EP"
_FOOTPRINT_ID = "SHTC3_EP:Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm"
_ENTRY_KEYS = frozenset(
    {
        "kind",
        "library_id",
        "uri",
        "relative_path",
        "content_sha256",
        "source_record_id",
        "source_record_fingerprint",
    }
)
_KINDS = frozenset({"KICAD_SYMBOL_LIBRARY", "KICAD_FOOTPRINT"})


@dataclass(frozen=True)
class ManufacturerTerminal:
    number: str
    name: str
    locator: ParameterLocator


@dataclass(frozen=True)
class ManufacturerExposedPad:
    name: str
    internally_connected_to: str
    may_connect_to_pcb_ground: bool
    locator: ParameterLocator


@dataclass(frozen=True)
class Shtc3LibraryOriginals:
    symbol_bytes: bytes
    footprint_bytes: bytes
    symbol_sha256: str
    footprint_sha256: str
    pdf_bytes: bytes
    pdf_sha256: str
    manufacturer_terminals: tuple[ManufacturerTerminal, ...]
    manufacturer_ep: ManufacturerExposedPad
    library_symbol_pins: tuple[tuple[str, str], ...]
    library_pad_ids: tuple[str, ...]
    non_electrical_paste_pad_count: int


@dataclass(frozen=True)
class _LibraryEntry:
    kind: str
    library_id: str
    uri: str
    relative_path: str
    content_sha256: str
    source_record_id: str
    source_record_fingerprint: str


def _scope_identifier(value: object, field: str) -> str:
    try:
        checked = identifier(value, field=field)
    except (TypeError, ValueError):
        raise ValueError("MAPPING_INVALID") from None
    if checked != value:
        raise ValueError("MAPPING_INVALID")
    return checked


def _library_mapping(
    root: Path, tenant_id: str, project_id: str
) -> tuple[bytes, tuple[_LibraryEntry, ...]]:
    _scope_identifier(tenant_id, "tenant_id")
    _scope_identifier(project_id, "project_id")
    try:
        raw = _safe_read(root, _LIBRARY_MAP, 128 * 1024)
        data = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError):
        raise ValueError("MAPPING_INVALID") from None
    if (
        type(data) is not dict
        or set(data) != {"schema_version", "tenant_id", "project_id", "libraries"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or type(data["libraries"]) is not list
        or len(data["libraries"]) > 64
    ):
        raise ValueError("MAPPING_INVALID")
    if (
        _scope_identifier(data["tenant_id"], "tenant_id") != tenant_id
        or _scope_identifier(data["project_id"], "project_id") != project_id
    ):
        raise ValueError("MAPPING_INVALID")
    entries: list[_LibraryEntry] = []
    identities: set[tuple[str, str, str, str, str]] = set()
    for item in data["libraries"]:
        if (
            type(item) is not dict
            or set(item) != _ENTRY_KEYS
            or type(item["kind"]) is not str
            or item["kind"] not in _KINDS
            or any(
                type(item[key]) is not str or not item[key] or len(item[key]) > 1024
                for key in _ENTRY_KEYS - {"kind"}
            )
            or not _DIGEST.fullmatch(item["content_sha256"])
            or not _DIGEST.fullmatch(item["source_record_fingerprint"])
        ):
            raise ValueError("MAPPING_INVALID")
        identity = (
            item["kind"],
            item["library_id"],
            item["uri"],
            item["source_record_id"],
            item["source_record_fingerprint"],
        )
        if identity in identities:
            raise ValueError("MAPPING_AMBIGUOUS")
        identities.add(identity)
        entries.append(_LibraryEntry(**item))
    return raw, tuple(entries)


def _pdf_terminals(
    content: bytes,
) -> tuple[tuple[ManufacturerTerminal, ...], ManufacturerExposedPad]:
    if len(content) != 743217 or _sha(content) != _PDF_SHA:
        raise ValueError("UNSUPPORTED_DOCUMENT")
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.needs_pass or len(document) != 14:
                raise ValueError("UNSUPPORTED_DOCUMENT")
            first = document[0].get_text("text")
            blocks = document[5].get_text("blocks")
    except (RuntimeError, TypeError, OSError, ValueError):
        raise ValueError("UNSUPPORTED_DOCUMENT") from None
    sections = [
        block[4]
        for block in blocks
        if re.search(r"(?m)^4\s*\nInterface Specifications", block[4])
    ]
    table = [
        block[4]
        for block in blocks
        if "Pin Name" in block[4] and "Comments" in block[4]
    ]
    rows = [block[4] for block in blocks if re.search(r"(?m)^1\s*\nVDD\b", block[4])]
    captions = [
        block[4] for block in blocks if "Table 7 SHTC3 pin assignment" in block[4]
    ]
    center = [
        re.sub(r"\s+", " ", block[4]).strip()
        for block in blocks
        if "Electrically, the center pad" in block[4]
    ]
    if (
        "Datasheet SHTC3" not in first
        or "Version 4" not in first
        or len(sections) != 1
        or len(table) != 1
        or len(rows) != 1
        or len(captions) != 1
        or len(center) != 1
        or "The center pad is internally connected to VSS."
        not in re.sub(r"\s+", " ", captions[0])
        or "Electrically, the center pad is internally connected to GND and may be connected to the GND net on the PCB additionally."
        not in center[0]
    ):
        raise ValueError("LOCATOR_UNRESOLVED")
    # The four manufacturer numbers and names are parsed from the table block,
    # independently of the five logical pins in the private KiCad symbol.
    parsed = re.findall(r"(?m)^([1-4])\s*\n(VDD|SCL|SDA|VSS)\b", rows[0])
    if parsed != [("1", "VDD"), ("2", "SCL"), ("3", "SDA"), ("4", "VSS")] or re.search(
        r"(?m)^5\s*\n", rows[0]
    ):
        raise ValueError("LOCATOR_UNRESOLVED")
    terminals = tuple(
        ManufacturerTerminal(
            number, name, ParameterLocator(page=6, table="Table 7", row=number)
        )
        for number, name in parsed
    )
    ep = ManufacturerExposedPad(
        "EP",
        "VSS/GND",
        True,
        ParameterLocator(
            section="4 Interface Specifications",
            clause="CENTER_PAD_INTERNAL_VSS_AND_OPTIONAL_PCB_GND",
        ),
    )
    return terminals, ep


def read_shtc3_library_originals(
    *,
    root: Path,
    tenant_id: str,
    project_id: str,
    source: ComponentSourceRecord,
    symbol_library_id: str,
    symbol_uri: str,
    symbol_sha256: str,
    footprint_library_id: str,
    footprint_uri: str,
    footprint_sha256: str,
) -> Shtc3LibraryOriginals:
    """Read one fixed private profile from the caller's trusted project root."""
    source = ComponentSourceRecord.model_validate(source.model_dump(mode="python"))
    root = Path(root)
    if not root.is_absolute() or _reparse(root) or not root.is_dir():
        raise ValueError("PROJECT_UNAVAILABLE")
    root = root.resolve(strict=True)
    if (
        (source.manufacturer, source.mpn, source.package)
        != ("Sensirion", "SHTC3", "DFN-4 2x2 mm")
        or symbol_library_id != _SYMBOL_ID
        or footprint_library_id != _FOOTPRINT_ID
        or type(symbol_uri) is not str
        or not symbol_uri
        or type(footprint_uri) is not str
        or not footprint_uri
        or symbol_uri == footprint_uri
        or type(symbol_sha256) is not str
        or not _DIGEST.fullmatch(symbol_sha256)
        or type(footprint_sha256) is not str
        or not _DIGEST.fullmatch(footprint_sha256)
    ):
        raise ValueError("LIBRARY_IDENTITY_MISMATCH")
    map_before, entries = _library_mapping(root, tenant_id, project_id)
    selected = {}
    for kind, library_id, uri, digest in (
        ("KICAD_SYMBOL_LIBRARY", symbol_library_id, symbol_uri, symbol_sha256),
        ("KICAD_FOOTPRINT", footprint_library_id, footprint_uri, footprint_sha256),
    ):
        same_source = [
            entry
            for entry in entries
            if entry.kind == kind
            and (entry.source_record_id, entry.source_record_fingerprint)
            == (source.source_id, source.fingerprint)
        ]
        if len(same_source) != 1:
            raise ValueError("LIBRARY_UNAVAILABLE")
        entry = same_source[0]
        if (entry.library_id, entry.uri, entry.content_sha256) != (
            library_id,
            uri,
            digest,
        ):
            raise ValueError("LIBRARY_IDENTITY_MISMATCH")
        selected[kind] = entry
    symbol_entry = selected["KICAD_SYMBOL_LIBRARY"]
    footprint_entry = selected["KICAD_FOOTPRINT"]
    if symbol_entry.relative_path == footprint_entry.relative_path:
        raise ValueError("MAPPING_AMBIGUOUS")
    symbol = _safe_read(root, symbol_entry.relative_path, 2 * 1024 * 1024)
    footprint = _safe_read(root, footprint_entry.relative_path, 2 * 1024 * 1024)
    if _sha(symbol) != symbol_sha256 or _sha(footprint) != footprint_sha256:
        raise ValueError("CONTENT_MISMATCH")
    pins = _symbol_pins(symbol, footprint_library_id)
    pads, paste_count = _footprint_pads(footprint)
    pdf_map_before, pdf_entries = _parameter_mapping(root, tenant_id, project_id)
    pdf_matches = [
        entry
        for entry in pdf_entries
        if entry.kind == "PDF"
        and (
            entry.source_record_id,
            entry.source_record_fingerprint,
            entry.manufacturer,
            entry.mpn,
            entry.package,
        )
        == (source.source_id, source.fingerprint, "Sensirion", "SHTC3", "DFN-4 2x2 mm")
    ]
    if len(pdf_matches) != 1:
        raise ValueError("DOCUMENT_UNAVAILABLE")
    pdf_entry = pdf_matches[0]
    if (
        pdf_entry.document_uri,
        pdf_entry.document_revision,
        pdf_entry.content_sha256,
    ) != (_PDF_URI, _PDF_REVISION, _PDF_SHA):
        raise ValueError("UNSUPPORTED_DOCUMENT")
    pdf = _parameter_original(root, pdf_entry)
    terminals, ep = _pdf_terminals(pdf)
    if (
        _library_mapping(root, tenant_id, project_id)[0] != map_before
        or _safe_read(root, symbol_entry.relative_path, 2 * 1024 * 1024) != symbol
        or _safe_read(root, footprint_entry.relative_path, 2 * 1024 * 1024) != footprint
        or _parameter_mapping(root, tenant_id, project_id)[0] != pdf_map_before
        or _parameter_original(root, pdf_entry) != pdf
    ):
        raise ValueError("DOCUMENT_CHANGED")
    return Shtc3LibraryOriginals(
        symbol,
        footprint,
        _sha(symbol),
        _sha(footprint),
        pdf,
        _sha(pdf),
        terminals,
        ep,
        pins,
        pads,
        paste_count,
    )
