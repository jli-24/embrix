from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Protocol

from .contracts import (
    Artifact,
    ArtifactCoverage,
    ArtifactEvidence,
    ArtifactIntelligenceSnapshot,
    ArtifactInventory,
    ArtifactStatus,
    ArtifactType,
    DesignSource,
    EngineeringEvidence,
    EngineeringEvidenceBundle,
    EvidenceType,
    PlanProvenance,
    TruthLevel,
)

MAX_FILE_BYTES = 16_777_216
MAX_ZIP_MEMBERS = 2_000
MAX_ZIP_UNCOMPRESSED = 64 * 1024 * 1024
MAX_SCAN_ENTRIES = 5_000
_FINGERPRINT_PREFIX = "sha256:"
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


class ArtifactParser(Protocol):
    def parse(self, artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]: ...


class ArtifactParseError(ValueError):
    """A deterministic parser rejected an unsupported or malformed artifact."""


def _content_fingerprint(content: bytes) -> str:
    return _FINGERPRINT_PREFIX + hashlib.sha256(content).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _text(content: bytes) -> str:
    if type(content) is not bytes or not content or len(content) > MAX_FILE_BYTES:
        raise ArtifactParseError("artifact content size is invalid")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ArtifactParseError("artifact must be UTF-8") from error


def _evidence(
    artifact: Artifact,
    *,
    evidence_type: EvidenceType,
    claim_type: str,
    claim: str,
    locator: str,
    attributes: tuple[tuple[str, str], ...],
) -> ArtifactEvidence:
    return ArtifactEvidence.create(
        evidence_id=_id("artifact-evidence", artifact.artifact_id, locator),
        tenant_id=artifact.tenant_id,
        project_id=artifact.project_id,
        conversation_id=_metadata_value(artifact, "conversation_id", "conversation"),
        artifact_id=artifact.artifact_id,
        evidence_type=evidence_type,
        source=DesignSource(artifact.type.value),
        claim_type=claim_type,
        claim=claim,
        locator=locator,
        truth_level=TruthLevel.EXPLICIT,
        confidence=1.0,
        artifact_version=artifact.version,
        content_fingerprint=artifact.hash,
        attributes=attributes,
    )


def _metadata_value(artifact: Artifact, key: str, default: str) -> str:
    return dict(artifact.metadata).get(key, default)


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(value):
        if value[index].isspace():
            index += 1
            continue
        if value[index] in "();":
            if value[index] == ";":
                end = value.find("\n", index)
                index = len(value) if end < 0 else end + 1
                continue
            tokens.append(value[index])
            index += 1
            continue
        if value[index] == '"':
            index += 1
            chars: list[str] = []
            while index < len(value) and value[index] != '"':
                if value[index] == "\\":
                    index += 1
                    if index >= len(value):
                        raise ArtifactParseError("unterminated schematic escape")
                chars.append(value[index])
                index += 1
            if index >= len(value):
                raise ArtifactParseError("unterminated schematic string")
            tokens.append("".join(chars))
            index += 1
            continue
        end = index
        while end < len(value) and not value[end].isspace() and value[end] not in "();":
            end += 1
        tokens.append(value[index:end])
        index = end
    if len(tokens) > 300_000:
        raise ArtifactParseError("schematic token limit exceeded")
    return tokens


def _expressions(tokens: list[str]) -> list[object]:
    roots: list[object] = []
    stack: list[list[object]] = []
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            if not stack:
                raise ArtifactParseError("unexpected schematic closing parenthesis")
            value = stack.pop()
            (stack[-1] if stack else roots).append(value)
        else:
            (stack[-1] if stack else roots).append(token)
    if stack:
        raise ArtifactParseError("unterminated schematic expression")
    return roots


def _walk(value: object) -> Iterable[list[object]]:
    if isinstance(value, list):
        yield value
        for child in value:
            yield from _walk(child)


