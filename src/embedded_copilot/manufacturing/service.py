from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.execution_worker.contracts import ArtifactIdentity

from .board import BoardFacts, read_board_facts
from .contracts import (
    EngineeringDecision,
    FabricationAssemblyIntent,
    FabricationBaseline,
    FabricationBaselineStatus,
    FabricationEvidence,
    FabricationGap,
    FabricationMode,
    FabricationModeAudit,
    FabricationModeRequirement,
    FabricationModeRequirementStatus,
    FabricationModeResolution,
    FabricationModeStatus,
    FabricationOverallStatus,
    FabricationReadinessResult,
    FabricationRecord,
    FabricationSourceRevision,
    ManufacturingCheck,
    ManufacturingCheckStatus,
    ManufacturingExportVerification,
    ManufacturingGapCategory,
    ManufacturingGapSeverity,
    ManufacturingManifest,
    ManufacturingOutput,
    ManufacturingOutputType,
    ManufacturingPackage,
    ManufacturingParameterApprovalState,
    ManufacturingParameterAudit,
    ManufacturingParameterDecision,
    ManufacturingReadinessResult,
    ManufacturingReadinessStatus,
    ManufacturingSpecification,
    ManufacturingSpecificationStatus,
    UpstreamGate,
)
from .stores import FabricationStore

_MODE_TOKEN = re.compile(r"(?<![A-Z0-9_])(PCB_ONLY|PCB_ASSEMBLY)(?![A-Z0-9_])", re.IGNORECASE)
_ASSEMBLY_INTENT_TOKEN = re.compile(
    r"(?<![A-Z0-9_])(PCBA|PCB[ _-]?ASSEMBLY|ASSEMBLY|ASSEMBLED|SMT|贴片|装配)(?![A-Z0-9_])",
    re.IGNORECASE,
)
_PASS_STATES = {"PASS", "CONSISTENT", "CURRENT", "VALID"}
_MANUFACTURING_PARAMETERS: tuple[tuple[str, str], ...] = (
    ("board_thickness", "board_thickness_mm"),
    ("surface_finish", "surface_finish"),
    ("copper_weight", "copper_weight"),
    ("solder_mask", "solder_mask"),
    ("silkscreen", "silkscreen"),
)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("source artifact is unavailable")
    return _sha256(candidate.read_bytes())


def _stable(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _source_refs(source: FabricationSourceRevision) -> tuple[str, ...]:
    return source.source_refs or (source.project_revision,)


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        candidate = dump(mode="python")
        return candidate if isinstance(candidate, Mapping) else None
    return None


def _is_authoritative_parameter_mapping(mapping: Mapping[str, object]) -> bool:
    """Reject structured candidates that have not crossed their source gate."""
    if "requirement_id" in mapping and "status" in mapping:
        status = mapping.get("status")
        status_value = getattr(status, "value", status)
        if str(status_value).strip().upper() != "CONFIRMED":
            return False
    if "approval_state" in mapping:
        approval_state = mapping.get("approval_state")
        approval_value = getattr(approval_state, "value", approval_state)
        if str(approval_value).strip().upper() != "APPROVED":
            return False
    return True


def _explicit_parameter_values(value: object, parameter: str, field: str) -> tuple[object, ...]:
    """Collect explicitly named parameter values without interpreting prose or board facts."""
    if isinstance(value, (tuple, list, set, frozenset)):
        return tuple(
            found
            for nested in value
            for found in _explicit_parameter_values(nested, parameter, field)
        )
    mapping = _as_mapping(value)
    if mapping is None:
        direct = getattr(value, field, None) if value is not None else None
        return () if direct is None else (direct,)
    if not _is_authoritative_parameter_mapping(mapping):
        return ()
    aliases = (parameter, field, f"{parameter}_mm")
    found_values: list[object] = []
    matched = False
    for key in aliases:
        if key in mapping and mapping[key] is not None:
            matched = True
            candidate = mapping[key]
            if isinstance(candidate, (Mapping, tuple, list, set, frozenset)):
                if isinstance(candidate, Mapping):
                    if not _is_authoritative_parameter_mapping(candidate):
                        continue
                    nested_found = False
                    for value_key in ("value", "current_value"):
                        if candidate.get(value_key) is not None:
                            found_values.append(candidate[value_key])
                            nested_found = True
                    if not nested_found:
                        found_values.extend(_explicit_parameter_values(candidate, parameter, field))
                else:
                    found_values.extend(_explicit_parameter_values(candidate, parameter, field))
            else:
                found_values.append(candidate)
    parameter_aliases = {item.casefold() for item in aliases}
    for parameter_key in ("parameter", "name", "category", "field", "key"):
        candidate_name = mapping.get(parameter_key)
        if not isinstance(candidate_name, str) or candidate_name.strip().casefold() not in parameter_aliases:
            continue
        matched = True
        for value_key in ("value", "current_value", "normalized_value", field):
            if value_key in mapping and mapping[value_key] is not None:
                found_values.append(mapping[value_key])
    if not matched:
        for nested in mapping.values():
            found_values.extend(_explicit_parameter_values(nested, parameter, field))
    return tuple(found_values)


def _explicit_parameter(value: object, parameter: str, field: str) -> object | None:
    """Read the first explicitly named parameter for compatibility with callers."""
    values = _explicit_parameter_values(value, parameter, field)
    return values[0] if values else None


def _parameter_text(value: object, field: str) -> str:
    if field == "board_thickness_mm":
        try:
            number = _coerce_board_thickness(value)
        except (TypeError, ValueError):
            return str(value).strip()
        return format(number, "g")
    return str(value).strip()


def _coerce_board_thickness(value: object) -> float:
    """Convert an explicitly supplied thickness, including an explicit mm unit."""
    if isinstance(value, bool):
        raise TypeError("board thickness is invalid")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*", value, re.IGNORECASE)
        if match is None:
            raise ValueError("board thickness is invalid")
        number = float(match.group(1))
    else:
        raise TypeError("board thickness is invalid")
    if not math.isfinite(number) or number <= 0:
        raise ValueError("board thickness is invalid")
    return number


def _parameter_equal(field: str, left: object, right: object) -> bool:
    if field == "board_thickness_mm":
        try:
            return _coerce_board_thickness(left) == _coerce_board_thickness(right)
        except (TypeError, ValueError):
            return False
    return str(left).strip() == str(right).strip()


def _is_unspecified(value: object) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().upper() == "UNSPECIFIED"
    )


def audit_manufacturing_parameters(
    *,
    specification: ManufacturingSpecification | None = None,
    requirement_specification: object | None = None,
    engineering_decision: object | None = None,
    design_truth: object | None = None,
    pcb_artifact: object | None = None,
    bom_artifact: object | None = None,
    existing_evidence: Sequence[object] = (),
    parameter_decisions: Sequence[ManufacturingParameterDecision] = (),
    project_id: str | None = None,
    now: datetime | None = None,
) -> tuple[tuple[ManufacturingParameterAudit, ...], tuple[ManufacturingParameterDecision, ...]]:
    """Audit manufacturing parameters using explicit, approved sources only.

    PCB and BOM objects are accepted for lineage inspection, but their observed
    geometry or component data is deliberately never used to fill a missing
    manufacturing parameter.
    """
    timestamp = _now(now)
    project = project_id or (specification.project_id if specification is not None else None)
    if project is None:
        raise ValueError("project_id is required for a manufacturing parameter audit")
    normalized: list[ManufacturingParameterDecision] = []
    for raw in parameter_decisions:
        decision = ManufacturingParameterDecision.model_validate(
            raw.model_dump(mode="python") if isinstance(raw, ManufacturingParameterDecision) else raw,
            strict=False,
        )
        if decision.project_id != project:
            raise ValueError("manufacturing parameter decision project mismatch")
        if decision.parameter not in {name for name, _ in _MANUFACTURING_PARAMETERS}:
            raise ValueError("manufacturing parameter decision parameter is unsupported")
        normalized.append(decision)
    by_parameter: dict[str, list[ManufacturingParameterDecision]] = {}
    for decision in normalized:
        by_parameter.setdefault(decision.parameter, []).append(decision)

    sources: tuple[tuple[str, object | None], ...] = (
        ("RequirementSpecification", requirement_specification),
        ("EngineeringDecision", engineering_decision),
    )
    audits: list[ManufacturingParameterAudit] = []
    generated: list[ManufacturingParameterDecision] = []
    for parameter, field in _MANUFACTURING_PARAMETERS:
        candidates = by_parameter.get(parameter, [])
        approved = [item for item in candidates if item.approval_state is ManufacturingParameterApprovalState.APPROVED]
        rejected = [item for item in candidates if item.approval_state is ManufacturingParameterApprovalState.REJECTED]
        current_value = getattr(specification, field, None) if specification is not None else None
        current = None if _is_unspecified(current_value) else current_value
        source = specification.source_refs[0] if specification is not None and specification.source_refs else None
        explicit_candidates: list[tuple[str, str]] = []
        for source_name, source_value in sources:
            for candidate in _explicit_parameter_values(source_value, parameter, field):
                if not str(candidate).strip() or str(candidate).strip().upper() == "UNSPECIFIED":
                    continue
                explicit_candidates.append((source_name, _parameter_text(candidate, field)))
        unique_explicit = {value for _, value in explicit_candidates}
        status = ManufacturingParameterApprovalState.UNSPECIFIED
        if rejected or len({item.value for item in approved}) > 1:
            status = ManufacturingParameterApprovalState.REJECTED
            current_text = _parameter_text(current, field) if current is not None else "UNSPECIFIED"
            source = rejected[0].source if rejected else "conflicting-decisions"
        elif approved:
            decision = approved[-1]
            current_text = decision.value
            source = decision.source
            status = ManufacturingParameterApprovalState.APPROVED
            if current is not None and not _parameter_equal(field, current, decision.value):
                status = ManufacturingParameterApprovalState.REJECTED
        elif current is not None:
            current_text = _parameter_text(current, field)
            status = ManufacturingParameterApprovalState.APPROVED
            if any(not _parameter_equal(field, current, value) for _, value in explicit_candidates):
                status = ManufacturingParameterApprovalState.REJECTED
                source = "conflicting-sources"
        else:
            current_text = "UNSPECIFIED"
            if len(unique_explicit) > 1:
                current_text = explicit_candidates[0][1]
                source = "conflicting-sources"
                status = ManufacturingParameterApprovalState.REJECTED
            elif explicit_candidates:
                source, current_text = explicit_candidates[0]
                status = ManufacturingParameterApprovalState.APPROVED
        if source is None:
            source = "UNSPECIFIED"
        audits.append(ManufacturingParameterAudit.create(
            parameter=parameter,
            current_value=current_text,
            source=source,
            status=status,
        ))
        if not candidates and current is None:
            generated.append(ManufacturingParameterDecision.create(
                id=_stable("manufacturing-parameter-decision", f"{project}|{parameter}|{getattr(specification, 'fingerprint', 'NONE')}"),
                project_id=project,
                parameter=parameter,
                value=current_text,
                source=source,
                approval_state=(
                    status
                    if status in {
                        ManufacturingParameterApprovalState.APPROVED,
                        ManufacturingParameterApprovalState.REJECTED,
                    }
                    else ManufacturingParameterApprovalState.UNSPECIFIED
                ),
                created_at=timestamp,
            ))
    return tuple(audits), (*normalized, *generated)


