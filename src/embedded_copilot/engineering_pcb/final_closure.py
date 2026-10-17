from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from importlib import import_module

from pydantic import Field, field_validator, model_validator

_verification_loop = import_module("embedded_copilot." + "verification_loop")
VerificationIssue = _verification_loop.VerificationIssue
VerificationResult = _verification_loop.VerificationResult
VerificationStatus = _verification_loop.VerificationStatus

from .foundation import (
    PCBApplicability,
    PCBConstraintSet,
    PCBEngineeringState,
    PCBVerificationPlan,
)
from .models import PCBContract

_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_LOGICAL_REF = re.compile(r"^(?:artifact|knowledge|pcb|project|tool)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$")
_ZERO = "sha256:" + "0" * 64


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _ref(value: object, field: str) -> str:
    if not isinstance(value, str) or _LOGICAL_REF.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _text(value: object, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not checked or len(checked) > maximum or any(char in checked for char in ("\x00", "\r", "\n")):
        raise ValueError(f"{field} is invalid")
    return checked


def _tuple_text(value: object, field: str, maximum: int = 256) -> tuple[str, ...]:
    if type(value) not in (tuple, list):
        raise TypeError(f"{field} must be a tuple or list")
    return tuple(_text(item, field, maximum) for item in value)


def _evidence_digest(values: tuple[object, ...]) -> str:
    payload = []
    for item in values:
        if hasattr(item, "model_dump"):
            payload.append(item.model_dump(mode="json", exclude={"fingerprint"}))
        else:
            payload.append(item)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _model_values(value: object) -> object:
    return value.model_dump(mode="python") if hasattr(value, "model_dump") else value


class EngineeringEvidenceSource(StrEnum):
    DATASHEET = "DATASHEET"
    APP_NOTE = "APP_NOTE"
    REFERENCE_MANUAL = "REFERENCE_MANUAL"
    HARDWARE_DESIGN_GUIDE = "HARDWARE_DESIGN_GUIDE"
    INTERFACE_SPEC = "INTERFACE_SPEC"
    MANUFACTURER_PROFILE = "MANUFACTURER_PROFILE"
    PROJECT_REQUIREMENT = "PROJECT_REQUIREMENT"
    USER_DECISION = "USER_DECISION"
    EDA_ARTIFACT = "EDA_ARTIFACT"
    TOOL_RECEIPT = "TOOL_RECEIPT"


class EngineeringVerificationType(StrEnum):
    DATASHEET_COMPLIANCE = "DATASHEET_COMPLIANCE"
    ELECTRICAL_RATING = "ELECTRICAL_RATING"
    POWER_BUDGET = "POWER_BUDGET"
    DERATING = "DERATING"
    THERMAL = "THERMAL"
    MECHANICAL = "MECHANICAL"
    DFM = "DFM"
    CRITICAL_DFT = "CRITICAL_DFT"


class ReadyForFabStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    STALE = "STALE"


class BlockingUnknownCategory(StrEnum):
    """Typed reason families for a blocking UNKNOWN in the final gate."""

    ENGINEERING_EVIDENCE_GAP = "ENGINEERING_EVIDENCE_GAP"
    EXECUTION_GAP = "EXECUTION_GAP"
    STALE_RESULT = "STALE_RESULT"
    MISSING_RESULT = "MISSING_RESULT"
    ENVIRONMENT = "ENVIRONMENT"
    APPROVAL = "APPROVAL"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EngineeringEvidenceRequirement(PCBContract):
    requirement_id: str
    project_id: str
    scope: str
    affected_objects: tuple[str, ...]
    requirement_type: str
    value: str | float | int | bool | None = None
    rule: str | None = None
    unit: str | None = None
    comparison: str | None = None
    source: EngineeringEvidenceSource
    source_ref: str
    evidence_locator: str
    confidence: float = Field(ge=0.0, le=1.0)
    machine_evaluable: bool
    source_fingerprint: str

    @field_validator("requirement_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("scope", "requirement_type", "rule", "unit", "comparison", mode="before")
    @classmethod
    def _labels(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 128)

    @field_validator("affected_objects", mode="before")
    @classmethod
    def _objects(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "affected_objects")

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> EngineeringEvidenceSource:
        return value if isinstance(value, EngineeringEvidenceSource) else EngineeringEvidenceSource(value)

    @field_validator("source_ref", "evidence_locator", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> str:
        return _ref(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")


class EngineeringApplicabilityDecision(PCBContract):
    verifier: EngineeringVerificationType
    applicability: PCBApplicability
    reason: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator("verifier", mode="before")
    @classmethod
    def _verifier(cls, value: object) -> EngineeringVerificationType:
        return value if isinstance(value, EngineeringVerificationType) else EngineeringVerificationType(value)

    @field_validator("applicability", mode="before")
    @classmethod
    def _applicability(cls, value: object) -> PCBApplicability:
        return value if isinstance(value, PCBApplicability) else PCBApplicability(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return _text(value, "reason", 1024)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "evidence_refs")

    @model_validator(mode="after")
    def _typed_applicability(self) -> EngineeringApplicabilityDecision:
        if self.applicability is not PCBApplicability.UNKNOWN and not self.evidence_refs:
            raise ValueError("resolved applicability requires evidence_refs")
        return self


class EngineeringCheck(PCBContract):
    check_id: str
    project_id: str
    verifier: EngineeringVerificationType
    requirement_id: str
    subject: str
    actual_value: float | str | bool | None = None
    expected_value: float | str | bool | None = None
    comparison: str | None = None
    unit: str | None = None
    evidence_refs: tuple[str, ...] = ()
    critical: bool = False
    blocking: bool = True
    unknown_reason: str | None = None

    @field_validator("check_id", "project_id", "requirement_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("verifier", mode="before")
    @classmethod
    def _verifier(cls, value: object) -> EngineeringVerificationType:
        return value if isinstance(value, EngineeringVerificationType) else EngineeringVerificationType(value)

    @field_validator("subject", "comparison", "unit", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 256)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "evidence_refs")

    @field_validator("unknown_reason", mode="before")
    @classmethod
    def _unknown_reason(cls, value: object) -> str | None:
        return None if value is None else _text(value, "unknown_reason", 512)


class ManufacturerRuleSeverity(StrEnum):
    HARD_LIMIT = "HARD_LIMIT"
    RECOMMENDED = "RECOMMENDED"
    ORDER_OPTION_DEPENDENT = "ORDER_OPTION_DEPENDENT"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class ManufacturerCapabilityRule(PCBContract):
    rule_id: str
    category: str
    measurement: str
    expected_value: float | int | str | bool
    comparison: str
    severity: ManufacturerRuleSeverity
    source_semantics: str
    source_evidence_ref: str
    unit: str | None = None
    blocking: bool = True
    condition_layer_min: int | None = Field(default=None, ge=1)
    condition_layer_max: int | None = Field(default=None, ge=1)
    condition_outer_copper_weight: str | None = None
    condition_thickness_min_mm: float | None = Field(default=None, ge=0.0)
    condition_thickness_max_mm: float | None = Field(default=None, ge=0.0)
    presence_measurement: str | None = None

    @field_validator("rule_id", "category", mode="before")
    @classmethod
    def _rule_labels(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("measurement", mode="before")
    @classmethod
    def _measurement(cls, value: object) -> str:
        return _text(value, "measurement", 128)

    @field_validator("comparison", "source_semantics", "source_evidence_ref", mode="before")
    @classmethod
    def _rule_text(cls, value: object, info) -> str:
        return _text(value, info.field_name, 512)

    @field_validator("unit", "condition_outer_copper_weight", "presence_measurement", mode="before")
    @classmethod
    def _optional_rule_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 128)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: object) -> ManufacturerRuleSeverity:
        return value if isinstance(value, ManufacturerRuleSeverity) else ManufacturerRuleSeverity(value)

    @model_validator(mode="after")
    def _rule_binding(self) -> ManufacturerCapabilityRule:
        if self.condition_layer_min is not None and self.condition_layer_max is not None and self.condition_layer_min > self.condition_layer_max:
            raise ValueError("manufacturer rule layer condition is invalid")
        if self.condition_thickness_min_mm is not None and self.condition_thickness_max_mm is not None and self.condition_thickness_min_mm > self.condition_thickness_max_mm:
            raise ValueError("manufacturer rule thickness condition is invalid")
        if self.severity is ManufacturerRuleSeverity.RECOMMENDED and self.blocking:
            raise ValueError("recommended manufacturer rule cannot be blocking")
        return self


class ManufacturerProfile(PCBContract):
    project_id: str
    manufacturer_id: str
    process_id: str
    min_trace_mm: float | None = Field(default=None, ge=0.0)
    min_clearance_mm: float | None = Field(default=None, ge=0.0)
    min_via_drill_mm: float | None = Field(default=None, ge=0.0)
    min_drill_mm: float | None = Field(default=None, ge=0.0)
    copper_to_edge_mm: float | None = Field(default=None, ge=0.0)
    layer_count: int | None = Field(default=None, ge=1)
    evidence_refs: tuple[str, ...]
    source_fingerprint: str
    profile_id: str | None = None
    pcb_type: str = "RIGID"
    source_identity: str | None = None
    source_revision: str | None = None
    retrieved_at: datetime | None = None
    selection_provenance: str = "UNKNOWN"
    source_conflicts: tuple[str, ...] = ()
    rules: tuple[ManufacturerCapabilityRule, ...] = ()

    @field_validator("project_id", "manufacturer_id", "process_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "evidence_refs")

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")

    @field_validator("profile_id", "pcb_type", "source_identity", "source_revision", "selection_provenance", mode="before")
    @classmethod
    def _profile_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 256)

    @field_validator("source_conflicts", mode="before")
    @classmethod
    def _source_conflicts(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "source_conflicts")

    @field_validator("rules", mode="before")
    @classmethod
    def _rules(cls, value: object) -> tuple[ManufacturerCapabilityRule, ...]:
        if type(value) not in (tuple, list):
            raise TypeError("rules must be a tuple or list")
        return tuple(
            item if isinstance(item, ManufacturerCapabilityRule)
            else ManufacturerCapabilityRule.model_validate(copy.deepcopy(item), strict=False)
            for item in value
        )

    @model_validator(mode="after")
    def _evidence_binding(self) -> ManufacturerProfile:
        if not self.evidence_refs:
            raise ValueError("manufacturer profile requires evidence_refs")
        rule_ids = [item.rule_id for item in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("manufacturer profile rule ids must be unique")
        if (
            self.manufacturer_id == "JLCPCB"
            and self.pcb_type == "RIGID"
            and self.selection_provenance == "USER_SELECTED"
            and (
                self.source_identity is None
                or self.source_revision is None
                or self.retrieved_at is None
                or not self.rules
            )
        ):
            raise ValueError("JLCPCB profile requires current official source provenance and rules")
        return self


class CopperToEdgeEvidence(PCBContract):
    """Evidence for a measured copper-to-board-edge distance."""

    artifact_fingerprint: str
    geometry_source: str
    measurement_method: str
    affected_objects: tuple[str, ...]
    actual_minimum_mm: float = Field(ge=0.0)
    minimum_object: str
    edge_segment: tuple[float, float, float, float]

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> str:
        return _fp(value, "artifact_fingerprint")

    @field_validator("geometry_source", "measurement_method", "minimum_object", mode="before")
    @classmethod
    def _evidence_text(cls, value: object, info) -> str:
        return _text(value, info.field_name, 2048)

    @field_validator("affected_objects", mode="before")
    @classmethod
    def _affected_objects(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "affected_objects", 512)

    @field_validator("edge_segment", mode="before")
    @classmethod
    def _edge_segment(cls, value: object) -> tuple[float, float, float, float]:
        if type(value) not in (tuple, list) or len(value) != 4:
            raise TypeError("edge_segment must contain four coordinates")
        values = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in values):
            raise ValueError("edge_segment coordinates must be finite")
        return values


class PCBManufacturingMeasurements(PCBContract):
    """Read-only measurements projected from the current canonical PCB state."""

    project_id: str
    artifact_fingerprint: str
    pcb_type: str = "RIGID"
    layer_count: int | None = Field(default=None, ge=1)
    board_width_mm: float | None = Field(default=None, ge=0.0)
    board_height_mm: float | None = Field(default=None, ge=0.0)
    material: str | None = None
    board_thickness_mm: float | None = Field(default=None, ge=0.0)
    outer_copper_weight: str | None = None
    inner_copper_weight: str | None = None
    min_trace_width_mm: float | None = Field(default=None, ge=0.0)
    min_trace_spacing_mm: float | None = Field(default=None, ge=0.0)
    min_via_drill_mm: float | None = Field(default=None, ge=0.0)
    min_via_diameter_mm: float | None = Field(default=None, ge=0.0)
    min_pth_drill_mm: float | None = Field(default=None, ge=0.0)
    min_npth_drill_mm: float | None = Field(default=None, ge=0.0)
    min_pth_annular_ring_mm: float | None = Field(default=None, ge=0.0)
    min_copper_to_edge_mm: float | None = Field(default=None, ge=0.0)
    copper_to_edge_evidence: CopperToEdgeEvidence | None = None
    min_plated_slot_width_mm: float | None = Field(default=None, ge=0.0)
    min_non_plated_slot_width_mm: float | None = Field(default=None, ge=0.0)
    min_soldermask_clearance_mm: float | None = Field(default=None, ge=0.0)
    min_silkscreen_to_pad_mm: float | None = Field(default=None, ge=0.0)
    vias_present: bool = False
    pth_holes_present: bool | None = None
    npth_holes_present: bool | None = None
    slots_present: bool | None = None
    castellated_holes_present: bool | None = None
    via_in_pad_present: bool | None = None
    blind_buried_via_present: bool | None = None

    @field_validator("project_id", mode="before")
    @classmethod
    def _measurement_project(cls, value: object) -> str:
        return _id(value, "project_id")

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _measurement_artifact(cls, value: object) -> str:
        return _fp(value, "artifact_fingerprint")

    @field_validator("pcb_type", "material", "outer_copper_weight", "inner_copper_weight", mode="before")
    @classmethod
    def _measurement_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 128)

    @field_validator("vias_present", "pth_holes_present", "npth_holes_present", "slots_present", "castellated_holes_present", "via_in_pad_present", "blind_buried_via_present", mode="before")
    @classmethod
    def _measurement_flags(cls, value: object, info) -> bool | None:
        if value is None and info.field_name != "vias_present":
            return None
        if type(value) is not bool:
            raise TypeError(f"{info.field_name} must be a bool")
        return value

    @field_validator("copper_to_edge_evidence", mode="before")
    @classmethod
    def _copper_to_edge_evidence(cls, value: object) -> CopperToEdgeEvidence | None:
        return None if value is None else CopperToEdgeEvidence.model_validate(_model_values(value))

    @model_validator(mode="after")
    def _copper_to_edge_binding(self) -> PCBManufacturingMeasurements:
        evidence = self.copper_to_edge_evidence
        if evidence is not None:
            if evidence.artifact_fingerprint != self.artifact_fingerprint:
                raise ValueError("copper-to-edge evidence artifact fingerprint mismatch")
            if self.min_copper_to_edge_mm is None:
                raise ValueError("copper-to-edge evidence requires a measured value")
            if not math.isclose(self.min_copper_to_edge_mm, evidence.actual_minimum_mm, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("copper-to-edge measurement and evidence mismatch")
        return self

    @classmethod
    def from_state(
        cls,
        state: PCBEngineeringState,
        *,
        copper_to_edge_evidence: CopperToEdgeEvidence | None = None,
    ) -> PCBManufacturingMeasurements:
        from embedded_copilot.manufacturing.board import min_trace_spacing_from_routes

        trace_widths = tuple(item.width_mm for item in state.routing)
        via_drills = tuple(item.drill_mm for item in state.vias if item.drill_mm is not None)
        via_diameters = tuple(item.diameter_mm for item in state.vias if item.diameter_mm is not None)
        outer_layers = tuple(state.board.copper_layers)
        blind_or_buried = None
        if state.vias:
            blind_or_buried = any(
                item.from_layer != outer_layers[0] or item.to_layer != outer_layers[-1]
                for item in state.vias
            ) if outer_layers else None
        return cls.create(
            project_id=state.project_id,
            artifact_fingerprint=state.source_artifact_fingerprint,
            layer_count=len(outer_layers) if outer_layers else None,
            board_width_mm=state.board.width_mm,
            board_height_mm=state.board.height_mm,
            material=state.stackup.material,
            board_thickness_mm=state.stackup.thickness_mm,
            min_trace_width_mm=min(trace_widths) if trace_widths else None,
            min_trace_spacing_mm=min_trace_spacing_from_routes(state.routing),
            min_via_drill_mm=min(via_drills) if via_drills else None,
            min_via_diameter_mm=min(via_diameters) if via_diameters else None,
            min_copper_to_edge_mm=(
                None if copper_to_edge_evidence is None else copper_to_edge_evidence.actual_minimum_mm
            ),
            copper_to_edge_evidence=copper_to_edge_evidence,
            vias_present=bool(state.vias),
            blind_buried_via_present=blind_or_buried,
        )

    @classmethod
    def from_artifact(
        cls,
        state: PCBEngineeringState,
        pcb_path,
    ) -> PCBManufacturingMeasurements:
        """Project state measurements and compute copper clearance from the artifact."""

        from embedded_copilot.manufacturing.board import measure_copper_to_edge

        measured = measure_copper_to_edge(
            pcb_path,
            artifact_fingerprint=state.source_artifact_fingerprint,
        )
        evidence = CopperToEdgeEvidence.create(
            artifact_fingerprint=measured.artifact_fingerprint,
            geometry_source=measured.geometry_source,
            measurement_method=measured.measurement_method,
            affected_objects=measured.affected_objects,
            actual_minimum_mm=measured.actual_minimum_mm,
            minimum_object=measured.minimum_object,
            edge_segment=measured.edge_segment,
        )
        return cls.from_state(state, copper_to_edge_evidence=evidence)


class DFTRequirement(PCBContract):
    requirement_id: str
    project_id: str
    name: str
    applicable: bool
    required: bool
    observed: bool | None = None
    evidence_refs: tuple[str, ...] = ()

    @field_validator("requirement_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return _text(value, "name", 256)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _tuple_text(value, "evidence_refs")


def build_jlcpcb_rigid_manufacturer_profile(
    project_id: str,
    *,
    source_fingerprint: str,
    retrieved_at: datetime,
    source_revision: str,
    layer_count: int | None = None,
    source_identity: str = "jlcpcb.com/capabilities/Capab",
) -> ManufacturerProfile:
    """Project the current official JLCPCB rigid-PCB capability snapshot.

    Numeric capabilities are deliberately supplied by the current official
    source snapshot at the call site.  The profile stores only the resulting
    typed rules and provenance; it never invents a board process selection.
    """

    source_ref = "knowledge://manufacturer/JLCPCB/rigid-capabilities"
    rules: list[ManufacturerCapabilityRule] = [
        ManufacturerCapabilityRule.create(
            rule_id="layer-count-min",
            category="LAYER_COUNT",
            measurement="layer_count",
            expected_value=1,
            comparison="GE",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="layer-count-max",
            category="LAYER_COUNT",
            measurement="layer_count",
            expected_value=32,
            comparison="LE",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="thickness-min",
            category="BOARD_THICKNESS",
            measurement="board_thickness_mm",
            expected_value=0.4,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="thickness-max",
            category="BOARD_THICKNESS",
            measurement="board_thickness_mm",
            expected_value=4.5,
            comparison="LE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="min-board-width",
            category="BOARD_DIMENSIONS",
            measurement="board_width_mm",
            expected_value=3.0,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="min-board-height",
            category="BOARD_DIMENSIONS",
            measurement="board_height_mm",
            expected_value=3.0,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
        ManufacturerCapabilityRule.create(
            rule_id="via-hole-min",
            category="VIA_DRILL",
            measurement="min_via_drill_mm",
            expected_value=0.15,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
            presence_measurement="vias_present",
        ),
        ManufacturerCapabilityRule.create(
            rule_id="via-diameter-min",
            category="VIA_DIAMETER",
            measurement="min_via_diameter_mm",
            expected_value=0.25,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
            presence_measurement="vias_present",
        ),
        ManufacturerCapabilityRule.create(
            rule_id="blind-buried-via-unsupported",
            category="BLIND_BURIED_VIA",
            measurement="blind_buried_via_present",
            expected_value=False,
            comparison="EQ",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="UNSUPPORTED_FEATURE",
            source_evidence_ref=source_ref,
            presence_measurement="vias_present",
        ),
        ManufacturerCapabilityRule.create(
            rule_id="copper-to-edge-min",
            category="COPPER_TO_EDGE",
            measurement="min_copper_to_edge_mm",
            expected_value=0.2,
            comparison="GE",
            unit="mm",
            severity=ManufacturerRuleSeverity.HARD_LIMIT,
            source_semantics="HARD_LIMIT",
            source_evidence_ref=source_ref,
        ),
    ]
    max_dimensions_by_layer = {
        1: (606.0, 510.0),
        2: (670.0, 600.0),
        4: (663.0, 593.0),
    }
    if layer_count in max_dimensions_by_layer:
        max_width, max_height = max_dimensions_by_layer[layer_count]
        dimension_condition: dict[str, int | float] = {
            "condition_layer_min": layer_count,
            "condition_layer_max": layer_count,
        }
        if layer_count == 2:
            dimension_condition["condition_thickness_min_mm"] = 0.8
        rules.extend(
            (
                ManufacturerCapabilityRule.create(
                    rule_id="max-board-width",
                    category="BOARD_DIMENSIONS",
                    measurement="board_width_mm",
                    expected_value=max_width,
                    comparison="LE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    **dimension_condition,
                ),
                ManufacturerCapabilityRule.create(
                    rule_id="max-board-height",
                    category="BOARD_DIMENSIONS",
                    measurement="board_height_mm",
                    expected_value=max_height,
                    comparison="LE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    **dimension_condition,
                ),
            )
        )
    elif layer_count is not None and layer_count >= 6:
        rules.extend(
            (
                ManufacturerCapabilityRule.create(
                    rule_id="max-board-width",
                    category="BOARD_DIMENSIONS",
                    measurement="board_width_mm",
                    expected_value=656.0,
                    comparison="LE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    condition_layer_min=6,
                    condition_layer_max=32,
                ),
                ManufacturerCapabilityRule.create(
                    rule_id="max-board-height",
                    category="BOARD_DIMENSIONS",
                    measurement="board_height_mm",
                    expected_value=586.0,
                    comparison="LE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    condition_layer_min=6,
                    condition_layer_max=32,
                ),
            )
        )
    trace_rules = (
        ("1oz", 0.10, 0.10, 1, 2),
        ("2oz", 0.16, 0.16, 1, 2),
        ("2.5oz", 0.20, 0.20, 1, 2),
        ("3.5oz", 0.25, 0.25, 1, 2),
        ("4.5oz", 0.30, 0.30, 1, 2),
        ("1oz", 0.09, 0.09, 3, 32),
        ("2oz", 0.15, 0.15, 3, 32),
    )
    for index, (weight, trace, spacing, layer_min, layer_max) in enumerate(trace_rules):
        condition = {
            "condition_outer_copper_weight": weight,
            "condition_layer_min": layer_min,
            "condition_layer_max": layer_max,
        }
        rules.extend(
            (
                ManufacturerCapabilityRule.create(
                    rule_id=f"trace-width-{index}",
                    category="TRACE_WIDTH",
                    measurement="min_trace_width_mm",
                    expected_value=trace,
                    comparison="GE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    **condition,
                ),
                ManufacturerCapabilityRule.create(
                    rule_id=f"trace-spacing-{index}",
                    category="TRACE_SPACING",
                    measurement="min_trace_spacing_mm",
                    expected_value=spacing,
                    comparison="GE",
                    unit="mm",
                    severity=ManufacturerRuleSeverity.HARD_LIMIT,
                    source_semantics="HARD_LIMIT",
                    source_evidence_ref=source_ref,
                    **condition,
                ),
            )
        )
    return ManufacturerProfile.create(
        project_id=project_id,
        manufacturer_id="JLCPCB",
        process_id="JLCPCB-RIGID-PCB",
        layer_count=layer_count,
        evidence_refs=(source_ref,),
        source_fingerprint=source_fingerprint,
        profile_id=f"jlcpcb-rigid-{source_fingerprint[7:23]}",
        pcb_type="RIGID",
        source_identity=source_identity,
        source_revision=source_revision,
        retrieved_at=retrieved_at,
        selection_provenance="USER_SELECTED",
        rules=tuple(rules),
    )


def build_jlcpcb_dfm_inputs(
    profile: ManufacturerProfile,
    measurements: PCBManufacturingMeasurements,
) -> tuple[tuple[EngineeringEvidenceRequirement, ...], EngineeringApplicabilityDecision, tuple[EngineeringCheck, ...]]:
    """Create DFM evidence/checks for the existing final closure verifier."""

    checked_profile = ManufacturerProfile.model_validate(profile.model_dump(mode="python"), strict=False)
    checked_measurements = PCBManufacturingMeasurements.model_validate(
        measurements.model_dump(mode="python"), strict=False
    )
    if checked_profile.manufacturer_id != "JLCPCB" or checked_profile.pcb_type != checked_measurements.pcb_type:
        raise ValueError("manufacturer profile is not applicable to the current PCB type")
    if checked_profile.project_id != checked_measurements.project_id:
        raise ValueError("manufacturer profile and PCB measurements belong to different projects")
    if (
        checked_profile.layer_count is not None
        and checked_measurements.layer_count is not None
        and checked_profile.layer_count != checked_measurements.layer_count
    ):
        raise ValueError("manufacturer profile layer applicability mismatch")
    if checked_profile.source_conflicts:
        raise ValueError("JLCPCB official source conflict requires resolution")

    def normalized_weight(value: str | None) -> str | None:
        return None if value is None else "".join(value.casefold().split())

    def active(rule: ManufacturerCapabilityRule) -> bool:
        layer = checked_measurements.layer_count
        if rule.condition_layer_min is not None and (layer is None or layer < rule.condition_layer_min):
            return False
        if rule.condition_layer_max is not None and (layer is None or layer > rule.condition_layer_max):
            return False
        thickness = checked_measurements.board_thickness_mm
        if rule.condition_thickness_min_mm is not None and (thickness is None or thickness < rule.condition_thickness_min_mm):
            return False
        if rule.condition_thickness_max_mm is not None and (thickness is None or thickness > rule.condition_thickness_max_mm):
            return False
        if rule.condition_outer_copper_weight is not None:
            return normalized_weight(checked_measurements.outer_copper_weight) == normalized_weight(rule.condition_outer_copper_weight)
        return True

    evidence: list[EngineeringEvidenceRequirement] = []
    checks: list[EngineeringCheck] = []
    copper_dependent = False
    for rule in checked_profile.rules:
        if rule.condition_outer_copper_weight is not None and rule.measurement in {"min_trace_width_mm", "min_trace_spacing_mm"}:
            copper_dependent = True
            if checked_measurements.outer_copper_weight is None:
                continue
        if not active(rule):
            continue
        presence = True if rule.presence_measurement is None else getattr(checked_measurements, rule.presence_measurement)
        if presence is False:
            continue
        requirement_id = f"dfm-{rule.rule_id}"
        actual = getattr(checked_measurements, rule.measurement)
        evidence.append(
            EngineeringEvidenceRequirement.create(
                requirement_id=requirement_id,
                project_id=checked_measurements.project_id,
                scope="PCB",
                affected_objects=(checked_measurements.artifact_fingerprint,),
                requirement_type=f"DFM_{rule.category}",
                value=rule.expected_value,
                rule=rule.source_semantics,
                unit=rule.unit,
                comparison=rule.comparison,
                source=EngineeringEvidenceSource.MANUFACTURER_PROFILE,
                source_ref="knowledge://manufacturer/JLCPCB/rigid-capabilities",
                evidence_locator=rule.source_evidence_ref,
                confidence=1.0,
                machine_evaluable=True,
                source_fingerprint=checked_profile.source_fingerprint,
            )
        )
        checks.append(
            EngineeringCheck.create(
                check_id=requirement_id,
                project_id=checked_measurements.project_id,
                verifier=EngineeringVerificationType.DFM,
                requirement_id=requirement_id,
                subject=rule.category,
                actual_value=actual,
                expected_value=rule.expected_value,
                comparison=rule.comparison,
                unit=rule.unit,
                evidence_refs=(requirement_id,),
                critical=rule.severity is ManufacturerRuleSeverity.HARD_LIMIT,
                blocking=rule.blocking,
                unknown_reason=(
                    "MISSING_CURRENT_PCB_MEASUREMENT"
                    if actual is None
                    else None
                ),
            )
        )
    if copper_dependent and checked_measurements.outer_copper_weight is not None:
        requirement_id = "dfm-process-copper-weight"
        evidence.append(
            EngineeringEvidenceRequirement.create(
                requirement_id=requirement_id,
                project_id=checked_measurements.project_id,
                scope="PCB",
                affected_objects=(checked_measurements.artifact_fingerprint,),
                requirement_type="DFM_COPPER_WEIGHT_SELECTION",
                value=checked_measurements.outer_copper_weight,
                rule="USER_SELECTED_PROCESS_PARAMETER",
                unit="oz",
                comparison="EQ",
                source=EngineeringEvidenceSource.USER_DECISION,
                source_ref="project://decision/JLCPCB/copper-weight",
                evidence_locator="project://decision/JLCPCB/copper-weight",
                confidence=1.0,
                machine_evaluable=True,
                source_fingerprint=checked_measurements.artifact_fingerprint,
            )
        )
        checks.append(
            EngineeringCheck.create(
                check_id=requirement_id,
                project_id=checked_measurements.project_id,
                verifier=EngineeringVerificationType.DFM,
                requirement_id=requirement_id,
                subject="OUTER_COPPER_WEIGHT",
                actual_value=checked_measurements.outer_copper_weight,
                expected_value=checked_measurements.outer_copper_weight,
                comparison="EQ",
                unit="oz",
                evidence_refs=(requirement_id,),
                critical=True,
                blocking=True,
            )
        )
    elif copper_dependent and checked_measurements.outer_copper_weight is None:
        requirement_id = "dfm-process-copper-weight"
        evidence.append(
            EngineeringEvidenceRequirement.create(
                requirement_id=requirement_id,
                project_id=checked_measurements.project_id,
                scope="PCB",
                affected_objects=(checked_measurements.artifact_fingerprint,),
                requirement_type="DFM_COPPER_WEIGHT_SELECTION",
                value="selected",
                rule="ORDER_OPTION_DEPENDENT",
                source=EngineeringEvidenceSource.MANUFACTURER_PROFILE,
                source_ref="project://decision/JLCPCB/copper-weight",
                evidence_locator="knowledge://manufacturer/JLCPCB/rigid-capabilities",
                confidence=1.0,
                machine_evaluable=True,
                source_fingerprint=checked_profile.source_fingerprint,
            )
        )
        checks.append(
            EngineeringCheck.create(
                check_id=requirement_id,
                project_id=checked_measurements.project_id,
                verifier=EngineeringVerificationType.DFM,
                requirement_id=requirement_id,
                subject="OUTER_COPPER_WEIGHT",
                actual_value=None,
                expected_value="selected",
                comparison="EQ",
                evidence_refs=(requirement_id,),
                critical=True,
                unknown_reason="USER_DECISION_REQUIRED:Select copper weight",
            )
        )
    applicability = EngineeringApplicabilityDecision.create(
        verifier=EngineeringVerificationType.DFM,
        applicability=PCBApplicability.REQUIRED,
        reason="current canonical rigid PCB is evaluated against the user-selected JLCPCB profile",
        evidence_refs=(checked_profile.profile_id or checked_profile.process_id,),
    )
    return tuple(evidence), applicability, tuple(checks)


class AcceptedRisk(PCBContract):
    risk_id: str
    source_verification: str
    issue_or_unknown: str
    reason: str
    decision: str
    actor: str
    timestamp: datetime
    affected_objects: tuple[str, ...]
    required_reverification: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("risk_id", "source_verification", "actor", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("issue_or_unknown", "reason", "decision", mode="before")
    @classmethod
    def _risk_text(cls, value: object, info) -> str:
        return _text(value, info.field_name, 1024)

    @field_validator("affected_objects", "required_reverification", "evidence_refs", mode="before")
    @classmethod
    def _risk_tuples(cls, value: object, info) -> tuple[str, ...]:
        return _tuple_text(value, info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("timestamp must be UTC") from error
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be UTC")
        return value.astimezone(UTC)


class HumanApprovalBinding(PCBContract):
    approval_id: str
    reviewer: str
    approved_at: datetime
    decision: ApprovalDecision
    project_id: str | None = None
    conversation_id: str | None = None
    summary_fingerprint: str
    artifact_fingerprint: str
    state_fingerprint: str | None = None
    manufacturer_profile_fingerprint: str | None = None
    dfm_fingerprint: str | None = None
    ready_for_fab_target_fingerprint: str | None = None
    selected_by: str | None = None
    provenance: str | None = None
    blocking_issue_count: int = Field(ge=0)
    blocking_unknown_count: int = Field(ge=0)

    @field_validator("approval_id", "reviewer", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value: object) -> ApprovalDecision:
        return value if isinstance(value, ApprovalDecision) else ApprovalDecision(value)

    @field_validator(
        "summary_fingerprint",
        "artifact_fingerprint",
        "state_fingerprint",
        "manufacturer_profile_fingerprint",
        "dfm_fingerprint",
        "ready_for_fab_target_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str | None:
        return None if value is None else _fp(value, info.field_name)

    @field_validator("approved_at", mode="before")
    @classmethod
    def _approved_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("approved_at must be UTC") from error
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("approved_at must be UTC")
        return value.astimezone(UTC)

    @field_validator("selected_by", mode="before")
    @classmethod
    def _selected_by(cls, value: object) -> str | None:
        if value is None:
            return None
        if value != "USER":
            raise ValueError("selected_by must be USER")
        return value

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value: object) -> str | None:
        if value is None:
            return None
        if value != "EXPLICIT_USER_APPROVAL":
            raise ValueError("provenance must be EXPLICIT_USER_APPROVAL")
        return value


class VerificationTransition(PCBContract):
    verifier: str
    before: VerificationStatus
    after: VerificationStatus
    reason: str

    @field_validator("verifier", "reason", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, info.field_name, 512)

    @field_validator("before", "after", mode="before")
    @classmethod
    def _status(cls, value: object) -> VerificationStatus:
        return value if isinstance(value, VerificationStatus) else VerificationStatus(value)


class PCBEngineeringVerificationSummary(PCBContract):
    _legacy_fingerprint_excludes = ("blocking_unknown_categories",)
    project_id: str
    pcb_state_fingerprint: str
    verification_plan_fingerprint: str
    required_results: tuple[VerificationResult, ...]
    optional_results: tuple[VerificationResult, ...]
    not_applicable_results: tuple[VerificationResult, ...]
    unknown_results: tuple[VerificationResult, ...]
    blocking_issues: tuple[VerificationIssue, ...]
    blocking_unknowns: tuple[str, ...]
    blocking_unknown_categories: tuple[BlockingUnknownCategory, ...] = ()
    accepted_risks: tuple[AcceptedRisk, ...]
    evidence_refs: tuple[str, ...]
    physics_reverification: tuple[VerificationTransition, ...] = ()
    erc_status: str = "UNKNOWN"
    drc_status: str = "UNKNOWN"
    hw_fw_consistency_status: str = "UNKNOWN"
    capability_complete: bool = True
    canonical_executed: bool = False

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return _id(value, "project_id")

    @field_validator("pcb_state_fingerprint", "verification_plan_fingerprint", mode="before")
    @classmethod
    def _summary_fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("blocking_unknowns", "evidence_refs", mode="before")
    @classmethod
    def _summary_tuples(cls, value: object, info) -> tuple[str, ...]:
        return _tuple_text(value, info.field_name)

    @field_validator("blocking_unknown_categories", mode="before")
    @classmethod
    def _unknown_categories(cls, value: object) -> tuple[BlockingUnknownCategory, ...]:
        if type(value) not in (tuple, list):
            raise TypeError("blocking_unknown_categories must be a tuple or list")
        return tuple(
            item if isinstance(item, BlockingUnknownCategory) else BlockingUnknownCategory(item)
            for item in value
        )

    @field_validator("erc_status", "drc_status", "hw_fw_consistency_status", mode="before")
    @classmethod
    def _gate_statuses(cls, value: object, info) -> str:
        return _text(value, info.field_name, 64)

    @model_validator(mode="after")
    def _binding(self) -> PCBEngineeringVerificationSummary:
        all_results = (*self.required_results, *self.optional_results, *self.not_applicable_results, *self.unknown_results)
        if any(item.project_id != self.project_id for item in all_results):
            raise ValueError("engineering verification result project mismatch")
        if any(item.stale for item in all_results):
            raise ValueError("stale verification result cannot enter a current summary")
        return self


class ReadyForFabGate(PCBContract):
    project_id: str
    summary_fingerprint: str
    artifact_fingerprint: str
    status: ReadyForFabStatus
    reason: str
    blocking_issue_count: int = Field(ge=0)
    blocking_unknown_count: int = Field(ge=0)
    approval_valid: bool
    stale: bool = False
    stale_reason: str | None = None
    next_action: str | None = None

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return _id(value, "project_id")

    @field_validator("summary_fingerprint", "artifact_fingerprint", mode="before")
    @classmethod
    def _gate_fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _gate_status(cls, value: object) -> ReadyForFabStatus:
        return value if isinstance(value, ReadyForFabStatus) else ReadyForFabStatus(value)

    @field_validator("reason", "stale_reason", "next_action", mode="before")
    @classmethod
    def _gate_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 1024)

    @model_validator(mode="after")
    def _stale_binding(self) -> ReadyForFabGate:
        if self.stale and self.stale_reason is None:
            raise ValueError("stale READY_FOR_FAB gate requires a reason")
        if not self.stale and self.stale_reason is not None:
            raise ValueError("current READY_FOR_FAB gate cannot have stale reason")
        return self


def build_ready_for_fab_approval_target(
    summary: PCBEngineeringVerificationSummary,
    artifact_fingerprint: str,
) -> ReadyForFabGate:
    """Build the deterministic pre-approval target without creating a PASS cycle."""

    project_id = _id(getattr(summary, "project_id", None), "project_id")
    summary_fingerprint = _fp(getattr(summary, "fingerprint", None), "summary_fingerprint")
    blocking_issues = tuple(getattr(summary, "blocking_issues", ()))
    blocking_unknowns = tuple(getattr(summary, "blocking_unknowns", ()))
    if blocking_issues or blocking_unknowns:
        status = ReadyForFabStatus.BLOCKED
        reason = "blocking verification issue or unknown remains"
        next_action = "resolve the smallest blocking issue or authoritative evidence gap"
    else:
        status = ReadyForFabStatus.AWAITING_APPROVAL
        reason = "current verification summary has no blockers but lacks valid human approval"
        next_action = "human approval for current verification summary"
    return ReadyForFabGate.create(
        project_id=project_id,
        summary_fingerprint=summary_fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        status=status,
        reason=reason,
        blocking_issue_count=len(blocking_issues),
        blocking_unknown_count=len(blocking_unknowns),
        approval_valid=False,
        next_action=next_action,
    )


class PCBEngineeringVerificationClosure(PCBContract):
    verification_plan: PCBVerificationPlan
    summary: PCBEngineeringVerificationSummary
    ready_for_fab: ReadyForFabGate
    approval: HumanApprovalBinding | None = None

    @model_validator(mode="after")
    def _closure_binding(self) -> PCBEngineeringVerificationClosure:
        if self.summary.verification_plan_fingerprint != self.verification_plan.fingerprint:
            raise ValueError("closure summary plan binding mismatch")
        if self.ready_for_fab.summary_fingerprint != self.summary.fingerprint:
            raise ValueError("closure gate summary binding mismatch")
        if self.approval is not None and (
            self.approval.summary_fingerprint != self.summary.fingerprint
            or self.approval.artifact_fingerprint != self.ready_for_fab.artifact_fingerprint
            or self.approval.blocking_issue_count != len(self.summary.blocking_issues)
            or self.approval.blocking_unknown_count != len(self.summary.blocking_unknowns)
        ):
            raise ValueError("closure approval binding mismatch")
        return self


_PHYSICS_ALIASES = {
    "DECOUPLING": "DecouplingVerifier",
    "DecouplingVerifier": "DecouplingVerifier",
    "RETURN_PATH": "ReturnPathVerifier",
    "ReturnPathVerifier": "ReturnPathVerifier",
    "POWER_LOOP": "PowerLoopVerifier",
    "PowerLoopVerifier": "PowerLoopVerifier",
    "DIFFERENTIAL_PAIR": "DifferentialPairVerifier",
    "DifferentialPairVerifier": "DifferentialPairVerifier",
    "FOOTPRINT_SYMBOL_PIN_CONSISTENCY": "FootprintPinConsistencyVerifier",
    "FootprintPinConsistencyVerifier": "FootprintPinConsistencyVerifier",
    "DFM": "DFMVerifier",
    "DFMVerifier": "DFMVerifier",
    "CRITICAL_DFT": "DFTVerifier",
    "DFTVerifier": "DFTVerifier",
    "DATASHEET_COMPLIANCE": "DatasheetComplianceVerifier",
    "DatasheetComplianceVerifier": "DatasheetComplianceVerifier",
    "ELECTRICAL_RATING": "ELECTRICAL_RATING",
    "POWER_BUDGET": "POWER_BUDGET",
    "DERATING": "DERATING",
    "THERMAL": "THERMAL",
    "MECHANICAL": "MECHANICAL",
}


class PCBEngineeringVerificationService:
    """Close G7 over the existing state and unified VerificationResult contract."""

    def extend_plan(
        self,
        plan: PCBVerificationPlan,
        *,
        applicability: tuple[EngineeringApplicabilityDecision, ...],
        evidence: tuple[EngineeringEvidenceRequirement, ...],
    ) -> PCBVerificationPlan:
        checked = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        if len({item.verifier for item in applicability}) != len(applicability):
            raise ValueError("engineering applicability must be unique")
        by_status = {
            PCBApplicability.REQUIRED: [],
            PCBApplicability.OPTIONAL: [],
            PCBApplicability.NOT_APPLICABLE: [],
            PCBApplicability.UNKNOWN: [],
        }
        for item in applicability:
            by_status[item.applicability].append(item.verifier.value)
        evidence_fp = None if not evidence else _evidence_digest(evidence)
        values = checked.model_dump(mode="python", exclude={"fingerprint"})
        values.update(
            engineering_required_verifiers=tuple(sorted(by_status[PCBApplicability.REQUIRED])),
            engineering_optional_verifiers=tuple(sorted(by_status[PCBApplicability.OPTIONAL])),
            engineering_not_applicable_verifiers=tuple(sorted(by_status[PCBApplicability.NOT_APPLICABLE])),
            engineering_unknown_applicability=tuple(sorted(by_status[PCBApplicability.UNKNOWN])),
            engineering_evidence_fingerprint=evidence_fp,
        )
        return PCBVerificationPlan.create(**values)

    def close(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        plan: PCBVerificationPlan,
        *,
        existing_results: tuple[VerificationResult, ...] = (),
        previous_results: tuple[VerificationResult, ...] = (),
        evidence: tuple[EngineeringEvidenceRequirement, ...] = (),
        applicability: tuple[EngineeringApplicabilityDecision, ...] = (),
        checks: tuple[EngineeringCheck, ...] = (),
        manufacturer_profile: ManufacturerProfile | None = None,
        dft_requirements: tuple[DFTRequirement, ...] = (),
        accepted_risks: tuple[AcceptedRisk, ...] = (),
        approval: HumanApprovalBinding | None = None,
        artifact_fingerprint: str | None = None,
        erc_status: str = "UNKNOWN",
        drc_status: str = "UNKNOWN",
        hw_fw_consistency_status: str = "UNKNOWN",
        reverified_verifiers: tuple[str, ...] = (),
        canonical_executed: bool = False,
    ) -> PCBEngineeringVerificationClosure:
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_constraints = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        checked_plan = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        if checked_state.project_id != checked_constraints.project_id or checked_state.project_id != checked_plan.project_id:
            raise ValueError("G7 inputs belong to different projects")
        if checked_constraints.state_fingerprint != checked_state.fingerprint or checked_plan.pcb_state_fingerprint != checked_state.fingerprint or checked_plan.constraint_set_fingerprint != checked_constraints.fingerprint:
            raise ValueError("G7 input lineage is stale or mismatched")
        checked_evidence = tuple(EngineeringEvidenceRequirement.model_validate(_model_values(item)) for item in evidence)
        if any(item.project_id != checked_state.project_id for item in checked_evidence):
            raise ValueError("engineering evidence project mismatch")
        checked_applicability = tuple(EngineeringApplicabilityDecision.model_validate(_model_values(item)) for item in applicability)
        checked_checks = tuple(EngineeringCheck.model_validate(_model_values(item)) for item in checks)
        if any(item.project_id != checked_state.project_id for item in checked_checks):
            raise ValueError("engineering check project mismatch")
        if any(item.requirement_id not in {req.requirement_id for req in checked_evidence} for item in checked_checks):
            raise ValueError("engineering check is not bound to authoritative evidence")
        checked_profile = None if manufacturer_profile is None else ManufacturerProfile.model_validate(_model_values(manufacturer_profile))
        if checked_profile is not None and checked_profile.project_id != checked_state.project_id:
            raise ValueError("manufacturer profile project mismatch")
        checked_dft = tuple(DFTRequirement.model_validate(_model_values(item)) for item in dft_requirements)
        if any(item.project_id != checked_state.project_id for item in checked_dft):
            raise ValueError("DFT requirement project mismatch")
        checked_risks = tuple(
            AcceptedRisk.model_validate(item.model_dump(mode="python") if hasattr(item, "model_dump") else item)
            for item in accepted_risks
        )
        checked_approval = None if approval is None else HumanApprovalBinding.model_validate(_model_values(approval))
        if checked_approval is not None and checked_approval.artifact_fingerprint != (artifact_fingerprint or checked_state.source_artifact_fingerprint):
            raise ValueError("approval artifact fingerprint mismatch")
        if checked_approval is not None and checked_approval.state_fingerprint is not None and checked_approval.state_fingerprint != checked_state.fingerprint:
            raise ValueError("approval PCB state fingerprint mismatch")
        if checked_approval is not None and checked_approval.manufacturer_profile_fingerprint is not None:
            if checked_profile is None or checked_approval.manufacturer_profile_fingerprint != checked_profile.fingerprint:
                raise ValueError("approval manufacturer profile fingerprint mismatch")

        extended_plan = self.extend_plan(checked_plan, applicability=checked_applicability, evidence=checked_evidence)
        common_fingerprints = (checked_state.fingerprint, checked_constraints.fingerprint, extended_plan.fingerprint)
        current_engineering = {
            *checked_plan.engineering_required_verifiers,
            *checked_plan.engineering_optional_verifiers,
            *checked_plan.engineering_not_applicable_verifiers,
            *checked_plan.engineering_unknown_applicability,
        }
        extended_engineering = {
            *extended_plan.engineering_required_verifiers,
            *extended_plan.engineering_optional_verifiers,
            *extended_plan.engineering_not_applicable_verifiers,
            *extended_plan.engineering_unknown_applicability,
        }
        dfm_only_extension = extended_engineering.difference(current_engineering) <= {"DFM"}
        physics_fingerprints = (
            (checked_state.fingerprint, checked_constraints.fingerprint, checked_plan.fingerprint)
            if dfm_only_extension
            else common_fingerprints
        )
        physics = self._physics_results(
            checked_state,
            checked_constraints,
            extended_plan,
            existing_results=existing_results,
            common_fingerprints=physics_fingerprints,
            alternative_common_fingerprints=(
                (checked_state.fingerprint, checked_constraints.fingerprint, extended_plan.fingerprint)
                if dfm_only_extension and extended_plan.fingerprint != checked_plan.fingerprint
                else ()
            ),
        )
        engineering = tuple(
            self._engineering_result(
                kind,
                checked_state,
                common_fingerprints + (
                    (checked_profile.fingerprint,)
                    if kind is EngineeringVerificationType.DFM and checked_profile is not None
                    else ()
                ),
                checked_evidence,
                checked_applicability,
                checked_checks,
                checked_profile,
                checked_dft,
            )
            for kind in EngineeringVerificationType
        )
        all_results = (*physics, *engineering)
        required_names = set(extended_plan.required_verifiers) | set(extended_plan.engineering_required_verifiers)
        optional_names = set(extended_plan.optional_verifiers) | set(extended_plan.engineering_optional_verifiers)
        not_applicable_names = set(extended_plan.not_applicable_verifiers) | set(extended_plan.engineering_not_applicable_verifiers)
        unknown_names = set(extended_plan.unknown_applicability) | set(extended_plan.engineering_unknown_applicability)
        by_name = {
            _PHYSICS_ALIASES.get(item.verifier, item.verifier): item
            for item in physics
        }
        for item in engineering:
            name = _PHYSICS_ALIASES.get(item.verifier, item.verifier)
            existing = by_name.get(name)
            # Engineering projections are the authority for their own typed
            # checks.  Preserve a real physics result (including a stale
            # synthetic result) when the typed decision explicitly says the
            # engineering verifier is not applicable.
            if existing is None or item.applicability != PCBApplicability.NOT_APPLICABLE.value:
                by_name[name] = item
        required_results = tuple(by_name[name] for name in sorted(required_names) if name in by_name)
        optional_results = tuple(by_name[name] for name in sorted(optional_names) if name in by_name)
        not_applicable_results = tuple(by_name[name] for name in sorted(not_applicable_names) if name in by_name)
        unknown_results = tuple(by_name[name] for name in sorted(unknown_names) if name in by_name)
        # A result with a status inconsistent with plan applicability is still
        # retained, but the gate fails closed through the blocking analysis.
        blocking_issues = list(self._gate_status_issues(erc_status, drc_status, hw_fw_consistency_status, checked_state.project_id))
        blocking_unknowns = list(self._gate_status_unknowns(erc_status, drc_status, hw_fw_consistency_status))
        for result in required_results:
            if result.status is VerificationStatus.FAIL:
                blocking_issues.extend(result.issues or (self._fallback_issue(result),))
            elif result.status in {VerificationStatus.UNKNOWN, VerificationStatus.ESCALATE} or result.stale:
                blocking_unknowns.extend(f"{result.verifier}:{unknown}" for unknown in (result.unknowns or ("RESULT_NOT_VERIFIED",)))
        for name in sorted(unknown_names):
            if name not in by_name:
                blocking_unknowns.append(f"{name}:APPLICABILITY_UNKNOWN")
        accepted = self._validate_risks(checked_risks, required_results, blocking_unknowns)
        blocking_unknown_categories = self._unknown_categories(blocking_unknowns)
        evidence_refs = tuple(sorted({
            *[item.requirement_id for item in checked_evidence],
            *[item.check_id for item in checked_checks],
            *[item.evidence_id for result in all_results for item in result.evidence],
            *[ref for result in all_results for ref in result.evidence_refs],
            *[ref for risk in accepted for ref in risk.evidence_refs],
        }))
        transitions = self._transitions(previous_results, all_results, reverified_verifiers)
        summary = PCBEngineeringVerificationSummary.create(
            project_id=checked_state.project_id,
            pcb_state_fingerprint=checked_state.fingerprint,
            verification_plan_fingerprint=extended_plan.fingerprint,
            required_results=required_results,
            optional_results=optional_results,
            not_applicable_results=not_applicable_results,
            unknown_results=unknown_results,
            blocking_issues=tuple(dict.fromkeys(blocking_issues)),
            blocking_unknowns=tuple(sorted(set(blocking_unknowns))),
            blocking_unknown_categories=blocking_unknown_categories,
            accepted_risks=accepted,
            evidence_refs=evidence_refs,
            physics_reverification=transitions,
            erc_status=erc_status,
            drc_status=drc_status,
            hw_fw_consistency_status=hw_fw_consistency_status,
            capability_complete=True,
            canonical_executed=canonical_executed,
        )
        approval_target = build_ready_for_fab_approval_target(
            summary,
            artifact_fingerprint or checked_state.source_artifact_fingerprint,
        )
        dfm_result = next(
            (
                item
                for item in (*summary.required_results, *summary.optional_results)
                if item.verifier in {"DFM", "DFMVerifier"}
            ),
            None,
        )
        approval_bound = bool(
            checked_approval is not None
            and checked_approval.summary_fingerprint == summary.fingerprint
            and checked_approval.artifact_fingerprint == (artifact_fingerprint or checked_state.source_artifact_fingerprint)
            and checked_approval.blocking_issue_count == len(summary.blocking_issues)
            and checked_approval.blocking_unknown_count == len(summary.blocking_unknowns)
            and (checked_approval.project_id is None or checked_approval.project_id == checked_state.project_id)
            and (
                checked_approval.ready_for_fab_target_fingerprint is None
                or checked_approval.ready_for_fab_target_fingerprint == approval_target.fingerprint
            )
            and (
                checked_approval.dfm_fingerprint is None
                or (dfm_result is not None and checked_approval.dfm_fingerprint == dfm_result.fingerprint)
            )
        )
        approval_valid = approval_bound and checked_approval is not None and checked_approval.decision is ApprovalDecision.APPROVED
        if summary.blocking_issues or summary.blocking_unknowns:
            gate_status = ReadyForFabStatus.BLOCKED
            reason = "blocking verification issue or unknown remains"
            next_action = "resolve the smallest blocking issue or authoritative evidence gap"
        elif not approval_valid:
            gate_status = ReadyForFabStatus.AWAITING_APPROVAL
            reason = "current verification summary has no blockers but lacks valid human approval"
            next_action = "human approval for current verification summary"
        else:
            gate_status = ReadyForFabStatus.PASS
            reason = "all required verification results passed and approval is current"
            next_action = None
        gate = ReadyForFabGate.create(
            project_id=checked_state.project_id,
            summary_fingerprint=summary.fingerprint,
            artifact_fingerprint=artifact_fingerprint or checked_state.source_artifact_fingerprint,
            status=gate_status,
            reason=reason,
            blocking_issue_count=len(summary.blocking_issues),
            blocking_unknown_count=len(summary.blocking_unknowns),
            approval_valid=approval_valid,
            next_action=next_action,
        )
        return PCBEngineeringVerificationClosure.create(
            verification_plan=extended_plan,
            summary=summary,
            ready_for_fab=gate,
            approval=checked_approval if approval_bound else None,
        )

    def _physics_results(
        self,
        state,
        constraints,
        plan,
        *,
        existing_results,
        common_fingerprints,
        alternative_common_fingerprints=(),
    ):
        normalized = {
            _PHYSICS_ALIASES.get(item.verifier, item.verifier): item
            for item in existing_results
            if item.project_id == state.project_id
        }
        names = set(plan.required_verifiers) | set(plan.optional_verifiers) | set(plan.not_applicable_verifiers) | set(plan.unknown_applicability)
        results = []
        for name in sorted(names):
            result = normalized.get(name)
            required_fingerprints = self._result_fingerprints(
                name,
                state,
                common_fingerprints,
            )
            alternative_fingerprints = self._result_fingerprints(
                name,
                state,
                alternative_common_fingerprints,
            ) if alternative_common_fingerprints else ()
            current = (
                result is not None
                and not result.stale
                and (
                    set(required_fingerprints).issubset(set(result.compared_fingerprints))
                    or (
                        alternative_fingerprints
                        and set(alternative_fingerprints).issubset(set(result.compared_fingerprints))
                    )
                    or (
                        name == "FootprintPinConsistencyVerifier"
                        and set(common_fingerprints).issubset(set(result.compared_fingerprints))
                    )
                    or (
                        name == "FootprintPinConsistencyVerifier"
                        and alternative_common_fingerprints
                        and set(alternative_common_fingerprints).issubset(set(result.compared_fingerprints))
                    )
                )
            )
            if current:
                results.append(result)
                continue
            if name in plan.not_applicable_verifiers:
                results.append(self._synthetic_result(name, state.project_id, common_fingerprints, VerificationStatus.NOT_APPLICABLE, PCBApplicability.NOT_APPLICABLE.value, ()))
            else:
                unknown = "MISSING_EXISTING_G7_RESULT" if result is None else "STALE_EXISTING_G7_RESULT"
                results.append(self._synthetic_result(name, state.project_id, common_fingerprints, VerificationStatus.UNKNOWN, PCBApplicability.UNKNOWN.value, (unknown,)))
        return tuple(results)

    @staticmethod
    def _result_fingerprints(verifier, state, common_fingerprints):
        """Return only the dependencies owned by the selected verifier.

        B5 deliberately uses a mapping projection fingerprint and an authority
        fingerprint instead of the full G7 plan fingerprint.  Requiring the
        common physics tuple here made a valid current B5 result look stale
        whenever Final Closure extended the plan with engineering evidence.
        """

        if verifier == "FootprintPinConsistencyVerifier":
            from .mapping import mapping_state_fingerprint

            return tuple(
                item
                for item in (
                    mapping_state_fingerprint(state),
                    state.source_artifact_fingerprint,
                    state.design_truth_fingerprint,
                    state.hardware_truth_fingerprint,
                )
                if item is not None
            )
        return common_fingerprints

    @staticmethod
    def _unknown_categories(values):
        categories = set()
        for value in values:
            text = str(value).upper()
            if "STALE_EXISTING_G7_RESULT" in text or text.startswith("STALE_RESULT"):
                categories.add(BlockingUnknownCategory.STALE_RESULT)
            elif "MISSING_EXISTING_G7_RESULT" in text or text.startswith("MISSING_RESULT"):
                categories.add(BlockingUnknownCategory.MISSING_RESULT)
            elif any(token in text for token in ("APPROVAL", "HUMAN_APPROVAL")):
                categories.add(BlockingUnknownCategory.APPROVAL)
            elif any(token in text for token in ("ENVIRONMENT", "TOOLCHAIN", "BACKEND")):
                categories.add(BlockingUnknownCategory.ENVIRONMENT)
            elif any(
                token in text
                for token in (
                    "MACHINE_EVALUABLE",
                    "MANUFACTURER_PROFILE",
                    "USER_DECISION_REQUIRED",
                    "COPPER_WEIGHT",
                    "BOARD_THICKNESS",
                    "EVIDENCE_GAP",
                    "PAD_PIN_IDENTITY_MISSING",
                    "DATASHEET_PIN_EVIDENCE_MISSING",
                    "PHYSICAL_ORIENTATION_UNVERIFIED",
                    "SYMBOL_IDENTITY_UNVERIFIED",
                    "ROLE_EVIDENCE_INCOMPLETE",
                )
            ):
                categories.add(BlockingUnknownCategory.ENGINEERING_EVIDENCE_GAP)
            else:
                categories.add(BlockingUnknownCategory.EXECUTION_GAP)
        return tuple(sorted(categories, key=lambda item: item.value))

    @staticmethod
    def _synthetic_result(verifier, project_id, fingerprints, status, applicability, unknowns):
        return VerificationResult.create(
            verification_id=f"g7-final-{verifier}",
            project_id=project_id,
            target_id="pcb-engineering",
            status=status,
            attempt=0,
            compared_fingerprints=tuple(fingerprints),
            evidence_refs=(),
            summary=f"G7 final closure has {status.value} for {verifier}",
            verifier=verifier,
            scope="PCB",
            unknowns=unknowns,
            applicability=applicability,
            revision=fingerprints[0],
        )

    def _engineering_result(self, kind, state, fingerprints, evidence, applicability, checks, profile, dft):
        decision = next((item for item in applicability if item.verifier is kind), None)
        app = PCBApplicability.UNKNOWN if decision is None else decision.applicability
        refs = () if decision is None else decision.evidence_refs
        if kind is EngineeringVerificationType.DFM and profile is None and app is not PCBApplicability.NOT_APPLICABLE:
            unknowns = ("MISSING_MANUFACTURER_PROFILE",)
            return self._engineering_result_from_values(kind, state.project_id, fingerprints, app, VerificationStatus.UNKNOWN, unknowns, refs, ())
        if kind is EngineeringVerificationType.CRITICAL_DFT and dft:
            local_checks = tuple(
                EngineeringCheck.create(
                    check_id=item.requirement_id,
                    project_id=item.project_id,
                    verifier=kind,
                    requirement_id=item.requirement_id,
                    subject=item.name,
                    actual_value=item.observed,
                    expected_value=True,
                    comparison="EQ",
                    evidence_refs=item.evidence_refs,
                )
                for item in dft if item.applicable and item.required
            )
        else:
            local_checks = tuple(item for item in checks if item.verifier is kind)
        if app is PCBApplicability.NOT_APPLICABLE:
            return self._engineering_result_from_values(kind, state.project_id, fingerprints, app, VerificationStatus.NOT_APPLICABLE, (), refs, ())
        if not local_checks:
            return self._engineering_result_from_values(kind, state.project_id, fingerprints, app, VerificationStatus.UNKNOWN, ("MISSING_MACHINE_EVALUABLE_CHECK",), refs, ())
        statuses: list[VerificationStatus] = []
        issues: list[VerificationIssue] = []
        unknowns: list[str] = []
        evidence_refs = set(refs)
        if kind is EngineeringVerificationType.DFM and profile is not None:
            evidence_refs.add(profile.profile_id or profile.process_id)
        for check in local_checks:
            evidence_refs.update(check.evidence_refs)
            if not check.evidence_refs:
                if check.blocking:
                    statuses.append(VerificationStatus.UNKNOWN)
                    unknowns.append(f"{check.check_id}:MISSING_EVIDENCE")
                else:
                    statuses.append(VerificationStatus.WARNING)
                continue
            outcome = self._compare(check.actual_value, check.expected_value, check.comparison)
            if outcome is None:
                if check.blocking:
                    statuses.append(VerificationStatus.UNKNOWN)
                    unknowns.append(f"{check.check_id}:{check.unknown_reason or 'MISSING_ACTUAL_OR_LIMIT'}")
                else:
                    statuses.append(VerificationStatus.WARNING)
            elif outcome:
                statuses.append(VerificationStatus.PASS)
            else:
                statuses.append(VerificationStatus.FAIL if check.blocking else VerificationStatus.WARNING)
                issues.append(VerificationIssue.create(
                    issue_id=f"g7-{kind.value.lower()}-{check.check_id}",
                    severity=("CRITICAL" if check.critical else "ERROR") if check.blocking else "WARNING",
                    category=kind.value,
                    location=check.subject,
                    affected_objects=(check.subject,),
                    problem=f"{kind.value} check failed for {check.subject}",
                    why="actual value violates the authoritative engineering requirement",
                    evidence=tuple(check.evidence_refs),
                    recommendation="review the evidence-bound requirement and submit a repair proposal if the design must change",
                    reverification=(kind.value,),
                ))
        if VerificationStatus.FAIL in statuses:
            status = VerificationStatus.FAIL
        elif VerificationStatus.UNKNOWN in statuses:
            status = VerificationStatus.UNKNOWN
        elif VerificationStatus.WARNING in statuses:
            status = VerificationStatus.WARNING
        else:
            status = VerificationStatus.PASS
        return self._engineering_result_from_values(kind, state.project_id, fingerprints, app, status, tuple(sorted(set(unknowns))), tuple(sorted(evidence_refs)), tuple(issues))

    @staticmethod
    def _engineering_result_from_values(kind, project_id, fingerprints, applicability, status, unknowns, refs, issues):
        return VerificationResult.create(
            verification_id=f"g7-final-{kind.value.lower()}",
            project_id=project_id,
            target_id="pcb-engineering",
            status=status,
            attempt=0,
            compared_fingerprints=tuple(fingerprints),
            evidence_refs=tuple(sorted(set(refs))),
            summary=f"G7 final closure {kind.value} is {status.value}",
            verifier=kind.value,
            scope="PCB_ENGINEERING",
            issues=tuple(issues),
            unknowns=tuple(unknowns),
            applicability=applicability.value,
            revision=fingerprints[0],
        )

    @staticmethod
    def _compare(actual, expected, comparison):
        if actual is None or expected is None:
            return None
        operator = (comparison or "EQ").upper()
        if isinstance(actual, (int, float)) and not isinstance(actual, bool) and isinstance(expected, (int, float)) and not isinstance(expected, bool):
            if not all(math.isfinite(float(item)) for item in (actual, expected)):
                return None
            left, right = float(actual), float(expected)
        else:
            left, right = actual, expected
        return {
            "LE": left <= right,
            "LT": left < right,
            "GE": left >= right,
            "GT": left > right,
            "EQ": left == right,
        }.get(operator)

    @staticmethod
    def _fallback_issue(result):
        return VerificationIssue.create(
            issue_id=f"g7-blocking-{result.verifier.lower()}",
            severity="ERROR",
            category=result.verifier,
            affected_objects=("pcb-engineering",),
            problem=f"required verification {result.verifier} failed",
            why="the unified verification result is not PASS",
            evidence=tuple(result.evidence_refs),
            recommendation="resolve the verification failure and re-run the affected verifier",
            reverification=(result.verifier,),
        )

    @staticmethod
    def _gate_status_issues(erc, drc, hw_fw, project_id):
        issues = []
        for label, status in (("ERC", erc), ("DRC", drc), ("HW_FW_CONSISTENCY", hw_fw)):
            if str(status).upper() == "FAIL":
                issues.append(VerificationIssue.create(
                    issue_id=f"g7-{label.lower()}-failed",
                    severity="CRITICAL",
                    category=label,
                    affected_objects=(project_id,),
                    problem=f"upstream {label} is not PASS",
                    why="READY_FOR_FAB requires the upstream engineering gate to pass",
                    recommendation="resolve the upstream gate before fabrication review",
                    reverification=(label,),
                ))
        return tuple(issues)

    @staticmethod
    def _gate_status_unknowns(erc, drc, hw_fw):
        return tuple(f"{label}:UNKNOWN" for label, status in (("ERC", erc), ("DRC", drc), ("HW_FW_CONSISTENCY", hw_fw)) if str(status).upper() not in {"PASS", "FAIL"})

    @staticmethod
    def _validate_risks(risks, required_results, blocking_unknowns):
        by_verifier = {item.verifier: item for item in required_results}
        for risk in risks:
            result = by_verifier.get(risk.source_verification)
            if result is None or result.status not in {VerificationStatus.WARNING}:
                raise ValueError("accepted risk cannot bypass a required FAIL or UNKNOWN")
            if risk.issue_or_unknown in blocking_unknowns:
                raise ValueError("accepted risk cannot bypass a blocking unknown")
        return tuple(risks)

    @staticmethod
    def _transitions(previous, current, requested):
        if not requested:
            return ()
        before = {_PHYSICS_ALIASES.get(item.verifier, item.verifier): item.status for item in previous}
        after = {_PHYSICS_ALIASES.get(item.verifier, item.verifier): item.status for item in current}
        result = []
        for verifier in requested:
            canonical = _PHYSICS_ALIASES.get(verifier, verifier)
            result.append(VerificationTransition.create(
                verifier=canonical,
                before=before.get(canonical, VerificationStatus.UNKNOWN),
                after=after.get(canonical, VerificationStatus.UNKNOWN),
                reason="affected verifier was explicitly re-run for G7 final closure",
            ))
        return tuple(result)


__all__ = (
    "AcceptedRisk",
    "ApprovalDecision",
    "BlockingUnknownCategory",
    "DFTRequirement",
    "EngineeringApplicabilityDecision",
    "EngineeringCheck",
    "EngineeringEvidenceRequirement",
    "EngineeringEvidenceSource",
    "EngineeringVerificationType",
    "HumanApprovalBinding",
    "ManufacturerCapabilityRule",
    "ManufacturerProfile",
    "ManufacturerRuleSeverity",
    "PCBEngineeringVerificationClosure",
    "PCBEngineeringVerificationService",
    "PCBEngineeringVerificationSummary",
    "PCBManufacturingMeasurements",
    "ReadyForFabGate",
    "ReadyForFabStatus",
    "VerificationTransition",
    "build_jlcpcb_dfm_inputs",
    "build_jlcpcb_rigid_manufacturer_profile",
    "build_ready_for_fab_approval_target",
)
