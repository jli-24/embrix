from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_AUTO = "sha256:" + "0" * 64


def _fingerprint(value: BaseModel) -> str:
    payload = value.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class PCBProductionContract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", revalidate_instances="always", hide_input_in_errors=True)
    fingerprint: str = _AUTO
    _tuple_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _copy_and_require_tuples(cls, value: object) -> object:
        if isinstance(value, cls):
            value = value.model_dump(mode="python")
        if not isinstance(value, dict):
            return value
        material = copy.deepcopy(value)
        return material

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = _fingerprint(self)
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected and self.fingerprint not in self._legacy_fingerprint_candidates():
            raise ValueError("fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return ()

    @classmethod
    def create(cls, **values: object):
        for field in cls._tuple_fields:
            if field in values and type(values[field]) is not tuple:
                raise TypeError(f"{field} must be a tuple")
        return cls.model_validate(values, strict=False)


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


class PCBReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED_SCHEMATIC_NOT_VERIFIED = "BLOCKED_SCHEMATIC_NOT_VERIFIED"
    BLOCKED_MISSING_FOOTPRINT = "BLOCKED_MISSING_FOOTPRINT"
    BLOCKED_FOOTPRINT_UNAVAILABLE = "BLOCKED_FOOTPRINT_UNAVAILABLE"
    BLOCKED_PIN_PAD_MISMATCH = "BLOCKED_PIN_PAD_MISMATCH"
    BLOCKED_MISSING_BOARD_OUTLINE = "BLOCKED_MISSING_BOARD_OUTLINE"
    BLOCKED_MISSING_LAYER_CONFIG = "BLOCKED_MISSING_LAYER_CONFIG"
    BLOCKED_MISSING_ROUTING_RULE = "BLOCKED_MISSING_ROUTING_RULE"
    BLOCKED_MISSING_PLACEMENT_FACT = "BLOCKED_MISSING_PLACEMENT_FACT"
    BLOCKED_UNSUPPORTED_STACKUP = "BLOCKED_UNSUPPORTED_STACKUP"
    BLOCKED_UNSUPPORTED_CONSTRAINT = "BLOCKED_UNSUPPORTED_CONSTRAINT"
    BLOCKED_UNSUPPORTED_BOARD_GEOMETRY = "BLOCKED_UNSUPPORTED_BOARD_GEOMETRY"
    BLOCKED_AMBIGUOUS_DESIGN_TRUTH = "BLOCKED_AMBIGUOUS_DESIGN_TRUTH"


class PCBProductionStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    QUEUED = "QUEUED"
    GENERATED = "GENERATED"
    DRC_PENDING = "DRC_PENDING"
    DRC_PASS = "DRC_PASS"
    DRC_WARNINGS = "DRC_WARNINGS"
    DRC_FAIL = "DRC_FAIL"
    STALE = "STALE"
    REJECTED = "REJECTED"


class PCBProposalDecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PCBDrcStatus(StrEnum):
    DRC_PASS = "DRC_PASS"
    DRC_WARNINGS = "DRC_WARNINGS"
    DRC_FAIL = "DRC_FAIL"
    DRC_BLOCKED = "DRC_BLOCKED"
    DRC_UNAVAILABLE = "DRC_UNAVAILABLE"


class PCBParseValidationStatus(StrEnum):
    PARSE_VERIFIED = "PARSE_VERIFIED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class PCBEngineeringStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    WARNINGS_REMAIN = "WARNINGS_REMAIN"
    UNAVAILABLE = "UNAVAILABLE"


class PCBArtifactDiffStatus(StrEnum):
    NO_UNEXPECTED_GEOMETRY = "NO_UNEXPECTED_GEOMETRY"
    BLOCKED = "BLOCKED"


class PCBDrcIssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class PCBDrcIssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"
    STALE = "STALE"


class PCBDrcIssueClassification(StrEnum):
    UNCLASSIFIED = "UNCLASSIFIED"
    PCB_ARTIFACT_RENDERING_DEFECT = "PCB_ARTIFACT_RENDERING_DEFECT"
    PCB_PHYSICAL_DESIGN_DEFECT = "PCB_PHYSICAL_DESIGN_DEFECT"
    ELECTRICAL_INPUT_DEFECT = "ELECTRICAL_INPUT_DEFECT"
    FOOTPRINT_OR_PAD_MAPPING_DEFECT = "FOOTPRINT_OR_PAD_MAPPING_DEFECT"
    DESIGN_RULE_OR_CONSTRAINT_DEFECT = "DESIGN_RULE_OR_CONSTRAINT_DEFECT"
    TOOLCHAIN_OR_ENVIRONMENT = "TOOLCHAIN_OR_ENVIRONMENT"
    NON_BLOCKING_WARNING = "NON_BLOCKING_WARNING"


class PCBRepairDecisionAction(StrEnum):
    APPROVE_FIX = "APPROVE_FIX"
    REJECT_FIX = "REJECT_FIX"
    EDIT_FIX = "EDIT_FIX"
    DEFER = "DEFER"
    WAIVE_WITH_REASON = "WAIVE_WITH_REASON"


class PCBRepairApplicationStatus(StrEnum):
    APPLIED = "APPLIED"
    STALE = "STALE"


class PCBEndpointTruth(PCBProductionContract):
    reference: str
    pin_number: str
    _tuple_fields = ()

    @model_validator(mode="after")
    def _ids(self):
        _id(self.reference, "reference")
        if not self.pin_number.strip():
            raise ValueError("pin_number is invalid")
        return self


class PCBComponentTruth(PCBProductionContract):
    reference: str
    symbol_binding: str
    footprint_binding: str | None
    footprint_source_refs: tuple[str, ...]
    pin_numbers: tuple[str, ...]
    pad_numbers: tuple[str, ...]
    _tuple_fields = ("footprint_source_refs", "pin_numbers", "pad_numbers")

    @model_validator(mode="after")
    def _binding(self):
        _id(self.reference, "reference")
        if len(self.pin_numbers) != len(set(self.pin_numbers)) or len(self.pad_numbers) != len(set(self.pad_numbers)):
            raise ValueError("component pin and pad numbers must be unique")
        if self.footprint_binding is not None and not self.footprint_source_refs:
            raise ValueError("footprint binding requires source refs")
        return self


class PCBNetTruth(PCBProductionContract):
    name: str
    endpoints: tuple[PCBEndpointTruth, ...]
    net_class: Literal["default", "power", "ground"]
    source_refs: tuple[str, ...]
    _tuple_fields = ("endpoints", "source_refs")


class PCBBoardDefinition(PCBProductionContract):
    outline_kind: Literal["RECTANGLE"] = "RECTANGLE"
    width_mm: float = Field(gt=0, le=1000)
    height_mm: float = Field(gt=0, le=1000)
    origin_x_mm: float
    origin_y_mm: float
    layer_count: int
    copper_layers: tuple[str, ...]
    board_thickness_mm: float | None = Field(default=None, gt=0, le=20)
    source_refs: tuple[str, ...]
    _tuple_fields = ("copper_layers", "source_refs")


class PCBPlacementConstraint(PCBProductionContract):
    reference: str
    x_mm: float
    y_mm: float
    rotation_deg: float = 0.0
    side: Literal["F.Cu", "B.Cu"] = "F.Cu"
    source_refs: tuple[str, ...]
    _tuple_fields = ("source_refs",)


class PCBRoutingConstraint(PCBProductionContract):
    trace_width_mm: float = Field(gt=0, le=20)
    clearance_mm: float = Field(gt=0, le=20)
    via_diameter_mm: float | None = Field(default=None, gt=0, le=20)
    via_drill_mm: float | None = Field(default=None, gt=0, le=20)
    source_refs: tuple[str, ...]
    _tuple_fields = ("source_refs",)

    @model_validator(mode="after")
    def _via_pair(self):
        if (self.via_diameter_mm is None) != (self.via_drill_mm is None):
            raise ValueError("via rules must be complete")
        return self


class PCBRouteSegment(PCBProductionContract):
    net_name: str
    layer: Literal["F.Cu", "B.Cu"]
    width_mm: float = Field(gt=0, le=20)
    points: tuple[tuple[float, float], ...]
    source_refs: tuple[str, ...]
    _tuple_fields = ("points", "source_refs")

    @model_validator(mode="after")
    def _shape(self):
        if len(self.points) < 2:
            raise ValueError("route requires at least two points")
        return self


class PCBPhysicalDesignProposal(PCBProductionContract):
    """Typed physical PCB input; it cannot carry electrical truth fields."""

    physical_design_id: str
    project_id: str
    conversation_id: str
    board: PCBBoardDefinition
    placements: tuple[PCBPlacementConstraint, ...]
    routes: tuple[PCBRouteSegment, ...]
    routing: PCBRoutingConstraint
    source_refs: tuple[str, ...]
    _tuple_fields = ("placements", "routes", "source_refs")

    @model_validator(mode="after")
    def _physical(self):
        for field in ("physical_design_id", "project_id", "conversation_id"):
            _id(getattr(self, field), field)
        if not self.source_refs:
            raise ValueError("PCB physical design proposal requires source refs")
        if len({item.reference for item in self.placements}) != len(self.placements):
            raise ValueError("PCB placement identities must be unique")
        if len({item.net_name for item in self.routes}) != len(self.routes):
            raise ValueError("PCB route identities must be unique")
        return self


class PCBProductionRequest(PCBProductionContract):
    request_id: str
    source_request_id: str | None = None
    trace_id: str | None = None
    project_id: str
    conversation_id: str
    requester: str
    schematic_artifact_id: str
    schematic_artifact_fingerprint: str
    schematic_receipt_id: str
    schematic_receipt_fingerprint: str
    schematic_erc_result_id: str
    schematic_erc_fingerprint: str
    schematic_erc_status: Literal["ERC_PASS", "ERC_FAIL", "ERC_BLOCKED", "ERC_UNAVAILABLE"]
    schematic_content_fingerprint: str
    real_schematic_exists: bool
    schematic_hash_matches_artifact: bool
    schematic_erc_executed: bool
    schematic_connectivity_fingerprint: str
    design_truth_id: str
    design_truth_fingerprint: str
    hardware_design_id: str
    hardware_design_fingerprint: str
    implementation_id: str
    implementation_fingerprint: str
    implementation_task_id: str
    capability_id: Literal["ARTIFACT_CREATION"]
    capability_status: Literal["AVAILABLE"]
    authorization_id: str
    authorization_request_id: str
    authorization_fingerprint: str
    authorization_status: Literal["APPROVED"]
    workspace_identity: str
    target_relative_path: str
    board: PCBBoardDefinition
    components: tuple[PCBComponentTruth, ...]
    nets: tuple[PCBNetTruth, ...]
    placements: tuple[PCBPlacementConstraint, ...]
    routes: tuple[PCBRouteSegment, ...]
    routing: PCBRoutingConstraint
    source_refs: tuple[str, ...]
    requested_at: datetime
    pcb_state_fingerprint: str | None = None
    pcb_source_binding_fingerprint: str | None = None
    pcb_constraint_set_fingerprint: str | None = None
    pcb_verification_plan_fingerprint: str | None = None
    pcb_element_binding_fingerprint: str | None = None
    _tuple_fields = ("components", "nets", "placements", "routes", "source_refs")

    @model_validator(mode="after")
    def _request(self):
        for field in ("request_id", "project_id", "conversation_id", "requester", "schematic_artifact_id", "schematic_receipt_id", "schematic_erc_result_id", "design_truth_id", "hardware_design_id", "implementation_id", "implementation_task_id", "authorization_id", "authorization_request_id"):
            _id(getattr(self, field), field)
        for field in ("source_request_id", "trace_id"):
            value = getattr(self, field)
            if value is not None:
                _id(value, field)
        for field in ("schematic_artifact_fingerprint", "schematic_receipt_fingerprint", "schematic_erc_fingerprint", "schematic_content_fingerprint", "schematic_connectivity_fingerprint", "design_truth_fingerprint", "hardware_design_fingerprint", "implementation_fingerprint", "authorization_fingerprint"):
            _fp(getattr(self, field), field)
        for field in ("pcb_state_fingerprint", "pcb_source_binding_fingerprint", "pcb_constraint_set_fingerprint", "pcb_verification_plan_fingerprint", "pcb_element_binding_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                _fp(value, field)
        normalized = self.target_relative_path.replace("\\", "/")
        if normalized != self.target_relative_path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("target_relative_path is invalid")
        component_refs = {item.reference for item in self.components}
        if len(component_refs) != len(self.components) or len({item.name for item in self.nets}) != len(self.nets):
            raise ValueError("PCB truth identities must be unique")
        if len({item.reference for item in self.placements}) != len(self.placements):
            raise ValueError("placement identities must be unique")
        required_source_refs = {
            self.schematic_artifact_id,
            self.schematic_receipt_id,
            self.schematic_erc_result_id,
            self.design_truth_id,
            self.hardware_design_id,
            self.implementation_id,
            self.implementation_task_id,
            self.authorization_id,
            self.authorization_request_id,
        }
        if not required_source_refs.issubset(set(self.source_refs)):
            raise ValueError("PCB production source provenance is incomplete")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        payload = self.model_dump(mode="json", exclude={"fingerprint", "source_request_id", "trace_id"})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        legacy_without_correlation = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.source_request_id is None and self.trace_id is None:
            return (legacy_without_correlation,)
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return (legacy_without_correlation, "sha256:" + hashlib.sha256(encoded).hexdigest())


class PCBReadinessResult(PCBProductionContract):
    status: PCBReadinessStatus
    missing_facts: tuple[str, ...]
    affected_components: tuple[str, ...]
    affected_nets: tuple[str, ...]
    source_refs: tuple[str, ...]
    required_next_action: str
    schematic_artifact_fingerprint: str
    design_truth_fingerprint: str
    hardware_design_fingerprint: str
    _tuple_fields = ("missing_facts", "affected_components", "affected_nets", "source_refs")


class PCBFootprint(PCBProductionContract):
    component_ref: str
    footprint_binding: str
    pad_numbers: tuple[str, ...]
    placement: PCBPlacementConstraint
    _tuple_fields = ("pad_numbers",)


class PCBNetAssignment(PCBProductionContract):
    net_name: str
    net_class: Literal["default", "power", "ground"]
    endpoints: tuple[PCBEndpointTruth, ...]
    _tuple_fields = ("endpoints",)


class PCBProposal(PCBProductionContract):
    proposal_id: str
    request_id: str
    project_id: str
    target_relative_path: str
    board: PCBBoardDefinition
    footprints: tuple[PCBFootprint, ...]
    net_assignments: tuple[PCBNetAssignment, ...]
    routing: tuple[PCBRouteSegment, ...]
    vias: tuple[object, ...]
    zones: tuple[object, ...]
    keepouts: tuple[object, ...]
    source_refs: tuple[str, ...]
    verification_refs: tuple[str, ...]
    schematic_fingerprint: str
    schematic_connectivity_fingerprint: str
    design_truth_fingerprint: str
    hardware_design_fingerprint: str
    authorization_fingerprint: str
    pcb_state_fingerprint: str | None = None
    pcb_source_binding_fingerprint: str | None = None
    pcb_constraint_set_fingerprint: str | None = None
    pcb_verification_plan_fingerprint: str | None = None
    pcb_element_binding_fingerprint: str | None = None
    _tuple_fields = ("footprints", "net_assignments", "routing", "vias", "zones", "keepouts", "source_refs", "verification_refs")

    @model_validator(mode="after")
    def _phase5_binding(self):
        values = (
            self.pcb_state_fingerprint,
            self.pcb_source_binding_fingerprint,
            self.pcb_constraint_set_fingerprint,
            self.pcb_verification_plan_fingerprint,
            self.pcb_element_binding_fingerprint,
        )
        if any(value is not None for value in values) and any(value is None for value in values):
            raise ValueError("PCB proposal Phase5 binding is incomplete")
        for field in ("pcb_state_fingerprint", "pcb_source_binding_fingerprint", "pcb_constraint_set_fingerprint", "pcb_verification_plan_fingerprint", "pcb_element_binding_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                _fp(value, field)
        return self


class PCBArtifactDiff(PCBProductionContract):
    """Typed serializer diff; unexpected generated geometry is not evidence."""

    proposal_fingerprint: str
    status: PCBArtifactDiffStatus
    unexpected_objects: tuple[str, ...]
    source_refs: tuple[str, ...]
    reason: str
    _tuple_fields = ("unexpected_objects", "source_refs")

    @model_validator(mode="after")
    def _diff(self):
        _fp(self.proposal_fingerprint, "proposal_fingerprint")
        if not self.reason.strip():
            raise ValueError("artifact diff reason is required")
        if self.status is PCBArtifactDiffStatus.NO_UNEXPECTED_GEOMETRY and self.unexpected_objects:
            raise ValueError("clean artifact diff cannot contain unexpected objects")
        if self.status is PCBArtifactDiffStatus.BLOCKED and not self.unexpected_objects:
            raise ValueError("blocked artifact diff requires unexpected objects")
        return self


class PCBProposalDecision(PCBProductionContract):
    decision_id: str
    proposal_id: str
    proposal_fingerprint: str
    decision: PCBProposalDecisionStatus
    reviewer: str | None = None
    decided_at: datetime | None = None
    reason: str

    @model_validator(mode="after")
    def _decision_boundary(self):
        if self.decision is PCBProposalDecisionStatus.PENDING:
            if self.reviewer is not None or self.decided_at is not None:
                raise ValueError("pending PCB decision cannot have reviewer or time")
        elif self.reviewer is None or self.decided_at is None:
            raise ValueError("terminal PCB decision requires reviewer and time")
        return self


class DrcIssue(PCBProductionContract):
    issue_id: str
    severity: PCBDrcIssueSeverity
    rule_id: str
    message: str
    component_ref: str | None = None
    footprint_ref: str | None = None
    pad_ref: str | None = None
    net_ref: str | None = None
    location: tuple[float, float] | None = None
    raw_evidence_ref: str
    pcb_artifact_fingerprint: str
    status: PCBDrcIssueStatus = PCBDrcIssueStatus.OPEN
    classification: PCBDrcIssueClassification = PCBDrcIssueClassification.UNCLASSIFIED

    @model_validator(mode="after")
    def _issue(self):
        _id(self.issue_id, "issue_id")
        for field in ("rule_id", "message", "raw_evidence_ref"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f"{field} is invalid")
        _fp(self.pcb_artifact_fingerprint, "pcb_artifact_fingerprint")
        for field in ("component_ref", "footprint_ref", "pad_ref", "net_ref"):
            value = getattr(self, field)
            if value is not None:
                _id(value, field)
        if self.location is not None and (len(self.location) != 2 or any(not isinstance(value, (int, float)) for value in self.location)):
            raise ValueError("issue location is invalid")
        return self


class PCBDrcViolation(PCBProductionContract):
    severity: str
    rule: str
    message: str
    object_refs: tuple[str, ...] = ()
    net_ref: str | None = None
    layer_ref: str | None = None
    raw_source_ref: str
    _tuple_fields = ("object_refs",)


class PCBDrcResult(PCBProductionContract):
    operation_id: str
    pcb_artifact_ref: str
    pcb_content_hash: str
    kicad_version: str
    status: PCBDrcStatus
    exit_status: int
    violations: tuple[PCBDrcViolation, ...]
    warnings: tuple[str, ...]
    report_artifact_ref: str | None
    started_at: datetime
    finished_at: datetime
    receipt_ref: str | None
    tool_observed: bool
    project_id: str | None = None
    conversation_id: str | None = None
    kicad_cli_path: str = ""
    command_identity: str = ""
    raw_report_fingerprint: str | None = None
    tool_execution_status: str = "UNKNOWN"
    engineering_status: PCBEngineeringStatus | None = None
    parse_validation: PCBParseValidationEvidence | None = None
    issues: tuple[DrcIssue, ...] = ()
    _tuple_fields = ("violations", "warnings", "issues")

    @model_validator(mode="after")
    def _binding(self):
        legacy_evidence = self.fingerprint in self._legacy_fingerprint_candidates()
        _id(self.operation_id, "operation_id")
        _fp(self.pcb_content_hash, "pcb_content_hash")
        if self.project_id is not None:
            _id(self.project_id, "project_id")
        if self.conversation_id is not None:
            _id(self.conversation_id, "conversation_id")
        if self.receipt_ref is not None:
            _fp(self.receipt_ref, "receipt_ref")
        if self.raw_report_fingerprint is not None:
            _fp(self.raw_report_fingerprint, "raw_report_fingerprint")
        if not self.tool_observed and self.status in {PCBDrcStatus.DRC_PASS, PCBDrcStatus.DRC_FAIL}:
            raise ValueError("observed DRC result requires tool observation")
        if not legacy_evidence and self.status in {PCBDrcStatus.DRC_PASS, PCBDrcStatus.DRC_WARNINGS, PCBDrcStatus.DRC_FAIL} and (
            not self.report_artifact_ref
            or self.raw_report_fingerprint is None
            or not self.tool_observed
            or self.tool_execution_status != "SUCCEEDED"
        ):
            raise ValueError("observed DRC result requires raw report and tool evidence")
        if self.status is PCBDrcStatus.DRC_PASS and (self.exit_status != 0 or self.violations or self.issues):
            raise ValueError("DRC_PASS requires zero blocking violations")
        if self.status is PCBDrcStatus.DRC_FAIL and not (self.violations or self.issues):
            raise ValueError("DRC_FAIL requires structured issues")
        if self.engineering_status is not None:
            expected = {
                PCBDrcStatus.DRC_PASS: PCBEngineeringStatus.PASS,
                PCBDrcStatus.DRC_WARNINGS: PCBEngineeringStatus.WARNINGS_REMAIN,
                PCBDrcStatus.DRC_FAIL: PCBEngineeringStatus.BLOCKED,
                PCBDrcStatus.DRC_BLOCKED: PCBEngineeringStatus.BLOCKED,
                PCBDrcStatus.DRC_UNAVAILABLE: PCBEngineeringStatus.UNAVAILABLE,
            }[self.status]
            if self.engineering_status is not expected:
                raise ValueError("PCB DRC engineering status mismatch")
        if self.parse_validation is not None and (
            self.parse_validation.operation_id != self.operation_id
            or self.parse_validation.artifact_fingerprint != self.pcb_content_hash
            or self.parse_validation.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED
        ):
            raise ValueError("PCB parse validation binding mismatch")
        if self.issues and any(item.pcb_artifact_fingerprint != self.pcb_content_hash for item in self.issues):
            raise ValueError("PCB DRC issue artifact binding mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if (
            self.project_id is not None
            or self.conversation_id is not None
            or self.kicad_cli_path
            or self.command_identity
            or self.raw_report_fingerprint is not None
            or self.tool_execution_status != "UNKNOWN"
            or self.engineering_status is not None
            or self.parse_validation is not None
            or self.issues
        ):
            return ()
        payload = self.model_dump(
            mode="json",
            exclude={
                "fingerprint",
                "project_id",
                "conversation_id",
                "kicad_cli_path",
                "command_identity",
                "raw_report_fingerprint",
                "tool_execution_status",
                "engineering_status",
                "parse_validation",
                "issues",
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)


class PCBDrcReceipt(PCBProductionContract):
    receipt_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    operation_receipt_fingerprint: str
    artifact_path: str
    artifact_fingerprint: str
    kicad_version: str
    command_identity: str
    raw_report_fingerprint: str | None
    tool_execution_status: str
    engineering_status: PCBEngineeringStatus
    result: PCBDrcResult
    created_at: datetime

    @model_validator(mode="after")
    def _receipt(self):
        for field in ("receipt_id", "project_id", "conversation_id", "operation_id"):
            _id(getattr(self, field), field)
        for field in ("operation_receipt_fingerprint", "artifact_fingerprint"):
            _fp(getattr(self, field), field)
        if self.raw_report_fingerprint is not None:
            _fp(self.raw_report_fingerprint, "raw_report_fingerprint")
        normalized = self.artifact_path.replace("\\", "/")
        if normalized != self.artifact_path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("PCB DRC artifact path is invalid")
        legacy_result = self.result.fingerprint in self.result._legacy_fingerprint_candidates()
        if self.result.operation_id != self.operation_id or self.result.pcb_content_hash != self.artifact_fingerprint or self.result.kicad_version != self.kicad_version or (not legacy_result and self.result.command_identity != self.command_identity) or self.result.raw_report_fingerprint != self.raw_report_fingerprint or (not legacy_result and self.result.engineering_status is not self.engineering_status):
            raise ValueError("PCB DRC receipt binding mismatch")
        return self


class PCBArtifactRecord(PCBProductionContract):
    artifact_id: str
    project_id: str
    artifact_type: Literal["KICAD_PCB"] = "KICAD_PCB"
    path: str
    content_hash: str
    availability: Literal["AVAILABLE"] = "AVAILABLE"
    source_operation_id: str
    proposal_fingerprint: str
    schematic_source_ref: str

    @model_validator(mode="after")
    def _binding(self):
        for field in ("artifact_id", "project_id", "source_operation_id", "schematic_source_ref"):
            _id(getattr(self, field), field)
        for field in ("content_hash", "proposal_fingerprint"):
            _fp(getattr(self, field), field)
        normalized = self.path.replace("\\", "/")
        if normalized != self.path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("PCB artifact path is invalid")
        return self


class PCBParseValidationEvidence(PCBProductionContract):
    validation_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    artifact_path: str
    artifact_fingerprint: str
    kicad_cli_path: str
    kicad_version: str
    command_identity: str
    exit_code: int
    validation_status: PCBParseValidationStatus
    artifact_unchanged: bool
    stdout_summary: str = ""
    stderr_summary: str = ""
    validated_at: datetime

    @model_validator(mode="after")
    def _validation(self):
        for field in ("validation_id", "project_id", "conversation_id", "operation_id"):
            _id(getattr(self, field), field)
        _fp(self.artifact_fingerprint, "artifact_fingerprint")
        normalized = self.artifact_path.replace("\\", "/")
        if normalized != self.artifact_path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("PCB parse artifact path is invalid")
        if not self.kicad_cli_path.strip() or not self.kicad_version.strip() or not self.command_identity.strip():
            raise ValueError("PCB parse validation identity is incomplete")
        if type(self.exit_code) is not int or not -255 <= self.exit_code <= 255:
            raise ValueError("PCB parse exit code is invalid")
        if type(self.artifact_unchanged) is not bool:
            raise TypeError("PCB parse artifact_unchanged is invalid")
        if self.validation_status is PCBParseValidationStatus.PARSE_VERIFIED and (self.exit_code != 0 or not self.artifact_unchanged):
            raise ValueError("verified PCB parse requires successful immutable validation")
        return self


class PCBGenerationReceipt(PCBProductionContract):
    receipt_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    operation_fingerprint: str
    operation_receipt_fingerprint: str
    workspace_apply_receipt_fingerprint: str
    artifact_path: str
    artifact_fingerprint: str
    proposal_fingerprint: str
    schematic_fingerprint: str
    schematic_erc_fingerprint: str
    design_truth_fingerprint: str
    hardware_design_fingerprint: str
    generator: str
    generator_version: str
    validation_status: PCBParseValidationStatus
    parse_validation: PCBParseValidationEvidence | None = None
    artifact: PCBArtifactRecord
    status: Literal["ARTIFACT_GENERATED", "SUCCESS", "FAILED", "STALE"]
    source_refs: tuple[str, ...]
    created_at: datetime
    _tuple_fields = ("source_refs",)

    @model_validator(mode="after")
    def _binding(self):
        for field in ("receipt_id", "project_id", "conversation_id", "operation_id"):
            _id(getattr(self, field), field)
        for field in ("operation_fingerprint", "operation_receipt_fingerprint", "workspace_apply_receipt_fingerprint", "artifact_fingerprint", "proposal_fingerprint", "schematic_fingerprint", "schematic_erc_fingerprint", "design_truth_fingerprint", "hardware_design_fingerprint"):
            _fp(getattr(self, field), field)
        normalized = self.artifact_path.replace("\\", "/")
        if normalized != self.artifact_path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("PCB generation artifact path is invalid")
        if not self.source_refs:
            raise ValueError("PCB generation receipt requires provenance")
        if self.artifact.project_id != self.project_id or self.artifact.path != self.artifact_path or self.artifact.content_hash != self.artifact_fingerprint or self.artifact.proposal_fingerprint != self.proposal_fingerprint:
            raise ValueError("PCB artifact receipt binding mismatch")
        if self.status == "SUCCESS" and self.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED:
            raise ValueError("successful PCB generation requires parse validation")
        if self.parse_validation is not None:
            evidence = self.parse_validation
            if (
                evidence.project_id != self.project_id
                or evidence.conversation_id != self.conversation_id
                or evidence.operation_id != self.operation_id
                or evidence.artifact_path != self.artifact_path
                or evidence.artifact_fingerprint != self.artifact_fingerprint
                or evidence.validation_status is not self.validation_status
            ):
                raise ValueError("PCB parse evidence binding mismatch")
        elif self.validation_status is PCBParseValidationStatus.PARSE_VERIFIED:
            raise ValueError("parse-verified PCB generation requires parse evidence")
        elif self.status == "FAILED":
            raise ValueError("failed PCB generation requires parse evidence")
        return self


class PCBRepairProposal(PCBProductionContract):
    repair_id: str
    issue_refs: tuple[str, ...]
    classification: PCBDrcIssueClassification
    root_cause: str
    proposed_change: str
    target_authority: str
    affected_constraints: tuple[str, ...]
    affected_placements: tuple[str, ...]
    affected_routing: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    expected_effect: str
    risk: str
    source_fingerprint: str
    base_proposal_id: str
    base_proposal_fingerprint: str
    corrected_proposal: PCBProposal | None = None
    requires_user_approval: Literal[True] = True
    status: Literal["AWAITING_APPROVAL"] = "AWAITING_APPROVAL"
    _tuple_fields = ("issue_refs", "affected_constraints", "affected_placements", "affected_routing", "supporting_evidence")

    @model_validator(mode="after")
    def _repair(self):
        for field in ("repair_id", "base_proposal_id"):
            _id(getattr(self, field), field)
        for field in ("source_fingerprint", "base_proposal_fingerprint"):
            _fp(getattr(self, field), field)
        if not self.issue_refs or len(self.issue_refs) != len(set(self.issue_refs)):
            raise ValueError("PCB repair issue references are invalid")
        if not self.requires_user_approval or self.status != "AWAITING_APPROVAL":
            raise ValueError("PCB repair proposal requires explicit approval")
        if not self.target_authority.strip() or not self.proposed_change.strip():
            raise ValueError("PCB repair authority and change are required")
        return self


class PCBRepairDecision(PCBProductionContract):
    decision_id: str
    repair_id: str
    proposal_fingerprint: str
    artifact_fingerprint: str
    action: PCBRepairDecisionAction
    reviewer: str
    decided_at: datetime
    comment: str = ""
    edited_proposal_fingerprint: str | None = None

    @model_validator(mode="after")
    def _decision(self):
        for field in ("decision_id", "repair_id", "reviewer"):
            _id(getattr(self, field), field)
        _fp(self.proposal_fingerprint, "proposal_fingerprint")
        _fp(self.artifact_fingerprint, "artifact_fingerprint")
        if self.action is PCBRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is None:
            raise ValueError("EDIT_FIX requires edited proposal fingerprint")
        if self.action is not PCBRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is not None:
            raise ValueError("edited proposal fingerprint is only valid for EDIT_FIX")
        if self.action is PCBRepairDecisionAction.WAIVE_WITH_REASON and not self.comment.strip():
            raise ValueError("WAIVE_WITH_REASON requires a reason")
        return self


class PCBRepairApplicationReceipt(PCBProductionContract):
    receipt_id: str
    project_id: str
    conversation_id: str
    repair_id: str
    repair_proposal_fingerprint: str
    repair_decision_id: str
    repair_decision_fingerprint: str
    repair_action: PCBRepairDecisionAction
    artifact_path: str
    pre_proposal_fingerprint: str
    post_proposal_fingerprint: str
    pre_artifact_fingerprint: str
    post_artifact_fingerprint: str
    workspace_apply_receipt_fingerprint: str
    post_generation_receipt_fingerprint: str
    post_parse_validation_fingerprint: str
    post_drc_receipt_fingerprint: str
    post_drc_result_fingerprint: str
    status: PCBRepairApplicationStatus = PCBRepairApplicationStatus.APPLIED
    generator_changed: bool = False
    source_refs: tuple[str, ...]
    applied_at: datetime
    _tuple_fields = ("source_refs",)

    @model_validator(mode="after")
    def _receipt(self):
        for field in ("receipt_id", "project_id", "conversation_id", "repair_id", "repair_decision_id"):
            _id(getattr(self, field), field)
        for field in ("repair_proposal_fingerprint", "repair_decision_fingerprint", "pre_proposal_fingerprint", "post_proposal_fingerprint", "pre_artifact_fingerprint", "post_artifact_fingerprint", "workspace_apply_receipt_fingerprint", "post_generation_receipt_fingerprint", "post_parse_validation_fingerprint", "post_drc_receipt_fingerprint", "post_drc_result_fingerprint"):
            _fp(getattr(self, field), field)
        normalized = self.artifact_path.replace("\\", "/")
        if normalized != self.artifact_path or normalized.startswith(("/", "//")) or ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")) or not normalized.casefold().endswith(".kicad_pcb"):
            raise ValueError("PCB repair artifact path is invalid")
        if not self.source_refs:
            raise ValueError("PCB repair receipt requires provenance")
        return self


class PCBProductionState(PCBProductionContract):
    production_id: str
    project_id: str
    conversation_id: str
    request: PCBProductionRequest
    proposal: PCBProposal
    decision: PCBProposalDecision
    operation_id: str | None = None
    operation_fingerprint: str | None = None
    status: PCBProductionStatus
    stale_reason: str | None = None
    generation_receipt: PCBGenerationReceipt | None = None
    drc_result: PCBDrcResult | None = None
    drc_receipt: PCBDrcReceipt | None = None
    drc_receipt_fingerprint: str | None = None
    drc_gate_status: PCBEngineeringStatus | None = None
    repair_proposals: tuple[PCBRepairProposal, ...] = ()
    repair_decisions: tuple[PCBRepairDecision, ...] = ()
    repair_application_receipts: tuple[PCBRepairApplicationReceipt, ...] = ()
    _tuple_fields = ("repair_proposals", "repair_decisions", "repair_application_receipts")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.request.project_id != self.project_id
            or self.request.conversation_id != self.conversation_id
            or self.proposal.request_id != self.request.request_id
            or self.proposal.project_id != self.project_id
            or self.proposal.target_relative_path != self.request.target_relative_path
            or self.proposal.schematic_fingerprint != self.request.schematic_artifact_fingerprint
            or self.proposal.design_truth_fingerprint != self.request.design_truth_fingerprint
            or self.proposal.hardware_design_fingerprint != self.request.hardware_design_fingerprint
            or self.proposal.authorization_fingerprint != self.request.authorization_fingerprint
            or self.decision.proposal_id != self.proposal.proposal_id
            or self.decision.proposal_fingerprint != self.proposal.fingerprint
            or ((self.operation_id is None) != (self.operation_fingerprint is None))
        ):
            raise ValueError("PCB production state binding mismatch")
        if self.status is PCBProductionStatus.AWAITING_APPROVAL and self.decision.decision is not PCBProposalDecisionStatus.PENDING:
            raise ValueError("awaiting PCB production requires pending decision")
        if self.status is PCBProductionStatus.QUEUED and (
            self.decision.decision is not PCBProposalDecisionStatus.APPROVED
            or self.operation_id is None
        ):
            raise ValueError("queued PCB production requires approved operation")
        if self.status is PCBProductionStatus.REJECTED and (
            self.decision.decision is not PCBProposalDecisionStatus.REJECTED
            or self.operation_id is not None
        ):
            raise ValueError("rejected PCB production state is invalid")
        if self.status in {PCBProductionStatus.GENERATED, PCBProductionStatus.DRC_PENDING, PCBProductionStatus.DRC_PASS, PCBProductionStatus.DRC_WARNINGS, PCBProductionStatus.DRC_FAIL, PCBProductionStatus.STALE}:
            if self.decision.decision is not PCBProposalDecisionStatus.APPROVED or self.operation_id is None or self.generation_receipt is None:
                raise ValueError("terminal PCB production state requires an approved generation receipt")
            receipt = self.generation_receipt
            if receipt.project_id != self.project_id or receipt.conversation_id != self.conversation_id or receipt.operation_id != self.operation_id or receipt.operation_fingerprint != self.operation_fingerprint or receipt.artifact_path != self.request.target_relative_path or receipt.proposal_fingerprint != self.proposal.fingerprint or receipt.schematic_fingerprint != self.request.schematic_artifact_fingerprint or receipt.schematic_erc_fingerprint != self.request.schematic_erc_fingerprint or receipt.design_truth_fingerprint != self.request.design_truth_fingerprint or receipt.hardware_design_fingerprint != self.request.hardware_design_fingerprint:
                raise ValueError("PCB generation receipt binding mismatch")
        elif self.generation_receipt is not None:
            raise ValueError("pre-generation PCB state cannot contain a generation receipt")
        if self.status is PCBProductionStatus.STALE and not self.stale_reason:
            raise ValueError("stale PCB production requires a reason")
        if self.drc_result is not None:
            if self.generation_receipt is None or self.drc_result.pcb_content_hash != self.generation_receipt.artifact_fingerprint:
                raise ValueError("PCB DRC result is not bound to the current artifact")
            if self.drc_gate_status is None or self.drc_receipt is None or self.drc_receipt_fingerprint != self.drc_receipt.fingerprint:
                raise ValueError("PCB DRC result requires gate status")
        elif self.drc_gate_status is not None or self.drc_receipt is not None or self.drc_receipt_fingerprint is not None or self.status in {PCBProductionStatus.DRC_PENDING, PCBProductionStatus.DRC_PASS, PCBProductionStatus.DRC_WARNINGS, PCBProductionStatus.DRC_FAIL}:
            raise ValueError("PCB DRC state requires a DRC result")
        if self.drc_result is not None:
            expected_status = {
                PCBProductionStatus.DRC_PENDING: None,
                PCBProductionStatus.DRC_PASS: PCBDrcStatus.DRC_PASS,
                PCBProductionStatus.DRC_WARNINGS: PCBDrcStatus.DRC_WARNINGS,
                PCBProductionStatus.DRC_FAIL: PCBDrcStatus.DRC_FAIL,
                PCBProductionStatus.GENERATED: None,
                PCBProductionStatus.STALE: None,
            }.get(self.status)
            if expected_status is not None and self.drc_result.status is not expected_status:
                raise ValueError("PCB production DRC status mismatch")
        if self.repair_proposals and self.drc_result is None:
            raise ValueError("PCB repair proposal requires a DRC result")
        if self.repair_proposals and any(not set(item.issue_refs).issubset({issue.issue_id for issue in self.drc_result.issues}) for item in self.repair_proposals):
            raise ValueError("PCB repair issue binding mismatch")
        if self.repair_decisions:
            proposal_by_id = {item.repair_id: item for item in self.repair_proposals}
            if any(item.repair_id not in proposal_by_id or item.proposal_fingerprint != proposal_by_id[item.repair_id].fingerprint for item in self.repair_decisions):
                raise ValueError("PCB repair decision binding mismatch")
        if self.repair_application_receipts:
            decision_by_id = {item.decision_id: item for item in self.repair_decisions}
            proposal_by_id = {item.repair_id: item for item in self.repair_proposals}
            for item in self.repair_application_receipts:
                if item.repair_id not in proposal_by_id or item.repair_decision_id not in decision_by_id:
                    raise ValueError("PCB repair application lineage mismatch")
                if item.status is PCBRepairApplicationStatus.APPLIED and item.post_artifact_fingerprint != self.generation_receipt.artifact_fingerprint:
                    raise ValueError("current PCB repair application is not bound to current artifact")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if (
            self.stale_reason is not None
            or self.generation_receipt is not None
            or self.drc_result is not None
            or self.drc_receipt is not None
            or self.drc_receipt_fingerprint is not None
            or self.drc_gate_status is not None
            or self.repair_proposals
            or self.repair_decisions
            or self.repair_application_receipts
        ):
            return ()
        payload = self.model_dump(
            mode="json",
            exclude={
                "fingerprint",
                "stale_reason",
                "generation_receipt",
                "drc_result",
                "drc_receipt",
                "drc_receipt_fingerprint",
                "drc_gate_status",
                "repair_proposals",
                "repair_decisions",
                "repair_application_receipts",
            },
        )
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)


class PCBFootprintAvailabilityPort(Protocol):
    def is_available(self, footprint_binding: str) -> bool: ...


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {"BaseModel", "ConfigDict", "Field", "ClassVar", "Literal", "Protocol", "copy", "hashlib", "json", "re"})