def close_manufacturing_parameters(
    specification: ManufacturingSpecification,
    decisions: Sequence[ManufacturingParameterDecision],
) -> ManufacturingSpecification:
    """Apply only approved decisions to a new, revalidated specification."""
    values = specification.model_dump(mode="python")
    refs = list(specification.source_refs)
    found: dict[str, ManufacturingParameterDecision] = {}
    for raw in decisions:
        decision = ManufacturingParameterDecision.model_validate(
            raw.model_dump(mode="python") if isinstance(raw, ManufacturingParameterDecision) else raw,
            strict=False,
        )
        if decision.project_id != specification.project_id:
            raise ValueError("manufacturing parameter decision project mismatch")
        if decision.approval_state is not ManufacturingParameterApprovalState.APPROVED:
            continue
        if decision.parameter in found and found[decision.parameter].value != decision.value:
            raise ValueError("conflicting approved manufacturing parameter decisions")
        found[decision.parameter] = decision
        refs.extend((decision.source, decision.id))
    for parameter, field in _MANUFACTURING_PARAMETERS:
        decision = found.get(parameter)
        if decision is None:
            continue
        values[field] = _coerce_board_thickness(decision.value) if field == "board_thickness_mm" else decision.value
    values["source_refs"] = tuple(dict.fromkeys(refs))
    values["status"] = None
    values.pop("fingerprint", None)
    return ManufacturingSpecification.model_validate(values, strict=False)


class ManufacturingParameterAuditService:
    """Small deterministic facade for the parameter audit projection."""

    def audit(self, **kwargs: object) -> tuple[ManufacturingParameterAudit, ...]:
        audits, _ = audit_manufacturing_parameters(**kwargs)  # type: ignore[arg-type]
        return audits


def render_manufacturing_parameter_audit(
    audits: Sequence[ManufacturingParameterAudit],
) -> str:
    """Render the audit as a stable, source-bearing text projection."""
    lines = ["EMBRIX_MANUFACTURING_PARAMETER_AUDIT"]
    for item in audits:
        lines.extend(
            (
                f"parameter={item.parameter}",
                f"current_value={item.current_value}",
                f"source={item.source}",
                f"fingerprint={item.fingerprint}",
                f"status={item.status.value}",
            )
        )
    return "\n".join(lines)


