from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_AUTO = "sha256:" + "0" * 64
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")


def _fingerprint(value: BaseModel) -> str:
    payload = value.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _text(value: object, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    candidate = value.strip()
    if not candidate or len(candidate) > maximum or "\x00" in candidate or "\r" in candidate or "\n" in candidate:
        raise ValueError(f"{field} is invalid")
    return candidate


def _parameter_text(value: object, field: str, maximum: int = 256) -> str:
    """Canonicalize an explicit scalar parameter while preserving its source value."""
    if isinstance(value, bool):
        raise TypeError(f"{field} is invalid")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field} is invalid")
        value = format(number, "g")
    return _text(value, field, maximum)


def _specified(value: object) -> bool:
    return value is not None and not (
        isinstance(value, str) and value.strip().upper() == "UNSPECIFIED"
    )


def _board_thickness_value(value: object) -> object:
    """Accept an explicit numeric millimetre value without adding a default."""
    if isinstance(value, str):
        if value.strip().upper() == "UNSPECIFIED":
            return None
        match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(?:mm)?\s*", value, re.IGNORECASE)
        if match is not None:
            return float(match.group(1))
    return value


def _relative(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    normalized = value.replace("\\", "/")
    if normalized != value or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(f"{field} is invalid")
    return normalized


def _utc(value: object, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} must be timezone-aware") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class ManufacturingContract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", revalidate_instances="always", hide_input_in_errors=True)
    fingerprint: str = _AUTO
    _tuple_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _copy(cls, value: object) -> object:
        return copy.deepcopy(value.model_dump(mode="python") if isinstance(value, cls) else value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint_shape(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @model_validator(mode="after")
    def _bind(self):
        expected = _fingerprint(self)
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object):
        for field in cls._tuple_fields:
            if field in values and type(values[field]) is not tuple:
                raise TypeError(f"{field} must be a tuple")
        return cls.model_validate(values, strict=False)


class FabricationMode(StrEnum):
    PCB_ONLY = "PCB_ONLY"
    PCB_ASSEMBLY = "PCB_ASSEMBLY"


class FabricationModeStatus(StrEnum):
    RESOLVED = "RESOLVED"
    REQUIRED = "FABRICATION_MODE_REQUIRED"


class FabricationModeRequirementStatus(StrEnum):
    BLOCKED = "BLOCKED_FABRICATION_MODE_REQUIRED"


class FabricationAssemblyIntent(StrEnum):
    ABSENT = "ABSENT"
    PRESENT = "PRESENT"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"


class ManufacturingSpecificationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class ManufacturingParameterApprovalState(StrEnum):
    """Approval state for one explicit manufacturing parameter decision."""

    UNSPECIFIED = "UNSPECIFIED"
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ManufacturingReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED_MANUFACTURING_PARAMETER_REQUIRED = "BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"


class FabricationOverallStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INCOMPLETE = "INCOMPLETE"
    STALE = "STALE"


class ManufacturingCheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    STALE = "STALE"


class FabricationBaselineStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    FABRICATION_READY = "FABRICATION_READY"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"
    REVOKED = "REVOKED"


class ManufacturingOutputType(StrEnum):
    GERBER = "GERBER"
    DRILL = "DRILL"
    BOM = "BOM"
    CPL = "CPL"
    FABRICATION_NOTES = "FABRICATION_NOTES"
    MANIFEST = "MANIFEST"
    MANUFACTURING_PACKAGE = "MANUFACTURING_PACKAGE"


class ManufacturingGapCategory(StrEnum):
    MODE = "MODE"
    UPSTREAM = "UPSTREAM"
    ARTIFACT_INTEGRITY = "ARTIFACT_INTEGRITY"
    BOARD_OUTLINE = "BOARD_OUTLINE"
    GERBER = "GERBER"
    DRILL = "DRILL"
    BOM = "BOM"
    CPL = "CPL"
    SPECIFICATION = "SPECIFICATION"
    PACKAGE = "PACKAGE"
    REVISION = "REVISION"


class ManufacturingGapSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"


class FabricationModeResolution(ManufacturingContract):
    project_id: str
    status: FabricationModeStatus
    mode: FabricationMode | None = None
    source_ref: str | None = None
    reason: str

    _ids = field_validator("project_id", mode="before")(
        lambda value: _id(value, "project_id")
    )
    _source = field_validator("source_ref", mode="before")(
        lambda value: None if value is None else _id(value, "source_ref")
    )

    @model_validator(mode="after")
    def _mode_binding(self):
        if (self.status is FabricationModeStatus.RESOLVED) != (self.mode is not None):
            raise ValueError("fabrication mode resolution is incomplete")
        if self.status is FabricationModeStatus.RESOLVED and self.source_ref is None:
            raise ValueError("resolved fabrication mode requires provenance")
        return self


class FabricationModeAudit(ManufacturingContract):
    audit_id: str
    project_id: str
    project_revision: str
    existing_mode: FabricationMode | None = None
    source_ref: str | None = None
    requirement_evidence: tuple[str, ...]
    assembly_intent: FabricationAssemblyIntent
    missing_facts: tuple[str, ...]
    created_at: datetime

    _ids = field_validator("audit_id", "project_id", "project_revision", "source_ref", mode="before")(
        lambda value, info: None if value is None else _id(value, info.field_name)
    )
    _refs = field_validator("requirement_evidence", mode="before")(
        lambda value: tuple(_id(item, "requirement_evidence") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("requirement_evidence is invalid"))
    )
    _facts = field_validator("missing_facts", mode="before")(
        lambda value: tuple(_text(item, "missing_fact") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("missing_facts is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )


class FabricationModeRequirement(ManufacturingContract):
    requirement_id: str
    project_id: str
    project_revision: str
    requirement_revision: str
    required_fact: str
    status: FabricationModeRequirementStatus
    source_refs: tuple[str, ...]
    created_at: datetime

    _ids = field_validator(
        "requirement_id", "project_id", "project_revision", "requirement_revision", mode="before"
    )(lambda value, info: _id(value, info.field_name))
    _text_value = field_validator("required_fact", mode="before")(
        lambda value: _text(value, "required_fact")
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: tuple(_id(item, "source_ref") for item in value)
        if isinstance(value, (tuple, list)) and value else (_ for _ in ()).throw(ValueError("source_refs is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )


class EngineeringDecision(ManufacturingContract):
    decision_id: str
    project_id: str
    project_revision: str
    requirement_revision: str
    decision_topic: str
    selected_mode: FabricationMode
    rationale: str
    source_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    approved_by: str
    approved_at: datetime
    design_truth_fingerprint: str
    pcb_sha256: str
    bom_sha256: str

    _ids = field_validator(
        "decision_id", "project_id", "project_revision", "requirement_revision", "decision_topic", "approved_by", mode="before"
    )(lambda value, info: _id(value, info.field_name))
    _text_value = field_validator("rationale", mode="before")(
        lambda value: _text(value, "rationale", 2048)
    )
    _refs = field_validator("source_refs", "evidence_refs", mode="before")(
        lambda value: tuple(_id(item, "decision_reference") for item in value)
        if isinstance(value, (tuple, list)) and value else (_ for _ in ()).throw(ValueError("decision references are invalid"))
    )
    _fps = field_validator("design_truth_fingerprint", "pcb_sha256", "bom_sha256", mode="before")(
        lambda value, info: _fp(value, info.field_name)
    )
    _time = field_validator("approved_at", mode="before")(
        lambda value: _utc(value, "approved_at")
    )


class FabricationSourceRevision(ManufacturingContract):
    project_id: str
    project_revision: str
    requirement_revision: str
    design_truth_id: str
    design_truth_fingerprint: str
    schematic_artifact_id: str
    schematic_sha256: str
    pcb_artifact_id: str
    pcb_sha256: str
    bom_artifact_id: str
    bom_sha256: str
    firmware_specification_id: str
    build_configuration_id: str
    elf_artifact_id: str
    elf_sha256: str
    hw_fw_consistency_result_id: str
    virtual_assembly_id: str
    virtual_assembly_fingerprint: str
    simulation_result_id: str
    functional_verification_result_id: str
    functional_verification_fingerprint: str
    source_refs: tuple[str, ...]

    _ids = field_validator(
        "project_id", "project_revision", "requirement_revision", "design_truth_id",
        "schematic_artifact_id", "pcb_artifact_id", "bom_artifact_id",
        "firmware_specification_id", "build_configuration_id", "elf_artifact_id",
        "hw_fw_consistency_result_id", "virtual_assembly_id", "simulation_result_id",
        "functional_verification_result_id", mode="before",
    )(lambda value, info: _id(value, info.field_name))
    _fps = field_validator(
        "design_truth_fingerprint", "schematic_sha256", "pcb_sha256", "bom_sha256",
        "elf_sha256", "virtual_assembly_fingerprint", "functional_verification_fingerprint",
        mode="before",
    )(lambda value, info: _fp(value, info.field_name))
    _refs = field_validator("source_refs", mode="before")(
        lambda value: tuple(_id(item, "source_ref") for item in value)
        if isinstance(value, (tuple, list)) and value else (_ for _ in ()).throw(ValueError("source_refs is invalid"))
    )


class UpstreamGate(ManufacturingContract):
    gate_id: str
    name: str
    status: Literal["PASS", "CONSISTENT", "CURRENT", "VALID", "FAIL", "STALE", "UNKNOWN"]
    evidence_id: str
    evidence_fingerprint: str
    source_artifact_id: str | None = None
    source_sha256: str | None = None
    checked_at: datetime

    _ids = field_validator("gate_id", "name", "evidence_id", "source_artifact_id", mode="before")(
        lambda value, info: None if value is None else _id(value, info.field_name)
    )
    _fps = field_validator("evidence_fingerprint", "source_sha256", mode="before")(
        lambda value, info: None if value is None else _fp(value, info.field_name)
    )
    _time = field_validator("checked_at", mode="before")(
        lambda value: _utc(value, "checked_at")
    )


class ManufacturingSpecification(ManufacturingContract):
    specification_id: str
    project_id: str
    project_revision: str
    fabrication_mode: FabricationMode
    layer_count: int = Field(ge=1, le=64)
    copper_layers: tuple[str, ...]
    board_thickness_mm: float | None = Field(default=None, gt=0, le=20)
    copper_weight: str | None = None
    solder_mask: str | None = None
    silkscreen: str | None = None
    surface_finish: str | None = None
    min_track_width_mm: float | None = Field(default=None, gt=0, le=20)
    min_clearance_mm: float | None = Field(default=None, gt=0, le=20)
    min_drill_mm: float | None = Field(default=None, gt=0, le=20)
    min_annular_ring_mm: float | None = Field(default=None, gt=0, le=20)
    gerber_required: bool = True
    drill_required: bool = True
    bom_required: bool = False
    cpl_required: bool = False
    allow_factory_defaults: bool = False
    source_refs: tuple[str, ...]
    notes: tuple[str, ...] = ()
    board_size_mm: tuple[float, float] | None = None
    requirement_revision: str | None = None
    design_truth_fingerprint: str | None = None
    pcb_sha256: str | None = None
    bom_sha256: str | None = None
    status: ManufacturingSpecificationStatus | None = None

    _ids = field_validator("specification_id", "project_id", "project_revision", mode="before")(
        lambda value, info: _id(value, info.field_name)
    )
    _layers = field_validator("copper_layers", mode="before")(
        lambda value: tuple(_id(item, "copper_layer") for item in value)
        if isinstance(value, (tuple, list)) and value else (_ for _ in ()).throw(ValueError("copper_layers is invalid"))
    )
    _text = field_validator("copper_weight", "solder_mask", "silkscreen", "surface_finish", mode="before")(
        lambda value, info: None if value is None else _text(value, info.field_name)
    )
    _thickness = field_validator("board_thickness_mm", mode="before")(_board_thickness_value)
    _refs = field_validator("source_refs", "notes", mode="before")(
        lambda value: tuple(_id(item, "source_ref") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("specification list is invalid"))
    )
    _board_size = field_validator("board_size_mm", mode="before")(
        lambda value: None
        if value is None
        else (
            (float(value[0]), float(value[1]))
            if isinstance(value, (tuple, list)) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value) and all(float(item) > 0 for item in value)
            else (_ for _ in ()).throw(ValueError("board_size_mm is invalid"))
        )
    )
    _lineage = field_validator(
        "requirement_revision", "design_truth_fingerprint", "pcb_sha256", "bom_sha256", mode="before"
    )(
        lambda value, info: None if value is None else (_fp(value, info.field_name) if info.field_name != "requirement_revision" else _id(value, info.field_name))
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_status_before_fingerprint(cls, value: object) -> object:
        data = copy.deepcopy(value.model_dump(mode="python") if isinstance(value, cls) else value)
        if not isinstance(data, dict) or data.get("status") is not None:
            return data
        complete = all(
            _specified(data.get(field))
            for field in ("board_thickness_mm", "copper_weight", "solder_mask", "silkscreen", "surface_finish")
        )
        data["status"] = "COMPLETE" if complete else "INCOMPLETE"
        return data

    @model_validator(mode="after")
    def _applicability(self):
        if self.fabrication_mode is FabricationMode.PCB_ONLY and self.cpl_required:
            raise ValueError("PCB_ONLY cannot require CPL")
        if self.fabrication_mode is FabricationMode.PCB_ASSEMBLY and not self.cpl_required:
            raise ValueError("PCB_ASSEMBLY requires CPL")
        if self.layer_count != len(self.copper_layers):
            raise ValueError("layer_count and copper_layers mismatch")
        expected_status = (
            ManufacturingSpecificationStatus.COMPLETE
            if all(
                _specified(getattr(self, field))
                for field in ("board_thickness_mm", "copper_weight", "solder_mask", "silkscreen", "surface_finish")
            )
            else ManufacturingSpecificationStatus.INCOMPLETE
        )
        if self.status is None:
            object.__setattr__(self, "status", expected_status)
        elif self.status is not expected_status:
            raise ValueError("manufacturing specification status is not deterministic")
        return self


class ManufacturingParameterDecision(ManufacturingContract):
    """Lineage-bearing decision for one manufacturing parameter.

    ``UNSPECIFIED`` is a durable value, rather than a request to apply a
    factory default.  Only an explicit ``APPROVED`` decision can close a
    parameter gap.
    """

    id: str
    project_id: str
    parameter: str
    value: str
    source: str
    approval_state: ManufacturingParameterApprovalState
    created_at: datetime

    _ids = field_validator("id", "project_id", "parameter", "source", mode="before")(
        lambda value, info: _id(value, info.field_name)
    )
    _value = field_validator("value", mode="before")(
        lambda value: (
            "UNSPECIFIED"
            if _parameter_text(value, "value").upper() == "UNSPECIFIED"
            else _parameter_text(value, "value")
        )
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )

    @model_validator(mode="after")
    def _approval_binding(self):
        if self.parameter not in {
            "board_thickness",
            "surface_finish",
            "copper_weight",
            "solder_mask",
            "silkscreen",
        }:
            raise ValueError("manufacturing parameter is unsupported")
        if self.approval_state is ManufacturingParameterApprovalState.APPROVED and self.value == "UNSPECIFIED":
            raise ValueError("an UNSPECIFIED manufacturing parameter cannot be approved")
        if self.approval_state is ManufacturingParameterApprovalState.UNSPECIFIED and self.value != "UNSPECIFIED":
            raise ValueError("an unspecified decision must retain UNSPECIFIED value")
        return self


class ManufacturingParameterAudit(ManufacturingContract):
    """Safe projection used by the manufacturing parameter audit."""

    parameter: str
    current_value: str
    source: str
    status: ManufacturingParameterApprovalState

    _ids = field_validator("parameter", "source", mode="before")(
        lambda value, info: _id(value, info.field_name)
    )
    _value = field_validator("current_value", mode="before")(
        lambda value: _parameter_text(value, "current_value")
    )

    @model_validator(mode="after")
    def _status_binding(self):
        unspecified = self.current_value.upper() == "UNSPECIFIED"
        if self.status is ManufacturingParameterApprovalState.UNSPECIFIED and not unspecified:
            raise ValueError("an unspecified parameter audit must retain UNSPECIFIED value")
        if self.status is ManufacturingParameterApprovalState.APPROVED and unspecified:
            raise ValueError("an approved parameter audit requires an explicit value")
        return self


class ManufacturingExportVerification(ManufacturingContract):
    """Read-back verification for a Worker-produced manufacturing export."""

    operation_type: str
    output_exists: bool
    artifact_hash: str
    pcb_hash_match: bool
    revision_match: bool
    evidence_created: bool
    status: ManufacturingCheckStatus

    _operation = field_validator("operation_type", mode="before")(
        lambda value: _id(value, "operation_type")
    )
    _artifact = field_validator("artifact_hash", mode="before")(
        lambda value: _fp(value, "artifact_hash")
    )


class ManufacturingOutput(ManufacturingContract):
    artifact_id: str
    relative_path: str
    output_type: ManufacturingOutputType
    layer: str | None = None
    sha256: str
    source_revision_fingerprint: str
    size_bytes: int = Field(gt=0, le=512 * 1024 * 1024)
    required: bool
    validation: ManufacturingCheckStatus
    component_references: tuple[str, ...] = ()

    _id_value = field_validator("artifact_id", mode="before")(
        lambda value: _id(value, "artifact_id")
    )
    _path = field_validator("relative_path", mode="before")(
        lambda value: _relative(value, "relative_path")
    )
    _sha = field_validator("sha256", mode="before")(
        lambda value: _fp(value, "sha256")
    )
    _source_fp = field_validator("source_revision_fingerprint", mode="before")(
        lambda value: _fp(value, "source_revision_fingerprint")
    )
    _layer = field_validator("layer", mode="before")(
        lambda value: None if value is None else _id(value, "layer")
    )
    _refs = field_validator("component_references", mode="before")(
        lambda value: tuple(_id(item, "component_reference") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("component_references is invalid"))
    )


class FabricationGap(ManufacturingContract):
    gap_id: str
    category: ManufacturingGapCategory
    description: str
    severity: ManufacturingGapSeverity
    blocking: bool
    source_refs: tuple[str, ...]
    resolution: str

    _ids = field_validator("gap_id", mode="before")(
        lambda value: _id(value, "gap_id")
    )
    _text = field_validator("description", "resolution", mode="before")(
        lambda value, info: _text(value, info.field_name)
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: tuple(_id(item, "source_ref") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("gap source_refs is invalid"))
    )

    @model_validator(mode="after")
    def _severity_binding(self):
        if self.blocking != (self.severity is ManufacturingGapSeverity.BLOCKING):
            raise ValueError("gap severity binding mismatch")
        return self


class ManufacturingCheck(ManufacturingContract):
    check_id: str
    category: ManufacturingGapCategory
    status: ManufacturingCheckStatus
    required: bool
    detail: str
    evidence_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    _id_value = field_validator("check_id", mode="before")(
        lambda value: _id(value, "check_id")
    )
    _text = field_validator("detail", mode="before")(
        lambda value: _text(value, "detail")
    )
    _refs = field_validator("evidence_refs", "source_refs", mode="before")(
        lambda value: tuple(_id(item, "reference") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("check references are invalid"))
    )


class ManufacturingManifest(ManufacturingContract):
    manifest_id: str
    project_id: str
    project_revision: str
    requirement_revision: str
    fabrication_mode: FabricationMode
    source: FabricationSourceRevision
    manufacturing_specification_id: str
    manufacturing_specification_fingerprint: str
    kicad_version: str
    outputs: tuple[ManufacturingOutput, ...]
    notes: tuple[str, ...]
    created_at: datetime

    _ids = field_validator("manifest_id", "manufacturing_specification_id", "kicad_version", mode="before")(
        lambda value, info: _id(value, info.field_name)
    )
    _fp_value = field_validator("manufacturing_specification_fingerprint", mode="before")(
        lambda value: _fp(value, "manufacturing_specification_fingerprint")
    )
    _lists = field_validator("outputs", "notes", mode="before")(
        lambda value: tuple(copy.deepcopy(value)) if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("manifest list is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )

    @model_validator(mode="after")
    def _binding(self):
        if self.project_id != self.source.project_id or self.project_revision != self.source.project_revision or self.requirement_revision != self.source.requirement_revision or self.fabrication_mode is not self.source_mode:
            raise ValueError("manifest source binding mismatch")
        if self.manufacturing_specification_id == "" or len({item.artifact_id for item in self.outputs}) != len(self.outputs):
            raise ValueError("manifest output identities are invalid")
        if any(item.source_revision_fingerprint != self.source.fingerprint for item in self.outputs):
            raise ValueError("manifest output source binding mismatch")
        return self

    @property
    def source_mode(self) -> FabricationMode:
        return self.fabrication_mode


class ManufacturingPackage(ManufacturingContract):
    package_id: str
    package_name: str
    project_id: str
    project_revision: str
    manifest_id: str
    manifest_fingerprint: str
    root_relative_path: str
    output_artifact_ids: tuple[str, ...]
    required_output_count: int = Field(ge=0)
    package_integrity: ManufacturingCheckStatus
    created_at: datetime

    _ids = field_validator("package_id", "package_name", "project_id", "project_revision", "manifest_id", mode="before")(
        lambda value, info: _id(value, info.field_name)
    )
    _fp_value = field_validator("manifest_fingerprint", mode="before")(
        lambda value: _fp(value, "manifest_fingerprint")
    )
    _path = field_validator("root_relative_path", mode="before")(
        lambda value: _relative(value, "root_relative_path")
    )
    _outputs = field_validator("output_artifact_ids", mode="before")(
        lambda value: tuple(_id(item, "output_artifact_id") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("output_artifact_ids is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )

    @model_validator(mode="after")
    def _output_binding(self):
        if self.required_output_count != len(self.output_artifact_ids) or len(set(self.output_artifact_ids)) != len(self.output_artifact_ids):
            raise ValueError("manufacturing package output binding is invalid")
        return self


class ManufacturingReadinessResult(ManufacturingContract):
    """Parameter and artifact gate projection for fabrication readiness."""

    fabrication_mode: FabricationMode
    parameter_completeness: bool
    missing_parameters: tuple[str, ...]
    pcb_revision: str
    artifact_fingerprint: str
    status: ManufacturingReadinessStatus

    _ids = field_validator("pcb_revision", mode="before")(
        lambda value: _id(value, "pcb_revision")
    )
    _artifact = field_validator("artifact_fingerprint", mode="before")(
        lambda value: _fp(value, "artifact_fingerprint")
    )
    _missing = field_validator("missing_parameters", mode="before")(
        lambda value: tuple(_id(item, "missing_parameter") for item in value)
        if isinstance(value, (tuple, list))
        else (_ for _ in ()).throw(ValueError("missing_parameters is invalid"))
    )

    @model_validator(mode="after")
    def _status_binding(self):
        expected = (
            ManufacturingReadinessStatus.READY
            if self.parameter_completeness and not self.missing_parameters
            else ManufacturingReadinessStatus.BLOCKED_MANUFACTURING_PARAMETER_REQUIRED
        )
        if self.status is not expected:
            raise ValueError("manufacturing readiness status is not deterministic")
        return self


class FabricationReadinessResult(ManufacturingContract):
    result_id: str
    project_id: str
    project_revision: str
    mode_resolution: FabricationModeResolution
    fabrication_mode: FabricationMode | None
    source_revision: FabricationSourceRevision
    manufacturing_specification_id: str | None = None
    manufacturing_specification_fingerprint: str | None = None
    upstream_checks: tuple[ManufacturingCheck, ...]
    manufacturing_checks: tuple[ManufacturingCheck, ...]
    blocking_gaps: tuple[FabricationGap, ...]
    non_blocking_gaps: tuple[FabricationGap, ...]
    manufacturing_manifest_id: str | None = None
    manufacturing_package_id: str | None = None
    baseline_id: str | None = None
    stale_inputs: tuple[str, ...] = ()
    overall_status: FabricationOverallStatus
    fabrication_ready: bool
    evidence_refs: tuple[str, ...]
    created_at: datetime
    blocking_reason: str | None = None
    manufacturing_readiness: ManufacturingReadinessResult | None = None
    parameter_audit: tuple[ManufacturingParameterAudit, ...] = ()
    parameter_decisions: tuple[ManufacturingParameterDecision, ...] = ()
    export_verifications: tuple[ManufacturingExportVerification, ...] = ()

    _ids = field_validator("result_id", "project_id", "project_revision", "manufacturing_specification_id", "manufacturing_manifest_id", "manufacturing_package_id", "baseline_id", mode="before")(
        lambda value, info: None if value is None else _id(value, info.field_name)
    )
    _fps = field_validator("manufacturing_specification_fingerprint", mode="before")(
        lambda value, info: None if value is None else _fp(value, info.field_name)
    )
    _lists = field_validator("upstream_checks", "manufacturing_checks", "blocking_gaps", "non_blocking_gaps", "stale_inputs", "evidence_refs", "parameter_audit", "parameter_decisions", "export_verifications", mode="before")(
        lambda value: tuple(copy.deepcopy(value)) if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("readiness list is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )
    _reason = field_validator("blocking_reason", mode="before")(
        lambda value: None if value is None else _text(value, "blocking_reason")
    )

    @model_validator(mode="after")
    def _result_binding(self):
        if self.project_id != self.source_revision.project_id or self.project_revision != self.source_revision.project_revision:
            raise ValueError("readiness source binding mismatch")
        if (self.fabrication_mode is None) != (self.mode_resolution.status is FabricationModeStatus.REQUIRED):
            raise ValueError("readiness mode binding mismatch")
        if self.mode_resolution.project_id != self.project_id:
            raise ValueError("readiness mode project mismatch")
        if self.fabrication_mode is not None and self.mode_resolution.mode is not self.fabrication_mode:
            raise ValueError("readiness mode value mismatch")
        if self.mode_resolution.status is FabricationModeStatus.REQUIRED and any(
            value is not None
            for value in (
                self.manufacturing_specification_id,
                self.manufacturing_specification_fingerprint,
                self.manufacturing_manifest_id,
                self.manufacturing_package_id,
                self.baseline_id,
            )
        ):
            raise ValueError("unresolved fabrication mode cannot have manufacturing outputs")
        expected_ready = self.overall_status is FabricationOverallStatus.PASS and not self.blocking_gaps and not self.stale_inputs and self.baseline_id is not None
        if self.fabrication_ready != expected_ready:
            raise ValueError("fabrication ready flag is not deterministic")
        if self.manufacturing_readiness is not None and self.manufacturing_readiness.fabrication_mode != self.fabrication_mode:
            raise ValueError("manufacturing readiness mode mismatch")
        if any(item.project_id != self.project_id for item in self.parameter_decisions):
            raise ValueError("manufacturing parameter decision project mismatch")
        if self.manufacturing_readiness is not None and self.manufacturing_readiness.artifact_fingerprint != self.source_revision.pcb_sha256:
            raise ValueError("manufacturing readiness artifact binding mismatch")
        return self


class FabricationBaseline(ManufacturingContract):
    baseline_id: str
    baseline_name: str
    project_id: str
    project_revision: str
    requirement_revision: str
    fabrication_mode: FabricationMode
    source_revision: FabricationSourceRevision
    erc_evidence_id: str
    drc_evidence_id: str
    manufacturing_specification_id: str
    manufacturing_specification_fingerprint: str
    manufacturing_manifest_id: str
    manufacturing_package_id: str
    manufacturing_package_fingerprint: str
    readiness_result_id: str
    readiness_result_fingerprint: str
    status: FabricationBaselineStatus
    created_at: datetime

    _ids = field_validator(
        "baseline_id", "baseline_name", "project_id", "project_revision", "requirement_revision",
        "erc_evidence_id", "drc_evidence_id", "manufacturing_specification_id",
        "manufacturing_manifest_id", "manufacturing_package_id", "readiness_result_id", mode="before",
    )(lambda value, info: _id(value, info.field_name))
    _fps = field_validator(
        "manufacturing_specification_fingerprint", "manufacturing_package_fingerprint", "readiness_result_fingerprint", mode="before",
    )(lambda value, info: _fp(value, info.field_name))
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )

    @model_validator(mode="after")
    def _baseline_binding(self):
        source = self.source_revision
        if self.project_id != source.project_id or self.project_revision != source.project_revision or self.requirement_revision != source.requirement_revision or self.fabrication_mode is not self.source_mode:
            raise ValueError("baseline source binding mismatch")
        return self

    @property
    def source_mode(self) -> FabricationMode:
        return self.fabrication_mode


class FabricationEvidence(ManufacturingContract):
    evidence_id: str
    project_id: str
    result_id: str
    baseline_id: str | None
    manifest_id: str | None
    package_id: str | None
    source_revision_fingerprint: str
    evidence_refs: tuple[str, ...]
    blocking_gap_ids: tuple[str, ...]
    non_blocking_gap_ids: tuple[str, ...]
    lineage_complete: bool
    tool_observed: bool
    fingerprints_complete: bool
    status: FabricationOverallStatus
    created_at: datetime

    _ids = field_validator("evidence_id", "project_id", "result_id", "baseline_id", "manifest_id", "package_id", mode="before")(
        lambda value, info: None if value is None else _id(value, info.field_name)
    )
    _fp_value = field_validator("source_revision_fingerprint", mode="before")(
        lambda value: _fp(value, "source_revision_fingerprint")
    )
    _refs = field_validator("evidence_refs", "blocking_gap_ids", "non_blocking_gap_ids", mode="before")(
        lambda value: tuple(_id(item, "reference") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("evidence references are invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: _utc(value, "created_at")
    )


class FabricationRecord(ManufacturingContract):
    baseline: FabricationBaseline | None
    readiness: FabricationReadinessResult
    manifest: ManufacturingManifest | None
    package: ManufacturingPackage | None
    evidence: FabricationEvidence
    mode_audit: FabricationModeAudit | None = None
    mode_requirement: FabricationModeRequirement | None = None
    engineering_decision: EngineeringDecision | None = None
    specification: ManufacturingSpecification | None = None

    @model_validator(mode="after")
    def _record_binding(self):
        if self.evidence.result_id != self.readiness.result_id or self.evidence.project_id != self.readiness.project_id:
            raise ValueError("fabrication record evidence binding mismatch")
        if self.evidence.source_revision_fingerprint != self.readiness.source_revision.fingerprint:
            raise ValueError("fabrication record source evidence mismatch")
        if self.manifest is not None and self.readiness.manufacturing_manifest_id != self.manifest.manifest_id:
            raise ValueError("fabrication record manifest binding mismatch")
        if self.package is not None and self.readiness.manufacturing_package_id != self.package.package_id:
            raise ValueError("fabrication record package binding mismatch")
        if self.baseline is not None and (
            self.evidence.baseline_id != self.baseline.baseline_id
            or self.readiness.baseline_id != self.baseline.baseline_id
        ):
            raise ValueError("fabrication record baseline binding mismatch")
        for value in (self.mode_audit, self.mode_requirement, self.engineering_decision, self.specification):
            if value is not None and value.project_id != self.readiness.project_id:
                raise ValueError("fabrication record project binding mismatch")
        if self.mode_audit is not None and self.mode_audit.project_revision != self.readiness.project_revision:
            raise ValueError("fabrication record mode audit revision mismatch")
        if self.mode_requirement is not None and self.mode_requirement.project_revision != self.readiness.project_revision:
            raise ValueError("fabrication record mode requirement revision mismatch")
        if self.engineering_decision is not None:
            if self.engineering_decision.selected_mode is not self.readiness.fabrication_mode:
                raise ValueError("fabrication record decision mode mismatch")
            if self.engineering_decision.design_truth_fingerprint != self.readiness.source_revision.design_truth_fingerprint:
                raise ValueError("fabrication record decision DesignTruth mismatch")
        if self.specification is not None and (
            self.readiness.manufacturing_specification_id != self.specification.specification_id
            or self.readiness.manufacturing_specification_fingerprint != self.specification.fingerprint
        ):
            raise ValueError("fabrication record specification binding mismatch")
        if (
            self.readiness.manufacturing_readiness is not None
            and self.readiness.manufacturing_readiness.fabrication_mode != self.readiness.fabrication_mode
        ):
            raise ValueError("fabrication record manufacturing readiness mode mismatch")
        return self


__all__ = (
    "EngineeringDecision",
    "FabricationAssemblyIntent",
    "FabricationBaseline",
    "FabricationBaselineStatus",
    "FabricationEvidence",
    "FabricationGap",
    "FabricationMode",
    "FabricationModeAudit",
    "FabricationModeRequirement",
    "FabricationModeRequirementStatus",
    "FabricationModeResolution",
    "FabricationModeStatus",
    "FabricationOverallStatus",
    "FabricationReadinessResult",
    "FabricationRecord",
    "FabricationSourceRevision",
    "ManufacturingCheck",
    "ManufacturingCheckStatus",
    "ManufacturingContract",
    "ManufacturingExportVerification",
    "ManufacturingGapCategory",
    "ManufacturingGapSeverity",
    "ManufacturingManifest",
    "ManufacturingOutput",
    "ManufacturingOutputType",
    "ManufacturingPackage",
    "ManufacturingParameterApprovalState",
    "ManufacturingParameterAudit",
    "ManufacturingParameterDecision",
    "ManufacturingReadinessResult",
    "ManufacturingReadinessStatus",
    "ManufacturingSpecification",
    "ManufacturingSpecificationStatus",
    "UpstreamGate",
)
