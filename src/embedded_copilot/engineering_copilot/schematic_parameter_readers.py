"""Bounded readers for explicitly mapped project-local parameter originals.

The mapping supplies locations and identities only. Parameter facts are read from
the referenced bytes on each request and are never supplied by the mapping.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath

import fitz

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.datasheet_runtime.parser.pdf_structure import (
    PdfPageStructure,
    PdfStructure,
)
from embedded_copilot.datasheet_runtime.parser.table import detect_tables
from embedded_copilot.datasheet_runtime.security.policy import (
    MAX_EXTRACTED_CHARACTERS,
    MAX_PDF_PAGES,
    MAX_PDF_SIZE_BYTES,
)
from embedded_copilot.engineering_copilot.schematic_evidence import (
    LocalParameterSnapshotPort,
    ParameterCandidate,
    ParameterConditions,
    ParameterLocator,
)

_MAP_RELATIVE = ".embrix/parameter-documents.json"
_MAP_MAX = 128 * 1024
_JSON_MAX = 2 * 1024 * 1024
_ENTRY_KEYS = frozenset(
    {
        "kind",
        "document_uri",
        "document_revision",
        "relative_path",
        "content_sha256",
        "source_record_id",
        "source_record_fingerprint",
        "manufacturer",
        "mpn",
        "package",
    }
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


@dataclass(frozen=True)
class _Entry:
    kind: str
    document_uri: str
    document_revision: str
    relative_path: str
    content_sha256: str
    source_record_id: str
    source_record_fingerprint: str
    manufacturer: str
    mpn: str
    package: str


@dataclass(frozen=True)
class StandardDocumentFact:
    """One verified UM10204 Table 11 fact, independent of device conditions."""

    document_uri: str
    document_revision: str
    content_sha256: str
    locator: ParameterLocator
    mode: str
    frequency_max_hz: str | None = None
    rise_time_max_s: str | None = None
    cbus_max_f: str | None = None


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("MAPPING_INVALID")
        result[key] = value
    return result


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400
    )


def _safe_read(root: Path, relative: str, maximum: int) -> bytes:
    if (
        type(relative) is not str
        or not relative
        or len(relative) > 512
        or "\\" in relative
        or "\x00" in relative
        or ":" in relative
        or PurePosixPath(relative).is_absolute()
        or PureWindowsPath(relative).drive
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("MAPPING_PATH_INVALID")
    cursor = root
    for part in relative.split("/"):
        cursor = cursor / part
        try:
            if _reparse(cursor):
                raise ValueError("MAPPING_PATH_INVALID")
        except FileNotFoundError:
            raise ValueError("DOCUMENT_UNAVAILABLE") from None
    resolved = cursor.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("MAPPING_PATH_INVALID")
    before = resolved.stat()
    if before.st_size < 1 or before.st_size > maximum:
        raise ValueError("DOCUMENT_SIZE_INVALID")
    with resolved.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read(maximum + 1)
        closed = os.fstat(stream.fileno())
    after = resolved.stat()
    cursor_after = root
    for part in relative.split("/"):
        cursor_after = cursor_after / part
        if _reparse(cursor_after):
            raise ValueError("MAPPING_PATH_INVALID")
    if (
        len(content) != before.st_size
        or len(content) > maximum
        or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
        != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
        or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
        != (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size)
        or (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_size)
        != (closed.st_dev, closed.st_ino, closed.st_mtime_ns, closed.st_size)
        or not cursor_after.resolve(strict=True).is_relative_to(root)
    ):
        raise ValueError("DOCUMENT_CHANGED")
    return content


def _mapping(
    root: Path, tenant_id: str, project_id: str
) -> tuple[bytes, tuple[_Entry, ...]]:
    try:
        raw = _safe_read(root, _MAP_RELATIVE, _MAP_MAX)
        data = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, TypeError):
        raise ValueError("MAPPING_INVALID") from None
    if (
        type(data) is not dict
        or set(data) != {"schema_version", "tenant_id", "project_id", "documents"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or data["tenant_id"] != tenant_id
        or data["project_id"] != project_id
        or type(data["documents"]) is not list
        or len(data["documents"]) > 64
    ):
        raise ValueError("MAPPING_INVALID")
    entries: list[_Entry] = []
    identities: set[tuple[str, str]] = set()
    for item in data["documents"]:
        if type(item) is not dict or set(item) != _ENTRY_KEYS:
            raise ValueError("MAPPING_INVALID")
        if (
            type(item["kind"]) is not str
            or item["kind"] not in {"PDF", "COMPONENT_SOURCE_JSON"}
            or any(
                type(item[key]) is not str or not item[key] or len(item[key]) > 1024
                for key in _ENTRY_KEYS - {"kind"}
            )
            or not _DIGEST.fullmatch(item["content_sha256"])
            or not _DIGEST.fullmatch(item["source_record_fingerprint"])
        ):
            raise ValueError("MAPPING_INVALID")
        identity = (item["document_uri"], item["document_revision"])
        if identity in identities:
            raise ValueError("MAPPING_AMBIGUOUS")
        identities.add(identity)
        entries.append(_Entry(**item))
    return raw, tuple(entries)


def _original(root: Path, entry: _Entry) -> bytes:
    maximum = MAX_PDF_SIZE_BYTES if entry.kind == "PDF" else _JSON_MAX
    content = _safe_read(root, entry.relative_path, maximum)
    if _sha(content) != entry.content_sha256:
        raise ValueError("CONTENT_MISMATCH")
    return content


def _source_json(content: bytes) -> ComponentSourceRecord:
    try:
        return ComponentSourceRecord.model_validate_json(content, strict=False)
    except (ValueError, TypeError):
        raise ValueError("UNSUPPORTED_COMPONENT_SOURCE") from None


def _text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", value)
        if unicodedata.category(char) != "Cf"
    )


def _table(structure: PdfStructure, page_number: int, first_header: str):
    if page_number > structure.page_count:
        raise ValueError("LOCATOR_UNRESOLVED")
    matches = [
        table.rows
        for table in structure.pages[page_number - 1].tables
        if table.rows and _text(table.rows[0][0]).strip() == first_header
    ]
    if len(matches) != 1:
        raise ValueError("LOCATOR_UNRESOLVED")
    return matches[0]


def _single_row(
    rows: tuple[tuple[str, ...], ...], label: str, column: int = 0
) -> tuple[str, ...]:
    found = [
        row for row in rows if len(row) > column and _text(row[column]).strip() == label
    ]
    if len(found) != 1:
        raise ValueError("LOCATOR_UNRESOLVED")
    return found[0]


def _decimal(value: str) -> str:
    value = _text(value).strip()
    if not _DECIMAL.fullmatch(value):
        raise ValueError("LOCATOR_UNRESOLVED")
    return format(Decimal(value).normalize(), "f")


def _pdf_structure(
    content: bytes, target_page: int, *, crop_lower_half: bool = False
) -> PdfStructure:
    if not content or len(content) > MAX_PDF_SIZE_BYTES:
        raise ValueError("UNSUPPORTED_DOCUMENT")
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if (
                document.needs_pass
                or not 1 <= document.page_count <= MAX_PDF_PAGES
                or target_page > document.page_count
            ):
                raise ValueError("UNSUPPORTED_DOCUMENT")
            pages = []
            chars = 0
            for index, page in enumerate(document):
                page_text = page.get_text("text", sort=True)
                chars += len(page_text)
                if chars > MAX_EXTRACTED_CHARACTERS:
                    raise ValueError("UNSUPPORTED_DOCUMENT")
                tables = ()
                if index + 1 == target_page and target_page in {3, 44}:
                    if crop_lower_half:
                        # SHTC3 p3 has an unrelated plot table above Table 3; its
                        # oversized cells fail the shared full-page table limit.
                        page.set_cropbox(
                            fitz.Rect(
                                0,
                                page.rect.height / 2,
                                page.rect.width,
                                page.rect.height,
                            )
                        )
                    tables = detect_tables(page)
                pages.append(
                    PdfPageStructure(
                        page_number=index + 1, text=page_text, tables=tables
                    )
                )
            return PdfStructure(page_count=document.page_count, pages=tuple(pages))
    except (RuntimeError, ValueError, TypeError, OSError):
        raise ValueError("UNSUPPORTED_DOCUMENT") from None


def _pdf_fact(entry: _Entry, content: bytes, locator: ParameterLocator, key: str):
    if locator.section is not None or locator.clause is not None:
        raise ValueError("LOCATOR_UNRESOLVED")
    if entry.manufacturer == "Sensirion" and entry.mpn == "SHTC3":
        structure = _pdf_structure(content, 3, crop_lower_half=True)
        page = structure.pages[2] if structure.page_count >= 3 else None
        if (
            page is None
            or entry.document_revision != "Version 4 - December 2022"
            or entry.package != "DFN-4 2x2 mm"
            or "Datasheet SHTC3" not in structure.pages[0].text
            or "Small DFN package: 2 × 2 × 0.75 mm3" not in structure.pages[0].text
            or structure.page_count < 6
            or "4-pin package" not in structure.pages[5].text
            or "Table 7 SHTC3 pin assignment" not in structure.pages[5].text
            or "Version 4 – December 2022" not in page.text
            or "Table 3 Electrical specifications." not in page.text
            or "Default conditions of 25 °C and 3.3 V supply voltage apply"
            not in page.text
        ):
            raise ValueError("UNSUPPORTED_DOCUMENT")
        if (locator.page, locator.table, locator.row) != (
            3,
            "Table 3",
            "Supply voltage",
        ) or key not in {"SUPPLY_MIN", "SUPPLY_MAX"}:
            raise ValueError("LOCATOR_UNRESOLVED")
        rows = _table(structure, 3, "Parameter")
        if tuple(_text(c).strip() for c in rows[0][4:8]) != (
            "Min",
            "Typ.",
            "Max",
            "Units",
        ):
            raise ValueError("LOCATOR_UNRESOLVED")
        row = _single_row(rows, "Supply voltage")
        if (
            len(row) < 8
            or _text(row[1]).replace(" ", "").strip() != "VDD"
            or row[7].strip() != "V"
        ):
            raise ValueError("LOCATOR_UNRESOLVED")
        return (
            "MIN" if key == "SUPPLY_MIN" else "MAX",
            _decimal(row[4 if key == "SUPPLY_MIN" else 6]),
            "V",
            "UNKNOWN",
        )
    if entry.manufacturer == "NXP" and entry.mpn == "UM10204":
        structure = _pdf_structure(content, 44)
        page = structure.pages[43] if structure.page_count >= 44 else None
        # UM10204 is a bus specification; the sentinel is not a device package.
        if (
            page is None
            or entry.document_revision != "Rev. 7.0 — 1 October 2021"
            or entry.package != "I2C_BUS_SPECIFICATION"
            or "UM10204" not in page.text
            or "NXP Semiconductors" not in page.text
            or "Rev. 7.0 — 1 October 2021" not in page.text
            or "Table 11." not in page.text
            or "Standard, Fast, and Fast-mode Plus" not in page.text
        ):
            raise ValueError("UNSUPPORTED_DOCUMENT")
        labels = {
            "BUS_FREQUENCY_MAX": ("SCL clock frequency", "kHz", Decimal(1000)),
            "RISE_TIME_MAX": (
                "rise time of both SDA and SCL signals",
                "ns",
                Decimal("0.000000001"),
            ),
            "BUS_CAPACITANCE_MAX": (
                "capacitive load for each bus line",
                "pF",
                Decimal("0.000000000001"),
            ),
        }
        if key not in labels or (locator.page, locator.table, locator.row) != (
            44,
            "Table 11",
            labels[key][0],
        ):
            raise ValueError("LOCATOR_UNRESOLVED")
        rows = _table(structure, 44, "Symbol")
        if (
            len(rows) < 2
            or len(rows[0]) < 10
            or _text(rows[0][3]).replace(" ", "") != "Standard-mode"
            or rows[1][3:5] != ("Min", "Max")
        ):
            raise ValueError("LOCATOR_UNRESOLVED")
        matches = [
            row
            for row in rows
            if len(row) >= 10
            and _text(row[1]).replace("\n", " ").strip().startswith(labels[key][0])
        ]
        if len(matches) != 1 or matches[0][9].strip() != labels[key][1]:
            raise ValueError("LOCATOR_UNRESOLVED")
        value = Decimal(_decimal(matches[0][4])) * labels[key][2]
        return (
            "MAX",
            format(value.normalize(), "f"),
            {"kHz": "Hz", "ns": "s", "pF": "F"}[labels[key][1]],
            "I2C_STANDARD",
        )
    if entry.manufacturer == "YAGEO" and entry.mpn in {
        "RC0603FR-0710KL",
        "CC0603KRX7R9BB104",
        "CC0603JRX7R9BB105",
        "CC0805KKX7R7BB475",
    }:
        structure = _pdf_structure(content, 1)
        if locator.page != 1 or locator.table != "Specifications":
            raise ValueError("UNSUPPORTED_DOCUMENT")
        text = _text(structure.pages[0].text)
        case_codes = re.findall(
            r"(?m)^\s*Case Code \(EIA / mm\)\s+(\d{4} / \d{4})(?:\s|$)", text
        )
        expected_package = (
            "0805 / 2012" if entry.mpn == "CC0805KKX7R7BB475" else "0603 / 1608"
        )
        if (
            entry.document_revision != "Generated 10/03/2026"
            or entry.package != expected_package
            or case_codes != [expected_package]
            or text.splitlines()[0].strip() != entry.mpn
            or "Specifications" not in text
            or "Generated 10/03/2026" not in text
        ):
            raise ValueError("UNSUPPORTED_DOCUMENT")
        if entry.mpn == "RC0603FR-0710KL":
            if key == "PULLUP_RESISTANCE" and locator.row == "Resistance":
                matches = re.findall(
                    r"(?m)^.*?\bResistance\s+(\d+(?:\.\d+)?)\s+kOhms\s*$", text
                )
                if len(matches) == 1:
                    return (
                        "EXACT",
                        format((Decimal(matches[0]) * 1000).normalize(), "f"),
                        "ohm",
                        "UNKNOWN",
                    )
            if key == "PULLUP_TOLERANCE" and locator.row == "Resistance Tolerance":
                matches = re.findall(
                    r"(?m)^.*?\bResistance Tolerance\s+(\d+(?:\.\d+)?)\s*%\s*$", text
                )
                if len(matches) == 1:
                    return (
                        "MAX",
                        format((Decimal(matches[0]) / 100).normalize(), "f"),
                        "ratio",
                        "UNKNOWN",
                    )
        elif key == "DECOUPLING_NOMINAL" and locator.row == "Capacitance":
            capacitance = re.findall(
                r"(?m)^.*?\bCapacitance\s+(\d+(?:\.\d+)?)\s+uF\s*$", text
            )
            tolerance = re.findall(
                r"(?m)^.*?\bTolerance\s+(\d+(?:\.\d+)?)\s*%\s*$", text
            )
            if len(capacitance) == len(tolerance) == 1:
                return (
                    "EXACT",
                    format((Decimal(capacitance[0]) / 1000000).normalize(), "f"),
                    "F",
                    "UNKNOWN",
                )
        raise ValueError("LOCATOR_UNRESOLVED")
    raise ValueError("UNSUPPORTED_DOCUMENT")


def _json_fact(entry: _Entry, content: bytes, locator: ParameterLocator, key: str):
    if (
        locator.section is not None
        or locator.clause is not None
        or (locator.page, locator.table, locator.row)
        not in {
            (1, "electrical_facts", "supply_voltage"),
            (1, "electrical_facts", "operating_voltage"),
        }
    ):
        raise ValueError("LOCATOR_UNRESOLVED")
    if key not in {"SUPPLY_MIN", "SUPPLY_MAX"}:
        raise ValueError("UNSUPPORTED_PARAMETER")
    source = _source_json(content)
    if (
        source.source_id,
        source.fingerprint,
        source.source_uri,
        source.source_revision,
        source.manufacturer,
        source.mpn,
        source.package,
    ) != (
        entry.source_record_id,
        entry.source_record_fingerprint,
        entry.document_uri,
        entry.document_revision,
        entry.manufacturer,
        entry.mpn,
        entry.package,
    ):
        raise ValueError("SOURCE_MISMATCH")
    facts = [
        fact
        for fact in source.electrical_facts
        if fact.parameter == locator.row and fact.unit == "V"
    ]
    if len(facts) != 1:
        raise ValueError("LOCATOR_UNRESOLVED")
    value = facts[0].minimum if key == "SUPPLY_MIN" else facts[0].maximum
    if value is None:
        raise ValueError("LOCATOR_UNRESOLVED")
    return (
        "MIN" if key == "SUPPLY_MIN" else "MAX",
        format(Decimal(str(value)).normalize(), "f"),
        "V",
        "UNKNOWN",
    )


class ProjectMappedParameterPort:
    """Read mapped project originals without caching or modifying project files."""

    def __init__(self, root_resolver: Callable[[str, str], Path]) -> None:
        if not callable(root_resolver):
            raise TypeError("root_resolver must be callable")
        self._root_resolver = root_resolver

    def _root(self, tenant_id: str, project_id: str) -> Path:
        root = Path(self._root_resolver(tenant_id, project_id))
        if not root.is_absolute() or _reparse(root) or not root.is_dir():
            raise ValueError("PROJECT_UNAVAILABLE")
        return root.resolve(strict=True)

    @staticmethod
    def _entry(entries: tuple[_Entry, ...], candidate: ParameterCandidate) -> _Entry:
        found = [
            entry
            for entry in entries
            if (entry.document_uri, entry.document_revision)
            == (candidate.document_uri, candidate.document_revision)
        ]
        if len(found) != 1:
            raise ValueError("DOCUMENT_UNAVAILABLE")
        entry = found[0]
        if (
            entry.content_sha256,
            entry.source_record_id,
            entry.source_record_fingerprint,
            entry.manufacturer,
            entry.mpn,
            entry.package,
        ) != (
            candidate.content_sha256,
            candidate.source_record_id,
            candidate.source_record_fingerprint,
            candidate.manufacturer,
            candidate.mpn,
            candidate.package,
        ):
            raise ValueError("SOURCE_MISMATCH")
        return entry

    def read_parameter(self, candidate: ParameterCandidate) -> ParameterCandidate:
        root = self._root(candidate.tenant_id, candidate.project_id)
        map_before, entries = _mapping(root, candidate.tenant_id, candidate.project_id)
        entry = self._entry(entries, candidate)
        original = _original(root, entry)

        def read_document(uri: str, revision: str) -> bytes:
            if (uri, revision) != (entry.document_uri, entry.document_revision):
                raise ValueError("DOCUMENT_UNAVAILABLE")
            return original

        def read_locator(
            content: bytes, locator: ParameterLocator
        ) -> ParameterCandidate:
            if locator != candidate.locator:
                raise ValueError("LOCATOR_UNRESOLVED")
            if entry.kind == "PDF":
                bound, value, unit, mode = _pdf_fact(
                    entry, content, locator, candidate.parameter_key
                )
            else:
                bound, value, unit, mode = _json_fact(
                    entry, content, locator, candidate.parameter_key
                )
            return ParameterCandidate(
                evidence_id=candidate.evidence_id,
                tenant_id=candidate.tenant_id,
                project_id=candidate.project_id,
                conversation_id=candidate.conversation_id,
                source_record_id=entry.source_record_id,
                source_record_fingerprint=entry.source_record_fingerprint,
                manufacturer=entry.manufacturer,
                mpn=entry.mpn,
                package=entry.package,
                document_uri=entry.document_uri,
                document_revision=entry.document_revision,
                content_sha256=_sha(content),
                locator=locator,
                parameter_key=candidate.parameter_key,
                bound_kind=bound,
                value_decimal=value,
                unit=unit,
                conditions=ParameterConditions(mode=mode, package=entry.package),
            )

        result = LocalParameterSnapshotPort(
            read_document=read_document, read_locator=read_locator
        ).read_parameter(candidate)
        if (
            _mapping(root, candidate.tenant_id, candidate.project_id)[0] != map_before
            or _original(root, entry) != original
        ):
            raise ValueError("DOCUMENT_CHANGED")
        return result

    def read_standard_fact(
        self,
        *,
        tenant_id: str,
        project_id: str,
        document_uri: str,
        document_revision: str,
        content_sha256: str,
        locator: ParameterLocator,
        mode: str,
        frequency_max_hz: str | None = None,
        rise_time_max_s: str | None = None,
        cbus_max_f: str | None = None,
    ) -> StandardDocumentFact:
        """Verify one Standard-mode limit against the mapped original PDF."""
        if mode != "I2C_STANDARD" or type(locator) is not ParameterLocator:
            raise ValueError("UNSUPPORTED_STANDARD")
        profiles = {
            "SCL clock frequency": ("BUS_FREQUENCY_MAX", "frequency_max_hz", "Hz"),
            "rise time of both SDA and SCL signals": (
                "RISE_TIME_MAX", "rise_time_max_s", "s"
            ),
            "capacitive load for each bus line": (
                "BUS_CAPACITANCE_MAX", "cbus_max_f", "F"
            ),
        }
        profile = profiles.get(locator.row)
        supplied = {
            "frequency_max_hz": frequency_max_hz,
            "rise_time_max_s": rise_time_max_s,
            "cbus_max_f": cbus_max_f,
        }
        if (
            profile is None
            or (locator.page, locator.table) != (44, "Table 11")
            or locator.section is not None
            or locator.clause is not None
            or {key for key, value in supplied.items() if value is not None}
            != {profile[1]}
        ):
            raise ValueError("LOCATOR_UNRESOLVED")
        expected = supplied[profile[1]]
        if type(expected) is not str or _decimal(expected) != expected:
            raise ValueError("FACT_MISMATCH")

        root = self._root(tenant_id, project_id)
        map_before, entries = _mapping(root, tenant_id, project_id)
        matched = [
            entry for entry in entries
            if (entry.document_uri, entry.document_revision)
            == (document_uri, document_revision)
        ]
        if len(matched) != 1:
            raise ValueError("DOCUMENT_UNAVAILABLE")
        entry = matched[0]
        if (
            entry.kind != "PDF"
            or entry.manufacturer != "NXP"
            or entry.mpn != "UM10204"
            or entry.package != "I2C_BUS_SPECIFICATION"
            or entry.content_sha256 != content_sha256
        ):
            raise ValueError("UNSUPPORTED_DOCUMENT")
        original = _original(root, entry)
        bound, value, unit, extracted_mode = _pdf_fact(
            entry, original, locator, profile[0]
        )
        if (bound, value, unit, extracted_mode) != (
            "MAX", expected, profile[2], mode
        ):
            raise ValueError("FACT_MISMATCH")
        if (
            _mapping(root, tenant_id, project_id)[0] != map_before
            or _original(root, entry) != original
        ):
            raise ValueError("DOCUMENT_CHANGED")
        return StandardDocumentFact(
            document_uri=entry.document_uri,
            document_revision=entry.document_revision,
            content_sha256=_sha(original),
            locator=locator,
            mode=mode,
            **{profile[1]: value},
        )

    def read_library_originals(self, source: ComponentSourceRecord, *, tenant_id: str,
        project_id: str, symbol_library_id: str, symbol_uri: str, symbol_sha256: str,
        footprint_library_id: str, footprint_uri: str, footprint_sha256: str):
        from .schematic_library_readers import read_shtc3_library_originals

        return read_shtc3_library_originals(
            root=self._root(tenant_id, project_id), tenant_id=tenant_id, project_id=project_id,
            source=source, symbol_library_id=symbol_library_id, symbol_uri=symbol_uri,
            symbol_sha256=symbol_sha256, footprint_library_id=footprint_library_id,
            footprint_uri=footprint_uri, footprint_sha256=footprint_sha256,
        )

    def read_device_conditions(
        self, source: ComponentSourceRecord, *, tenant_id: str, project_id: str
    ) -> ParameterConditions:
        """Re-derive approved finite device envelopes from mapped originals."""
        source = ComponentSourceRecord.model_validate(source.model_dump(mode="python"))
        profile = (source.manufacturer, source.mpn, source.package)
        stm32 = profile == ("STMicroelectronics", "STM32F103C8T6", "LQFP48")
        if not stm32 and profile != ("Sensirion", "SHTC3", "DFN-4 2x2 mm"):
            raise ValueError("DEVICE_CONDITIONS_UNAVAILABLE")
        root = self._root(tenant_id, project_id)
        map_before, entries = _mapping(root, tenant_id, project_id)
        common = (
            source.source_id, source.fingerprint, source.manufacturer,
            source.mpn, source.package,
        )
        component = [
            entry for entry in entries
            if entry.kind == "COMPONENT_SOURCE_JSON"
            and (
                entry.source_record_id, entry.source_record_fingerprint,
                entry.manufacturer, entry.mpn, entry.package,
            ) == common
            and (entry.document_uri, entry.document_revision)
            == (source.source_uri, source.source_revision)
        ]
        documents = [
            entry for entry in entries
            if entry.kind == "PDF"
            and (
                entry.source_record_id, entry.source_record_fingerprint,
                entry.manufacturer, entry.mpn, entry.package,
            ) == common
            and (stm32 or entry.document_revision == "Version 4 - December 2022")
        ]
        if len(component) != 1 or len(documents) != 1:
            raise ValueError("DEVICE_CONDITIONS_UNAVAILABLE")
        original_source = _original(root, component[0])
        if _source_json(original_source) != source:
            raise ValueError("SOURCE_MISMATCH")
        pdf = _original(root, documents[0])
        if stm32:
            if (
                documents[0].document_uri != "https://www.st.com/resource/en/datasheet/stm32f103r8.pdf"
                or documents[0].document_revision != "DS5319 Rev 20"
                or len(pdf) != 1965230
                or _sha(pdf) != "sha256:1e54ce18e6c34c5b4a78986f26e245e143748c721009fc9af5953795c30686bc"
            ):
                raise ValueError("UNSUPPORTED_DOCUMENT")
            try:
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    if len(document) != 114:
                        raise ValueError("UNSUPPORTED_DOCUMENT")
                    pages = tuple(re.sub(r"\s+", " ", document[index].get_text())
                                  for index in (0, 37, 67, 103))
            except (RuntimeError, TypeError, ValueError):
                raise ValueError("UNSUPPORTED_DOCUMENT") from None
            if not (
                "DS5319 Rev 20" in pages[0]
                and "STM32F103x8 STM32F103xB" in pages[0]
                and "STM32F103C8, STM32F103R8" in pages[0]
                and "LQFP48 7 × 7 mm" in pages[0]
                and "Table 9. General operating conditions" in pages[1]
                and "VDD Standard operating voltage - 2 3.6 V" in pages[1]
                and "TA Ambient temperature for 6 suffix version Maximum power dissipation –40 85 °C" in pages[1]
                and "5.3.16 Communications interfaces" in pages[2]
                and "Table 41. I2C characteristics" in pages[2]
                and "Standard mode I2C" in pages[2]
                and "fPCLK1 must be at least 2 MHz to achieve standard mode I2C frequencies" in pages[2]
                and "Specified by design, not tested in production" in pages[2]
                and "7 Ordering information scheme" in pages[3]
                and "C = 48 pins" in pages[3] and "T = LQFP" in pages[3]
                and "6 = Industrial temperature range, –40 to 85 °C" in pages[3]
            ):
                raise ValueError("DEVICE_CONDITIONS_UNAVAILABLE")
            # Protocol classification only; peripheral clock/configuration and all-corner
            # timing must still be proved independently by the existing evidence chain.
            conditions = ParameterConditions(
                voltage_min="2", voltage_max="3.6", temperature_min="-40",
                temperature_max="85", mode="I2C_STANDARD", package=source.package,
            )
        else:
            if _sha(pdf) != "sha256:dbe979c975fe520663606b5c278d2b9130f4e402e6d9d943ca06c4e4f38e63d9":
                raise ValueError("UNSUPPORTED_DOCUMENT")
            try:
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    if len(document) != 14:
                        raise ValueError("UNSUPPORTED_DOCUMENT")
                    pages = tuple(
                        re.sub(r"\s+", " ", document[index].get_text())
                        for index in (0, 2, 4, 5)
                    )
            except (RuntimeError, TypeError, ValueError):
                raise ValueError("UNSUPPORTED_DOCUMENT") from None
            if not (
                "Datasheet SHTC3" in pages[0]
                and "Version 4" in pages[0]
                and "Small DFN package: 2 × 2 × 0.75 mm3" in pages[0]
                and "normal temperature and humidity range of 5 – 60 °C" in pages[1]
                and "Supply voltage VDD 1.62 3.3 3.6 V" in pages[1]
                and "Standard-mode Fast-mode Fast-mode Plus" in pages[2]
                and "SCL clock frequency fSCL - 0 100 0 400 0 1000 kHz" in pages[2]
                and "supports I2C Normal, Fast Mode and Fast Mode Plus" in pages[3]
                and "comes in a 4-pin package" in pages[3]
            ):
                raise ValueError("DEVICE_CONDITIONS_UNAVAILABLE")
            conditions = ParameterConditions(
                voltage_min="1.62", voltage_max="3.6", temperature_min="5",
                temperature_max="60", mode="I2C_STANDARD", package=source.package,
            )
        if (
            _mapping(root, tenant_id, project_id)[0] != map_before
            or _original(root, component[0]) != original_source
            or _original(root, documents[0]) != pdf
        ):
            raise ValueError("DOCUMENT_CHANGED")
        return conditions

    def read_component_source(
        self, source: ComponentSourceRecord, candidates: tuple[ParameterCandidate, ...]
    ) -> ComponentSourceRecord:
        if not candidates:
            raise ValueError("SOURCE_UNAVAILABLE")
        scope = (candidates[0].tenant_id, candidates[0].project_id)
        if any(
            (
                candidate.tenant_id,
                candidate.project_id,
                candidate.source_record_id,
                candidate.source_record_fingerprint,
                candidate.manufacturer,
                candidate.mpn,
                candidate.package,
            )
            != (
                *scope,
                source.source_id,
                source.fingerprint,
                source.manufacturer,
                source.mpn,
                source.package,
            )
            for candidate in candidates
        ):
            raise ValueError("SOURCE_MISMATCH")
        root = self._root(*scope)
        map_before, entries = _mapping(root, *scope)
        found = [
            entry
            for entry in entries
            if entry.kind == "COMPONENT_SOURCE_JSON"
            and (
                entry.source_record_id,
                entry.source_record_fingerprint,
                entry.document_uri,
                entry.document_revision,
                entry.manufacturer,
                entry.mpn,
                entry.package,
            )
            == (
                source.source_id,
                source.fingerprint,
                source.source_uri,
                source.source_revision,
                source.manufacturer,
                source.mpn,
                source.package,
            )
        ]
        if len(found) != 1:
            raise ValueError("UNSUPPORTED_COMPONENT_SOURCE")
        entry = found[0]
        original = _original(root, entry)
        prepared = tuple(
            (candidate, self._entry(entries, candidate)) for candidate in candidates
        )
        originals = tuple(_original(root, mapped) for _, mapped in prepared)
        call_count = 0

        def read_document(uri: str, revision: str) -> bytes:
            nonlocal call_count
            index = call_count // 2
            if index >= len(prepared):
                raise ValueError("DOCUMENT_UNAVAILABLE")
            _, mapped = prepared[index]
            if (uri, revision) != (mapped.document_uri, mapped.document_revision):
                raise ValueError("DOCUMENT_UNAVAILABLE")
            call_count += 1
            return originals[index]

        def read_locator(
            content: bytes, locator: ParameterLocator
        ) -> ParameterCandidate:
            index = (call_count - 1) // 2
            candidate, _ = prepared[index]
            if locator != candidate.locator or content != originals[index]:
                raise ValueError("LOCATOR_UNRESOLVED")
            # This nested request extracts the facts afresh from the mapped bytes.
            return self.read_parameter(candidate)

        def read_source(
            read_originals: tuple[bytes, ...], source_id: str
        ) -> ComponentSourceRecord:
            if source_id != entry.source_record_id or read_originals != originals:
                raise ValueError("SOURCE_MISMATCH")
            return _source_json(original)

        result = LocalParameterSnapshotPort(
            read_document=read_document,
            read_locator=read_locator,
            read_component_source=read_source,
        ).read_component_source(source, candidates)
        if (
            _mapping(root, *scope)[0] != map_before
            or _original(root, entry) != original
            or any(
                _original(root, mapped) != content
                for (_, mapped), content in zip(prepared, originals)
            )
        ):
            raise ValueError("DOCUMENT_CHANGED")
        return result