def parse_kicad_schematic(artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
    if artifact.type is not ArtifactType.SCHEMATIC:
        raise ArtifactParseError("schematic parser received a non-schematic artifact")
    raw = _text(content)
    expressions = _expressions(_tokenize(raw))
    if len(expressions) != 1 or not isinstance(expressions[0], list) or expressions[0][0] != "kicad_sch":
        raise ArtifactParseError("schematic root must be kicad_sch")
    root = expressions[0]
    version = next(
        (item[1] for item in root[1:] if isinstance(item, list) and len(item) > 1 and item[0] == "version"),
        None,
    )
    if version != "20231120":
        raise ArtifactParseError("schematic version is not supported")
    if any(expr and expr[0] in {"sheet", "bus", "bus_entry", "hierarchical_label"} for expr in _walk(root)):
        raise ArtifactParseError("schematic hierarchy or bus connectivity is not supported")

    result: list[ArtifactEvidence] = []
    references: set[str] = set()
    for expr in _walk(root):
        if not expr or expr[0] != "symbol":
            continue
        # ``lib_symbols`` contains reusable definitions whose Reference and
        # Value properties are library defaults (for example ``R``/``R``),
        # not placed schematic components.  Only instances carry ``lib_id``.
        if not any(
            isinstance(child, list) and child and child[0] == "lib_id"
            for child in expr[1:]
        ):
            continue
        properties: dict[str, str] = {}
        for child in expr[1:]:
            if (
                isinstance(child, list)
                and len(child) >= 3
                and child[0] == "property"
                and isinstance(child[1], str)
                and isinstance(child[2], str)
            ):
                properties[child[1]] = child[2]
        reference = properties.get("Reference", "").strip()
        value = properties.get("Value", "").strip()
        if not reference or reference.startswith("#"):
            continue
        if not value or reference in references:
            raise ArtifactParseError("schematic symbol identity is missing or duplicated")
        references.add(reference)
        category = "MCU" if any(token in value.upper() for token in ("STM32", "ESP32", "RP2040")) else "COMPONENT"
        result.append(
            _evidence(
                artifact,
                evidence_type=EvidenceType.SCHEMATIC_COMPONENT,
                claim_type="COMPONENT_EXISTS",
                claim=f"{reference} {value}",
                locator=f"symbol:{reference}",
                attributes=(("reference", reference), ("name", value), ("category", category)),
            )
        )
    if not result:
        raise ArtifactParseError("schematic contains no placed components")

    # A strict, explicit textual subset is supported for hand-authored test
    # fixtures and exported annotations. No pin is invented from a symbol name.
    for match in re.finditer(
        r"(?:pin|node)\s+(?:(?P<component>[A-Za-z][A-Za-z0-9_-]*)\.)?"
        r"(?P<pin>[A-Za-z]{1,4}\d+)\s+(?:connected|net)\s+"
        r"(?P<signal>[A-Za-z0-9_.+/-]+)",
        raw,
        flags=re.IGNORECASE,
    ):
        component_ref = match.group("component")
        pin_name = match.group("pin")
        net_name = match.group("signal")
        attributes = [("pin_name", pin_name), ("signal", net_name)]
        if component_ref:
            attributes.insert(0, ("component_ref", component_ref))
        result.append(
            _evidence(
                artifact,
                evidence_type=EvidenceType.SCHEMATIC_PIN,
                claim_type="PIN_CONNECTED",
                claim=f"{pin_name} connected {net_name}",
                locator=f"text:{match.start()}",
                attributes=tuple(attributes),
            )
        )
    for match in re.finditer(
        r"net\s+(?P<name>[A-Za-z0-9_.+/-]+)\s+source\s+"
        r"(?P<source>[A-Za-z][A-Za-z0-9_-]*)\s+targets?\s+"
        r"(?P<targets>[A-Za-z][A-Za-z0-9_., -]*)",
        raw,
        flags=re.IGNORECASE,
    ):
        targets = tuple(
            item.strip()
            for item in match.group("targets").split(",")
            if item.strip()
        )
        if not targets:
            continue
        result.append(
            _evidence(
                artifact,
                evidence_type=EvidenceType.SCHEMATIC_NET,
                claim_type="NET_CONNECTED",
                claim=f"{match.group('name')} source {match.group('source')} targets {','.join(targets)}",
                locator=f"text:{match.start()}",
                attributes=(
                    ("name", match.group("name")),
                    ("source", match.group("source")),
                    ("targets", ",".join(targets)),
                    ("signal_type", "ELECTRICAL"),
                    ("direction", "SOURCE_TO_TARGET"),
                ),
            )
        )
    return tuple(result)


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_xml(content: bytes) -> tuple[zipfile.ZipFile, list[str]]:
    if type(content) is not bytes or not content or len(content) > MAX_FILE_BYTES:
        raise ArtifactParseError("XLSX content size is invalid")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (OSError, zipfile.BadZipFile) as error:
        raise ArtifactParseError("XLSX archive is malformed") from error
    names = archive.namelist()
    if len(names) > MAX_ZIP_MEMBERS or any(
        PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names
    ):
        archive.close()
        raise ArtifactParseError("XLSX archive contains unsafe members")
    if sum(item.file_size for item in archive.infolist()) > MAX_ZIP_UNCOMPRESSED:
        archive.close()
        raise ArtifactParseError("XLSX archive is too large")
    return archive, names


def _shared_strings(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in names:
        return []
    try:
        root = ET.fromstring(archive.read(name))
    except (ET.ParseError, KeyError) as error:
        raise ArtifactParseError("XLSX shared strings are malformed") from error
    return ["".join(item.text or "" for item in node.iter() if _xml_name(node.tag) == "si" or _xml_name(item.tag) == "t") for node in root if _xml_name(node.tag) == "si"]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    value = next((child.text or "" for child in cell if _xml_name(child.tag) == "v"), "")
    if cell.attrib.get("t") == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError) as error:
            raise ArtifactParseError("XLSX shared string reference is invalid") from error
    if cell.attrib.get("t") == "inlineStr":
        return "".join(item.text or "" for item in cell.iter() if _xml_name(item.tag) == "t")
    return value


def _column(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference or "")
    if letters is None:
        raise ArtifactParseError("XLSX cell reference is invalid")
    value = 0
    for char in letters.group(0).upper():
        value = value * 26 + ord(char) - 64
    return value - 1


def parse_xlsx_bom(artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
    if artifact.type is not ArtifactType.BOM:
        raise ArtifactParseError("BOM parser received a non-BOM artifact")
    archive, names = _zip_xml(content)
    try:
        shared = _shared_strings(archive, names)
        sheet = "xl/worksheets/sheet1.xml"
        if sheet not in names:
            raise ArtifactParseError("XLSX first worksheet is missing")
        root = ET.fromstring(archive.read(sheet))
        rows: list[dict[int, str]] = []
        for row in root.iter():
            if _xml_name(row.tag) != "row":
                continue
            values: dict[int, str] = {}
            for cell in row:
                if _xml_name(cell.tag) == "c":
                    values[_column(cell.attrib.get("r", ""))] = _cell_value(cell, shared).strip()
            if values:
                rows.append(values)
    except ET.ParseError as error:
        raise ArtifactParseError("XLSX worksheet is malformed") from error
    finally:
        archive.close()
    if not rows:
        raise ArtifactParseError("BOM worksheet is empty")
    headers = {value.casefold(): index for index, value in rows[0].items() if value}
    reference_index = next((headers[key] for key in ("references", "reference", "refs") if key in headers), None)
    value_index = next((headers[key] for key in ("value", "part", "part number", "mpn") if key in headers), None)
    if reference_index is None or value_index is None:
        raise ArtifactParseError("BOM headers are unsupported")
    evidence: list[ArtifactEvidence] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows[1:], start=2):
        references = tuple(sorted(item.strip() for item in row.get(reference_index, "").replace(";", ",").split(",") if item.strip()))
        value = row.get(value_index, "").strip()
        if not references or not value:
            continue
        for reference in references:
            if reference in seen:
                raise ArtifactParseError("BOM references are duplicated")
            seen.add(reference)
            evidence.append(
                _evidence(
                    artifact,
                    evidence_type=EvidenceType.BOM_COMPONENT,
                    claim_type="COMPONENT_EXISTS",
                    claim=f"{reference} {value}",
                    locator=f"sheet1:row:{row_number}",
                    attributes=(("reference", reference), ("name", value), ("category", "COMPONENT")),
                )
            )
    if not evidence:
        raise ArtifactParseError("BOM contains no component evidence")
    return tuple(evidence)


def parse_csv_bom(artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
    if artifact.type is not ArtifactType.BOM:
        raise ArtifactParseError("BOM parser received a non-BOM artifact")
    try:
        rows = list(csv.DictReader(io.StringIO(_text(content), newline="")))
    except csv.Error as error:
        raise ArtifactParseError("BOM CSV is malformed") from error
    if not rows:
        raise ArtifactParseError("BOM CSV is empty")
    reference_key = next((key for key in ("References", "Reference", "Refs") if key in rows[0]), None)
    value_key = next((key for key in ("Value", "Part", "MPN") if key in rows[0]), None)
    if reference_key is None or value_key is None:
        raise ArtifactParseError("BOM CSV headers are unsupported")
    evidence: list[ArtifactEvidence] = []
    for row_number, row in enumerate(rows, start=2):
        reference = (row.get(reference_key) or "").strip()
        value = (row.get(value_key) or "").strip()
        if reference and value:
            evidence.append(
                _evidence(
                    artifact,
                    evidence_type=EvidenceType.BOM_COMPONENT,
                    claim_type="COMPONENT_EXISTS",
                    claim=f"{reference} {value}",
                    locator=f"row:{row_number}",
                    attributes=(("reference", reference), ("name", value), ("category", "COMPONENT")),
                )
            )
    if not evidence:
        raise ArtifactParseError("BOM CSV contains no component evidence")
    return tuple(evidence)


def _artifact_type(path: str) -> ArtifactType:
    lower = path.casefold()
    suffix = PurePosixPath(lower).suffix
    name = PurePosixPath(lower).name
    if suffix == ".kicad_sch":
        return ArtifactType.SCHEMATIC
    if suffix == ".kicad_pcb":
        return ArtifactType.PCB
    if suffix in {".xlsx", ".xls", ".csv"} or "bom" in name:
        return ArtifactType.BOM
    if suffix in {".gbr", ".ger", ".gtl", ".gbl", ".gto", ".gbo", ".drl"}:
        return ArtifactType.GERBER
    if suffix in {".c", ".h", ".cc", ".cpp", ".hpp", ".ino", ".s", ".asm", ".ioc", ".cmake"} or name in {"makefile", "cmakelists.txt", "platformio.ini"}:
        return ArtifactType.FIRMWARE
    if suffix == ".pdf":
        return ArtifactType.DATASHEET
    if name.startswith("readme") or suffix in {".md", ".rst", ".txt"}:
        return ArtifactType.README
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        return ArtifactType.IMAGE
    return ArtifactType.OTHER


def _logical_member(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or ":" in path.parts[0]:
        raise ArtifactParseError("artifact path is unsafe")
    return path.as_posix()


def _make_artifact(
    *,
    tenant_id: str,
    project_id: str,
    conversation_id: str,
    path_reference: str,
    content: bytes,
    version: str,
    source_operation_id: str | None = None,
    source_proposal_fingerprint: str | None = None,
) -> Artifact:
    digest = _content_fingerprint(content)
    metadata: list[tuple[str, str]] = [("conversation_id", conversation_id)]
    if source_operation_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}", source_operation_id):
            raise ValueError("source operation identity is invalid")
        metadata.append(("source_operation_id", source_operation_id))
    if source_proposal_fingerprint is not None:
        if _FINGERPRINT.fullmatch(source_proposal_fingerprint) is None:
            raise ValueError("source proposal fingerprint is invalid")
        metadata.append(("source_proposal_fingerprint", source_proposal_fingerprint))
    return Artifact.create(
        artifact_id=_id("artifact", project_id, conversation_id, path_reference, digest),
        tenant_id=tenant_id,
        project_id=project_id,
        type=_artifact_type(path_reference),
        name=PurePosixPath(path_reference).name,
        path_reference=path_reference,
        hash=digest,
        version=version,
        metadata=tuple(metadata),
        provenance=(
            PlanProvenance.create(
                project_id=project_id,
                source_type="ARTIFACT",
                source_id=path_reference.replace("/", "-"),
                source_fingerprint=digest,
            ),
        ),
    )


def _parser_for(artifact: Artifact) -> ArtifactParser | None:
    if artifact.type is ArtifactType.SCHEMATIC and artifact.name.casefold().endswith(".kicad_sch"):
        return _SchematicParser()
    if artifact.type is ArtifactType.BOM and artifact.name.casefold().endswith(".xlsx"):
        return _XLSXBOMParser()
    if artifact.type is ArtifactType.BOM and artifact.name.casefold().endswith(".csv"):
        return _CSVBOMParser()
    return None


class _SchematicParser:
    def parse(self, artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
        return parse_kicad_schematic(artifact, content)


class _XLSXBOMParser:
    def parse(self, artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
        return parse_xlsx_bom(artifact, content)


class _CSVBOMParser:
    def parse(self, artifact: Artifact, content: bytes) -> tuple[ArtifactEvidence, ...]:
        return parse_csv_bom(artifact, content)


def _as_engineering_evidence(item: ArtifactEvidence) -> EngineeringEvidence:
    return EngineeringEvidence.create(
        evidence_id=_id("engineering-evidence", item.evidence_id),
        tenant_id=item.tenant_id,
        project_id=item.project_id,
        conversation_id=item.conversation_id,
        artifact_id=item.artifact_id,
        source=item.source,
        evidence_type=item.evidence_type,
        claim_type=item.claim_type,
        claim=item.claim,
        locator=item.locator,
        truth_level=item.truth_level,
        confidence=item.confidence,
        artifact_version=item.artifact_version,
        content_fingerprint=item.content_fingerprint,
        attributes=item.attributes,
        provenance=(
            PlanProvenance.create(
                project_id=item.project_id,
                source_type=item.source.value,
                source_id=item.artifact_id,
                source_fingerprint=item.content_fingerprint,
            ),
        ),
    )


def _coverage(
    *, tenant_id: str, project_id: str, conversation_id: str, artifacts: tuple[Artifact, ...]
) -> ArtifactCoverage:
    statuses: dict[ArtifactType, ArtifactStatus] = {}
    for item in artifacts:
        current = statuses.get(item.type)
        if current is None or item.status is ArtifactStatus.PARSED:
            statuses[item.type] = item.status
    fields = {
        "schematic_status": statuses.get(ArtifactType.SCHEMATIC, ArtifactStatus.MISSING),
        "pcb_status": statuses.get(ArtifactType.PCB, ArtifactStatus.MISSING),
        "bom_status": statuses.get(ArtifactType.BOM, ArtifactStatus.MISSING),
        "firmware_status": statuses.get(ArtifactType.FIRMWARE, ArtifactStatus.MISSING),
        "gerber_status": statuses.get(ArtifactType.GERBER, ArtifactStatus.MISSING),
        "documentation_status": (
            statuses.get(ArtifactType.README)
            or statuses.get(ArtifactType.DATASHEET)
            or ArtifactStatus.MISSING
        ),
    }
    missing = tuple(
        label
        for key, label in (
            ("schematic_status", "缺少原理图"),
            ("pcb_status", "缺少 PCB"),
            ("bom_status", "缺少 BOM"),
            ("firmware_status", "缺少 Firmware 源码"),
            ("gerber_status", "缺少 Gerber"),
            ("documentation_status", "缺少工程文档"),
        )
        if fields[key] is ArtifactStatus.MISSING
    )
    return ArtifactCoverage.create(
        tenant_id=tenant_id,
        project_id=project_id,
        conversation_id=conversation_id,
        completeness_score=(6 - len(missing)) / 6,
        missing_recommendations=missing,
        **fields,
    )


class ArtifactIntelligenceService:
    """Read-only deterministic artifact inventory and evidence extraction."""

    def scan_eda_import_directory(
        self, *, root: Path, native_entry_relative_path: str, backend: str
    ) -> dict[str, object]:
        """Inventory a bounded native source set using the existing read service.

        The returned payloads are transient parser input; callers must never
        persist or project them. Two complete reads detect changes during scan,
        without claiming an atomic filesystem snapshot.
        """
        if backend not in {"KICAD", "JLCEDA_PRO"} or not isinstance(root, Path):
            raise ValueError("IMPORT_INPUT_REJECTED")
        if not root.is_dir() or self._eda_import_reparse(root):
            raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
        normalized = self._eda_import_relative(native_entry_relative_path)
        suffix = Path(normalized).suffix.casefold()
        if (backend == "KICAD" and suffix not in {".kicad_pro", ".kicad_pcb"}) or (
            backend == "JLCEDA_PRO" and suffix != ".eprj3"
        ):
            raise ValueError("IMPORT_INPUT_REJECTED")
        scope_relative = Path(normalized).parent.as_posix()
        scope = root if scope_relative == "." else root.joinpath(*scope_relative.split("/"))
        current = root
        for part in (() if scope_relative == "." else scope_relative.split("/")):
            current = current / part
            if not current.is_dir() or self._eda_import_reparse(current):
                raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
        if not scope.is_dir() or self._eda_import_reparse(scope):
            raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
        try:
            if not scope.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
                raise ValueError("IMPORT_INPUT_REJECTED")
        except OSError as error:
            raise ValueError("IMPORT_SOURCE_UNAVAILABLE") from error
        first = self._eda_import_scan_once(root, scope, normalized, backend)
        try:
            second = self._eda_import_scan_once(root, scope, normalized, backend)
        except (OSError, ValueError) as error:
            raise ValueError("SOURCE_SET_CHANGED") from error
        if first["manifest"] != second["manifest"] or first["closure_issues"] != second["closure_issues"]:
            raise ValueError("SOURCE_SET_CHANGED")
        canonical = {
            "schema": "eda-source-set-v1",
            "scope_policy_id": "eda-scope-r1",
            "native_entry_relative_path": normalized,
            "scope_root_relative_path": scope_relative,
            "manifest": first["manifest"],
        }
        digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        return {**canonical, "source_set_fingerprint": f"sha256:{digest}", "payloads": first["payloads"],
                "closure_issues": first["closure_issues"]}

    @staticmethod
    def _eda_import_reparse(path: Path) -> bool:
        value = path.lstat()
        return path.is_symlink() or bool(getattr(value, "st_file_attributes", 0) & 0x400)

    @staticmethod
    def _eda_import_relative(value: str) -> str:
        if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            raise ValueError("IMPORT_INPUT_REJECTED")
        parts = value.split("/")
        if any(
            part in {"", ".", ".."}
            or unicodedata.normalize("NFC", part) != part
            or ":" in part
            or any(unicodedata.category(character).startswith("C") for character in part)
            for part in parts
        ):
            raise ValueError("IMPORT_INPUT_REJECTED")
        return "/".join(parts)

    def _eda_import_scan_once(
        self, root: Path, scope: Path, native_entry: str, backend: str
    ) -> dict[str, object]:
        manifest: list[dict[str, object]] = []
        payloads: dict[str, bytes] = {}
        closure_issues: list[dict[str, object]] = []
        stack: list[tuple[Path, int]] = [(scope, 0)]
        seen: set[str] = set()
        count = 0
        total_bytes = 0
        while stack:
            directory, depth = stack.pop()
            if depth > 8 or self._eda_import_reparse(directory):
                raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
            for entry in sorted(directory.iterdir(), key=lambda item: item.name.encode("utf-8")):
                if entry.name in {".git", ".embedded-copilot", "-backups"} or entry.suffix.casefold() == ".kicad_prl":
                    continue
                count += 1
                if count > MAX_SCAN_ENTRIES or self._eda_import_reparse(entry):
                    raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
                relative = self._eda_import_relative(unicodedata.normalize("NFC", entry.relative_to(root).as_posix()))
                collision = relative.casefold()
                if collision in seen:
                    raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
                seen.add(collision)
                if entry.is_dir():
                    stack.append((entry, depth + 1))
                    continue
                before = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
                with entry.open("rb") as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                after = entry.stat(follow_symlinks=False)
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
                if len(content) > MAX_FILE_BYTES or identity(before) != identity(after) or len(content) != before.st_size:
                    raise ValueError("SOURCE_SET_CHANGED")
                total_bytes += len(content)
                if total_bytes > MAX_ZIP_UNCOMPRESSED:
                    raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
                manifest.append({"relative_path": relative, "kind": "FILE", "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
                if relative == native_entry or backend == "KICAD":
                    payloads[relative] = content
        paths = {item["relative_path"] for item in manifest}
        if native_entry not in paths:
            raise ValueError("IMPORT_SOURCE_UNAVAILABLE")
        if backend == "KICAD" and native_entry.endswith(".kicad_pro"):
            for suffix in (".kicad_sch", ".kicad_pcb"):
                required = native_entry.removesuffix(".kicad_pro") + suffix
                if required not in paths:
                    manifest.append({"relative_path": required, "kind": "MISSING_DEPENDENCY", "reference_digest": hashlib.sha256(required.encode("utf-8")).hexdigest(), "discovery_locator": native_entry})
        if backend == "KICAD":
            scope_prefix = scope.relative_to(root).as_posix()
            for source_path, content in sorted(payloads.items()):
                if not source_path.endswith(".kicad_sch"):
                    continue
                try:
                    expressions = _expressions(_tokenize(_text(content)))
                    if len(expressions) != 1 or not isinstance(expressions[0], list) or expressions[0][0] != "kicad_sch":
                        raise ArtifactParseError("schematic root is unsupported")
                    sheets = [item for item in _walk(expressions[0]) if item and item[0] == "sheet"]
                except (ArtifactParseError, IndexError, TypeError):
                    closure_issues.append({"kind": "UNKNOWN", "code": "SCHEMATIC_REFERENCES_UNPARSED",
                                           "locator": source_path, "blocks_mutation": True})
                    continue
                for index, sheet in enumerate(sheets):
                    refs = [item[2] for item in sheet[1:] if isinstance(item, list) and len(item) >= 3
                            and item[0] == "property" and item[1] == "Sheetfile" and isinstance(item[2], str)]
                    locator = f"{source_path}/sheet[{index}]"
                    if len(refs) != 1:
                        closure_issues.append({"kind": "UNKNOWN", "code": "SHEETFILE_REFERENCE_UNKNOWN",
                                               "locator": locator, "blocks_mutation": True})
                        continue
                    reference = refs[0]
                    digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
                    combined = str(PurePosixPath(source_path).parent / reference)
                    try:
                        relative_ref = self._eda_import_relative(combined)
                        if scope_prefix != "." and not relative_ref.startswith(scope_prefix + "/"):
                            raise ValueError("outside scope")
                    except ValueError:
                        manifest.append({"relative_path": f"@external/{digest}", "kind": "EXTERNAL_DEPENDENCY",
                                         "reference_digest": digest, "discovery_locator": locator})
                        continue
                    if relative_ref not in paths:
                        manifest.append({"relative_path": relative_ref, "kind": "MISSING_DEPENDENCY",
                                         "reference_digest": digest, "discovery_locator": locator})
        manifest = list({(str(item["kind"]), str(item["relative_path"]), str(item.get("discovery_locator", ""))): item for item in manifest}.values())
        manifest.sort(key=lambda item: str(item["relative_path"]).encode("utf-8"))
        return {"manifest": manifest, "payloads": payloads, "closure_issues": closure_issues}

    def scan_directory(
        self,
        *,
        root: Path,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        version: str = "workspace",
        source_operation_id: str | None = None,
        source_proposal_fingerprint: str | None = None,
    ) -> ArtifactIntelligenceSnapshot:
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("artifact root is unavailable")
        files: list[tuple[str, bytes]] = []
        stack = [(root, 0)]
        scanned = 0
        ignored = {".git", ".embedded-copilot", "node_modules", "build", "dist", "out"}
        while stack:
            directory, depth = stack.pop()
            if depth > 8:
                continue
            for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
                if entry.name in ignored or entry.name.startswith(".") or entry.is_symlink():
                    continue
                scanned += 1
                if scanned > MAX_SCAN_ENTRIES:
                    raise ValueError("artifact scan limit exceeded")
                if entry.is_dir():
                    stack.append((entry, depth + 1))
                    continue
                relative = entry.relative_to(root).as_posix()
                content = entry.read_bytes()
                if len(content) <= MAX_FILE_BYTES:
                    files.append((relative, content))
        return self._scan_files(
            files,
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            version=version,
            source_operation_id=source_operation_id,
            source_proposal_fingerprint=source_proposal_fingerprint,
        )

    def scan_zip(
        self,
        content: bytes,
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        version: str = "workspace",
        source_operation_id: str | None = None,
        source_proposal_fingerprint: str | None = None,
    ) -> ArtifactIntelligenceSnapshot:
        archive, names = _zip_xml(content)
        try:
            files: list[tuple[str, bytes]] = []
            for name in names:
                if name.endswith("/"):
                    continue
                relative = _logical_member(name)
                member = archive.getinfo(name)
                if member.file_size > MAX_FILE_BYTES:
                    continue
                files.append((relative, archive.read(name)))
        finally:
            archive.close()
        return self._scan_files(
            files,
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            version=version,
            source_operation_id=source_operation_id,
            source_proposal_fingerprint=source_proposal_fingerprint,
        )

    def _scan_files(
        self,
        files: list[tuple[str, bytes]],
        *,
        tenant_id: str,
        project_id: str,
        conversation_id: str,
        version: str,
        source_operation_id: str | None,
        source_proposal_fingerprint: str | None,
    ) -> ArtifactIntelligenceSnapshot:
        artifacts: list[Artifact] = []
        engineering_evidence: list[EngineeringEvidence] = []
        for path_reference, content in sorted(files, key=lambda item: item[0].casefold()):
            artifact = _make_artifact(
                tenant_id=tenant_id,
                project_id=project_id,
                conversation_id=conversation_id,
                path_reference=path_reference,
                content=content,
                version=version,
                source_operation_id=source_operation_id if _artifact_type(path_reference) is ArtifactType.SCHEMATIC else None,
                source_proposal_fingerprint=source_proposal_fingerprint if _artifact_type(path_reference) is ArtifactType.SCHEMATIC else None,
            )
            parser = _parser_for(artifact)
            if parser is None:
                status = ArtifactStatus.AVAILABLE if artifact.type is not ArtifactType.OTHER else ArtifactStatus.UNSUPPORTED
                artifacts.append(artifact.model_copy(update={"status": status, "fingerprint": "sha256:" + "0" * 64}))
                continue
            try:
                extracted = parser.parse(artifact, content)
                artifacts.append(artifact.model_copy(update={"status": ArtifactStatus.PARSED, "fingerprint": "sha256:" + "0" * 64}))
                engineering_evidence.extend(_as_engineering_evidence(item) for item in extracted)
            except (ArtifactParseError, ValueError, ET.ParseError, OSError):
                artifacts.append(artifact.model_copy(update={"status": ArtifactStatus.FAILED, "fingerprint": "sha256:" + "0" * 64}))
        inventory = ArtifactInventory.create(
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            artifacts=tuple(artifacts),
        )
        bundle = None
        if engineering_evidence:
            bundle = EngineeringEvidenceBundle.create(
                bundle_id=_id("evidence-bundle", project_id, conversation_id, *sorted(item.fingerprint for item in artifacts)),
                tenant_id=tenant_id,
                project_id=project_id,
                conversation_id=conversation_id,
                artifact_fingerprints=tuple(sorted(item.hash for item in artifacts if item.status is ArtifactStatus.PARSED)),
                evidence=tuple(sorted(engineering_evidence, key=lambda item: item.evidence_id)),
            )
        return ArtifactIntelligenceSnapshot.create(
            tenant_id=tenant_id,
            project_id=project_id,
            conversation_id=conversation_id,
            inventory=inventory,
            coverage=_coverage(tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id, artifacts=tuple(artifacts)),
            engineering_evidence_bundle=bundle,
        )


def scan_artifacts(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    conversation_id: str,
) -> ArtifactIntelligenceSnapshot:
    return ArtifactIntelligenceService().scan_directory(
        root=root,
        tenant_id=tenant_id,
        project_id=project_id,
        conversation_id=conversation_id,
    )


__all__ = (
    "ArtifactIntelligenceService",
    "ArtifactParseError",
    "ArtifactParser",
    "parse_csv_bom",
    "parse_kicad_schematic",
    "parse_xlsx_bom",
    "scan_artifacts",
)