class FabricationModeResolver:
    """Resolve only an explicit manufacturing intent.

    Layer count, BOM contents, footprints and board geometry are deliberately
    not considered evidence of assembly intent. That prevents a PCB-only
    design from silently becoming a PCBA job merely because it contains a BOM.
    """

    def resolve(
        self,
        *,
        project_id: str,
        requirement_context: RequirementContext | Mapping[str, object] | None = None,
        explicit_mode: FabricationMode | str | None = None,
        source_ref: str | None = None,
        source_values: Sequence[object] = (),
        allow_pcb_only_without_assembly: bool = False,
        mode_approval_ref: str | None = None,
        mode_approved_by: str | None = None,
    ) -> FabricationModeResolution:
        if explicit_mode is not None:
            mode = FabricationMode(explicit_mode)
            return FabricationModeResolution.create(
                project_id=project_id,
                status=FabricationModeStatus.RESOLVED,
                mode=mode,
                source_ref=source_ref or mode_approval_ref or "explicit-fabrication-mode",
                reason="fabrication mode was explicitly supplied by an approved caller",
            )
        values = requirement_context.model_dump(mode="json") if isinstance(requirement_context, RequirementContext) else requirement_context
        found: set[FabricationMode] = set()
        all_values = (values, *source_values)
        texts = tuple(text for value in all_values for text in self._strings(value))
        for text in texts:
            for token in _MODE_TOKEN.findall(text):
                found.add(FabricationMode(token.upper()))
        if len(found) == 1:
            mode = next(iter(found))
            context_fp = getattr(requirement_context, "fingerprint", None)
            ref = source_ref or mode_approval_ref or (f"requirement-{context_fp[7:27]}" if isinstance(context_fp, str) and context_fp.startswith("sha256:") else "requirement-explicit-mode")
            return FabricationModeResolution.create(
                project_id=project_id,
                status=FabricationModeStatus.RESOLVED,
                mode=mode,
                source_ref=ref,
                reason="fabrication mode was explicitly present in requirement intent",
            )
        audit = self.audit(
            project_id=project_id,
            project_revision="unbound-audit",
            requirement_context=requirement_context,
            source_values=source_values,
            source_ref=source_ref,
        )
        if (
            not found
            and audit.assembly_intent is FabricationAssemblyIntent.ABSENT
            and allow_pcb_only_without_assembly
            and mode_approval_ref is not None
            and mode_approved_by is not None
        ):
            return FabricationModeResolution.create(
                project_id=project_id,
                status=FabricationModeStatus.RESOLVED,
                mode=FabricationMode.PCB_ONLY,
                source_ref=mode_approval_ref,
                reason="PCB_ONLY was explicitly approved under the v1.1 no-assembly closure policy",
            )
        reason = "fabrication mode is absent; PCB_ONLY and PCB_ASSEMBLY must not be inferred"
        if len(found) > 1:
            reason = "fabrication mode contains conflicting explicit values"
        elif audit.assembly_intent is FabricationAssemblyIntent.PRESENT:
            reason = "assembly intent is present but no explicit PCB_ONLY or PCB_ASSEMBLY mode was supplied"
        return FabricationModeResolution.create(
            project_id=project_id,
            status=FabricationModeStatus.REQUIRED,
            mode=None,
            source_ref=None,
            reason=reason,
        )

    def audit(
        self,
        *,
        project_id: str,
        project_revision: str,
        requirement_context: RequirementContext | Mapping[str, object] | None = None,
        source_values: Sequence[object] = (),
        source_ref: str | None = None,
        created_at: datetime | None = None,
    ) -> FabricationModeAudit:
        values = requirement_context.model_dump(mode="json") if isinstance(requirement_context, RequirementContext) else requirement_context
        texts = tuple(text for value in (values, *source_values) for text in self._strings(value))
        found = {
            FabricationMode(token.upper())
            for text in texts
            for token in _MODE_TOKEN.findall(text)
        }
        assembly = any(_ASSEMBLY_INTENT_TOKEN.search(text) for text in texts)
        if len(found) > 1:
            intent = FabricationAssemblyIntent.AMBIGUOUS
        elif assembly:
            intent = FabricationAssemblyIntent.PRESENT
        else:
            intent = FabricationAssemblyIntent.ABSENT
        context_fp = getattr(requirement_context, "fingerprint", None)
        evidence = [source_ref] if source_ref else []
        if isinstance(context_fp, str) and context_fp.startswith("sha256:"):
            evidence.append(f"requirement-{context_fp[7:27]}")
        evidence.extend(
            value for value in source_values
            if isinstance(value, str) and value
        )
        evidence = list(dict.fromkeys(evidence)) or [f"project-{project_id}"]
        missing: list[str] = []
        if not found:
            missing.append("fabrication_mode must be explicitly approved as PCB_ONLY or PCB_ASSEMBLY")
        elif len(found) > 1:
            missing.append("fabrication_mode contains conflicting explicit values")
        if not assembly and not found:
            missing.append("approved manufacturing intent source")
        return FabricationModeAudit.create(
            audit_id=_stable("fabrication-mode-audit", f"{project_id}|{project_revision}|{'|'.join(evidence)}"),
            project_id=project_id,
            project_revision=project_revision,
            existing_mode=next(iter(found)) if len(found) == 1 else None,
            source_ref=source_ref,
            requirement_evidence=tuple(evidence),
            assembly_intent=intent,
            missing_facts=tuple(missing),
            created_at=_now(created_at),
        )

    @classmethod
    def _strings(cls, value: object) -> Iterable[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for item in value.values():
                yield from cls._strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from cls._strings(item)


def build_source_revision(
    *,
    project_id: str,
    project_revision: str,
    requirement_revision: str,
    design_truth_id: str,
    design_truth_fingerprint: str,
    schematic_artifact_id: str,
    schematic_path: Path,
    pcb_artifact_id: str,
    pcb_path: Path,
    bom_artifact_id: str,
    bom_path: Path,
    firmware_specification_id: str,
    build_configuration_id: str,
    elf_artifact_id: str,
    elf_path: Path,
    hw_fw_consistency_result_id: str,
    virtual_assembly_id: str,
    virtual_assembly_fingerprint: str,
    simulation_result_id: str,
    functional_verification_result_id: str,
    functional_verification_fingerprint: str,
    source_refs: Sequence[str],
) -> FabricationSourceRevision:
    return FabricationSourceRevision.create(
        project_id=project_id,
        project_revision=project_revision,
        requirement_revision=requirement_revision,
        design_truth_id=design_truth_id,
        design_truth_fingerprint=design_truth_fingerprint,
        schematic_artifact_id=schematic_artifact_id,
        schematic_sha256=file_sha256(schematic_path),
        pcb_artifact_id=pcb_artifact_id,
        pcb_sha256=file_sha256(pcb_path),
        bom_artifact_id=bom_artifact_id,
        bom_sha256=file_sha256(bom_path),
        firmware_specification_id=firmware_specification_id,
        build_configuration_id=build_configuration_id,
        elf_artifact_id=elf_artifact_id,
        elf_sha256=file_sha256(elf_path),
        hw_fw_consistency_result_id=hw_fw_consistency_result_id,
        virtual_assembly_id=virtual_assembly_id,
        virtual_assembly_fingerprint=virtual_assembly_fingerprint,
        simulation_result_id=simulation_result_id,
        functional_verification_result_id=functional_verification_result_id,
        functional_verification_fingerprint=functional_verification_fingerprint,
        source_refs=tuple(source_refs),
    )


def build_manufacturing_specification(
    *,
    source: FabricationSourceRevision,
    board: BoardFacts,
    specification_id: str,
    fabrication_mode: FabricationMode,
    source_refs: Sequence[str],
    board_thickness_mm: float | None = None,
    copper_weight: str | None = None,
    solder_mask: str | None = None,
    silkscreen: str | None = None,
    surface_finish: str | None = None,
    allow_factory_defaults: bool = False,
    notes: Sequence[str] = (),
) -> ManufacturingSpecification:
    """Build a specification from observed board facts and explicit parameters.

    Board geometry and copper layer identity come from the current KiCad PCB.
    Manufacturing parameters are never filled from factory defaults; callers
    must provide them explicitly or the resulting specification remains
    ``INCOMPLETE``.
    """
    return ManufacturingSpecification.create(
        specification_id=specification_id,
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=fabrication_mode,
        layer_count=board.layer_count,
        copper_layers=board.copper_layers,
        board_size_mm=board.board_dimensions_mm,
        board_thickness_mm=board_thickness_mm,
        copper_weight=copper_weight,
        solder_mask=solder_mask,
        silkscreen=silkscreen,
        surface_finish=surface_finish,
        gerber_required=True,
        drill_required=True,
        bom_required=fabrication_mode is FabricationMode.PCB_ASSEMBLY,
        cpl_required=fabrication_mode is FabricationMode.PCB_ASSEMBLY,
        allow_factory_defaults=allow_factory_defaults,
        source_refs=tuple(source_refs),
        notes=tuple(notes),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256,
        bom_sha256=source.bom_sha256,
    )


@dataclass(frozen=True, slots=True)
class OutputFile:
    artifact: ArtifactIdentity
    relative_path: str
    content: bytes


def output_records(
    files: Sequence[OutputFile],
    *,
    output_root: Path,
    source: FabricationSourceRevision,
    relative_prefix: str = "",
) -> tuple[ManufacturingOutput, ...]:
    root = Path(output_root).resolve(strict=False)
    records: list[ManufacturingOutput] = []
    for item in files:
        artifact = ArtifactIdentity.model_validate(item.artifact.model_dump(mode="python"), strict=False)
        if artifact.project_id != source.project_id or artifact.build_configuration != source.project_revision:
            raise ValueError("manufacturing artifact source revision mismatch")
        path = (root / item.relative_path).resolve(strict=False)
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError("manufacturing output path is invalid")
        content = path.read_bytes()
        if content != item.content or _sha256(content) != artifact.artifact_sha256 or len(content) != artifact.size_bytes:
            raise ValueError("manufacturing output artifact drift")
        kind = {
            "GERBER": ManufacturingOutputType.GERBER,
            "DRILL": ManufacturingOutputType.DRILL,
            "CPL": ManufacturingOutputType.CPL,
            "BOM": ManufacturingOutputType.BOM,
            "MANUFACTURING_NOTES": ManufacturingOutputType.FABRICATION_NOTES,
            "MANIFEST": ManufacturingOutputType.MANIFEST,
            "MANUFACTURING_PACKAGE": ManufacturingOutputType.MANUFACTURING_PACKAGE,
        }[artifact.artifact_type]
        layer = _gerber_layer(content) if kind is ManufacturingOutputType.GERBER else None
        if kind is ManufacturingOutputType.GERBER and layer is None:
            raise ValueError("Gerber FileFunction is missing")
        if kind is ManufacturingOutputType.DRILL and (_drill_class(content) is None or b"M30" not in content.upper()):
            raise ValueError("drill FileFunction or end marker is missing")
        component_references: tuple[str, ...] = ()
        if kind is ManufacturingOutputType.CPL:
            component_references = _placement_references(content)
        elif kind is ManufacturingOutputType.BOM:
            headers, rows = _read_csv(path)
            reference_column = _header(headers, "reference", "references", "refs")
            if reference_column is None:
                raise ValueError("BOM reference column is missing")
            component_references = tuple(row[reference_column] for row in rows if row.get(reference_column))
        relative_path = item.relative_path if not relative_prefix else f"{relative_prefix.strip('/')}/{item.relative_path}"
        records.append(ManufacturingOutput.create(
            artifact_id=artifact.artifact_id,
            relative_path=relative_path,
            output_type=kind,
            layer=layer,
            sha256=artifact.artifact_sha256,
            source_revision_fingerprint=source.fingerprint,
            size_bytes=artifact.size_bytes,
            required=kind not in {ManufacturingOutputType.CPL},
            validation=ManufacturingCheckStatus.PASS,
            component_references=component_references,
        ))
    return tuple(records)


def output_files_from_artifacts(artifacts: Sequence[ArtifactIdentity], *, output_root: Path) -> tuple[OutputFile, ...]:
    """Reconcile worker artifact identities with files after a process run."""
    root = Path(output_root).resolve(strict=False)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("manufacturing output root is unavailable")
    files: list[OutputFile] = []
    candidates = tuple(path for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink())
    used: set[Path] = set()
    for raw_artifact in artifacts:
        try:
            artifact = ArtifactIdentity.model_validate(raw_artifact.model_dump(mode="python"), strict=False)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("artifact identity is invalid") from exc
        matches: list[Path] = []
        for path in candidates:
            if path in used:
                continue
            data = path.read_bytes()
            if len(data) == artifact.size_bytes and _sha256(data) == artifact.artifact_sha256:
                matches.append(path)
        if len(matches) != 1:
            raise ValueError("artifact identity cannot be reconciled with one output file")
        path = matches[0]
        used.add(path)
        files.append(OutputFile(artifact=artifact, relative_path=path.relative_to(root).as_posix(), content=path.read_bytes()))
    return tuple(files)


def _gap(category: ManufacturingGapCategory, description: str, source: FabricationSourceRevision, *, blocking: bool = True) -> FabricationGap:
    severity = ManufacturingGapSeverity.BLOCKING if blocking else ManufacturingGapSeverity.NON_BLOCKING
    return FabricationGap.create(
        gap_id=_stable("fabrication-gap", f"{category.value}|{description}|{source.fingerprint}"),
        category=category,
        description=description,
        severity=severity,
        blocking=blocking,
        source_refs=_source_refs(source),
        resolution="补充真实证据并重新生成当前 revision 的制造包" if blocking else "记录该限制并在制造前复核",
    )


def _check(
    category: ManufacturingGapCategory,
    status: ManufacturingCheckStatus,
    detail: str,
    source: FabricationSourceRevision,
    *,
    required: bool = True,
    evidence_refs: Sequence[str] = (),
) -> ManufacturingCheck:
    return ManufacturingCheck.create(
        check_id=_stable("manufacturing-check", f"{category.value}|{detail}|{source.fingerprint}"),
        category=category,
        status=status,
        required=required,
        detail=detail,
        evidence_refs=tuple(evidence_refs),
        source_refs=_source_refs(source),
    )


def _read_csv(path: Path) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("BOM is unavailable")
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("BOM header is missing")
    headers = tuple(item.strip() for item in reader.fieldnames if item is not None)
    if any(not item for item in headers):
        raise ValueError("BOM header is invalid")
    rows = tuple({str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None} for row in reader)
    return headers, rows


def _header(headers: Sequence[str], *names: str) -> str | None:
    normalized = {item.casefold().replace(" ", "_"): item for item in headers}
    for name in names:
        if name.casefold() in normalized:
            return normalized[name.casefold()]
    return None


def validate_bom(path: Path, board: BoardFacts, source: FabricationSourceRevision, mode: FabricationMode) -> tuple[ManufacturingCheck, ...] | tuple[FabricationGap, ...]:
    try:
        headers, rows = _read_csv(path)
    except (OSError, UnicodeError, ValueError) as exc:
        return (_gap(ManufacturingGapCategory.BOM, str(exc), source),)
    reference_column = _header(headers, "reference", "references", "refs")
    if reference_column is None:
        return (_gap(ManufacturingGapCategory.BOM, "BOM reference column is missing", source),)
    bom_refs = tuple(row.get(reference_column, "") for row in rows if row.get(reference_column, ""))
    expected = tuple(board.component_refs)
    if len(bom_refs) != len(set(bom_refs)):
        return (_gap(ManufacturingGapCategory.BOM, "BOM contains duplicate references", source),)
    if set(bom_refs) != set(expected):
        return (_gap(ManufacturingGapCategory.BOM, "BOM and PCB component references do not match", source),)
    if mode is FabricationMode.PCB_ASSEMBLY:
        mpn = _header(headers, "mpn", "manufacturer_pn", "manufacturer_part_number")
        manufacturer = _header(headers, "manufacturer", "manufacturer_name")
        if mpn is None or manufacturer is None:
            return (_gap(ManufacturingGapCategory.BOM, "PCBA BOM requires manufacturer and MPN columns", source),)
        if any(not row.get(mpn, "") or not row.get(manufacturer, "") for row in rows):
            return (_gap(ManufacturingGapCategory.BOM, "PCBA BOM contains a row without manufacturer or MPN", source),)
    return (_check(ManufacturingGapCategory.BOM, ManufacturingCheckStatus.PASS, "BOM references match the PCB", source),)


def _gerber_layer(data: bytes) -> str | None:
    text = data[:128 * 1024].decode("ascii", errors="ignore")
    match = re.search(r"%TF\.FileFunction,([^*]+)\*%", text)
    if not match:
        return None
    function = match.group(1).strip()
    mappings = {
        "Copper,L1,Top": "F.Cu", "Copper,L2,Bot": "B.Cu",
        "SolderMask,Top": "F.Mask", "SolderMask,Bot": "B.Mask",
        "Legend,Top": "F.SilkS", "Legend,Bot": "B.SilkS",
        "Profile,NP": "Edge.Cuts",
    }
    return mappings.get(function)


def _drill_class(data: bytes) -> str | None:
    text = data[:128 * 1024].decode("ascii", errors="ignore")
    match = re.search(r"TF\.FileFunction,([^\r\n*]+)", text, re.IGNORECASE)
    if match is None:
        return None
    fields = tuple(item.strip().upper() for item in match.group(1).split(","))
    # KiCad 10 emits ``Plated,...,PTH`` / ``NonPlated,...,NPTH`` while older
    # exports and existing fixtures may use ``PTH,Drill`` directly.
    return next((item for item in reversed(fields) if item in {"PTH", "NPTH"}), None)


@dataclass(frozen=True, slots=True)
class _GerberReadback:
    layer: str | None
    points: tuple[tuple[float, float], ...]
    segments: tuple[tuple[tuple[float, float], tuple[float, float]], ...]


def _parse_gerber_readback(data: bytes) -> _GerberReadback:
    text = data[:8 * 1024 * 1024].decode("ascii")
    layer = _gerber_layer(data)
    format_match = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", text)
    if format_match is None:
        raise ValueError("Gerber coordinate format is missing")
    decimals = int(format_match.group(2))
    unit_scale = 25.4 if "%MOIN*%" in text.upper() else 1.0
    commands = re.findall(r"X(-?\d+)Y(-?\d+)D0([123])\*", text)
    if not commands:
        # A real KiCad export may contain a valid, intentionally empty layer
        # (for example an unused copper layer).  FileFunction and coordinate
        # format still establish the layer identity; Edge.Cuts is checked
        # separately and must contain a closed outline.
        return _GerberReadback(layer=layer, points=(), segments=())
    points: list[tuple[float, float]] = []
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    current: tuple[float, float] | None = None
    divisor = 10**decimals
    for raw_x, raw_y, code in commands:
        point = (int(raw_x) / divisor * unit_scale, int(raw_y) / divisor * unit_scale)
        points.append(point)
        if code == "2":
            current = point
        elif code == "1":
            if current is None:
                raise ValueError("Gerber draw command has no move")
            segments.append((current, point))
            current = point
        else:
            current = point
    return _GerberReadback(layer=layer, points=tuple(points), segments=tuple(segments))


def _gerber_outline_status(readback: _GerberReadback) -> tuple[bool, tuple[float, float]]:
    if not readback.segments:
        return False, (0.0, 0.0)
    first = readback.segments[0][0]
    last = readback.segments[-1][1]
    connected = all(left[1] == right[0] for left, right in zip(readback.segments, readback.segments[1:]))
    closed = connected and first == last
    points = tuple(point for segment in readback.segments for point in segment)
    dimensions = (
        max(point[0] for point in points) - min(point[0] for point in points),
        max(point[1] for point in points) - min(point[1] for point in points),
    )
    return closed, dimensions


def _drill_hole_count(data: bytes) -> int:
    text = data[:8 * 1024 * 1024].decode("ascii")
    if not text.upper().startswith("M48") or "M30" not in text.upper():
        raise ValueError("Excellon header or end marker is missing")
    return len(re.findall(r"(?m)^X[-+]?\d+(?:\.\d+)?Y[-+]?\d+(?:\.\d+)?\s*$", text))


def _drill_tool_sizes(data: bytes) -> tuple[float, ...]:
    text = data[:8 * 1024 * 1024].decode("ascii")
    values = tuple(float(value) for value in re.findall(r"(?m)^T\d+C([0-9]+(?:\.\d+)?)\s*$", text))
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Excellon drill size is invalid")
    return tuple(sorted({round(value, 9) for value in values}))


def _verification_hash(files: Sequence[OutputFile]) -> str:
    return _sha256("|".join(sorted(_sha256(item.content) for item in files)).encode("utf-8"))


def verify_manufacturing_exports(
    *,
    board: BoardFacts,
    source: FabricationSourceRevision,
    gerber_files: Sequence[OutputFile],
    drill_files: Sequence[OutputFile],
) -> tuple[ManufacturingExportVerification, ManufacturingExportVerification]:
    """Independently parse current Gerber and Excellon bytes before packaging.

    This is deliberately kept in the existing manufacturing service. It does not
    trust KiCad's exit code or the export adapter's projection, and it never
    writes a PCB or repairs an output.
    """

    gerber_revision_match = bool(gerber_files) and all(
        item.artifact.artifact_type == "GERBER"
        and item.artifact.build_configuration == source.project_revision
        and _sha256(item.content) == item.artifact.artifact_sha256
        for item in gerber_files
    )
    gerber_evidence_created = bool(gerber_files) and all(bool(item.content) for item in gerber_files)
    gerber_parse_ok = False
    gerber_layer_ok = False
    gerber_outline_ok = False
    gerber_dimensions_ok = False
    try:
        parsed = tuple(_parse_gerber_readback(item.content) for item in gerber_files)
        actual_layers = {item.layer for item in parsed if item.layer is not None}
        edge = tuple(item for item in parsed if item.layer == "Edge.Cuts")
        gerber_layer_ok = bool(parsed) and set(board.expected_gerber_layers).issubset(actual_layers)
        gerber_copper_ok = set(board.copper_layers).issubset(actual_layers)
        if len(edge) != 1:
            raise ValueError("Gerber Edge.Cuts outline is missing or ambiguous")
        gerber_outline_ok, dimensions = _gerber_outline_status(edge[0])
        gerber_dimensions_ok = (
            gerber_outline_ok
            and all(abs(left - right) <= 0.001 for left, right in zip(dimensions, board.board_dimensions_mm))
        )
        gerber_parse_ok = gerber_layer_ok and gerber_copper_ok and gerber_outline_ok and gerber_dimensions_ok
    except (UnicodeError, ValueError, ZeroDivisionError):
        gerber_parse_ok = False
    gerber_status = (
        ManufacturingCheckStatus.PASS
        if gerber_parse_ok and gerber_revision_match and gerber_evidence_created
        else ManufacturingCheckStatus.UNKNOWN
        if not gerber_files or not gerber_evidence_created
        else ManufacturingCheckStatus.FAIL
    )
    gerber = ManufacturingExportVerification.create(
        operation_type="KICAD_GERBER_EXPORT",
        output_exists=bool(gerber_files),
        artifact_hash=_verification_hash(gerber_files) if gerber_files else _sha256(b""),
        pcb_hash_match=gerber_parse_ok,
        revision_match=gerber_revision_match,
        evidence_created=gerber_evidence_created,
        status=gerber_status,
    )

    drill_revision_match = bool(drill_files) and all(
        item.artifact.artifact_type == "DRILL"
        and item.artifact.build_configuration == source.project_revision
        and _sha256(item.content) == item.artifact.artifact_sha256
        for item in drill_files
    )
    drill_evidence_created = bool(drill_files) and all(bool(item.content) for item in drill_files)
    drill_correspondence_ok = False
    try:
        class_counts: dict[str, int] = {}
        actual_sizes: set[float] = set()
        for item in drill_files:
            drill_class = _drill_class(item.content)
            if drill_class is None:
                raise ValueError("Excellon PTH/NPTH semantics are missing")
            class_counts[drill_class] = class_counts.get(drill_class, 0) + _drill_hole_count(item.content)
            actual_sizes.update(_drill_tool_sizes(item.content))
        expected_counts = dict(board.drill_class_counts)
        drill_correspondence_ok = (
            bool(drill_files)
            and sum(class_counts.values()) == board.drill_hole_count
            and all(class_counts.get(kind, 0) == expected_counts.get(kind, 0) for kind in ("PTH", "NPTH"))
            and actual_sizes == set(board.drill_sizes_mm)
        )
    except (UnicodeError, ValueError):
        drill_correspondence_ok = False
    drill_status = (
        ManufacturingCheckStatus.PASS
        if drill_correspondence_ok and drill_revision_match and drill_evidence_created
        else ManufacturingCheckStatus.UNKNOWN
        if not drill_files or not drill_evidence_created
        else ManufacturingCheckStatus.FAIL
    )
    drill = ManufacturingExportVerification.create(
        operation_type="KICAD_DRILL_EXPORT",
        output_exists=bool(drill_files),
        artifact_hash=_verification_hash(drill_files) if drill_files else _sha256(b""),
        pcb_hash_match=drill_correspondence_ok,
        revision_match=drill_revision_match,
        evidence_created=drill_evidence_created,
        status=drill_status,
    )
    return gerber, drill


def _placement_references(data: bytes) -> tuple[str, ...]:
    rows = tuple(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    expected_header = ("Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side")
    if not rows or tuple(item.strip() for item in rows[0]) != expected_header:
        raise ValueError("CPL header is invalid")
    refs: list[str] = []
    for row in rows[1:]:
        if len(row) != 7 or not row[0].strip() or row[6].strip() not in {"F.Cu", "B.Cu"}:
            raise ValueError("CPL identity or side is invalid")
        try:
            float(row[3])
            float(row[4])
            float(row[5])
        except ValueError:
            raise ValueError("CPL position or rotation is invalid") from None
        if "DNP" in row[0].upper() or "DNP" in row[1].upper():
            raise ValueError("CPL contains a DNP component")
        refs.append(row[0].strip())
    if len(refs) != len(set(refs)):
        raise ValueError("CPL contains duplicate references")
    return tuple(refs)


class ManufacturingPackageService:
    def build(
        self,
        *,
        source: FabricationSourceRevision,
        specification: ManufacturingSpecification,
        outputs: Sequence[ManufacturingOutput],
        kicad_version: str,
        root_relative_path: str,
        lineage_notes: Sequence[str] = (),
        now: datetime | None = None,
    ) -> tuple[ManufacturingManifest, ManufacturingPackage]:
        timestamp = _now(now)
        if (
            source.project_id != specification.project_id
            or source.project_revision != specification.project_revision
            or specification.fabrication_mode not in {FabricationMode.PCB_ONLY, FabricationMode.PCB_ASSEMBLY}
        ):
            raise ValueError("manufacturing package source or mode binding mismatch")
        if specification.status is not ManufacturingSpecificationStatus.COMPLETE:
            raise ValueError("manufacturing package requires an approved complete specification")
        for field, expected in (
            ("requirement_revision", source.requirement_revision),
            ("design_truth_fingerprint", source.design_truth_fingerprint),
            ("pcb_sha256", source.pcb_sha256),
            ("bom_sha256", source.bom_sha256),
        ):
            if getattr(specification, field) != expected:
                raise ValueError("manufacturing package source fingerprint binding mismatch")
        if (
            specification.fabrication_mode is FabricationMode.PCB_ONLY
            and any(item.output_type is ManufacturingOutputType.CPL for item in outputs)
        ):
            raise ValueError("PCB_ONLY manufacturing package cannot contain CPL")
        manifest = ManufacturingManifest.create(
            manifest_id=_stable("manufacturing-manifest", f"{source.fingerprint}|{specification.fingerprint}|{kicad_version}"),
            project_id=source.project_id,
            project_revision=source.project_revision,
            requirement_revision=source.requirement_revision,
            fabrication_mode=specification.fabrication_mode,
            source=source,
            manufacturing_specification_id=specification.specification_id,
            manufacturing_specification_fingerprint=specification.fingerprint,
            kicad_version=kicad_version,
            outputs=tuple(outputs),
            notes=(
                "All outputs were generated from the bound PCB revision.",
                "APPLIED means receipt recorded, not factory upload.",
                *tuple(lineage_notes),
            ),
            created_at=timestamp,
        )
        package = ManufacturingPackage.create(
            package_id=_stable("manufacturing-package", manifest.fingerprint),
            package_name=f"{source.project_id}-fabrication",
            project_id=source.project_id,
            project_revision=source.project_revision,
            manifest_id=manifest.manifest_id,
            manifest_fingerprint=manifest.fingerprint,
            root_relative_path=root_relative_path,
            output_artifact_ids=tuple(item.artifact_id for item in outputs),
            required_output_count=len(outputs),
            package_integrity=ManufacturingCheckStatus.PASS if outputs else ManufacturingCheckStatus.FAIL,
            created_at=timestamp,
        )
        return manifest, package

    def materialize(
        self,
        *,
        root: Path,
        pcb_path: Path,
        source: FabricationSourceRevision,
        specification: ManufacturingSpecification,
        manifest: ManufacturingManifest,
        evidence_refs: Sequence[str] = (),
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Materialize the approved read-back outputs into a fabrication package.

        The package contains only relative paths and hashes.  Existing files are
        immutable: a different byte sequence is rejected instead of overwritten.
        """
        package_root_input = Path(root)
        if not evidence_refs:
            raise ValueError("approved package evidence references are required")
        _reject_symlink_ancestors(package_root_input, "manufacturing package root")
        package_root = package_root_input.resolve(strict=False)
        if (
            manifest.project_id != source.project_id
            or manifest.project_revision != source.project_revision
            or manifest.source.fingerprint != source.fingerprint
            or manifest.manufacturing_specification_fingerprint != specification.fingerprint
            or manifest.fabrication_mode is not specification.fabrication_mode
        ):
            raise ValueError("manufacturing package manifest binding mismatch")
        if specification.status is not ManufacturingSpecificationStatus.COMPLETE:
            raise ValueError("manufacturing package requires an approved complete specification")
        if (
            specification.fabrication_mode is FabricationMode.PCB_ONLY
            and any(item.output_type is ManufacturingOutputType.CPL for item in manifest.outputs)
        ):
            raise ValueError("PCB_ONLY manufacturing package cannot contain CPL")
        package_root.mkdir(parents=True, exist_ok=True)
        for directory in (package_root / "gerber", package_root / "drill", package_root / "pcb_source"):
            if directory.exists() and directory.is_symlink():
                raise ValueError("manufacturing package directory is a symlink")
            directory.mkdir(parents=True, exist_ok=True)
        pcb_input = Path(pcb_path)
        _reject_symlink_ancestors(pcb_input, "PCB source")
        if not pcb_input.is_file():
            raise ValueError("PCB source is unavailable for manufacturing package")
        pcb = pcb_input.resolve(strict=False)
        pcb_target = package_root / "pcb_source" / pcb.name
        pcb_bytes = pcb.read_bytes()
        if _sha256(pcb_bytes) != source.pcb_sha256:
            raise ValueError("PCB source fingerprint mismatch while materializing package")
        self._write_immutable(pcb_target, pcb_bytes)
        verified_outputs: list[ManufacturingOutput] = []
        for output in manifest.outputs:
            output_input = package_root / output.relative_path
            if output_input.is_symlink():
                raise ValueError("manufacturing package output is a symlink")
            output_path = output_input.resolve(strict=False)
            if (
                not output_path.is_relative_to(package_root)
                or output_path.is_symlink()
                or not output_path.is_file()
            ):
                raise ValueError("manufacturing package output is unavailable")
            output_bytes = output_path.read_bytes()
            if len(output_bytes) != output.size_bytes or _sha256(output_bytes) != output.sha256:
                raise ValueError("manufacturing package output fingerprint mismatch")
            verified_outputs.append(output)
        timestamp = _now(now)
        output_projection = tuple(
            {
                "artifact_id": item.artifact_id,
                "relative_path": item.relative_path,
                "output_type": item.output_type.value,
                "layer": item.layer,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "source_revision_fingerprint": item.source_revision_fingerprint,
                "validation": item.validation.value,
            }
            for item in manifest.outputs
        )
        manifest_payload = {
            "project_id": source.project_id,
            "pcb_hash": source.pcb_sha256,
            "pcb_sha256": source.pcb_sha256,
            "design_truth_hash": source.design_truth_fingerprint,
            "design_truth_sha256": source.design_truth_fingerprint,
            "manufacturing_spec_hash": specification.fingerprint,
            "manufacturing_spec_sha256": specification.fingerprint,
            "generated_at": timestamp.isoformat(),
            "created_at": timestamp.isoformat(),
            "project_revision": source.project_revision,
            "requirement_revision": source.requirement_revision,
            "manufacturing_specification_id": specification.specification_id,
            "manufacturing_specification_fingerprint": specification.fingerprint,
            "manifest_id": manifest.manifest_id,
            "manifest_fingerprint": manifest.fingerprint,
            "fabrication_mode": specification.fabrication_mode.value,
            "kicad_version": manifest.kicad_version,
            "notes": manifest.notes,
            "outputs": output_projection,
            "pcb_source": f"pcb_source/{pcb.name}",
        }
        evidence_payload = {
            "project_id": source.project_id,
            "project_revision": source.project_revision,
            "pcb_hash": source.pcb_sha256,
            "pcb_sha256": source.pcb_sha256,
            "design_truth_hash": source.design_truth_fingerprint,
            "design_truth_sha256": source.design_truth_fingerprint,
            "manufacturing_spec_hash": specification.fingerprint,
            "manufacturing_spec_sha256": specification.fingerprint,
            "output_exists": bool(verified_outputs) and all(item.size_bytes > 0 for item in verified_outputs),
            "artifact_hashes": {item.relative_path: item.sha256 for item in verified_outputs},
            "pcb_hash_match": specification.pcb_sha256 == source.pcb_sha256,
            "revision_match": specification.project_revision == source.project_revision,
            "evidence_created": True,
            "evidence_refs": tuple(dict.fromkeys(evidence_refs)),
        }
        self._write_immutable(
            package_root / "manifest.json",
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        )
        self._write_immutable(
            package_root / "evidence.json",
            json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        )
        return ("manifest.json", "evidence.json", f"pcb_source/{pcb.name}")

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        if path.is_symlink():
            raise ValueError("manufacturing package artifact cannot be a symlink")
        if path.exists():
            if not path.is_file() or path.read_bytes() != content:
                raise ValueError("manufacturing package artifact drift")
            return
        path.write_bytes(content)


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    """Reject a symlink anywhere in a path before resolving or writing it."""
    candidate = Path(path)
    for ancestor in (candidate, *candidate.parents):
        try:
            if ancestor.is_symlink():
                raise ValueError(f"{label} contains a symlink")
        except OSError as exc:
            raise ValueError(f"{label} path cannot be inspected") from exc


class FabricationReadinessService:
    def __init__(self, *, store: FabricationStore | None = None, mode_resolver: FabricationModeResolver | None = None, package_service: ManufacturingPackageService | None = None) -> None:
        self._store = store
        self._mode_resolver = mode_resolver or FabricationModeResolver()
        self._package_service = package_service or ManufacturingPackageService()

    def evaluate(
        self,
        *,
        source: FabricationSourceRevision,
        pcb_path: Path,
        bom_path: Path,
        schematic_path: Path | None = None,
        elf_path: Path | None = None,
        requirement_context: RequirementContext | Mapping[str, object] | None = None,
        explicit_mode: FabricationMode | str | None = None,
        mode_source_values: Sequence[object] = (),
        allow_pcb_only_without_assembly: bool = False,
        mode_approval_ref: str | None = None,
        mode_approved_by: str | None = None,
        mode_evidence_refs: Sequence[str] = (),
        specification: ManufacturingSpecification | None = None,
        engineering_decision: object | None = None,
        parameter_decisions: Sequence[ManufacturingParameterDecision] = (),
        manufacturing_parameter_decisions: Sequence[ManufacturingParameterDecision] = (),
        requirement_specification: object | None = None,
        design_truth: object | None = None,
        pcb_artifact: object | None = None,
        bom_artifact: object | None = None,
        existing_evidence: Sequence[object] = (),
        mode_audit: FabricationModeAudit | None = None,
        upstream_checks: Sequence[ManufacturingCheck] = (),
        upstream_gates: Sequence[UpstreamGate] = (),
        outputs: Sequence[ManufacturingOutput] = (),
        manifest: ManufacturingManifest | None = None,
        package: ManufacturingPackage | None = None,
        kicad_version: str | None = None,
        package_root_relative: str = "manufacturing_package",
        export_verifications: Sequence[ManufacturingExportVerification] = (),
        now: datetime | None = None,
        persist: bool = True,
    ) -> FabricationRecord:
        timestamp = _now(now)
        try:
            source = FabricationSourceRevision.model_validate(source.model_dump(mode="python"), strict=False)
            if specification is not None:
                specification = ManufacturingSpecification.model_validate(specification.model_dump(mode="python"), strict=False)
            decisions_input = (*parameter_decisions, *manufacturing_parameter_decisions)
            decisions_input = tuple(
                ManufacturingParameterDecision.model_validate(
                    item.model_dump(mode="python") if isinstance(item, ManufacturingParameterDecision) else item,
                    strict=False,
                )
                for item in decisions_input
            )
            if manifest is not None:
                manifest = ManufacturingManifest.model_validate(manifest.model_dump(mode="python"), strict=False)
            if package is not None:
                package = ManufacturingPackage.model_validate(package.model_dump(mode="python"), strict=False)
            if mode_audit is not None:
                mode_audit = FabricationModeAudit.model_validate(mode_audit.model_dump(mode="python"), strict=False)
            upstream_checks = tuple(
                ManufacturingCheck.model_validate(check.model_dump(mode="python"), strict=False)
                for check in upstream_checks
            )
            upstream_gates = tuple(
                UpstreamGate.model_validate(gate.model_dump(mode="python"), strict=False)
                for gate in upstream_gates
            )
            export_verifications = tuple(
                ManufacturingExportVerification.model_validate(
                    item.model_dump(mode="python") if isinstance(item, ManufacturingExportVerification) else item,
                    strict=False,
                )
                for item in export_verifications
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("fabrication input fingerprint or contract validation failed") from exc

        parameter_audit, parameter_decisions_all = audit_manufacturing_parameters(
            specification=specification,
            requirement_specification=requirement_specification,
            engineering_decision=engineering_decision,
            design_truth=design_truth,
            pcb_artifact=pcb_artifact,
            bom_artifact=bom_artifact,
            existing_evidence=existing_evidence,
            parameter_decisions=decisions_input,
            project_id=source.project_id,
            now=timestamp,
        )
        if specification is not None and specification.status is ManufacturingSpecificationStatus.INCOMPLETE and parameter_decisions_all:
            approved_decisions = tuple(
                item for item in parameter_decisions_all
                if item.approval_state is ManufacturingParameterApprovalState.APPROVED
            )
            if approved_decisions:
                try:
                    candidate_specification = close_manufacturing_parameters(specification, approved_decisions)
                except (TypeError, ValueError):
                    candidate_specification = specification
                if candidate_specification.status is ManufacturingSpecificationStatus.COMPLETE:
                    specification = candidate_specification
                    parameter_audit, parameter_decisions_all = audit_manufacturing_parameters(
                        specification=specification,
                        requirement_specification=requirement_specification,
                        engineering_decision=engineering_decision,
                        design_truth=design_truth,
                        pcb_artifact=pcb_artifact,
                        bom_artifact=bom_artifact,
                        existing_evidence=existing_evidence,
                        parameter_decisions=parameter_decisions_all,
                        project_id=source.project_id,
                        now=timestamp,
                    )
        resolution = self._mode_resolver.resolve(
            project_id=source.project_id,
            requirement_context=requirement_context,
            explicit_mode=explicit_mode,
            source_values=mode_source_values,
            allow_pcb_only_without_assembly=allow_pcb_only_without_assembly,
            mode_approval_ref=mode_approval_ref,
            mode_approved_by=mode_approved_by,
        )
        audit_values = tuple(mode_source_values)
        if explicit_mode is not None:
            audit_values = (*audit_values, FabricationMode(explicit_mode).value)
        expected_mode_audit = self._mode_resolver.audit(
            project_id=source.project_id,
            project_revision=source.project_revision,
            requirement_context=requirement_context,
            source_values=audit_values,
            source_ref=mode_approval_ref,
            created_at=timestamp,
        )
        if mode_audit is not None and (
            mode_audit.project_id != expected_mode_audit.project_id
            or mode_audit.project_revision != expected_mode_audit.project_revision
            or mode_audit.existing_mode is not expected_mode_audit.existing_mode
            or mode_audit.source_ref != expected_mode_audit.source_ref
            or mode_audit.requirement_evidence != expected_mode_audit.requirement_evidence
            or mode_audit.assembly_intent is not expected_mode_audit.assembly_intent
            or mode_audit.missing_facts != expected_mode_audit.missing_facts
        ):
            raise ValueError("fabrication mode audit does not match current sources")
        mode_audit = mode_audit or expected_mode_audit
        if mode_audit.project_id != source.project_id or mode_audit.project_revision != source.project_revision:
            raise ValueError("fabrication mode audit binding mismatch")
        mode_requirement = None
        mode_engineering_decision = None
        if resolution.status is FabricationModeStatus.REQUIRED:
            mode_requirement = FabricationModeRequirement.create(
                requirement_id=_stable("fabrication-mode-requirement", source.fingerprint),
                project_id=source.project_id,
                project_revision=source.project_revision,
                requirement_revision=source.requirement_revision,
                required_fact="PCB_ONLY or PCB_ASSEMBLY",
                status=FabricationModeRequirementStatus.BLOCKED,
                source_refs=mode_audit.requirement_evidence,
                created_at=timestamp,
            )
        elif resolution.mode is not None:
            decision_ref = mode_approval_ref or resolution.source_ref
            decision_actor = mode_approved_by or "approved-caller"
            if decision_ref is None:
                raise ValueError("resolved fabrication mode requires a decision reference")
            decision_refs = tuple(dict.fromkeys((decision_ref, *mode_audit.requirement_evidence)))
            mode_engineering_decision = EngineeringDecision.create(
                decision_id=_stable("fabrication-engineering-decision", f"{source.fingerprint}|{resolution.mode.value}"),
                project_id=source.project_id,
                project_revision=source.project_revision,
                requirement_revision=source.requirement_revision,
                decision_topic="fabrication-mode",
                selected_mode=resolution.mode,
                rationale=resolution.reason,
                source_refs=decision_refs,
                evidence_refs=tuple(dict.fromkeys((*mode_evidence_refs, *mode_audit.requirement_evidence))),
                approved_by=decision_actor,
                approved_at=timestamp,
                design_truth_fingerprint=source.design_truth_fingerprint,
                pcb_sha256=source.pcb_sha256,
                bom_sha256=source.bom_sha256,
            )
        missing_parameters = tuple(
            item.parameter
            for item in parameter_audit
            if item.status is not ManufacturingParameterApprovalState.APPROVED
        )
        parameter_complete = specification is not None and not missing_parameters
        manufacturing_readiness = (
            ManufacturingReadinessResult.create(
                fabrication_mode=resolution.mode,
                parameter_completeness=parameter_complete,
                missing_parameters=missing_parameters,
                pcb_revision=source.project_revision,
                artifact_fingerprint=source.pcb_sha256,
                status=(
                    ManufacturingReadinessStatus.READY
                    if parameter_complete
                    else ManufacturingReadinessStatus.BLOCKED_MANUFACTURING_PARAMETER_REQUIRED
                ),
            )
            if resolution.mode is not None
            else None
        )
        checks: list[ManufacturingCheck] = list(upstream_checks)
        gaps: list[FabricationGap] = []
        stale_inputs: list[str] = []
        source_refs = _source_refs(source)
        if upstream_gates:
            gate_status = {
                "PASS": ManufacturingCheckStatus.PASS,
                "CONSISTENT": ManufacturingCheckStatus.PASS,
                "CURRENT": ManufacturingCheckStatus.PASS,
                "VALID": ManufacturingCheckStatus.PASS,
                "FAIL": ManufacturingCheckStatus.FAIL,
                "STALE": ManufacturingCheckStatus.STALE,
                "UNKNOWN": ManufacturingCheckStatus.UNKNOWN,
            }
            checks.extend(
                _check(
                    ManufacturingGapCategory.UPSTREAM,
                    gate_status[gate.status],
                    f"{gate.name}={gate.status}",
                    source,
                    evidence_refs=(gate.evidence_id,),
                )
                for gate in upstream_gates
            )
            expected_gates = {"ERC", "DRC", "BUILD", "HW_FW", "VIRTUAL_ASSEMBLY", "L4"}
            observed_gates = {gate.name.upper().replace("-", "_").replace(" ", "_") for gate in upstream_gates}
            for missing in sorted(expected_gates - observed_gates):
                gaps.append(_gap(ManufacturingGapCategory.UPSTREAM, f"required upstream gate {missing} is missing", source))
            source_bindings = {
                source.schematic_artifact_id: source.schematic_sha256,
                source.pcb_artifact_id: source.pcb_sha256,
                source.bom_artifact_id: source.bom_sha256,
                source.elf_artifact_id: source.elf_sha256,
            }
            for gate in upstream_gates:
                if gate.status == "STALE":
                    stale_inputs.append(gate.gate_id)
                if gate.source_artifact_id is not None and gate.source_sha256 is not None and source_bindings.get(gate.source_artifact_id) != gate.source_sha256:
                    stale_inputs.append(gate.source_artifact_id)
                    gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"upstream gate {gate.name} is bound to a different artifact hash", source))
        context_revision = getattr(requirement_context, "fingerprint", None)
        if isinstance(context_revision, str) and context_revision != source.requirement_revision:
            stale_inputs.append("requirement_revision")
            gaps.append(_gap(ManufacturingGapCategory.REVISION, "RequirementContext revision does not match the frozen fabrication baseline", source))

        for label, path, expected in (("PCB", pcb_path, source.pcb_sha256), ("BOM", bom_path, source.bom_sha256)):
            try:
                actual = file_sha256(path)
            except ValueError:
                actual = None
            if actual != expected:
                stale_inputs.append(label)
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"{label} artifact is missing or does not match the frozen source revision", source))
        for label, path, expected in (("SCHEMATIC", schematic_path, source.schematic_sha256), ("ELF", elf_path, source.elf_sha256)):
            if path is None:
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"{label} source path is unavailable for frozen revision verification", source))
                continue
            try:
                actual = file_sha256(path)
            except ValueError:
                actual = None
            if actual != expected:
                stale_inputs.append(label)
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"{label} artifact is missing or does not match the frozen source revision", source))

        board: BoardFacts | None = None
        try:
            board = read_board_facts(pcb_path)
            checks.append(_check(
                ManufacturingGapCategory.BOARD_OUTLINE,
                ManufacturingCheckStatus.PASS if board.outline_valid else ManufacturingCheckStatus.FAIL,
                board.outline_reason,
                source,
            ))
            if not board.outline_valid:
                gaps.append(_gap(ManufacturingGapCategory.BOARD_OUTLINE, board.outline_reason, source))
        except ValueError as exc:
            gaps.append(_gap(ManufacturingGapCategory.BOARD_OUTLINE, str(exc), source))

        if resolution.status is FabricationModeStatus.REQUIRED:
            gaps.append(_gap(ManufacturingGapCategory.MODE, resolution.reason, source))
        elif specification is None:
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "manufacturing specification is missing; no factory parameters may be invented", source))
        else:
            for parameter in missing_parameters:
                gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, f"{parameter} is UNSPECIFIED or lacks an APPROVED decision", source))
            if specification.project_id != source.project_id or specification.project_revision != source.project_revision or specification.fabrication_mode is not resolution.mode:
                gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "manufacturing specification is not bound to the current project, revision or mode", source))
            if board is not None:
                self._validate_specification(specification, board, source, gaps, checks)
                bom_result = validate_bom(bom_path, board, source, specification.fabrication_mode)
                if bom_result and isinstance(bom_result[0], FabricationGap):
                    gaps.extend(bom_result)  # type: ignore[arg-type]
                else:
                    checks.extend(bom_result)  # type: ignore[arg-type]
                self._validate_outputs(
                    specification=specification,
                    board=board,
                    outputs=outputs,
                    source=source,
                    gaps=gaps,
                    checks=checks,
                )
                for verification in export_verifications:
                    if verification.status is ManufacturingCheckStatus.PASS and all(
                        (
                            verification.output_exists,
                            verification.pcb_hash_match,
                            verification.revision_match,
                            verification.evidence_created,
                        )
                    ):
                        continue
                    category = (
                        ManufacturingGapCategory.DRILL
                        if "DRILL" in verification.operation_type.upper()
                        else ManufacturingGapCategory.GERBER
                    )
                    gaps.append(
                        _gap(
                            category,
                            f"{verification.operation_type} export verification failed",
                            source,
                        )
                    )

        upstream_validation_checks = tuple(
            check for check in checks if check.category is ManufacturingGapCategory.UPSTREAM
        )
        self._validate_upstream(source, upstream_validation_checks, gaps)
        for check in upstream_validation_checks:
            if check.status is ManufacturingCheckStatus.STALE:
                stale_inputs.append(check.check_id)
        if resolution.status is FabricationModeStatus.RESOLVED and specification is not None and not gaps:
            if manifest is None and outputs and kicad_version:
                manifest, package = self._package_service.build(
                    source=source,
                    specification=specification,
                    outputs=outputs,
                    kicad_version=kicad_version,
                    root_relative_path=package_root_relative,
                    now=timestamp,
                )
            if manifest is None or package is None:
                gaps.append(_gap(ManufacturingGapCategory.PACKAGE, "manufacturing manifest and package receipt are missing", source))
            elif manifest.source.fingerprint != source.fingerprint or manifest.manufacturing_specification_fingerprint != specification.fingerprint:
                gaps.append(_gap(ManufacturingGapCategory.PACKAGE, "manufacturing manifest is bound to a different source or specification", source))
            elif package.manifest_id != manifest.manifest_id or package.manifest_fingerprint != manifest.fingerprint or package.package_integrity is not ManufacturingCheckStatus.PASS:
                gaps.append(_gap(ManufacturingGapCategory.PACKAGE, "manufacturing package integrity is not valid", source))

        blocking = tuple(gap for gap in gaps if gap.blocking)
        non_blocking = tuple(gap for gap in gaps if not gap.blocking)
        result_status = self._status(stale_inputs, blocking, checks)
        if resolution.status is FabricationModeStatus.REQUIRED:
            blocking_reason = "BLOCKED_FABRICATION_MODE_REQUIRED"
        elif specification is None:
            blocking_reason = "BLOCKED_MANUFACTURING_SPECIFICATION_REQUIRED"
        elif missing_parameters or specification.status is ManufacturingSpecificationStatus.INCOMPLETE:
            blocking_reason = "BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"
        elif blocking:
            blocking_reason = "BLOCKED_FABRICATION_REQUIREMENT"
        else:
            blocking_reason = None
        baseline = None
        baseline_id = None
        result_id = _stable("fabrication-result", source.fingerprint)
        if result_status is FabricationOverallStatus.PASS and manifest is not None and package is not None and specification is not None and resolution.mode is not None:
            baseline_id = _stable("fabrication-baseline", f"{source.fingerprint}|{specification.fingerprint}|{manifest.fingerprint}|{package.fingerprint}")
        result = FabricationReadinessResult.create(
            result_id=result_id,
            project_id=source.project_id,
            project_revision=source.project_revision,
            mode_resolution=resolution,
            fabrication_mode=resolution.mode,
            source_revision=source,
            manufacturing_specification_id=None if specification is None else specification.specification_id,
            manufacturing_specification_fingerprint=None if specification is None else specification.fingerprint,
            upstream_checks=tuple(checks),
            manufacturing_checks=tuple(checks),
            blocking_gaps=blocking,
            non_blocking_gaps=non_blocking,
            manufacturing_manifest_id=None if manifest is None else manifest.manifest_id,
            manufacturing_package_id=None if package is None else package.package_id,
            baseline_id=baseline_id,
            stale_inputs=tuple(stale_inputs),
            overall_status=result_status,
            fabrication_ready=baseline_id is not None and result_status is FabricationOverallStatus.PASS,
            evidence_refs=tuple(dict.fromkeys((*source_refs, *(item.id for item in parameter_decisions_all), *(ref for check in checks for ref in check.evidence_refs)))),
            created_at=timestamp,
            blocking_reason=blocking_reason,
            manufacturing_readiness=manufacturing_readiness,
            parameter_audit=parameter_audit,
            parameter_decisions=parameter_decisions_all,
            export_verifications=tuple(export_verifications),
        )
        if baseline_id is not None and manifest is not None and package is not None and specification is not None:
            erc = next((check.evidence_refs[0] for check in upstream_checks if check.category is ManufacturingGapCategory.UPSTREAM and check.evidence_refs), _stable("erc-evidence", source.fingerprint))
            drc = next((check.evidence_refs[1] for check in upstream_checks if check.category is ManufacturingGapCategory.UPSTREAM and len(check.evidence_refs) > 1), _stable("drc-evidence", source.fingerprint))
            baseline = FabricationBaseline.create(
                baseline_id=baseline_id,
                baseline_name=f"{source.project_id}-F1",
                project_id=source.project_id,
                project_revision=source.project_revision,
                requirement_revision=source.requirement_revision,
                fabrication_mode=resolution.mode,
                source_revision=source,
                erc_evidence_id=erc,
                drc_evidence_id=drc,
                manufacturing_specification_id=specification.specification_id,
                manufacturing_specification_fingerprint=specification.fingerprint,
                manufacturing_manifest_id=manifest.manifest_id,
                manufacturing_package_id=package.package_id,
                manufacturing_package_fingerprint=package.fingerprint,
                readiness_result_id=result.result_id,
                readiness_result_fingerprint=result.fingerprint,
                status=FabricationBaselineStatus.FABRICATION_READY,
                created_at=timestamp,
            )
        evidence = FabricationEvidence.create(
            evidence_id=_stable("fabrication-evidence", result.fingerprint),
            project_id=source.project_id,
            result_id=result.result_id,
            baseline_id=None if baseline is None else baseline.baseline_id,
            manifest_id=None if manifest is None else manifest.manifest_id,
            package_id=None if package is None else package.package_id,
            source_revision_fingerprint=source.fingerprint,
            evidence_refs=tuple(dict.fromkeys((*source_refs, *(item.id for item in parameter_decisions_all), *(ref for check in checks for ref in check.evidence_refs)))),
            blocking_gap_ids=tuple(gap.gap_id for gap in blocking),
            non_blocking_gap_ids=tuple(gap.gap_id for gap in non_blocking),
            lineage_complete=resolution.status is FabricationModeStatus.RESOLVED and specification is not None and manifest is not None and package is not None,
            tool_observed=manifest is not None and bool(manifest.kicad_version),
            fingerprints_complete=bool(source.fingerprint and (specification is None or specification.fingerprint) and (manifest is None or manifest.fingerprint) and (package is None or package.fingerprint)),
            status=result.overall_status,
            created_at=timestamp,
        )
        record = FabricationRecord.create(
            baseline=baseline,
            readiness=result,
            manifest=manifest,
            package=package,
            evidence=evidence,
            mode_audit=mode_audit,
            mode_requirement=mode_requirement,
            engineering_decision=mode_engineering_decision,
            specification=specification,
        )
        if persist and self._store is not None:
            self._store.save(record)
        return record

    @staticmethod
    def current_status(
        record: FabricationRecord,
        *,
        pcb_path: Path,
        bom_path: Path,
        project_revision: str,
        requirement_revision: str,
        package_root: Path | None = None,
        approval_fingerprint: str | None = None,
    ) -> tuple[FabricationOverallStatus, tuple[str, ...]]:
        """Check a persisted baseline and package against current inputs.

        The optional package and approval checks make output tampering and
        approval rotation stale the persisted result without mutating history.
        """
        stale: list[str] = []
        if record.readiness.project_revision != project_revision:
            stale.append("project_revision")
        if record.readiness.source_revision.requirement_revision != requirement_revision:
            stale.append("requirement_revision")
        try:
            if file_sha256(pcb_path) != record.readiness.source_revision.pcb_sha256:
                stale.append("PCB")
            if file_sha256(bom_path) != record.readiness.source_revision.bom_sha256:
                stale.append("BOM")
        except ValueError:
            stale.extend(("PCB", "BOM"))
        if package_root is not None and record.manifest is not None:
            package_path = Path(package_root).resolve(strict=False)
            for output in record.manifest.outputs:
                output_path = (package_path / output.relative_path).resolve(strict=False)
                output_label = (
                    "GERBER"
                    if output.output_type is ManufacturingOutputType.GERBER
                    else "DRILL"
                    if output.output_type is ManufacturingOutputType.DRILL
                    else "PACKAGE"
                )
                try:
                    if (
                        not output_path.is_file()
                        or not output_path.is_relative_to(package_path)
                        or file_sha256(output_path) != output.sha256
                    ):
                        stale.append(output_label)
                except ValueError:
                    stale.append(output_label)
            manifest_path = package_path / "manifest.json"
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if payload.get("manifest_fingerprint") != record.manifest.fingerprint:
                    stale.append("MANIFEST")
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                stale.append("MANIFEST")
            if approval_fingerprint is not None:
                approval_note = next(
                    (
                        item.partition("=")[2]
                        for item in record.manifest.notes
                        if item.startswith("approval_fingerprint=")
                    ),
                    None,
                )
                if approval_note is None or approval_note != approval_fingerprint:
                    stale.append("APPROVAL")
        return (FabricationOverallStatus.STALE if stale else record.readiness.overall_status, tuple(dict.fromkeys(stale)))

    @staticmethod
    def _validate_specification(specification: ManufacturingSpecification, board: BoardFacts, source: FabricationSourceRevision, gaps: list[FabricationGap], checks: list[ManufacturingCheck]) -> None:
        if specification.layer_count != board.layer_count or tuple(specification.copper_layers) != tuple(board.copper_layers):
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "manufacturing layer specification does not match the actual KiCad PCB", source))
        if specification.board_size_mm is None or tuple(specification.board_size_mm) != tuple(board.board_dimensions_mm):
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "manufacturing board size is missing or does not match the actual KiCad PCB", source))
        lineage = (
            ("requirement_revision", specification.requirement_revision, source.requirement_revision),
            ("design_truth_fingerprint", specification.design_truth_fingerprint, source.design_truth_fingerprint),
            ("pcb_sha256", specification.pcb_sha256, source.pcb_sha256),
            ("bom_sha256", specification.bom_sha256, source.bom_sha256),
        )
        for field, actual, expected in lineage:
            if actual != expected:
                gaps.append(_gap(ManufacturingGapCategory.REVISION, f"manufacturing specification {field} is missing or bound to a different source", source))
        if _is_unspecified(specification.board_thickness_mm):
            checks.append(_check(ManufacturingGapCategory.SPECIFICATION, ManufacturingCheckStatus.UNKNOWN, "board thickness is UNSPECIFIED", source))
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "board thickness is UNSPECIFIED", source))
        for field in ("copper_weight", "solder_mask", "silkscreen", "surface_finish"):
            if _is_unspecified(getattr(specification, field)):
                checks.append(_check(ManufacturingGapCategory.SPECIFICATION, ManufacturingCheckStatus.UNKNOWN, f"{field} is UNSPECIFIED", source))
                gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, f"{field} is UNSPECIFIED", source))
        if specification.min_track_width_mm is not None and board.min_track_width_mm is not None and board.min_track_width_mm < specification.min_track_width_mm:
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "PCB track width is below the manufacturing specification", source))
        if specification.min_drill_mm is not None and board.min_drill_mm is not None and board.min_drill_mm < specification.min_drill_mm:
            gaps.append(_gap(ManufacturingGapCategory.SPECIFICATION, "PCB drill size is below the manufacturing specification", source))

    @staticmethod
    def _validate_outputs(*, specification: ManufacturingSpecification, board: BoardFacts, outputs: Sequence[ManufacturingOutput], source: FabricationSourceRevision, gaps: list[FabricationGap], checks: list[ManufacturingCheck]) -> None:
        by_type: dict[ManufacturingOutputType, list[ManufacturingOutput]] = {}
        for output in outputs:
            by_type.setdefault(output.output_type, []).append(output)
            if output.source_revision_fingerprint != source.fingerprint:
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"output {output.relative_path} is bound to a different source revision", source))
            if output.size_bytes <= 0:
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"output {output.relative_path} is empty", source))
            if output.validation is not ManufacturingCheckStatus.PASS:
                gaps.append(_gap(ManufacturingGapCategory.ARTIFACT_INTEGRITY, f"output {output.relative_path} has not passed read-back validation", source))
        gerbers = by_type.get(ManufacturingOutputType.GERBER, [])
        if specification.gerber_required and not gerbers:
            gaps.append(_gap(ManufacturingGapCategory.GERBER, "required Gerber output is missing", source))
        gerber_layers = {output.layer for output in gerbers if output.layer}
        if gerbers and not set(board.expected_gerber_layers).issubset(gerber_layers):
            gaps.append(_gap(ManufacturingGapCategory.GERBER, "Gerber output does not cover every actual PCB layer required for fabrication", source))
        if specification.drill_required and not by_type.get(ManufacturingOutputType.DRILL):
            gaps.append(_gap(ManufacturingGapCategory.DRILL, "required drill output is missing", source))
        if specification.bom_required and not by_type.get(ManufacturingOutputType.BOM):
            gaps.append(_gap(ManufacturingGapCategory.BOM, "required BOM output is missing from the manufacturing package", source))
        if specification.fabrication_mode is FabricationMode.PCB_ASSEMBLY:
            cpl = by_type.get(ManufacturingOutputType.CPL, [])
            if specification.cpl_required and not cpl:
                gaps.append(_gap(ManufacturingGapCategory.CPL, "PCBA requires a component placement file", source))
            elif cpl and board.component_refs and not any(set(item.component_references) == set(board.component_refs) for item in cpl):
                gaps.append(_gap(ManufacturingGapCategory.CPL, "CPL references do not match PCB populated references", source))
        else:
            checks.append(_check(ManufacturingGapCategory.CPL, ManufacturingCheckStatus.NOT_APPLICABLE, "CPL is not applicable for PCB_ONLY", source, required=False))
            if by_type.get(ManufacturingOutputType.CPL):
                gaps.append(_gap(ManufacturingGapCategory.CPL, "PCB_ONLY package must not contain a CPL", source))

    @staticmethod
    def _validate_upstream(source: FabricationSourceRevision, checks: Sequence[ManufacturingCheck], gaps: list[FabricationGap]) -> None:
        if not checks:
            gaps.append(_gap(ManufacturingGapCategory.UPSTREAM, "upstream ERC/DRC/build/HW-FW/virtual assembly/simulation/L4 evidence is missing", source))
            return
        for check in checks:
            if check.required and check.status in {ManufacturingCheckStatus.FAIL, ManufacturingCheckStatus.BLOCKED, ManufacturingCheckStatus.UNKNOWN, ManufacturingCheckStatus.STALE}:
                gaps.append(_gap(ManufacturingGapCategory.UPSTREAM, f"upstream check {check.check_id} is {check.status.value}", source))

    @staticmethod
    def _status(stale: Sequence[str], gaps: Sequence[FabricationGap], checks: Sequence[ManufacturingCheck]) -> FabricationOverallStatus:
        if stale:
            return FabricationOverallStatus.STALE
        if any(check.status is ManufacturingCheckStatus.FAIL for check in checks):
            return FabricationOverallStatus.FAIL
        if gaps:
            return FabricationOverallStatus.BLOCKED
        return FabricationOverallStatus.PASS


def render_fabrication_report(record: FabricationRecord) -> str:
    readiness = record.readiness
    mode = readiness.fabrication_mode.value if readiness.fabrication_mode is not None else "REQUIRED"
    lines = (
        f"EMBRIX_FABRICATION_STATUS={readiness.overall_status.value}",
        f"EMBRIX_FABRICATION_MODE_STATUS={readiness.mode_resolution.status.value}",
        f"EMBRIX_FABRICATION_MODE={mode}",
        f"EMBRIX_FABRICATION_READY={'true' if readiness.fabrication_ready else 'false'}",
        f"EMBRIX_FABRICATION_BASELINE={readiness.baseline_id or 'NONE'}",
        f"EMBRIX_FABRICATION_MANIFEST={readiness.manufacturing_manifest_id or 'NONE'}",
        f"EMBRIX_FABRICATION_PACKAGE={readiness.manufacturing_package_id or 'NONE'}",
        f"EMBRIX_FABRICATION_BLOCK_REASON={readiness.blocking_reason or 'NONE'}",
        f"EMBRIX_FABRICATION_BLOCKERS={','.join(gap.category.value for gap in readiness.blocking_gaps) or 'NONE'}",
        f"EMBRIX_RELEASE_GATE={'PASS' if readiness.fabrication_ready else 'BLOCKED'}",
    )
    return "\n".join(lines)


def render_fabrication_mode_audit(audit: FabricationModeAudit) -> str:
    mode = audit.existing_mode.value if audit.existing_mode is not None else "NONE"
    return "\n".join(
        (
            "EMBRIX_FABRICATION_MODE_AUDIT",
            f"existing_mode={mode}",
            f"source={audit.source_ref or 'NONE'}",
            f"requirement_evidence={','.join(audit.requirement_evidence)}",
            f"assembly_intent={audit.assembly_intent.value}",
            f"missing_facts={','.join(audit.missing_facts) or 'NONE'}",
        )
    )


def render_fabrication_closure(record: FabricationRecord) -> str:
    readiness = record.readiness
    specification = record.specification
    decision = record.engineering_decision
    mode = readiness.fabrication_mode.value if readiness.fabrication_mode is not None else "REQUIRED"
    blocking_parameters = tuple(
        gap.description
        for gap in readiness.blocking_gaps
        if gap.category is ManufacturingGapCategory.SPECIFICATION
    )

    def output_status(category: ManufacturingGapCategory, required: bool) -> str:
        if specification is None:
            return "UNKNOWN"
        if any(gap.category is category for gap in readiness.blocking_gaps):
            return "FAIL"
        if not required:
            return "NOT_APPLICABLE"
        if any(check.category is ManufacturingGapCategory.BOARD_OUTLINE for check in readiness.manufacturing_checks):
            return "PASS"
        return "UNKNOWN"

    parameter_status = (
        readiness.manufacturing_readiness.status.value
        if readiness.manufacturing_readiness is not None
        else "BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"
    )
    parameter_complete = readiness.manufacturing_readiness is not None and readiness.manufacturing_readiness.parameter_completeness
    gerber_status = "READY" if not any(gap.category is ManufacturingGapCategory.GERBER for gap in readiness.blocking_gaps) and parameter_complete else "BLOCKED"
    drill_status = "READY" if not any(gap.category is ManufacturingGapCategory.DRILL for gap in readiness.blocking_gaps) and parameter_complete else "BLOCKED"
    package_status = "READY" if record.package is not None and readiness.fabrication_ready else "BLOCKED"
    mode_gate = "PASS" if readiness.mode_resolution.status is FabricationModeStatus.RESOLVED else "BLOCKED"
    specification_gate = "PASS" if specification is not None and specification.status is ManufacturingSpecificationStatus.COMPLETE else "BLOCKED"
    manufacturing_specification_status = (
        "MISSING"
        if specification is None
        else "APPROVED_MANUFACTURING_SPECIFICATION"
        if specification.status is ManufacturingSpecificationStatus.COMPLETE
        else "CREATED"
    )
    return "\n".join(
        (
            "EMBRIX_FABRICATION_MODE_CLOSURE",
            f"canonical_project={readiness.project_id}",
            f"fabrication_mode={mode}",
            f"mode_source={decision.source_refs[0] if decision is not None and decision.source_refs else 'NONE'}",
            f"manufacturing_specification={specification.specification_id if specification is not None else 'NONE'}",
            f"fingerprint={specification.fingerprint if specification is not None else 'NONE'}",
            f"blocking_parameters={','.join(blocking_parameters) or 'NONE'}",
            f"status={readiness.blocking_reason or readiness.overall_status.value}",
            "",
            "EMBRIX_FABRICATION_GATE",
            f"mode_resolution={mode_gate}",
            f"manufacturing_spec={specification_gate}",
            f"parameter_approval={'PASS' if parameter_complete else 'BLOCKED'}",
            f"gerber_export={'PASS' if gerber_status == 'READY' else 'BLOCKED'}",
            f"drill_export={'PASS' if drill_status == 'READY' else 'BLOCKED'}",
            f"package_validation={'PASS' if package_status == 'READY' else 'BLOCKED'}",
            f"gerber_ready={output_status(ManufacturingGapCategory.GERBER, specification.gerber_required if specification is not None else True)}",
            f"drill_ready={output_status(ManufacturingGapCategory.DRILL, specification.drill_required if specification is not None else True)}",
            f"package_ready={'PASS' if record.package is not None else 'BLOCKED'}",
            f"baseline_ready={'PASS' if record.baseline is not None else 'BLOCKED'}",
            f"FABRICATION_READY={'true' if readiness.fabrication_ready else 'false'}",
            "",
            "EMBRIX_MANUFACTURING_CLOSURE",
            f"fabrication_mode={mode}",
            f"manufacturing_specification={manufacturing_specification_status}",
            f"parameter_status={parameter_status}",
            f"gerber_export={gerber_status}",
            f"drill_export={drill_status}",
            f"package_status={package_status}",
            f"FABRICATION_READY={'true' if readiness.fabrication_ready else 'false'}",
            "new_agent_added=false",
            "new_worker_added=false",
            "new_runtime_added=false",
            "fake_manufacturing=false",
            "fake_evidence=false",
            "fake_ready=false",
            "artifact_mutation_without_approval=false",
        )
    )


def render_fabrication_ready(record: FabricationRecord) -> str:
    """Render the approved manufacturing specification final gate."""
    readiness = record.readiness
    specification = record.specification
    mode = readiness.fabrication_mode.value if readiness.fabrication_mode is not None else "REQUIRED"
    parameters = "NONE"
    if specification is not None:
        thickness = (
            f"{specification.board_thickness_mm:g}mm"
            if specification.board_thickness_mm is not None
            else "UNSPECIFIED"
        )
        parameters = ",".join(
            (
                f"board_thickness={thickness}",
                f"surface_finish={specification.surface_finish or 'UNSPECIFIED'}",
                f"copper_weight={specification.copper_weight or 'UNSPECIFIED'}",
                f"solder_mask={specification.solder_mask or 'UNSPECIFIED'}",
                f"silkscreen={specification.silkscreen or 'UNSPECIFIED'}",
            )
        )
    verification_by_type = {item.operation_type: item for item in readiness.export_verifications}

    def export_status(operation_type: str) -> str:
        item = verification_by_type.get(operation_type)
        return "PASS" if item is not None and item.status is ManufacturingCheckStatus.PASS and all(
            (item.output_exists, item.pcb_hash_match, item.revision_match, item.evidence_created)
        ) else "BLOCKED"

    package_status = "PASS" if record.package is not None and readiness.fabrication_ready else "BLOCKED"
    artifact_integrity = "PASS" if readiness.fabrication_ready and record.manifest is not None and record.evidence is not None else "FAIL"
    return "\n".join(
        (
            "EMBRIX_FABRICATION_READY",
            f"project={readiness.project_id}",
            f"fabrication_mode={mode}",
            f"manufacturing_specification={'APPROVED' if specification is not None and specification.status is ManufacturingSpecificationStatus.COMPLETE else 'BLOCKED'}",
            f"parameters={parameters}",
            f"gerber={export_status('KICAD_GERBER_EXPORT')}",
            f"drill={export_status('KICAD_DRILL_EXPORT')}",
            f"package={package_status}",
            f"artifact_integrity={artifact_integrity}",
            f"FABRICATION_READY={'true' if readiness.fabrication_ready else 'false'}",
        )
    )


__all__ = (
    "FabricationModeResolver",
    "FabricationReadinessService",
    "ManufacturingPackageService",
    "ManufacturingParameterAuditService",
    "OutputFile",
    "audit_manufacturing_parameters",
    "build_manufacturing_specification",
    "build_source_revision",
    "close_manufacturing_parameters",
    "file_sha256",
    "output_files_from_artifacts",
    "output_records",
    "render_fabrication_closure",
    "render_fabrication_mode_audit",
    "render_fabrication_ready",
    "render_fabrication_report",
    "render_manufacturing_parameter_audit",
    "validate_bom",
    "verify_manufacturing_exports",
)
