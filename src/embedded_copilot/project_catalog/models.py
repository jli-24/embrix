from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    ChatContract,
    FingerprintedChatContract,
    fingerprint,
    identifier,
    safe_text,
    utc_datetime,
)
from embedded_copilot.desktop_copilot_chat.response_contract import ResponseType
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureReviewDecision,
    ArtifactIntelligenceSnapshot,
    CapabilityEvaluation,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    EngineeringEvidenceBundle,
    EngineeringKnowledgeModel,
    EngineeringPlan,
    EngineeringSessionMode,
    EngineeringWorkflow,
    ExecutionAuthorization,
    ExecutionIntent,
    FirmwareSourceGenerationState,
    FirmwareSpecification,
    HardwareDesignSpecification,
    HardwareSelectionState,
    ImplementationSpecification,
    PlanReviewDecision,
    PlanStatus,
    SchematicPlanningState,
    SchematicPlanningStatus,
    SchematicProductionState,
    SimulationSpecification,
    TeachingContext,
    VerificationSpecification,
    evaluate_capabilities,
    generate_execution_intent,
    generate_execution_requests,
    validate_execution_authorization,
)
from embedded_copilot.engineering_copilot.schematic_evidence import LibraryTerminalBindingV2, ParameterConditions, ParameterLocator, decimal_text
from embedded_copilot.engineering_pcb import (
    ComponentMappingRelationship,
    DecouplingMeasurement,
    DifferentialPairMeasurement,
    DifferentialPairRelationship,
    EngineeringEvidenceRequirement,
    FeedbackRelationship,
    HumanApprovalBinding,
    LayerTransitionReturnAssessment,
    ManufacturerProfile,
    PadMappingRelationship,
    PCBConstraintSet,
    PCBEngineeringState,
    PCBEngineeringVerificationSummary,
    PCBManufacturingMeasurements,
    PCBVerificationPlan,
    PinMappingRelationship,
    PowerLoopMeasurement,
    PowerLoopRelationship,
    PowerStageRelationship,
    ReadyForFabGate,
    ReferenceCoverageMeasurement,
    ReferenceDiscontinuity,
    ReturnPathRelationship,
    SwitchNodeRegion,
    build_ready_for_fab_approval_target,
)
from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.engineering_research import (
    EngineeringResearchSnapshot,
    FeatureDecision,
    ReferenceProjectDecision,
    RequirementChangeProposal,
    RequirementFinalConfirmation,
    RequirementSuggestionDecision,
    ResearchIntentRefinement,
)
from embedded_copilot.engineering_runtime.common import (
    RuntimeContract,
    canonical_fingerprint,
    normalize_text,
    validate_fingerprint,
    validate_identifier,
    validate_revision,
    validate_utc_datetime,
)
from embedded_copilot.engineering_runtime.evidence import EvidenceReceiptReference
from embedded_copilot.engineering_runtime.objects.models import (
    CircuitIntentObject,
    LifecycleStatus,
    ObjectType,
)
from embedded_copilot.engineering_runtime.proposals import (
    ApprovalRecord,
    ObjectMutationRecord,
    ObjectProposal,
    ProposalStatus,
)
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.firmware_engineering.models import (
    canonical_fingerprint as firmware_canonical_fingerprint,
)
from embedded_copilot.pcb_production import PCBProductionRequest, PCBProductionState
from embedded_copilot.verification_loop import (
    VerificationResult,
    VirtualSystemVerificationState,
)

from .firmware_production import FirmwareProductionState

_LEGACY_BUILD_CONFIGURATION_DEFAULTS = {
    "cmake_project": "CMakeLists.txt",
    "cmake_toolchain_file": "cmake/arm-none-eabi.cmake",
    "startup_source": "vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s",
    "system_source": "vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c",
    "cmsis_include": "vendor/arm/CMSIS/Core/Include",
    "device_include": "vendor/st/STM32F1xx/Include",
}


def _firmware_build_configuration_fingerprints(
    value: FirmwareBuildConfiguration,
) -> tuple[str, ...]:
    candidates = [firmware_canonical_fingerprint(value)]
    material = value.model_dump(mode="json")
    if all(material.get(field) == default for field, default in _LEGACY_BUILD_CONFIGURATION_DEFAULTS.items()):
        for field in _LEGACY_BUILD_CONFIGURATION_DEFAULTS:
            material.pop(field, None)
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
    return tuple(candidates)


def _tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) not in {list, tuple}:
        raise TypeError(f"{field} must be a list or tuple")
    return tuple(value)


def _visible_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or "\x00" in checked:
        raise ValueError(f"{field} is invalid")
    return checked


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    if type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


def _pcb_json_tuples(value: object) -> object:
    """Restore immutable tuple fields after JSON persistence."""
    if isinstance(value, list):
        return tuple(_pcb_json_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _pcb_json_tuples(item) for key, item in value.items()}
    return value


class ProjectType(StrEnum):
    ESP_IDF = "ESP_IDF"
    STM32 = "STM32"
    ARDUINO = "ARDUINO"
    GENERIC_FIRMWARE = "GENERIC_FIRMWARE"
    EXISTING_FIRMWARE = "EXISTING_FIRMWARE"


class ProjectCreationMode(StrEnum):
    CREATE_NEW = "CREATE_NEW"
    ATTACH_EXISTING = "ATTACH_EXISTING"


class ProjectLocationSource(StrEnum):
    PRODUCTION_CONFIG = "PRODUCTION_CONFIG"
    USER_NATIVE_PICKER = "USER_NATIVE_PICKER"
    TEST_FIXTURE = "TEST_FIXTURE"
    GOLDEN_ACCEPTANCE = "GOLDEN_ACCEPTANCE"
    SMOKE_TEST = "SMOKE_TEST"
    LEGACY_UNCLASSIFIED = "LEGACY_UNCLASSIFIED"


class ProjectLocationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class ProjectLegacyMode(StrEnum):
    CREATE = "CREATE"
    ATTACH = "ATTACH"


class ProjectInitializationStatus(StrEnum):
    PENDING = "PENDING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    FAILED = "FAILED"


class ConversationTurnStatus(StrEnum):
    PENDING = "PENDING"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ConversationAnalysisState(StrEnum):
    REQUESTED = "REQUESTED"
    CONTEXT_READY = "CONTEXT_READY"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DISCOVERY_STARTED = "DISCOVERY_STARTED"
    REQUIREMENTS_PARTIAL = "REQUIREMENTS_PARTIAL"
    REQUIREMENTS_REFINING = "REQUIREMENTS_REFINING"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    DESIGN_READY = "DESIGN_READY"


class ProjectCreationRequest(ChatContract):
    name: str
    project_type: ProjectType
    creation_mode: ProjectCreationMode | None = None
    # New clients call the controlled parent location explicitly.  ``location``
    # remains accepted for the v5.2.7 contract and legacy clients.
    parent_location: ProjectLocationReference | None = None
    location: ProjectLocationReference | None = None
    existing_directory: ProjectDirectoryReference | None = None
    relative_path: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        if isinstance(value, str) and value != value.rstrip():
            raise ValueError("project name cannot end with a space")
        return _visible_text(value, field="project name", maximum=96)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str | None:
        return None if value is None else _visible_text(value, field="project relative path", maximum=240)

    @model_validator(mode="after")
    def _contract(self) -> ProjectCreationRequest:
        if self.parent_location is not None and self.location is not None:
            if self.parent_location != self.location:
                raise ValueError("parent location references do not match")
        controlled_location = self.parent_location or self.location
        is_new = self.creation_mode is not None or controlled_location is not None or self.existing_directory is not None
        if is_new:
            if self.creation_mode is None or controlled_location is None or self.relative_path is not None:
                raise ValueError("new project creation contract is incomplete")
            if self.project_type is ProjectType.EXISTING_FIRMWARE:
                raise ValueError("legacy project type is not accepted by the new contract")
            if self.creation_mode is ProjectCreationMode.CREATE_NEW and self.existing_directory is not None:
                raise ValueError("new projects cannot select an existing directory")
            if self.creation_mode is ProjectCreationMode.ATTACH_EXISTING and self.existing_directory is None:
                raise ValueError("existing project directory is required")
        elif self.relative_path is None:
            raise ValueError("legacy relative path is required")
        return self


class ProjectLocationReference(ChatContract):
    location_id: str
    fingerprint: str

    @field_validator("location_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="location_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="location fingerprint")


class ProjectLocationView(FingerprintedChatContract):
    location_id: str
    display_label: str
    availability: str
    path_leaf: str | None = None

    @field_validator("location_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="location_id")

    @field_validator("display_label", "availability", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("path_leaf", mode="before")
    @classmethod
    def _path_leaf(cls, value: object) -> str | None:
        if value is None:
            return None
        return safe_text(value, field="path_leaf", maximum=128)


class ProjectDirectoryReference(ChatContract):
    directory_id: str
    fingerprint: str

    @field_validator("directory_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="directory_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="directory fingerprint")


class ProjectDirectoryView(ChatContract):
    directory_id: str
    display_name: str
    fingerprint: str

    @field_validator("directory_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="directory_id")

    @field_validator("display_name", mode="before")
    @classmethod
    def _display_name(cls, value: object) -> str:
        return _visible_text(value, field="directory display name", maximum=96)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="directory fingerprint")


class ProjectDirectoryPage(ChatContract):
    items: tuple[ProjectDirectoryView, ...]

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: object) -> tuple[ProjectDirectoryView, ...]:
        return tuple(_exact(item, ProjectDirectoryView, field="project directory") for item in _tuple(value, field="items"))


ProjectCreationRequest.model_rebuild()


class ProjectCreationPreview(FingerprintedChatContract):
    preview_id: str
    tenant_id: str
    requester: str
    name: str
    location: ProjectLocationView
    existing_directory: ProjectDirectoryView | None = None
    directory_name: str
    creation_mode: ProjectCreationMode
    relative_path: str
    project_type: ProjectType
    mode: ProjectLegacyMode
    created_at: datetime
    expires_at: datetime
    write_summary: tuple[str, ...]

    @field_validator("preview_id", "tenant_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("name", "directory_name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return _visible_text(value, field="project name", maximum=96)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return _visible_text(value, field="project relative path", maximum=240)

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @field_validator("write_summary", mode="before")
    @classmethod
    def _writes(cls, value: object) -> tuple[str, ...]:
        return tuple(
            safe_text(item, field="write summary", maximum=128)
            for item in _tuple(value, field="write_summary")
        )


class LegacyProjectCreationPreview(FingerprintedChatContract):
    preview_id: str
    tenant_id: str
    requester: str
    name: str
    relative_path: str
    project_type: ProjectType
    mode: ProjectLegacyMode
    created_at: datetime
    expires_at: datetime
    write_summary: tuple[str, ...]


class ProjectView(FingerprintedChatContract):
    project_id: str
    tenant_id: str
    name: str
    location_id: str
    directory_name: str
    creation_mode: ProjectCreationMode
    relative_path: str
    project_type: ProjectType
    initialization_status: ProjectInitializationStatus
    platform: str | None = None
    version: str | None = None
    branch: str | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime

    @field_validator("project_id", "tenant_id", "location_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("name", "directory_name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return _visible_text(value, field="project name", maximum=96)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return _visible_text(value, field="project relative path", maximum=240)

    @field_validator("platform", "version", "branch", mode="before")
    @classmethod
    def _metadata(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name, maximum=128)

    @field_validator("failure_code", mode="before")
    @classmethod
    def _failure(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="failure_code")

    @field_validator("created_at", "updated_at", "last_activity_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)


class ProjectDeletionResult(FingerprintedChatContract):
    project_id: str
    project_fingerprint: str
    deleted_at: datetime
    operation: str = "PROJECT_DELETED"

    @field_validator("project_id", "operation", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_fingerprint", mode="before")
    @classmethod
    def _project_fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="project fingerprint")

    @field_validator("deleted_at", mode="before")
    @classmethod
    def _deleted_at(cls, value: object) -> datetime:
        return utc_datetime(value, field="deleted_at")


class LegacyProjectView(FingerprintedChatContract):
    project_id: str
    tenant_id: str
    name: str
    relative_path: str
    project_type: ProjectType
    initialization_status: ProjectInitializationStatus
    platform: str | None = None
    version: str | None = None
    branch: str | None = None
    failure_code: str | None = None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime


class ProjectContextReference(FingerprintedChatContract):
    reference_id: str
    category: str
    display_label: str
    source_fingerprint: str

    @field_validator("reference_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="reference_id")

    @field_validator("category", "display_label", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")


class ProjectContextGroup(ChatContract):
    category: str
    references: tuple[ProjectContextReference, ...]

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: object) -> str:
        return safe_text(value, field="category", maximum=64)

    @field_validator("references", mode="before")
    @classmethod
    def _references(cls, value: object) -> tuple[ProjectContextReference, ...]:
        return tuple(
            _exact(item, ProjectContextReference, field="context reference")
            for item in _tuple(value, field="references")
        )


class EvidenceSourceCount(ChatContract):
    source: str
    count: int

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return safe_text(value, field="evidence source", maximum=64)

    @field_validator("count", mode="before")
    @classmethod
    def _count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("evidence count is invalid")
        return value


class ProjectCapability(ChatContract):
    capability: str
    status: str

    @field_validator("capability", "status", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=64)


class ProjectContextView(FingerprintedChatContract):
    project_id: str
    status: ProjectInitializationStatus
    groups: tuple[ProjectContextGroup, ...]
    evidence_sources: tuple[EvidenceSourceCount, ...]
    capabilities: tuple[ProjectCapability, ...]
    generated_at: datetime

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("groups", mode="before")
    @classmethod
    def _groups(cls, value: object) -> tuple[ProjectContextGroup, ...]:
        return tuple(
            _exact(item, ProjectContextGroup, field="context group")
            for item in _tuple(value, field="groups")
        )

    @field_validator("evidence_sources", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[EvidenceSourceCount, ...]:
        return tuple(
            _exact(item, EvidenceSourceCount, field="evidence source")
            for item in _tuple(value, field="evidence_sources")
        )

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[ProjectCapability, ...]:
        return tuple(
            _exact(item, ProjectCapability, field="project capability")
            for item in _tuple(value, field="capabilities")
        )

    @field_validator("generated_at", mode="before")
    @classmethod
    def _generated(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class ConversationTurn(FingerprintedChatContract):
    turn_id: str
    request_id: str
    trace_id: str | None = None
    route: str | None = None
    continuation_of_turn_id: str | None = None
    previous_analysis_state: str | None = None
    previous_route: str | None = None
    previous_intent: str | None = None
    status: ConversationTurnStatus
    question: str
    answer_text: str | None = None
    response_type: ResponseType = ResponseType.CHAT
    artifact_refs: tuple[str, ...] = ()
    evidence: tuple[dict[str, object], ...] = ()
    recommendations: tuple[dict[str, object], ...] = ()
    context_references: tuple[dict[str, object], ...] = ()
    error_code: str | None = None
    analysis_state: ConversationAnalysisState = ConversationAnalysisState.REQUESTED
    analysis_reason_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    terminal_event: str | None = None

    @field_validator("turn_id", "request_id", "trace_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return None if value is None and info.field_name == "trace_id" else identifier(value, field=info.field_name)

    @field_validator("route", "continuation_of_turn_id", "previous_analysis_state", "previous_route", "previous_intent", "terminal_event", mode="before")
    @classmethod
    def _optional_identifiers(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ConversationTurnStatus:
        return value if isinstance(value, ConversationTurnStatus) else ConversationTurnStatus(value)

    @field_validator("response_type", mode="before")
    @classmethod
    def _response_type(cls, value: object) -> ResponseType:
        return value if isinstance(value, ResponseType) else ResponseType(value)

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> str:
        return _visible_text(value, field="question", maximum=16384)

    @field_validator("answer_text", mode="before")
    @classmethod
    def _answer(cls, value: object) -> str | None:
        return None if value is None else _visible_text(value, field="answer_text", maximum=16384)

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _artifact_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(identifier(item, field="artifact_ref") for item in _tuple(value, field="artifact_refs"))

    @field_validator("evidence", "recommendations", "context_references", mode="before")
    @classmethod
    def _safe_objects(cls, value: object, info) -> tuple[dict[str, object], ...]:
        items = _tuple(value, field=info.field_name)
        if any(type(item) is not dict for item in items):
            raise TypeError(f"{info.field_name} is invalid")
        return tuple(copy.deepcopy(item) for item in items)

    @field_validator("error_code", mode="before")
    @classmethod
    def _error(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="error_code")

    @field_validator("analysis_state", mode="before")
    @classmethod
    def _analysis_state(cls, value: object) -> ConversationAnalysisState:
        return value if isinstance(value, ConversationAnalysisState) else ConversationAnalysisState(value)

    @field_validator("analysis_reason_code", mode="before")
    @classmethod
    def _analysis_reason(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="analysis_reason_code")

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @field_validator("completed_at", mode="before")
    @classmethod
    def _completed(cls, value: object) -> datetime | None:
        return None if value is None else utc_datetime(value, field="completed_at")


class _ProjectSupplyContract(RuntimeContract):
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return validate_fingerprint(value, field="fingerprint")

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("project supply fingerprint mismatch")
        return self


class ProjectSupplyRelationV2(_ProjectSupplyContract):
    power_domain_id: str
    source_component_refs: tuple[str, ...]
    consumer_component_refs: tuple[str, ...]
    nominal_voltage_v: str
    supply_net_ref: str
    ground_net_ref: str
    evidence_refs: tuple[str, ...]

    @field_validator("power_domain_id", "supply_net_ref", "ground_net_ref", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("source_component_refs", "consumer_component_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError(f"{info.field_name} must be a bounded nonempty sequence")
        refs = tuple(validate_identifier(item, field=info.field_name) for item in value)
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} contains duplicates")
        return refs

    @field_validator("nominal_voltage_v", mode="before")
    @classmethod
    def _voltage(cls, value: object) -> str:
        value = decimal_text(value)
        if Decimal(value) <= 0:
            raise ValueError("nominal voltage must be positive")
        return value

    @model_validator(mode="after")
    def _relationship(self):
        if (set(self.source_component_refs) & set(self.consumer_component_refs)
                or self.supply_net_ref == self.ground_net_ref):
            raise ValueError("project supply relation overlaps or uses one net")
        return self


class ProjectSupplyCandidateV2(_ProjectSupplyContract):
    schema_version: Literal[2]
    candidate_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    design_truth_id: str
    design_truth_fingerprint: str
    selection_id: str
    selection_fingerprint: str
    circuit_id: str
    circuit_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    relations: tuple[ProjectSupplyRelationV2, ...]
    created_at: datetime

    @field_validator("candidate_id", "tenant_id", "project_id", "conversation_id", "design_truth_id", "selection_id", "circuit_id", "plan_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("design_truth_fingerprint", "selection_fingerprint", "circuit_fingerprint", "plan_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object, info) -> str:
        return validate_fingerprint(value, field=info.field_name)

    @field_validator("relations", mode="before")
    @classmethod
    def _relations(cls, value: object) -> tuple[ProjectSupplyRelationV2, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 64:
            raise ValueError("project supply relations must be bounded and nonempty")
        return tuple(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _unique_domains(self):
        ids = tuple(item.power_domain_id for item in self.relations)
        if len(ids) != len(set(ids)):
            raise ValueError("project supply domains repeat")
        return self


class SourcePublicationBindingV1(RuntimeContract):
    source_id: str
    source_record_fingerprint: str
    publisher_id: str
    publication_id: str
    generation: int
    publication_fingerprint: str

    @field_validator("source_id", "publisher_id", "publication_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("source_record_fingerprint", "publication_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object, info) -> str:
        return validate_fingerprint(value, field=info.field_name)

    @field_validator("generation", mode="before")
    @classmethod
    def _generation(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("source publication generation is invalid")
        return value


class ProjectSupplyCandidateV3(ProjectSupplyCandidateV2):
    schema_version: Literal[3]
    source_publications: tuple[SourcePublicationBindingV1, ...]

    @field_validator("source_publications", mode="before")
    @classmethod
    def _publications(cls, value: object) -> tuple[SourcePublicationBindingV1, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("source publication bindings must be bounded and nonempty")
        return tuple(value)

    @model_validator(mode="after")
    def _source_order(self):
        ids = tuple(item.source_id for item in self.source_publications)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("source publication bindings must be unique and sorted")
        return self


class ProjectSupplyReviewV2(_ProjectSupplyContract):
    schema_version: Literal[2]
    review_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    candidate_id: str
    candidate_fingerprint: str
    decision: Literal["APPROVED", "REJECTED"]
    reviewer_id: str
    decided_at: datetime

    @field_validator("review_id", "tenant_id", "project_id", "conversation_id", "candidate_id", "reviewer_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("candidate_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return validate_fingerprint(value, field="candidate_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return validate_utc_datetime(value)


class ProjectSupplyStateV2(RuntimeContract):
    schema_version: Literal[2]
    candidate: ProjectSupplyCandidateV2
    review: ProjectSupplyReviewV2 | None = None

    @model_validator(mode="after")
    def _review_binding(self):
        review = self.review
        candidate = self.candidate
        if review is not None and (
            review.tenant_id != candidate.tenant_id or review.project_id != candidate.project_id
            or review.conversation_id != candidate.conversation_id
            or review.candidate_id != candidate.candidate_id
            or review.candidate_fingerprint != candidate.fingerprint
        ):
            raise ValueError("project supply review candidate binding mismatch")
        return self


class _StandardApplicabilityContract(RuntimeContract):
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return validate_fingerprint(value, field="fingerprint")

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("standard applicability fingerprint mismatch")
        return self


class StandardTargetBindingV2(RuntimeContract):
    component_instance_id: str
    source_record_id: str
    source_record_fingerprint: str
    device_conditions: ParameterConditions

    @field_validator("component_instance_id", "source_record_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("source_record_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return validate_fingerprint(value, field="source_record_fingerprint")


class StandardApplicabilityEvidenceV2(_StandardApplicabilityContract):
    schema_version: Literal[2]
    evidence_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    document_uri: str
    document_revision: str
    content_sha256: str
    locator: ParameterLocator
    mode: Literal["I2C_STANDARD"]
    frequency_max_hz: str | None = None
    rise_time_max_s: str | None = None
    cbus_max_f: str | None = None
    target_net_ids: tuple[str, ...]
    target_components: tuple[StandardTargetBindingV2, ...]
    requirement_id: str
    requirement_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    created_at: datetime

    @field_validator("evidence_id", "tenant_id", "project_id", "conversation_id", "requirement_id", "plan_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("requirement_fingerprint", "plan_fingerprint", "content_sha256", mode="before")
    @classmethod
    def _fp(cls, value: object, info) -> str:
        return validate_fingerprint(value, field=info.field_name)

    @field_validator("frequency_max_hz", "rise_time_max_s", "cbus_max_f", mode="before")
    @classmethod
    def _number(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = decimal_text(value)
        if Decimal(normalized) <= 0:
            raise ValueError("standard limit must be positive")
        return normalized

    @field_validator("document_uri", "document_revision", mode="before")
    @classmethod
    def _document(cls, value: object, info) -> str:
        if type(value) is not str or not value or len(value) > 1024:
            raise ValueError(f"{info.field_name} is invalid")
        return value

    @field_validator("target_net_ids", mode="before")
    @classmethod
    def _nets(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("standard target nets must be bounded and nonempty")
        refs = tuple(validate_identifier(item, field="target_net_id") for item in value)
        if len(refs) != len(set(refs)):
            raise ValueError("standard target net repeats")
        return refs

    @field_validator("target_components", mode="before")
    @classmethod
    def _components(cls, value: object) -> tuple[StandardTargetBindingV2, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("standard target components must be bounded and nonempty")
        return tuple(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return validate_utc_datetime(datetime.fromisoformat(value) if isinstance(value, str) else value)

    @model_validator(mode="after")
    def _binding(self):
        fields = (self.frequency_max_hz, self.rise_time_max_s, self.cbus_max_f)
        ids = tuple(item.component_instance_id for item in self.target_components)
        if not any(field is not None for field in fields) or len(ids) != len(set(ids)):
            raise ValueError("standard fact or target component is invalid")
        return self


class StandardApplicabilityReviewV2(_StandardApplicabilityContract):
    schema_version: Literal[2]
    evidence_id: str
    evidence_fingerprint: str
    review_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    decision: Literal["APPROVED", "REJECTED"]
    reviewer_id: str
    decided_at: datetime

    @field_validator("evidence_id", "review_id", "tenant_id", "project_id", "conversation_id", "reviewer_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("evidence_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return validate_fingerprint(value, field="evidence_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided(cls, value: object) -> datetime:
        return validate_utc_datetime(datetime.fromisoformat(value) if isinstance(value, str) else value)


class ReviewedStandardApplicabilityV2(RuntimeContract):
    evidence: StandardApplicabilityEvidenceV2
    review: StandardApplicabilityReviewV2 | None = None

    @model_validator(mode="after")
    def _binding(self):
        evidence, review = self.evidence, self.review
        if review is not None and (
            review.evidence_id != evidence.evidence_id or review.evidence_fingerprint != evidence.fingerprint
            or (review.tenant_id, review.project_id, review.conversation_id)
            != (evidence.tenant_id, evidence.project_id, evidence.conversation_id)
        ):
            raise ValueError("standard applicability review binding mismatch")
        return self


class _LibraryTerminalMapContract(RuntimeContract):
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return validate_fingerprint(value, field="fingerprint")

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("library terminal map fingerprint mismatch")
        return self


class LibraryTerminalMapEvidenceV2(_LibraryTerminalMapContract):
    schema_version: Literal[2]
    evidence_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    component_instance_id: str
    source_record_id: str
    source_record_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    symbol_library_id: str
    symbol_uri: str
    symbol_sha256: str
    footprint_library_id: str
    footprint_uri: str
    footprint_sha256: str
    terminal_bindings: tuple[LibraryTerminalBindingV2, ...]
    created_at: datetime

    @field_validator("evidence_id", "tenant_id", "project_id", "conversation_id", "component_instance_id", "source_record_id", "plan_id", "symbol_library_id", "footprint_library_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("source_record_fingerprint", "plan_fingerprint", "symbol_sha256", "footprint_sha256", mode="before")
    @classmethod
    def _fp(cls, value: object, info) -> str:
        return validate_fingerprint(value, field=info.field_name)

    @field_validator("symbol_uri", "footprint_uri", mode="before")
    @classmethod
    def _uri(cls, value: object) -> str:
        if type(value) is not str or not value or len(value) > 1024:
            raise ValueError("library uri is invalid")
        return value

    @field_validator("terminal_bindings", mode="before")
    @classmethod
    def _bindings(cls, value: object) -> tuple[LibraryTerminalBindingV2, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("library terminal bindings must be bounded and nonempty")
        return tuple(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return validate_utc_datetime(datetime.fromisoformat(value) if isinstance(value, str) else value)

    @model_validator(mode="after")
    def _coverage(self):
        manufacturers = tuple(item.manufacturer_terminal for item in self.terminal_bindings)
        logical = tuple(item.logical_symbol_pin for item in self.terminal_bindings)
        pads = tuple(pad for item in self.terminal_bindings for pad in item.physical_pad_ids)
        if (len(set(manufacturers)) != len(manufacturers) or len(set(logical)) != len(logical)
            or len(set(pads)) != len(pads)):
            raise ValueError("library terminal coverage repeats")
        return self


class LibraryTerminalMapReviewV2(StandardApplicabilityReviewV2):
    """The approved library Review uses the same exact identity fields as P2."""


class ReviewedLibraryTerminalMapV2(RuntimeContract):
    evidence: LibraryTerminalMapEvidenceV2
    review: LibraryTerminalMapReviewV2 | None = None

    @model_validator(mode="after")
    def _binding(self):
        evidence, review = self.evidence, self.review
        if review is not None and (
            review.evidence_id != evidence.evidence_id or review.evidence_fingerprint != evidence.fingerprint
            or (review.tenant_id, review.project_id, review.conversation_id)
            != (evidence.tenant_id, evidence.project_id, evidence.conversation_id)
        ):
            raise ValueError("library terminal map review binding mismatch")
        return self


class ProjectSupplyStateV3(ProjectSupplyStateV2):
    schema_version: Literal[3]
    candidate: ProjectSupplyCandidateV3


class ConversationDetail(FingerprintedChatContract):
    conversation_id: str
    project_id: str
    tenant_id: str
    requester: str
    title: str
    turns: tuple[ConversationTurn, ...]
    created_at: datetime
    updated_at: datetime
    engineering_session_mode: EngineeringSessionMode = EngineeringSessionMode.UNDETERMINED
    requirement_context: RequirementContext | None = None
    research_snapshot: EngineeringResearchSnapshot | None = None
    requirement_suggestion_decisions: tuple[RequirementSuggestionDecision, ...] = ()
    reference_project_decisions: tuple[ReferenceProjectDecision, ...] = ()
    feature_decisions: tuple[FeatureDecision, ...] = ()
    requirement_change_proposal: RequirementChangeProposal | None = None
    requirement_final_confirmation: RequirementFinalConfirmation | None = None
    research_intent_refinement: ResearchIntentRefinement | None = None
    engineering_plan: EngineeringPlan | None = None
    engineering_plan_review: PlanReviewDecision | None = None
    architecture_design: ArchitectureDesign | None = None
    architecture_review: ArchitectureReviewDecision | None = None
    design_truth: DesignTruth | None = None
    project_supply: ProjectSupplyStateV2 | ProjectSupplyStateV3 | None = Field(default=None, exclude_if=lambda value: value is None)
    standard_applicability: tuple[ReviewedStandardApplicabilityV2, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    library_terminal_maps: tuple[ReviewedLibraryTerminalMapV2, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    engineering_knowledge_model: EngineeringKnowledgeModel | None = None
    teaching_context: TeachingContext | None = None
    engineering_workflow: EngineeringWorkflow | None = None
    hardware_design_spec: HardwareDesignSpecification | None = None
    hardware_selection: HardwareSelectionState | None = None
    schematic_planning: SchematicPlanningState | None = None
    firmware_spec: FirmwareSpecification | None = None
    simulation_spec: SimulationSpecification | None = None
    verification_spec: VerificationSpecification | None = None
    virtual_system_verification: VirtualSystemVerificationState | None = None
    implementation_spec: ImplementationSpecification | None = None
    build_configuration: FirmwareBuildConfiguration | None = None
    capability_evaluation: CapabilityEvaluation | None = None
    execution_intents: tuple[ExecutionIntent, ...] = ()
    execution_authorization: ExecutionAuthorization | None = None
    firmware_source_generation: FirmwareSourceGenerationState | None = None
    firmware_production: FirmwareProductionState | None = None
    schematic_production: SchematicProductionState | None = None
    pcb_production_request: PCBProductionRequest | None = None
    pcb_production: PCBProductionState | None = None
    pcb_engineering_state: PCBEngineeringState | None = None
    pcb_constraint_set: PCBConstraintSet | None = None
    pcb_verification_plan: PCBVerificationPlan | None = None
    pcb_decoupling_verification: VerificationResult | None = None
    pcb_decoupling_measurements: tuple[DecouplingMeasurement, ...] = ()
    pcb_return_path_verification: VerificationResult | None = None
    pcb_return_path_measurements: tuple[ReferenceCoverageMeasurement, ...] = ()
    pcb_return_path_relationships: tuple[ReturnPathRelationship, ...] = ()
    pcb_return_path_discontinuities: tuple[ReferenceDiscontinuity, ...] = ()
    pcb_return_path_transitions: tuple[LayerTransitionReturnAssessment, ...] = ()
    pcb_power_loop_verification: VerificationResult | None = None
    pcb_power_stage_relationships: tuple[PowerStageRelationship, ...] = ()
    pcb_power_loop_relationships: tuple[PowerLoopRelationship, ...] = ()
    pcb_power_loop_measurements: tuple[PowerLoopMeasurement, ...] = ()
    pcb_power_switch_nodes: tuple[SwitchNodeRegion, ...] = ()
    pcb_power_feedback_relationships: tuple[FeedbackRelationship, ...] = ()
    pcb_differential_pair_verification: VerificationResult | None = None
    pcb_differential_pair_relationships: tuple[DifferentialPairRelationship, ...] = ()
    pcb_differential_pair_measurements: tuple[DifferentialPairMeasurement, ...] = ()
    pcb_differential_pair_dependency_fingerprints: tuple[str, ...] = ()
    pcb_mapping_verification: VerificationResult | None = None
    pcb_component_mapping_relationships: tuple[ComponentMappingRelationship, ...] = ()
    pcb_pin_mapping_relationships: tuple[PinMappingRelationship, ...] = ()
    pcb_pad_mapping_relationships: tuple[PadMappingRelationship, ...] = ()
    pcb_engineering_evidence: tuple[EngineeringEvidenceRequirement, ...] = ()
    pcb_manufacturer_profile: ManufacturerProfile | None = None
    pcb_manufacturing_measurements: PCBManufacturingMeasurements | None = None
    pcb_engineering_verification_summary: PCBEngineeringVerificationSummary | None = None
    pcb_ready_for_fab: ReadyForFabGate | None = None
    pcb_ready_for_fab_approval: HumanApprovalBinding | None = None
    artifact_intelligence_snapshot: ArtifactIntelligenceSnapshot | None = None
    engineering_evidence_bundle: EngineeringEvidenceBundle | None = None

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        plan = self.engineering_plan
        if plan is None or plan.model_operation_schema_version != 1:
            return super()._bind_fingerprint()
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        payload["engineering_plan"].pop("model_operation_schema_version", None)
        payload["engineering_plan"].pop("model_operation_bindings", None)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            # Historical rows can predate later optional projections. Their
            # stored canonical payload omitted those fields entirely.
            earlier = self.model_dump(
                mode="json", exclude={"fingerprint", *(set(type(self).model_fields) - self.model_fields_set)}
            )
            earlier["engineering_plan"].pop("model_operation_schema_version", None)
            earlier["engineering_plan"].pop("model_operation_bindings", None)
            earlier_encoded = json.dumps(earlier, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            earlier_fingerprint = "sha256:" + hashlib.sha256(earlier_encoded).hexdigest()
            if self.fingerprint != earlier_fingerprint and self.fingerprint not in self._legacy_fingerprint_candidates():
                raise ValueError("chat fingerprint mismatch")
        return self

    @field_validator("conversation_id", "project_id", "tenant_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return _visible_text(value, field="conversation title", maximum=64)

    @field_validator("turns", mode="before")
    @classmethod
    def _turns(cls, value: object) -> tuple[ConversationTurn, ...]:
        return tuple(
            _exact(item, ConversationTurn, field="conversation turn")
            for item in _tuple(value, field="turns")
        )

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @field_validator("engineering_session_mode", mode="before")
    @classmethod
    def _session_mode(cls, value: object) -> EngineeringSessionMode:
        return value if isinstance(value, EngineeringSessionMode) else EngineeringSessionMode(value)

    @field_validator("requirement_context", mode="before")
    @classmethod
    def _requirements(cls, value: object) -> RequirementContext | None:
        return None if value is None else RequirementContext.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("research_snapshot", mode="before")
    @classmethod
    def _research_snapshot(cls, value: object) -> EngineeringResearchSnapshot | None:
        return None if value is None else EngineeringResearchSnapshot.model_validate(
            copy.deepcopy(value), strict=False
        )

    @field_validator("requirement_suggestion_decisions", mode="before")
    @classmethod
    def _suggestion_decisions(cls, value: object) -> tuple[RequirementSuggestionDecision, ...]:
        return tuple(
            item if isinstance(item, RequirementSuggestionDecision)
            else RequirementSuggestionDecision.model_validate(copy.deepcopy(item), strict=False)
            for item in _tuple(value, field="requirement_suggestion_decisions")
        )

    @field_validator("reference_project_decisions", mode="before")
    @classmethod
    def _reference_project_decisions(cls, value: object) -> tuple[ReferenceProjectDecision, ...]:
        return tuple(
            item if isinstance(item, ReferenceProjectDecision)
            else ReferenceProjectDecision.model_validate(copy.deepcopy(item), strict=False)
            for item in _tuple(value, field="reference_project_decisions")
        )

    @field_validator("feature_decisions", mode="before")
    @classmethod
    def _feature_decisions(cls, value: object) -> tuple[FeatureDecision, ...]:
        return tuple(
            item if isinstance(item, FeatureDecision)
            else FeatureDecision.model_validate(copy.deepcopy(item), strict=False)
            for item in _tuple(value, field="feature_decisions")
        )

    @field_validator("requirement_change_proposal", mode="before")
    @classmethod
    def _requirement_change_proposal(cls, value: object) -> RequirementChangeProposal | None:
        return None if value is None else RequirementChangeProposal.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("requirement_final_confirmation", mode="before")
    @classmethod
    def _requirement_final_confirmation(cls, value: object) -> RequirementFinalConfirmation | None:
        return None if value is None else RequirementFinalConfirmation.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("research_intent_refinement", mode="before")
    @classmethod
    def _research_intent_refinement(cls, value: object) -> ResearchIntentRefinement | None:
        return None if value is None else ResearchIntentRefinement.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_plan", mode="before")
    @classmethod
    def _engineering_plan(cls, value: object) -> EngineeringPlan | None:
        return None if value is None else EngineeringPlan.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_plan_review", mode="before")
    @classmethod
    def _engineering_plan_review(cls, value: object) -> PlanReviewDecision | None:
        return None if value is None else PlanReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_design", mode="before")
    @classmethod
    def _architecture_design(cls, value: object) -> ArchitectureDesign | None:
        return None if value is None else ArchitectureDesign.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_review", mode="before")
    @classmethod
    def _architecture_review(cls, value: object) -> ArchitectureReviewDecision | None:
        return None if value is None else ArchitectureReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("design_truth", mode="before")
    @classmethod
    def _design_truth(cls, value: object) -> DesignTruth | None:
        return None if value is None else DesignTruth.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("project_supply", mode="before")
    @classmethod
    def _project_supply(cls, value: object) -> ProjectSupplyStateV2 | ProjectSupplyStateV3 | None:
        if value is None:
            return None
        material = copy.deepcopy(value.model_dump(mode="python") if hasattr(value, "model_dump") else value)
        if type(material) is not dict:
            raise ValueError("project supply state is invalid")
        version = material.get("schema_version")
        if version == 2:
            return ProjectSupplyStateV2.model_validate(material, strict=False)
        if version == 3:
            return ProjectSupplyStateV3.model_validate(material, strict=False)
        raise ValueError("project supply state version is invalid")

    @field_validator("standard_applicability", mode="before")
    @classmethod
    def _standard_applicability(cls, value: object) -> tuple[ReviewedStandardApplicabilityV2, ...] | None:
        if value is None:
            return None
        if type(value) not in (tuple, list):
            raise ValueError("standard applicability must be a sequence")
        return tuple(value)

    @field_validator("library_terminal_maps", mode="before")
    @classmethod
    def _library_terminal_maps(cls, value: object) -> tuple[ReviewedLibraryTerminalMapV2, ...] | None:
        if value is None:
            return None
        if type(value) not in (tuple, list):
            raise ValueError("library terminal maps must be a sequence")
        return tuple(value)

    @field_validator("engineering_knowledge_model", mode="before")
    @classmethod
    def _engineering_knowledge_model(cls, value: object) -> EngineeringKnowledgeModel | None:
        return None if value is None else EngineeringKnowledgeModel.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("teaching_context", mode="before")
    @classmethod
    def _teaching_context(cls, value: object) -> TeachingContext | None:
        return None if value is None else TeachingContext.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_workflow", mode="before")
    @classmethod
    def _engineering_workflow(cls, value: object) -> EngineeringWorkflow | None:
        return None if value is None else EngineeringWorkflow.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("hardware_design_spec", mode="before")
    @classmethod
    def _hardware_design_spec(cls, value: object) -> HardwareDesignSpecification | None:
        return None if value is None else HardwareDesignSpecification.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("hardware_selection", mode="before")
    @classmethod
    def _hardware_selection(cls, value: object) -> HardwareSelectionState | None:
        return None if value is None else HardwareSelectionState.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("schematic_planning", mode="before")
    @classmethod
    def _schematic_planning(cls, value: object) -> SchematicPlanningState | None:
        return None if value is None else SchematicPlanningState.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("firmware_spec", mode="before")
    @classmethod
    def _firmware_spec(cls, value: object) -> FirmwareSpecification | None:
        return None if value is None else FirmwareSpecification.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("simulation_spec", mode="before")
    @classmethod
    def _simulation_spec(cls, value: object) -> SimulationSpecification | None:
        return None if value is None else SimulationSpecification.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("verification_spec", mode="before")
    @classmethod
    def _verification_spec(cls, value: object) -> VerificationSpecification | None:
        return None if value is None else VerificationSpecification.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("virtual_system_verification", mode="before")
    @classmethod
    def _virtual_system_verification(cls, value: object) -> VirtualSystemVerificationState | None:
        return None if value is None else VirtualSystemVerificationState.model_validate(
            copy.deepcopy(value), strict=False
        )

    @field_validator("implementation_spec", mode="before")
    @classmethod
    def _implementation_spec(cls, value: object) -> ImplementationSpecification | None:
        return None if value is None else ImplementationSpecification.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("build_configuration", mode="before")
    @classmethod
    def _build_configuration(cls, value: object) -> FirmwareBuildConfiguration | None:
        return None if value is None else FirmwareBuildConfiguration.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("capability_evaluation", mode="before")
    @classmethod
    def _capability_evaluation(cls, value: object) -> CapabilityEvaluation | None:
        return None if value is None else CapabilityEvaluation.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("execution_intents", mode="before")
    @classmethod
    def _execution_intents(cls, value: object) -> tuple[ExecutionIntent, ...]:
        return tuple(
            _exact(item, ExecutionIntent, field="execution intent")
            for item in _tuple(value, field="execution_intents")
        )

    @field_validator("execution_authorization", mode="before")
    @classmethod
    def _execution_authorization(
        cls, value: object
    ) -> ExecutionAuthorization | None:
        return (
            None
            if value is None
            else ExecutionAuthorization.model_validate(copy.deepcopy(value), strict=False)
        )

    @field_validator("firmware_source_generation", mode="before")
    @classmethod
    def _firmware_source_generation(
        cls, value: object
    ) -> FirmwareSourceGenerationState | None:
        return (
            None
            if value is None
            else FirmwareSourceGenerationState.model_validate(copy.deepcopy(value), strict=False)
        )

    @field_validator("firmware_production", mode="before")
    @classmethod
    def _firmware_production(cls, value: object) -> FirmwareProductionState | None:
        return None if value is None else FirmwareProductionState.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("schematic_production", mode="before")
    @classmethod
    def _schematic_production(cls, value: object) -> SchematicProductionState | None:
        return None if value is None else SchematicProductionState.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("pcb_production_request", mode="before")
    @classmethod
    def _pcb_production_request(cls, value: object) -> PCBProductionRequest | None:
        return None if value is None else PCBProductionRequest.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("pcb_production", mode="before")
    @classmethod
    def _pcb_production(cls, value: object) -> PCBProductionState | None:
        return None if value is None else PCBProductionState.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("pcb_engineering_state", mode="before")
    @classmethod
    def _pcb_engineering_state(cls, value: object) -> PCBEngineeringState | None:
        return None if value is None else PCBEngineeringState.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_constraint_set", mode="before")
    @classmethod
    def _pcb_constraint_set(cls, value: object) -> PCBConstraintSet | None:
        return None if value is None else PCBConstraintSet.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_verification_plan", mode="before")
    @classmethod
    def _pcb_verification_plan(cls, value: object) -> PCBVerificationPlan | None:
        return None if value is None else PCBVerificationPlan.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_decoupling_verification", mode="before")
    @classmethod
    def _pcb_decoupling_verification(cls, value: object) -> VerificationResult | None:
        return None if value is None else VerificationResult.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_decoupling_measurements", mode="before")
    @classmethod
    def _pcb_decoupling_measurements(cls, value: object) -> tuple[DecouplingMeasurement, ...]:
        return tuple(
            item if isinstance(item, DecouplingMeasurement)
            else DecouplingMeasurement.model_validate(copy.deepcopy(item), strict=False)
            for item in _tuple(value, field="pcb_decoupling_measurements")
        )

    @field_validator("pcb_return_path_verification", mode="before")
    @classmethod
    def _pcb_return_path_verification(cls, value: object) -> VerificationResult | None:
        return None if value is None else VerificationResult.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_return_path_measurements", mode="before")
    @classmethod
    def _pcb_return_path_measurements(cls, value: object) -> tuple[ReferenceCoverageMeasurement, ...]:
        return tuple(
            item if isinstance(item, ReferenceCoverageMeasurement)
            else ReferenceCoverageMeasurement.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_return_path_measurements")
        )

    @field_validator("pcb_return_path_relationships", mode="before")
    @classmethod
    def _pcb_return_path_relationships(cls, value: object) -> tuple[ReturnPathRelationship, ...]:
        return tuple(
            item if isinstance(item, ReturnPathRelationship)
            else ReturnPathRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_return_path_relationships")
        )

    @field_validator("pcb_return_path_discontinuities", mode="before")
    @classmethod
    def _pcb_return_path_discontinuities(cls, value: object) -> tuple[ReferenceDiscontinuity, ...]:
        return tuple(
            item if isinstance(item, ReferenceDiscontinuity)
            else ReferenceDiscontinuity.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_return_path_discontinuities")
        )

    @field_validator("pcb_return_path_transitions", mode="before")
    @classmethod
    def _pcb_return_path_transitions(cls, value: object) -> tuple[LayerTransitionReturnAssessment, ...]:
        return tuple(
            item if isinstance(item, LayerTransitionReturnAssessment)
            else LayerTransitionReturnAssessment.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_return_path_transitions")
        )

    @field_validator("pcb_power_loop_verification", mode="before")
    @classmethod
    def _pcb_power_loop_verification(cls, value: object) -> VerificationResult | None:
        return None if value is None else VerificationResult.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_power_stage_relationships", mode="before")
    @classmethod
    def _pcb_power_stage_relationships(cls, value: object) -> tuple[PowerStageRelationship, ...]:
        return tuple(
            item if isinstance(item, PowerStageRelationship)
            else PowerStageRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_power_stage_relationships")
        )

    @field_validator("pcb_power_loop_relationships", mode="before")
    @classmethod
    def _pcb_power_loop_relationships(cls, value: object) -> tuple[PowerLoopRelationship, ...]:
        return tuple(
            item if isinstance(item, PowerLoopRelationship)
            else PowerLoopRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_power_loop_relationships")
        )

    @field_validator("pcb_power_loop_measurements", mode="before")
    @classmethod
    def _pcb_power_loop_measurements(cls, value: object) -> tuple[PowerLoopMeasurement, ...]:
        return tuple(
            item if isinstance(item, PowerLoopMeasurement)
            else PowerLoopMeasurement.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_power_loop_measurements")
        )

    @field_validator("pcb_power_switch_nodes", mode="before")
    @classmethod
    def _pcb_power_switch_nodes(cls, value: object) -> tuple[SwitchNodeRegion, ...]:
        return tuple(
            item if isinstance(item, SwitchNodeRegion)
            else SwitchNodeRegion.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_power_switch_nodes")
        )

    @field_validator("pcb_power_feedback_relationships", mode="before")
    @classmethod
    def _pcb_power_feedback_relationships(cls, value: object) -> tuple[FeedbackRelationship, ...]:
        return tuple(
            item if isinstance(item, FeedbackRelationship)
            else FeedbackRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_power_feedback_relationships")
        )

    @field_validator("pcb_differential_pair_verification", mode="before")
    @classmethod
    def _pcb_differential_pair_verification(cls, value: object) -> VerificationResult | None:
        return None if value is None else VerificationResult.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_differential_pair_relationships", mode="before")
    @classmethod
    def _pcb_differential_pair_relationships(cls, value: object) -> tuple[DifferentialPairRelationship, ...]:
        return tuple(
            item if isinstance(item, DifferentialPairRelationship)
            else DifferentialPairRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_differential_pair_relationships")
        )

    @field_validator("pcb_differential_pair_measurements", mode="before")
    @classmethod
    def _pcb_differential_pair_measurements(cls, value: object) -> tuple[DifferentialPairMeasurement, ...]:
        return tuple(
            item if isinstance(item, DifferentialPairMeasurement)
            else DifferentialPairMeasurement.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_differential_pair_measurements")
        )

    @field_validator("pcb_differential_pair_dependency_fingerprints", mode="before")
    @classmethod
    def _pcb_differential_pair_dependencies(cls, value: object) -> tuple[str, ...]:
        return tuple(fingerprint(item, field="pcb_differential_pair_dependency_fingerprint") for item in _tuple(value, field="pcb_differential_pair_dependency_fingerprints"))

    @field_validator("pcb_mapping_verification", mode="before")
    @classmethod
    def _pcb_mapping_verification(cls, value: object) -> VerificationResult | None:
        return None if value is None else VerificationResult.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_component_mapping_relationships", mode="before")
    @classmethod
    def _pcb_component_mapping_relationships(cls, value: object) -> tuple[ComponentMappingRelationship, ...]:
        return tuple(
            item if isinstance(item, ComponentMappingRelationship)
            else ComponentMappingRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_component_mapping_relationships")
        )

    @field_validator("pcb_pin_mapping_relationships", mode="before")
    @classmethod
    def _pcb_pin_mapping_relationships(cls, value: object) -> tuple[PinMappingRelationship, ...]:
        return tuple(
            item if isinstance(item, PinMappingRelationship)
            else PinMappingRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_pin_mapping_relationships")
        )

    @field_validator("pcb_pad_mapping_relationships", mode="before")
    @classmethod
    def _pcb_pad_mapping_relationships(cls, value: object) -> tuple[PadMappingRelationship, ...]:
        return tuple(
            item if isinstance(item, PadMappingRelationship)
            else PadMappingRelationship.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_pad_mapping_relationships")
        )

    @field_validator("pcb_engineering_evidence", mode="before")
    @classmethod
    def _pcb_engineering_evidence(cls, value: object) -> tuple[EngineeringEvidenceRequirement, ...]:
        return tuple(
            item if isinstance(item, EngineeringEvidenceRequirement)
            else EngineeringEvidenceRequirement.model_validate(_pcb_json_tuples(copy.deepcopy(item)), strict=False)
            for item in _tuple(value, field="pcb_engineering_evidence")
        )

    @field_validator("pcb_manufacturer_profile", mode="before")
    @classmethod
    def _pcb_manufacturer_profile(cls, value: object) -> ManufacturerProfile | None:
        return None if value is None else ManufacturerProfile.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_manufacturing_measurements", mode="before")
    @classmethod
    def _pcb_manufacturing_measurements(cls, value: object) -> PCBManufacturingMeasurements | None:
        return None if value is None else PCBManufacturingMeasurements.model_validate(
            _pcb_json_tuples(copy.deepcopy(value)), strict=False
        )

    @field_validator("pcb_engineering_verification_summary", mode="before")
    @classmethod
    def _pcb_engineering_verification_summary(cls, value: object) -> PCBEngineeringVerificationSummary | None:
        return None if value is None else PCBEngineeringVerificationSummary.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_ready_for_fab", mode="before")
    @classmethod
    def _pcb_ready_for_fab(cls, value: object) -> ReadyForFabGate | None:
        return None if value is None else ReadyForFabGate.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("pcb_ready_for_fab_approval", mode="before")
    @classmethod
    def _pcb_ready_for_fab_approval(cls, value: object) -> HumanApprovalBinding | None:
        return None if value is None else HumanApprovalBinding.model_validate(_pcb_json_tuples(copy.deepcopy(value)), strict=False)

    @field_validator("artifact_intelligence_snapshot", mode="before")
    @classmethod
    def _artifact_snapshot(cls, value: object) -> ArtifactIntelligenceSnapshot | None:
        return None if value is None else ArtifactIntelligenceSnapshot.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_evidence_bundle", mode="before")
    @classmethod
    def _evidence_bundle(cls, value: object) -> EngineeringEvidenceBundle | None:
        return None if value is None else EngineeringEvidenceBundle.model_validate(copy.deepcopy(value), strict=False)

    @model_validator(mode="after")
    def _plan_binding(self) -> ConversationDetail:
        if self.research_snapshot is not None:
            snapshot = self.research_snapshot
            if (
                snapshot.project_id != self.project_id
                or snapshot.conversation_id != self.conversation_id
            ):
                raise ValueError("conversation research snapshot identity mismatch")
            suggestions = {item.suggestion_id: item for item in snapshot.suggestions}
            seen_decisions: set[str] = set()
            for decision in self.requirement_suggestion_decisions:
                if (
                    decision.project_id != self.project_id
                    or decision.conversation_id != self.conversation_id
                    or decision.suggestion_id in seen_decisions
                ):
                    raise ValueError("conversation requirement decision identity mismatch")
                suggestion = suggestions.get(decision.suggestion_id)
                if suggestion is None:
                    raise ValueError("conversation requirement decision references unknown suggestion")
                if suggestion.user_decision is not decision.action:
                    raise ValueError("conversation requirement decision status mismatch")
                seen_decisions.add(decision.suggestion_id)
        elif self.requirement_suggestion_decisions:
            raise ValueError("conversation requirement decisions require research snapshot")
        if self.research_snapshot is None and (self.reference_project_decisions or self.feature_decisions):
            raise ValueError("G0R decisions require research snapshot")
        if self.research_snapshot is not None:
            projects = {item.project_identity: item for item in self.research_snapshot.evidence_pack.projects}
            reviews = {item.project_identity: item for item in self.research_snapshot.reference_reviews}
            for decision in self.reference_project_decisions:
                review = reviews.get(decision.project_identity)
                project = projects.get(decision.project_identity)
                if (
                    decision.project_id != self.project_id
                    or decision.conversation_id != self.conversation_id
                    or review is None
                    or project is None
                    or decision.research_fingerprint != self.research_snapshot.evidence_pack.fingerprint
                    or decision.reference_fingerprint != project.fingerprint
                ):
                    raise ValueError("conversation reference project decision binding mismatch")
            for decision in self.feature_decisions:
                project = projects.get(decision.project_identity)
                if (
                    decision.project_id != self.project_id
                    or decision.conversation_id != self.conversation_id
                    or project is None
                    or decision.research_fingerprint != self.research_snapshot.evidence_pack.fingerprint
                    or decision.reference_fingerprint != project.fingerprint
                ):
                    raise ValueError("conversation feature decision binding mismatch")
        proposal = self.requirement_change_proposal
        if proposal is not None:
            if proposal.project_id != self.project_id or proposal.conversation_id != self.conversation_id:
                raise ValueError("conversation requirement change proposal binding mismatch")
            if self.research_snapshot is not None:
                research_fingerprint = self.research_snapshot.evidence_pack.fingerprint
                reference_fingerprints = {item.fingerprint for item in self.research_snapshot.evidence_pack.projects}
                if proposal.research_fingerprint is not None and proposal.research_fingerprint != research_fingerprint:
                    raise ValueError("conversation requirement proposal research binding mismatch")
                if not set(proposal.reference_fingerprints).issubset(reference_fingerprints):
                    raise ValueError("conversation requirement proposal reference binding mismatch")
            confirmation = self.requirement_final_confirmation
            if confirmation is not None and (
                confirmation.proposal_id != proposal.proposal_id
                or confirmation.proposal_fingerprint != proposal.fingerprint
                or confirmation.project_id != self.project_id
                or confirmation.conversation_id != self.conversation_id
            ):
                raise ValueError("conversation requirement confirmation binding mismatch")
        elif self.requirement_final_confirmation is not None:
            raise ValueError("conversation requirement confirmation requires proposal")
        if self.research_intent_refinement is not None and (
            self.research_intent_refinement.project_id != self.project_id
            or self.research_intent_refinement.conversation_id != self.conversation_id
        ):
            raise ValueError("conversation research intent refinement binding mismatch")
        plan = self.engineering_plan
        if plan is not None:
            if plan.project_id != self.project_id:
                raise ValueError("conversation engineering plan project mismatch")
            if (
                self.requirement_context is not None
                and plan.status is not PlanStatus.SUPERSEDED
                and plan.context_fingerprint != self.requirement_context.fingerprint
            ):
                raise ValueError("conversation engineering plan context mismatch")
        review = self.engineering_plan_review
        if plan is not None and plan.status is PlanStatus.APPROVED and review is None:
            raise ValueError("approved engineering plan requires a review decision")
        if review is not None:
            if plan is None or review.plan_id != plan.plan_id or review.project_id != self.project_id:
                raise ValueError("conversation engineering plan review binding mismatch")
            if plan.status is not PlanStatus.APPROVED or review.status.value != "APPROVED":
                raise ValueError("conversation engineering plan review status mismatch")
            if review.proposal_fingerprint != plan.content_fingerprint:
                raise ValueError("conversation engineering plan review fingerprint mismatch")
        architecture = self.architecture_design
        architecture_review = self.architecture_review
        if architecture is not None:
            if architecture.project_id != self.project_id:
                raise ValueError("conversation architecture project mismatch")
            if plan is None or (
                architecture.plan_id != plan.plan_id
                and architecture.status not in {
                    ArchitectureDesignStatus.SUPERSEDED,
                    ArchitectureDesignStatus.INVALIDATED,
                }
            ):
                raise ValueError("conversation architecture plan binding mismatch")
            if self.requirement_context is not None and architecture.context_fingerprint != self.requirement_context.fingerprint and architecture.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }:
                raise ValueError("conversation architecture context mismatch")
            if architecture.plan_fingerprint != plan.fingerprint and architecture.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }:
                raise ValueError("conversation architecture plan fingerprint mismatch")
            if architecture.status is ArchitectureDesignStatus.APPROVED and architecture_review is None:
                raise ValueError("approved architecture requires a review decision")
        if architecture_review is not None:
            if architecture is None or plan is None:
                raise ValueError("conversation architecture review binding mismatch")
            if (
                architecture_review.architecture_id != architecture.architecture_id
                or architecture_review.plan_id != plan.plan_id
                or architecture_review.project_id != self.project_id
                or architecture.status is not ArchitectureDesignStatus.APPROVED
                or architecture_review.status.value != "APPROVED"
                or architecture_review.proposal_fingerprint != architecture.content_fingerprint
            ):
                raise ValueError("conversation architecture review binding mismatch")
        truth = self.design_truth
        if truth is not None:
            if truth.tenant_id != self.tenant_id or truth.project_id != self.project_id:
                raise ValueError("conversation design truth identity mismatch")
            if truth.status is not DesignTruthStatus.APPROVED:
                raise ValueError("conversation design truth must be approved")
            if (
                truth.conversation_id != "legacy-conversation"
                and truth.conversation_id != self.conversation_id
            ):
                raise ValueError("conversation design truth conversation mismatch")
            if truth.origin is DesignTruthOrigin.CREATE:
                if architecture is None or architecture.status is not ArchitectureDesignStatus.APPROVED:
                    raise ValueError("conversation design truth requires approved architecture")
                if truth.architecture_id != architecture.architecture_id:
                    raise ValueError("conversation design truth architecture mismatch")
                architecture_sources = tuple(
                    binding
                    for binding in truth.evidence_bindings
                    if binding.entity_id == truth.truth_id
                    and binding.source is DesignSource.ARCHITECTURE
                    and binding.source_id == architecture.architecture_id
                )
                if not architecture_sources or architecture_sources[0].source_fingerprint != architecture.fingerprint:
                    raise ValueError("conversation design truth source fingerprint mismatch")
            elif truth.architecture_id is not None:
                raise ValueError("conversation reverse design truth architecture mismatch")
            elif self.engineering_evidence_bundle is None or self.engineering_evidence_bundle.status.value != "APPROVED":
                raise ValueError("conversation reverse design truth requires approved evidence")
            elif not any(
                binding.evidence_id in {
                    item.evidence_id for item in self.engineering_evidence_bundle.evidence
                }
                for binding in truth.evidence_bindings
                if binding.evidence_id is not None
            ):
                raise ValueError("conversation reverse design truth evidence mismatch")
        knowledge = self.engineering_knowledge_model
        if knowledge is not None:
            if truth is None:
                raise ValueError("conversation knowledge model requires design truth")
            if knowledge.project_id != self.project_id or knowledge.conversation_id != self.conversation_id:
                raise ValueError("conversation knowledge model identity mismatch")
            if knowledge.source_truth_fingerprint != truth.fingerprint:
                raise ValueError("conversation knowledge model truth fingerprint mismatch")
        teaching = self.teaching_context
        if teaching is not None:
            if knowledge is None:
                raise ValueError("conversation teaching context requires knowledge model")
            if (
                teaching.project_id != self.project_id
                or teaching.conversation_id != self.conversation_id
                or teaching.knowledge_id != knowledge.knowledge_id
            ):
                raise ValueError("conversation teaching context identity mismatch")
            if (
                teaching.source_knowledge_schema_version != knowledge.schema_version
                or teaching.source_knowledge_fingerprint != knowledge.fingerprint
            ):
                raise ValueError("conversation teaching context knowledge binding mismatch")
            entity_by_id = {item.entity_id: item for item in knowledge.entities}
            source_entity = entity_by_id.get(teaching.subject.entity_id)
            if source_entity is None:
                raise ValueError("conversation teaching context subject mismatch")
            if (
                source_entity.entity_type.value != teaching.subject.entity_type.value
                or source_entity.name != teaching.subject.title
                or source_entity.description != teaching.subject.summary
                or source_entity.confidence != teaching.subject.confidence
                or source_entity.source_truth_refs != teaching.subject.source_refs
                or source_entity.evidence_bindings != teaching.subject.evidence_bindings
            ):
                raise ValueError("conversation teaching context subject mismatch")
            entity_ids = set(entity_by_id)
            if (
                not set(teaching.related_entities) <= entity_ids
                or not set(teaching.subject.related_entities) <= entity_ids
                or teaching.subject.entity_id in teaching.related_entities
            ):
                raise ValueError("conversation teaching context entity reference mismatch")
            binding_ids = set(knowledge.evidence_bindings)
            if any(
                not set(refs) <= binding_ids
                for refs in (
                    teaching.evidence_bindings,
                    teaching.subject.evidence_bindings,
                    *(step.evidence_refs for step in teaching.learning_path.steps),
                )
            ):
                raise ValueError("conversation teaching context evidence binding mismatch")
        workflow = self.engineering_workflow
        if workflow is not None:
            if plan is None or architecture is None:
                raise ValueError("conversation workflow requires plan and architecture")
            if plan.status is not PlanStatus.APPROVED or architecture.status is not ArchitectureDesignStatus.APPROVED:
                raise ValueError("conversation workflow requires approved plan and architecture")
            if (
                workflow.project_id != self.project_id
                or workflow.conversation_id != self.conversation_id
                or workflow.plan_id != plan.plan_id
                or workflow.architecture_id != architecture.architecture_id
            ):
                raise ValueError("conversation workflow identity mismatch")
            if architecture.plan_id != plan.plan_id or architecture.plan_fingerprint != plan.fingerprint:
                raise ValueError("conversation workflow source mismatch")
            plan_sources = tuple(
                item for item in workflow.provenance
                if item.source_type == "ENGINEERING_PLAN" and item.source_id == plan.plan_id
            )
            architecture_sources = tuple(
                item for item in workflow.provenance
                if item.source_type == "ARCHITECTURE_DESIGN" and item.source_id == architecture.architecture_id
            )
            if (
                len(plan_sources) != 1
                or plan_sources[0].source_fingerprint != plan.fingerprint
                or len(architecture_sources) != 1
                or architecture_sources[0].source_fingerprint != architecture.fingerprint
            ):
                raise ValueError("conversation workflow provenance mismatch")
        hardware_spec = self.hardware_design_spec
        if hardware_spec is not None:
            if (
                plan is None
                or architecture is None
                or workflow is None
                or plan.status is not PlanStatus.APPROVED
                or architecture.status is not ArchitectureDesignStatus.APPROVED
            ):
                raise ValueError("conversation hardware design requires approved architecture and workflow")
            if (
                hardware_spec.project_id != self.project_id
                or hardware_spec.architecture_id != architecture.architecture_id
                or hardware_spec.workflow_id != workflow.workflow_id
            ):
                raise ValueError("conversation hardware design identity mismatch")
            if (
                hardware_spec.architecture_fingerprint != architecture.fingerprint
                or hardware_spec.workflow_fingerprint != workflow.fingerprint
            ):
                raise ValueError("conversation hardware design source fingerprint mismatch")
        hardware_selection = self.hardware_selection
        if hardware_selection is not None:
            proposal = hardware_selection.proposal
            if (
                plan is None
                or architecture is None
                or workflow is None
                or self.requirement_context is None
                or plan.status is not PlanStatus.APPROVED
                or architecture.status is not ArchitectureDesignStatus.APPROVED
            ):
                raise ValueError("conversation hardware selection requires approved architecture and workflow")
            if (
                proposal.project_id != self.project_id
                or proposal.conversation_id != self.conversation_id
                or proposal.architecture_id != architecture.architecture_id
                or proposal.architecture_fingerprint != architecture.fingerprint
                or proposal.requirements_fingerprint != self.requirement_context.fingerprint
                or workflow.project_id != self.project_id
                or workflow.conversation_id != self.conversation_id
                or workflow.architecture_id != architecture.architecture_id
            ):
                raise ValueError("conversation hardware selection binding mismatch")
            if hardware_selection.status.value == "APPROVED":
                hardware_truth = hardware_selection.hardware_truth
                bom = hardware_selection.bom
                approval = hardware_selection.approval
                if hardware_truth is None or bom is None or approval is None:
                    raise ValueError("conversation approved hardware selection is incomplete")
                if (
                    hardware_truth.project_id != self.project_id
                    or hardware_truth.conversation_id != self.conversation_id
                    or hardware_truth.architecture_id != architecture.architecture_id
                    or hardware_truth.architecture_fingerprint != architecture.fingerprint
                    or hardware_truth.requirements_fingerprint != self.requirement_context.fingerprint
                    or bom.project_id != self.project_id
                    or bom.architecture_fingerprint != architecture.fingerprint
                    or approval.architecture_fingerprint != architecture.fingerprint
                ):
                    raise ValueError("conversation approved hardware truth binding mismatch")
        schematic_planning = self.schematic_planning
        if schematic_planning is not None:
            proposal = schematic_planning.proposal
            if (
                plan is None
                or architecture is None
                or workflow is None
                or self.requirement_context is None
                or hardware_selection is None
                or plan.status is not PlanStatus.APPROVED
                or architecture.status is not ArchitectureDesignStatus.APPROVED
                or hardware_selection.status.value != "APPROVED"
            ):
                raise ValueError("conversation schematic planning requires approved upstream state")
            if (
                proposal.project_id != self.project_id
                or proposal.conversation_id != self.conversation_id
                or proposal.architecture_id != architecture.architecture_id
                or proposal.architecture_fingerprint != architecture.fingerprint
                or proposal.requirements_fingerprint != self.requirement_context.fingerprint
                or proposal.hardware_truth_id != hardware_selection.hardware_truth.truth_id
                or proposal.hardware_truth_fingerprint != hardware_selection.hardware_truth.fingerprint
                or proposal.bom_id != hardware_selection.bom.bom_id
                or proposal.bom_fingerprint != hardware_selection.bom.fingerprint
                or workflow.project_id != self.project_id
                or workflow.conversation_id != self.conversation_id
                or workflow.architecture_id != architecture.architecture_id
            ):
                raise ValueError("conversation schematic planning binding mismatch")
            if schematic_planning.status is SchematicPlanningStatus.APPROVED:
                approved_plan = schematic_planning.approved_plan
                approval = schematic_planning.approval
                truth = self.design_truth
                if approved_plan is None or approval is None or truth is None:
                    raise ValueError("conversation approved schematic planning is incomplete")
                if (
                    truth.project_id != self.project_id
                    or truth.architecture_id != architecture.architecture_id
                    or approved_plan.fingerprint != proposal.fingerprint
                    or approval.plan_fingerprint != proposal.fingerprint
                    or not any(
                        item.source_id == approved_plan.plan_id
                        and item.source_fingerprint == approved_plan.fingerprint
                        for item in truth.provenance
                    )
                ):
                    raise ValueError("conversation schematic DesignTruth binding mismatch")
        firmware_spec = self.firmware_spec
        if firmware_spec is not None:
            if hardware_spec is None or workflow is None:
                raise ValueError("conversation firmware design requires hardware design and workflow")
            if (
                firmware_spec.project_id != self.project_id
                or firmware_spec.conversation_id != self.conversation_id
                or firmware_spec.hardware_design_id != hardware_spec.hardware_spec_id
                or firmware_spec.architecture_id != architecture.architecture_id
                or firmware_spec.workflow_id != workflow.workflow_id
            ):
                raise ValueError("conversation firmware design identity mismatch")
            if (
                firmware_spec.hardware_design_fingerprint != hardware_spec.fingerprint
                or firmware_spec.workflow_fingerprint != workflow.fingerprint
            ):
                raise ValueError("conversation firmware design source fingerprint mismatch")
        simulation_spec = self.simulation_spec
        if simulation_spec is not None:
            if firmware_spec is None or hardware_spec is None or workflow is None:
                raise ValueError("conversation simulation design requires hardware, firmware and workflow")
            if (
                simulation_spec.project_id != self.project_id
                or simulation_spec.conversation_id != self.conversation_id
                or simulation_spec.hardware_design_id != hardware_spec.hardware_spec_id
                or simulation_spec.firmware_spec_id != firmware_spec.firmware_spec_id
                or simulation_spec.workflow_id != workflow.workflow_id
            ):
                raise ValueError("conversation simulation design identity mismatch")
            if (
                simulation_spec.hardware_design_fingerprint != hardware_spec.fingerprint
                or simulation_spec.firmware_spec_fingerprint != firmware_spec.fingerprint
                or simulation_spec.workflow_fingerprint != workflow.fingerprint
            ):
                raise ValueError("conversation simulation design source fingerprint mismatch")
        verification_spec = self.verification_spec
        if verification_spec is not None:
            if simulation_spec is None or firmware_spec is None or hardware_spec is None or workflow is None:
                raise ValueError("conversation verification plan requires hardware, firmware, simulation and workflow")
            if (
                verification_spec.project_id != self.project_id
                or verification_spec.conversation_id != self.conversation_id
                or verification_spec.hardware_design_id != hardware_spec.hardware_spec_id
                or verification_spec.firmware_spec_id != firmware_spec.firmware_spec_id
                or verification_spec.simulation_spec_id != simulation_spec.simulation_spec_id
                or verification_spec.workflow_id != workflow.workflow_id
            ):
                raise ValueError("conversation verification plan identity mismatch")
            if (
                verification_spec.hardware_design_fingerprint != hardware_spec.fingerprint
                or verification_spec.firmware_spec_fingerprint != firmware_spec.fingerprint
                or verification_spec.simulation_spec_fingerprint != simulation_spec.fingerprint
                or verification_spec.workflow_fingerprint != workflow.fingerprint
            ):
                raise ValueError("conversation verification plan source fingerprint mismatch")
        implementation_spec = self.implementation_spec
        if implementation_spec is not None:
            if verification_spec is None or simulation_spec is None or firmware_spec is None or hardware_spec is None or workflow is None:
                raise ValueError("conversation implementation plan requires hardware, firmware, simulation, verification and workflow")
            if (
                implementation_spec.project_id != self.project_id
                or implementation_spec.conversation_id != self.conversation_id
                or implementation_spec.hardware_design_id != hardware_spec.hardware_spec_id
                or implementation_spec.firmware_spec_id != firmware_spec.firmware_spec_id
                or implementation_spec.simulation_spec_id != simulation_spec.simulation_spec_id
                or implementation_spec.verification_id != verification_spec.verification_id
                or implementation_spec.workflow_id != workflow.workflow_id
            ):
                raise ValueError("conversation implementation plan identity mismatch")
            if (
                implementation_spec.hardware_design_fingerprint != hardware_spec.fingerprint
                or implementation_spec.firmware_spec_fingerprint != firmware_spec.fingerprint
                or implementation_spec.simulation_spec_fingerprint != simulation_spec.fingerprint
                or implementation_spec.verification_fingerprint != verification_spec.fingerprint
                or implementation_spec.workflow_fingerprint != workflow.fingerprint
            ):
                raise ValueError("conversation implementation plan source fingerprint mismatch")
        build_configuration = self.build_configuration
        if build_configuration is not None:
            if firmware_spec is None or implementation_spec is None or truth is None:
                raise ValueError("conversation build configuration requires firmware, implementation and DesignTruth")
            target_values = (
                firmware_spec.mcu_exact_part,
                firmware_spec.build_system,
                firmware_spec.toolchain_family,
            )
            if any(value is None for value in target_values):
                raise ValueError("conversation build configuration requires canonical firmware target")
            if (
                build_configuration.target_kind != firmware_spec.mcu_exact_part
                or build_configuration.build_system != firmware_spec.build_system
                or build_configuration.toolchain_id != firmware_spec.toolchain_family
            ):
                raise ValueError("conversation build configuration target mismatch")
            if firmware_spec.design_truth_id != truth.truth_id or firmware_spec.design_truth_fingerprint != truth.fingerprint:
                raise ValueError("conversation firmware DesignTruth binding mismatch")
            if implementation_spec.design_truth_id != truth.truth_id or implementation_spec.design_truth_fingerprint != truth.fingerprint:
                raise ValueError("conversation implementation DesignTruth binding mismatch")
            if implementation_spec.build_configuration_id is None or implementation_spec.build_configuration_fingerprint is None:
                raise ValueError("conversation implementation BuildConfiguration binding is missing")
            if implementation_spec.build_configuration_fingerprint not in _firmware_build_configuration_fingerprints(build_configuration):
                raise ValueError("conversation implementation BuildConfiguration fingerprint mismatch")
        capability_evaluation = self.capability_evaluation
        execution_intents = self.execution_intents
        execution_authorization = self.execution_authorization
        if implementation_spec is None:
            if (
                capability_evaluation is not None
                or execution_intents
                or execution_authorization is not None
            ):
                raise ValueError("conversation capability state requires implementation plan")
        elif capability_evaluation is None:
            if execution_intents or execution_authorization is not None:
                raise ValueError("conversation execution intents require capability evaluation")
        else:
            if capability_evaluation.project_id != self.project_id:
                raise ValueError("conversation capability project mismatch")
            if (
                capability_evaluation.implementation_id
                != implementation_spec.implementation_id
                or capability_evaluation.implementation_fingerprint
                != implementation_spec.fingerprint
            ):
                raise ValueError("conversation capability implementation binding mismatch")
            expected_evaluation = evaluate_capabilities(implementation_spec)
            expected_intents = tuple(
                generate_execution_intent(task) for task in implementation_spec.tasks
            )
            if capability_evaluation != expected_evaluation:
                raise ValueError("conversation capability evaluation binding mismatch")
            if execution_intents != expected_intents:
                raise ValueError("conversation execution intent binding mismatch")
            if execution_authorization is not None:
                expected_requests = generate_execution_requests(
                    capability_evaluation,
                    execution_intents,
                    conversation_id=self.conversation_id,
                    requested_by=self.requester,
                )
                if tuple(item.request_id for item in execution_authorization.requests) != tuple(
                    item.request_id for item in expected_requests
                ):
                    raise ValueError("conversation execution authorization request mismatch")
                validate_execution_authorization(
                    capability_evaluation, execution_intents, execution_authorization
                )
            elif "execution_authorization" in self.model_fields_set:
                raise ValueError("conversation capability state requires execution authorization")
        generation = self.firmware_source_generation
        if generation is not None:
            if firmware_spec is None or implementation_spec is None or execution_authorization is None:
                raise ValueError("firmware source generation requires complete upstream state")
            request = generation.request
            task = next(
                (item for item in implementation_spec.tasks if item.task_id == request.implementation_task_id),
                None,
            )
            module = next(
                (item for item in firmware_spec.modules if item.module_id == request.firmware_module_id),
                None,
            )
            intent = next(
                (item for item in execution_intents if item.intent_id == request.execution_intent_id),
                None,
            )
            auth_request = next(
                (item for item in execution_authorization.requests if item.request_id == request.authorization_request_id),
                None,
            )
            auth_decision = next(
                (item for item in execution_authorization.decisions if item.decision_id == request.authorization_decision_id),
                None,
            )
            if (
                generation.project_id != self.project_id
                or generation.conversation_id != self.conversation_id
                or request.tenant_id != self.tenant_id
                or request.requester != self.requester
                or request.firmware_spec_id != firmware_spec.firmware_spec_id
                or request.firmware_spec_fingerprint != firmware_spec.fingerprint
                or request.implementation_id != implementation_spec.implementation_id
                or request.implementation_revision != implementation_spec.design_revision
                or request.implementation_fingerprint != implementation_spec.fingerprint
                or task is None
                or task.fingerprint != request.implementation_task_fingerprint
                or module is None
                or module.fingerprint != request.firmware_module_fingerprint
                or intent is None
                or intent.fingerprint != request.execution_intent_fingerprint
                or request.authorization_id != execution_authorization.authorization_id
                or request.authorization_fingerprint != execution_authorization.fingerprint
                or auth_request is None
                or auth_request.fingerprint != request.authorization_request_fingerprint
                or auth_decision is None
                or auth_decision.fingerprint != request.authorization_decision_fingerprint
            ):
                raise ValueError("conversation firmware source generation binding mismatch")
        schematic = self.schematic_production
        if schematic is not None:
            request = schematic.request
            truth = self.design_truth
            hardware = self.hardware_design_spec
            implementation = self.implementation_spec
            auth = self.execution_authorization
            task = None if implementation is None else next((item for item in implementation.tasks if item.task_id == request.implementation_task_id), None)
            auth_request = None if auth is None else next((item for item in auth.requests if item.request_id == request.authorization_request_id), None)
            auth_decision = None if auth is None or auth_request is None else next((item for item in auth.decisions if item.request_id == auth_request.request_id), None)
            stale = schematic.status.value == "STALE"
            if stale:
                receipt = schematic.generation_receipt
                current_planning = self.schematic_planning
                current_plan = (
                    None
                    if current_planning is None or current_planning.status.value != "APPROVED"
                    else current_planning.approved_plan
                )
                if (
                    receipt is None
                    or receipt.status.value != "STALE"
                    or schematic.project_id != self.project_id
                    or schematic.conversation_id != self.conversation_id
                    or request.project_id != self.project_id
                    or request.conversation_id != self.conversation_id
                    or request.requester != self.requester
                    or (
                        current_plan is not None
                        and current_plan.fingerprint == receipt.schematic_plan_fingerprint
                    )
                ):
                    raise ValueError("conversation stale schematic production binding mismatch")
            elif (
                truth is None or truth.origin is DesignTruthOrigin.REVERSE
                or hardware is None or implementation is None or auth is None
                or schematic.project_id != self.project_id or schematic.conversation_id != self.conversation_id
                or request.project_id != self.project_id or request.conversation_id != self.conversation_id
                or request.requester != self.requester
                or request.design_truth_id != truth.truth_id or request.design_truth_fingerprint != truth.fingerprint
                or request.hardware_design_id != hardware.hardware_spec_id or request.hardware_design_fingerprint != hardware.fingerprint
                or request.implementation_id != implementation.implementation_id or request.implementation_fingerprint != implementation.fingerprint
                or request.hardware_design_id != implementation.hardware_design_id
                or task is None or task.implementation_id != implementation.implementation_id
                or ("SCHEMATIC" not in task.expected_artifacts and not any(item.artifact_type.value == "SCHEMATIC" and item.artifact_id in task.expected_artifacts for item in implementation.expected_artifacts))
                or request.authorization_fingerprint != auth.fingerprint
                or request.authorization_id != auth.authorization_id
                or auth_request is None or auth_request.capability_id != "ARTIFACT_CREATION"
                or auth_request.status.value != "AUTHORIZED"
                or auth_decision is None or auth_decision.request_id != auth_request.request_id
                or auth_decision.decision.value != "APPROVED"
                or schematic.proposal.request_id != request.request_id
                or schematic.proposal.target_relative_path != request.target_relative_path
                or schematic.proposal.design_truth_fingerprint != request.design_truth_fingerprint
            ):
                raise ValueError("conversation schematic production binding mismatch")
        pcb_request = self.pcb_production_request
        pcb = self.pcb_production
        if pcb_request is not None:
            if (
                pcb_request.project_id != self.project_id
                or pcb_request.conversation_id != self.conversation_id
                or pcb_request.requester != self.requester
            ):
                raise ValueError("conversation PCB production request binding mismatch")
        if pcb is not None:
            if (
                pcb.project_id != self.project_id
                or pcb.conversation_id != self.conversation_id
                or pcb.request.request_id != (None if pcb_request is None else pcb_request.request_id)
                or pcb_request is None
                or pcb.request.fingerprint != pcb_request.fingerprint
                or pcb.request.requester != self.requester
            ):
                raise ValueError("conversation PCB production binding mismatch")
        pcb_state = self.pcb_engineering_state
        pcb_constraints = self.pcb_constraint_set
        pcb_plan = self.pcb_verification_plan
        if pcb_state is not None and pcb_state.project_id != self.project_id:
            raise ValueError("conversation PCB engineering state project mismatch")
        source_binding = None if pcb_state is None else pcb_state.intent_source_binding
        pcb_generation = None if pcb is None else pcb.generation_receipt
        if (
            pcb_state is not None
            and pcb_generation is not None
            and pcb_generation.artifact_fingerprint != pcb_state.pcb_artifact_fingerprint
        ):
            raise ValueError("conversation PCB engineering state artifact binding mismatch")
        if source_binding is not None:
            if (
                source_binding.project_id != self.project_id
                or source_binding.conversation_id != self.conversation_id
            ):
                raise ValueError("conversation PCB source binding identity mismatch")
            if source_binding.status.value == "BOUND" and source_binding.tenant_id != self.tenant_id:
                raise ValueError("conversation PCB source binding artifact mismatch")
            if (
                source_binding.status.value == "BOUND"
                and pcb is not None
                and source_binding.schematic_artifact_fingerprint
                != pcb.request.schematic_artifact_fingerprint
            ):
                raise ValueError("conversation PCB source binding artifact mismatch")
        if pcb_constraints is not None:
            if pcb_state is None:
                raise ValueError("conversation PCB constraint set requires engineering state")
            if (
                pcb_constraints.project_id != self.project_id
                or pcb_constraints.revision != pcb_state.revision
                or pcb_constraints.state_fingerprint != pcb_state.fingerprint
            ):
                raise ValueError("conversation PCB constraint set binding mismatch")
            if source_binding is not None and pcb_constraints.intent_source_binding != source_binding:
                raise ValueError("conversation PCB constraint source binding mismatch")
        if pcb_plan is not None:
            if pcb_state is None or pcb_constraints is None:
                raise ValueError("conversation PCB verification plan requires state and constraints")
            if (
                pcb_plan.project_id != self.project_id
                or pcb_plan.revision != pcb_state.revision
                or pcb_plan.pcb_state_fingerprint != pcb_state.fingerprint
                or pcb_plan.constraint_set_fingerprint != pcb_constraints.fingerprint
            ):
                raise ValueError("conversation PCB verification plan binding mismatch")
        for evidence in self.pcb_engineering_evidence:
            if evidence.project_id != self.project_id:
                raise ValueError("conversation engineering evidence project mismatch")
        manufacturer_profile = self.pcb_manufacturer_profile
        if manufacturer_profile is not None and manufacturer_profile.project_id != self.project_id:
            raise ValueError("conversation manufacturer profile project mismatch")
        manufacturing_measurements = self.pcb_manufacturing_measurements
        if manufacturing_measurements is not None:
            if pcb_state is None:
                raise ValueError("PCB manufacturing measurements require current PCB state")
            if (
                manufacturing_measurements.project_id != self.project_id
                or manufacturing_measurements.artifact_fingerprint != pcb_state.source_artifact_fingerprint
            ):
                raise ValueError("conversation PCB manufacturing measurement binding mismatch")
        engineering_summary = self.pcb_engineering_verification_summary
        if engineering_summary is not None:
            if pcb_state is None or pcb_plan is None:
                raise ValueError("engineering verification summary requires PCB state and plan")
            if (
                engineering_summary.project_id != self.project_id
                or engineering_summary.pcb_state_fingerprint != pcb_state.fingerprint
                or engineering_summary.verification_plan_fingerprint != pcb_plan.fingerprint
            ):
                raise ValueError("conversation engineering verification summary binding mismatch")
        ready_for_fab = self.pcb_ready_for_fab
        if ready_for_fab is not None:
            if engineering_summary is None or pcb_state is None:
                raise ValueError("READY_FOR_FAB gate requires engineering verification summary")
            if (
                ready_for_fab.project_id != self.project_id
                or ready_for_fab.summary_fingerprint != engineering_summary.fingerprint
                or ready_for_fab.artifact_fingerprint != pcb_state.source_artifact_fingerprint
            ):
                raise ValueError("conversation READY_FOR_FAB binding mismatch")
        approval = self.pcb_ready_for_fab_approval
        if approval is not None:
            if engineering_summary is None or ready_for_fab is None or pcb_state is None:
                raise ValueError("READY_FOR_FAB approval requires current summary, gate, and PCB state")
            if (
                (approval.project_id is not None and approval.project_id != self.project_id)
                or (approval.conversation_id is not None and approval.conversation_id != self.conversation_id)
                or approval.summary_fingerprint != engineering_summary.fingerprint
                or approval.artifact_fingerprint != pcb_state.source_artifact_fingerprint
                or approval.blocking_issue_count != len(engineering_summary.blocking_issues)
                or approval.blocking_unknown_count != len(engineering_summary.blocking_unknowns)
            ):
                raise ValueError("conversation READY_FOR_FAB approval binding mismatch")
            if approval.dfm_fingerprint is not None:
                dfm = next(
                    (
                        item
                        for item in (*engineering_summary.required_results, *engineering_summary.optional_results)
                        if item.verifier in {"DFM", "DFMVerifier"}
                    ),
                    None,
                )
                if dfm is None or approval.dfm_fingerprint != dfm.fingerprint:
                    raise ValueError("conversation READY_FOR_FAB DFM binding mismatch")
            if approval.ready_for_fab_target_fingerprint is not None:
                target = build_ready_for_fab_approval_target(
                    engineering_summary,
                    pcb_state.source_artifact_fingerprint,
                )
                if approval.ready_for_fab_target_fingerprint != target.fingerprint:
                    raise ValueError("conversation READY_FOR_FAB target binding mismatch")
        decoupling_result = self.pcb_decoupling_verification
        if decoupling_result is not None:
            if decoupling_result.project_id != self.project_id or decoupling_result.verifier != "DECOUPLING" or decoupling_result.scope != "PCB":
                raise ValueError("conversation PCB decoupling verification identity mismatch")
            if not decoupling_result.stale:
                if pcb_state is None or pcb_constraints is None or pcb_plan is None:
                    raise ValueError("current PCB decoupling verification requires G7-A inputs")
                required = {pcb_state.fingerprint, pcb_constraints.fingerprint, pcb_plan.fingerprint}
                if not required.issubset(set(decoupling_result.compared_fingerprints)):
                    raise ValueError("conversation PCB decoupling verification binding mismatch")
            measurement_ids = {item.measurement_id for item in self.pcb_decoupling_measurements}
            if not set(decoupling_result.measurement_refs).issubset(measurement_ids):
                raise ValueError("conversation PCB decoupling measurement binding mismatch")
        return_path_result = self.pcb_return_path_verification
        if return_path_result is not None:
            if return_path_result.project_id != self.project_id or return_path_result.verifier != "RETURN_PATH" or return_path_result.scope != "PCB":
                raise ValueError("conversation PCB return-path verification identity mismatch")
            if not return_path_result.stale:
                if pcb_state is None or pcb_constraints is None or pcb_plan is None:
                    raise ValueError("current PCB return-path verification requires G7-A inputs")
                required = {pcb_state.fingerprint, pcb_constraints.fingerprint, pcb_plan.fingerprint}
                if not required.issubset(set(return_path_result.compared_fingerprints)):
                    raise ValueError("conversation PCB return-path verification binding mismatch")
            measurement_ids = {item.measurement_id for item in self.pcb_return_path_measurements}
            if not set(return_path_result.measurement_refs).issubset(measurement_ids):
                raise ValueError("conversation PCB return-path measurement binding mismatch")
        power_result = self.pcb_power_loop_verification
        if power_result is not None:
            if power_result.project_id != self.project_id or power_result.verifier != "POWER_LOOP" or power_result.scope != "PCB":
                raise ValueError("conversation PCB power-loop verification identity mismatch")
            if not power_result.stale:
                if pcb_state is None or pcb_constraints is None or pcb_plan is None:
                    raise ValueError("current PCB power-loop verification requires G7-A inputs")
                required = {pcb_state.fingerprint, pcb_constraints.fingerprint, pcb_plan.fingerprint}
                if not required.issubset(set(power_result.compared_fingerprints)):
                    raise ValueError("conversation PCB power-loop verification binding mismatch")
            measurement_ids = {item.measurement_id for item in self.pcb_power_loop_measurements}
            if not set(power_result.measurement_refs).issubset(measurement_ids):
                raise ValueError("conversation PCB power-loop measurement binding mismatch")
        differential_result = self.pcb_differential_pair_verification
        if differential_result is not None:
            if differential_result.project_id != self.project_id or differential_result.verifier != "DIFFERENTIAL_PAIR" or differential_result.scope != "PCB":
                raise ValueError("conversation PCB differential-pair verification identity mismatch")
            if not differential_result.stale:
                if pcb_state is None or pcb_constraints is None or pcb_plan is None:
                    raise ValueError("current PCB differential-pair verification requires G7-A inputs")
                required = {pcb_state.fingerprint, pcb_constraints.fingerprint, pcb_plan.fingerprint}
                if not required.issubset(set(differential_result.compared_fingerprints)):
                    raise ValueError("conversation PCB differential-pair verification binding mismatch")
            measurement_ids = {item.measurement_id for item in self.pcb_differential_pair_measurements}
            if not set(differential_result.measurement_refs).issubset(measurement_ids):
                raise ValueError("conversation PCB differential-pair measurement binding mismatch")
            if not set(self.pcb_differential_pair_dependency_fingerprints).issubset(set(differential_result.compared_fingerprints)):
                raise ValueError("conversation PCB differential-pair dependency binding mismatch")
        firmware_production = self.firmware_production
        if firmware_production is not None:
            if (
                firmware_production.project_id != self.project_id
                or firmware_production.conversation_id != self.conversation_id
            ):
                raise ValueError("conversation firmware production identity mismatch")
            if firmware_production.status.value != "STALE":
                if self.firmware_spec is None or self.design_truth is None or self.implementation_spec is None or self.build_configuration is None:
                    raise ValueError("conversation firmware production requires complete upstream state")
                if (
                    firmware_production.firmware_spec_id != self.firmware_spec.firmware_spec_id
                    or firmware_production.firmware_plan_fingerprint != self.firmware_spec.fingerprint
                    or firmware_production.design_truth_id != self.design_truth.truth_id
                    or firmware_production.design_truth_fingerprint != self.design_truth.fingerprint
                    or firmware_production.implementation_id != self.implementation_spec.implementation_id
                    or firmware_production.implementation_fingerprint != self.implementation_spec.fingerprint
                    or firmware_production.build_configuration_fingerprint not in _firmware_build_configuration_fingerprints(self.build_configuration)
                ):
                    raise ValueError("conversation firmware production upstream binding mismatch")
        if self.engineering_evidence_bundle is not None:
            bundle = self.engineering_evidence_bundle
            if bundle.tenant_id != self.tenant_id or bundle.project_id != self.project_id or bundle.conversation_id != self.conversation_id:
                raise ValueError("conversation engineering evidence identity mismatch")
        if self.artifact_intelligence_snapshot is not None:
            snapshot = self.artifact_intelligence_snapshot
            if snapshot.tenant_id != self.tenant_id or snapshot.project_id != self.project_id or snapshot.conversation_id != self.conversation_id:
                raise ValueError("conversation artifact snapshot identity mismatch")
            if snapshot.engineering_evidence_bundle is not None and self.engineering_evidence_bundle is not None and snapshot.engineering_evidence_bundle.bundle_fingerprint != self.engineering_evidence_bundle.bundle_fingerprint:
                raise ValueError("conversation artifact evidence snapshot mismatch")
        if (
            self.virtual_system_verification is not None
            and (
                self.virtual_system_verification.plan.project_id != self.project_id
                or self.virtual_system_verification.plan.conversation_id != self.conversation_id
            )
        ):
            raise ValueError("conversation G9 verification identity mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        candidates: list[str] = []

        # ``blocking_unknown_categories`` was added to the nested PCB closure
        # summary after existing conversations had already been persisted.  The
        # outer conversation fingerprint must still accept that historical
        # serialization while new writes keep the complete current payload.
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        summary = payload.get("pcb_engineering_verification_summary")
        if isinstance(summary, dict) and "blocking_unknown_categories" in summary:
            summary.pop("blocking_unknown_categories", None)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        def compatibility_exclude(*fields: str) -> set[str]:
            excluded = {"fingerprint", *fields}
            if "hardware_selection" not in self.model_fields_set:
                excluded.add("hardware_selection")
            if "schematic_planning" not in self.model_fields_set:
                excluded.add("schematic_planning")
            if "firmware_spec" not in self.model_fields_set:
                excluded.add("firmware_spec")
            if "simulation_spec" not in self.model_fields_set:
                excluded.add("simulation_spec")
            if "verification_spec" not in self.model_fields_set:
                excluded.add("verification_spec")
            if "virtual_system_verification" not in self.model_fields_set:
                excluded.add("virtual_system_verification")
            if "implementation_spec" not in self.model_fields_set:
                excluded.add("implementation_spec")
            if "capability_evaluation" not in self.model_fields_set:
                excluded.add("capability_evaluation")
            if "execution_intents" not in self.model_fields_set:
                excluded.add("execution_intents")
            if "execution_authorization" not in self.model_fields_set:
                excluded.add("execution_authorization")
            if "firmware_source_generation" not in self.model_fields_set:
                excluded.add("firmware_source_generation")
            if "firmware_production" not in self.model_fields_set:
                excluded.add("firmware_production")
            if "schematic_production" not in self.model_fields_set:
                excluded.add("schematic_production")
            for field in (
                "pcb_engineering_state", "pcb_constraint_set", "pcb_verification_plan",
                "pcb_decoupling_verification", "pcb_decoupling_measurements",
                "pcb_return_path_verification", "pcb_return_path_measurements",
                "pcb_return_path_relationships", "pcb_return_path_discontinuities",
                "pcb_return_path_transitions",
                "pcb_power_loop_verification", "pcb_power_stage_relationships",
                "pcb_power_loop_relationships", "pcb_power_loop_measurements",
                "pcb_power_switch_nodes", "pcb_power_feedback_relationships",
                "pcb_differential_pair_verification", "pcb_differential_pair_relationships",
                "pcb_differential_pair_measurements", "pcb_differential_pair_dependency_fingerprints",
                "pcb_engineering_evidence", "pcb_manufacturer_profile", "pcb_manufacturing_measurements", "pcb_engineering_verification_summary", "pcb_ready_for_fab",
                "pcb_ready_for_fab_approval",
            ):
                if field not in self.model_fields_set:
                    excluded.add(field)
            if "research_snapshot" not in self.model_fields_set:
                excluded.add("research_snapshot")
            if "requirement_suggestion_decisions" not in self.model_fields_set:
                excluded.add("requirement_suggestion_decisions")
            if "reference_project_decisions" not in self.model_fields_set:
                excluded.add("reference_project_decisions")
            if "feature_decisions" not in self.model_fields_set:
                excluded.add("feature_decisions")
            if "requirement_change_proposal" not in self.model_fields_set:
                excluded.add("requirement_change_proposal")
            if "requirement_final_confirmation" not in self.model_fields_set:
                excluded.add("requirement_final_confirmation")
            if "research_intent_refinement" not in self.model_fields_set:
                excluded.add("research_intent_refinement")
            return excluded

        def legacy_payload(exclude: set[str], *, omit_truth_status: bool = False) -> dict[str, object]:
            legacy_exclude = {"fingerprint", "engineering_knowledge_model", *exclude}
            if "teaching_context" not in self.model_fields_set:
                legacy_exclude.add("teaching_context")
            if "hardware_design_spec" not in self.model_fields_set:
                legacy_exclude.add("hardware_design_spec")
            if "hardware_selection" not in self.model_fields_set:
                legacy_exclude.add("hardware_selection")
            if "schematic_planning" not in self.model_fields_set:
                legacy_exclude.add("schematic_planning")
            if "firmware_spec" not in self.model_fields_set:
                legacy_exclude.add("firmware_spec")
            if "simulation_spec" not in self.model_fields_set:
                legacy_exclude.add("simulation_spec")
            if "verification_spec" not in self.model_fields_set:
                legacy_exclude.add("verification_spec")
            if "virtual_system_verification" not in self.model_fields_set:
                legacy_exclude.add("virtual_system_verification")
            if "implementation_spec" not in self.model_fields_set:
                legacy_exclude.add("implementation_spec")
            if "capability_evaluation" not in self.model_fields_set:
                legacy_exclude.add("capability_evaluation")
            if "execution_intents" not in self.model_fields_set:
                legacy_exclude.add("execution_intents")
            if "execution_authorization" not in self.model_fields_set:
                legacy_exclude.add("execution_authorization")
            if "firmware_source_generation" not in self.model_fields_set:
                legacy_exclude.add("firmware_source_generation")
            if "firmware_production" not in self.model_fields_set:
                legacy_exclude.add("firmware_production")
            if "schematic_production" not in self.model_fields_set:
                legacy_exclude.add("schematic_production")
            for field in (
                "pcb_differential_pair_verification", "pcb_differential_pair_relationships",
                "pcb_differential_pair_measurements", "pcb_differential_pair_dependency_fingerprints",
                "pcb_engineering_evidence", "pcb_manufacturer_profile", "pcb_manufacturing_measurements", "pcb_engineering_verification_summary", "pcb_ready_for_fab",
                "pcb_ready_for_fab_approval",
            ):
                if field not in self.model_fields_set:
                    legacy_exclude.add(field)
            if "research_snapshot" not in self.model_fields_set:
                legacy_exclude.add("research_snapshot")
            if "requirement_suggestion_decisions" not in self.model_fields_set:
                legacy_exclude.add("requirement_suggestion_decisions")
            if "reference_project_decisions" not in self.model_fields_set:
                legacy_exclude.add("reference_project_decisions")
            if "feature_decisions" not in self.model_fields_set:
                legacy_exclude.add("feature_decisions")
            if "requirement_change_proposal" not in self.model_fields_set:
                legacy_exclude.add("requirement_change_proposal")
            if "requirement_final_confirmation" not in self.model_fields_set:
                legacy_exclude.add("requirement_final_confirmation")
            if "research_intent_refinement" not in self.model_fields_set:
                legacy_exclude.add("research_intent_refinement")
            payload = self.model_dump(
                mode="json",
                exclude=legacy_exclude,
            )
            if omit_truth_status and isinstance(payload.get("design_truth"), dict):
                payload["design_truth"].pop("status", None)
            return payload

        if "verification_spec" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "implementation_spec" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if (
            "capability_evaluation" not in self.model_fields_set
            or "execution_intents" not in self.model_fields_set
        ):
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "execution_authorization" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "firmware_source_generation" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "schematic_production" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("schematic_production")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "simulation_spec" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude()),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "firmware_spec" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("firmware_spec")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())

        if "teaching_context" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("teaching_context")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if "hardware_design_spec" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("hardware_design_spec")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if "engineering_workflow" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("engineering_workflow")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
            if "hardware_design_spec" not in self.model_fields_set:
                encoded = json.dumps(
                    self.model_dump(
                        mode="json",
                        exclude=compatibility_exclude("engineering_workflow", "hardware_design_spec"),
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if "engineering_workflow" not in self.model_fields_set and "teaching_context" not in self.model_fields_set:
            encoded = json.dumps(
                self.model_dump(mode="json", exclude=compatibility_exclude("engineering_workflow", "teaching_context")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
            if "hardware_design_spec" not in self.model_fields_set:
                encoded = json.dumps(
                    self.model_dump(
                        mode="json",
                        exclude=compatibility_exclude("engineering_workflow", "teaching_context", "hardware_design_spec"),
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if "design_truth" not in self.model_fields_set:
            encoded = json.dumps(
                legacy_payload({"design_truth"}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if not any(value is not None for value in (self.artifact_intelligence_snapshot, self.engineering_evidence_bundle)):
            encoded = json.dumps(
                legacy_payload({"engineering_session_mode", "artifact_intelligence_snapshot", "engineering_evidence_bundle"}),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if self.design_truth is not None and self.engineering_knowledge_model is None:
            encoded = json.dumps(
                legacy_payload(set(), omit_truth_status=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if any(
            value is not None
            for value in (
                self.engineering_plan,
                self.engineering_plan_review,
                self.architecture_design,
                self.architecture_review,
            )
        ):
            return tuple(candidates)
        encoded = json.dumps(
            legacy_payload({"design_truth", "engineering_plan", "engineering_plan_review", "architecture_design", "architecture_review"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        return tuple(candidates)


class ConversationSummary(FingerprintedChatContract):
    conversation_id: str
    project_id: str
    title: str
    turn_count: int
    created_at: datetime
    updated_at: datetime

    @field_validator("conversation_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return _visible_text(value, field="conversation title", maximum=64)

    @field_validator("turn_count", mode="before")
    @classmethod
    def _count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("turn count is invalid")
        return value

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)


class ConversationPage(ChatContract):
    items: tuple[ConversationSummary, ...]
    next_cursor: str | None = None

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: object) -> tuple[ConversationSummary, ...]:
        return tuple(
            _exact(item, ConversationSummary, field="conversation summary")
            for item in _tuple(value, field="items")
        )


class ConversationSessionView(ChatContract):
    conversation: ConversationDetail
    session_id: str
    chat_available: bool

    @field_validator("conversation", mode="before")
    @classmethod
    def _conversation(cls, value: object) -> ConversationDetail:
        return _exact(value, ConversationDetail, field="conversation")

    @field_validator("session_id", mode="before")
    @classmethod
    def _session(cls, value: object) -> str:
        return identifier(value, field="session_id")


def circuit_source_fingerprint(detail: ConversationDetail, turn_id: str) -> str:
    """Bind a Circuit proposal to its selected turn and three design authorities."""
    turn = next((item for item in detail.turns if item.turn_id == turn_id), None)
    material = {
        "turn": None if turn is None else {
            "turn_id": turn.turn_id, "request_id": turn.request_id,
            "route": turn.route, "status": turn.status.value, "question": turn.question,
        },
        "requirement": None if detail.requirement_context is None else detail.requirement_context.fingerprint,
        "architecture": None if detail.architecture_design is None else detail.architecture_design.fingerprint,
        "design_truth": None if detail.design_truth is None else detail.design_truth.fingerprint,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class PredesignSourceSnapshot(RuntimeContract):
    """Selected, approved Catalog sources; no artifact or ERC assertion."""

    project_id: str
    conversation_id: str
    turn_id: str
    turn_fingerprint: str
    requirement_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    plan_review_fingerprint: str
    architecture_id: str
    architecture_fingerprint: str
    architecture_review_fingerprint: str
    hardware_selection_approval_id: str
    hardware_selection_approval_fingerprint: str
    bom_fingerprint: str
    hardware_truth_fingerprint: str
    confirmed_hardware_decisions: tuple[tuple[str, str], ...] = ()

    _ids = field_validator(
        "project_id", "conversation_id", "turn_id", "plan_id", "architecture_id",
        "hardware_selection_approval_id",
    )(lambda value, info: validate_identifier(value, field=info.field_name))
    _fps = field_validator(
        "turn_fingerprint", "requirement_fingerprint", "plan_fingerprint",
        "plan_review_fingerprint", "architecture_fingerprint",
        "architecture_review_fingerprint", "hardware_selection_approval_fingerprint",
        "bom_fingerprint", "hardware_truth_fingerprint",
    )(lambda value, info: validate_fingerprint(value, field=info.field_name))

    @field_validator("confirmed_hardware_decisions")
    @classmethod
    def _decisions(cls, values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        normalized = tuple(
            (validate_identifier(identity, field="hardware_decision_id"), validate_fingerprint(fingerprint))
            for identity, fingerprint in values
        )
        if len({identity for identity, _ in normalized}) != len(normalized):
            raise ValueError("duplicate hardware decision identity")
        return normalized


def predesign_source_snapshot(detail: ConversationDetail, turn_id: str) -> PredesignSourceSnapshot:
    """Re-derive the exact source selection from one Owner-authorized conversation."""
    requirement = detail.requirement_context
    plan, plan_review = detail.engineering_plan, detail.engineering_plan_review
    architecture, architecture_review = detail.architecture_design, detail.architecture_review
    selection = detail.hardware_selection
    turn = next((item for item in detail.turns if item.turn_id == turn_id), None)
    if (turn is None or turn.status.value != "COMPLETED" or turn.route != "GREENFIELD_DESIGN"):
        raise ValueError("completed design turn is required")
    if (requirement is None or requirement.project_id != detail.project_id
            or requirement.conversation_id != detail.conversation_id
            or requirement.effective_status.value != "CONFIRMED"
            or requirement.open_questions or requirement.conflicts):
        raise ValueError("confirmed requirements are required")
    if (plan is None or plan_review is None or plan.project_id != detail.project_id
            or plan_review.project_id != detail.project_id or plan.status.value != "APPROVED"
            or plan_review.status.value != "APPROVED" or plan_review.plan_id != plan.plan_id
            or plan_review.proposal_fingerprint != plan.content_fingerprint):
        raise ValueError("approved engineering plan and review are required")
    if (architecture is None or architecture_review is None
            or architecture.project_id != detail.project_id
            or architecture.plan_id != plan.plan_id
            or architecture.status.value != "APPROVED"
            or architecture_review.status.value != "APPROVED"
            or architecture_review.project_id != detail.project_id
            or architecture_review.plan_id != plan.plan_id
            or architecture_review.architecture_id != architecture.architecture_id
            or architecture_review.proposal_fingerprint != architecture.content_fingerprint
            or any(finding.status.value == "OPEN" for finding in architecture_review.findings)):
        raise ValueError("approved architecture and review are required")
    if selection is None or selection.status.value != "APPROVED":
        raise ValueError("approved hardware selection is required")
    approval, bom, truth = selection.approval, selection.bom, selection.hardware_truth
    if (approval is None or bom is None or truth is None
            or approval.project_id != detail.project_id
            or bom.project_id != detail.project_id or truth.project_id != detail.project_id
            or approval.architecture_fingerprint != architecture.fingerprint
            or approval.bom_fingerprint != bom.fingerprint
            or truth.approval_fingerprint != approval.fingerprint
            or bom.architecture_fingerprint != architecture.fingerprint
            or truth.architecture_fingerprint != architecture.fingerprint
            or bom.requirements_fingerprint != requirement.fingerprint
            or truth.requirements_fingerprint != requirement.fingerprint):
        raise ValueError("hardware selection source binding is invalid")
    hardware_spec = detail.hardware_design_spec
    decisions = ()
    if hardware_spec is not None:
        if (hardware_spec.project_id != detail.project_id
                or hardware_spec.architecture_id != architecture.architecture_id
                or hardware_spec.architecture_fingerprint != architecture.fingerprint):
            raise ValueError("hardware decision source binding is invalid")
        decisions = tuple(sorted(
            (item.decision_id, item.fingerprint) for item in hardware_spec.decisions
            if item.status.value == "CONFIRMED"
        ))
    turn_material = {
        "turn_id": turn.turn_id, "request_id": turn.request_id,
        "route": turn.route, "status": turn.status.value, "question": turn.question,
        "answer_text": turn.answer_text, "response_type": turn.response_type.value,
        "artifact_refs": turn.artifact_refs, "evidence": turn.evidence,
        "recommendations": turn.recommendations,
        "context_references": turn.context_references,
    }
    turn_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(turn_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PredesignSourceSnapshot(
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        turn_id=turn_id, turn_fingerprint=turn_fingerprint,
        requirement_fingerprint=requirement.fingerprint,
        plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint,
        plan_review_fingerprint=plan_review.fingerprint,
        architecture_id=architecture.architecture_id,
        architecture_fingerprint=architecture.fingerprint,
        architecture_review_fingerprint=architecture_review.fingerprint,
        hardware_selection_approval_id=approval.approval_id,
        hardware_selection_approval_fingerprint=approval.fingerprint,
        bom_fingerprint=bom.fingerprint, hardware_truth_fingerprint=truth.fingerprint,
        confirmed_hardware_decisions=decisions,
    )


class CircuitProposalCandidate(RuntimeContract):
    """Persisted review candidate; it is never a current Circuit fact."""

    tenant_id: str
    project_id: str
    proposal: ObjectProposal
    evidence_reference: EvidenceReceiptReference
    idempotency_key: str
    request_fingerprint: str
    conversation_id: str
    turn_id: str
    object_id: str
    base_revision: str | None = None
    base_fingerprint: str | None = None
    context_fingerprint: str
    conversation_fingerprint: str
    source_fingerprint: str
    constraints: tuple[str, ...] = ()
    route: str
    created_at: datetime
    status: ProposalStatus = ProposalStatus.CREATED
    decision: ApprovalRecord | None = None

    _ids = field_validator("tenant_id", "project_id", "idempotency_key", "conversation_id", "turn_id", "object_id")(
        lambda value: validate_identifier(value, field="circuit_candidate_identity")
    )
    _fps = field_validator("request_fingerprint", "context_fingerprint", "conversation_fingerprint", "source_fingerprint")(
        lambda value: validate_fingerprint(value)
    )
    _base_fp = field_validator("base_fingerprint")(
        lambda value: None if value is None else validate_fingerprint(value)
    )
    _base_rev = field_validator("base_revision")(
        lambda value: None if value is None else validate_revision(value)
    )
    _created = field_validator("created_at")(validate_utc_datetime)
    _route = field_validator("route")(
        lambda value: validate_identifier(value, field="circuit_candidate_route")
    )
    _constraints = field_validator("constraints")(
        lambda values: tuple(normalize_text(value, field="circuit_constraint") for value in values)
    )

    @model_validator(mode="after")
    def _binding(self) -> CircuitProposalCandidate:
        if (self.base_revision is None) != (self.base_fingerprint is None):
            raise ValueError("circuit base identity is incomplete")
        proposal = self.proposal
        if (
            proposal.project_id != self.project_id
            or proposal.target_object_id != self.object_id
            or proposal.target_object_type is not ObjectType.CIRCUIT_INTENT
            or not isinstance(proposal.proposed_object, CircuitIntentObject)
            or proposal.proposed_object.project_id != self.project_id
            or proposal.proposed_object.object_id != self.object_id
            or proposal.base_revision != self.base_revision
            or proposal.base_fingerprint != self.base_fingerprint
            or proposal.context_fingerprint != self.context_fingerprint
            or proposal.status is not ProposalStatus.CREATED
            or proposal.fingerprint != canonical_fingerprint(proposal, exclude={"fingerprint", "status"})
        ):
            raise ValueError("circuit proposal binding is invalid")
        if self.evidence_reference.tenant_id != self.tenant_id or self.evidence_reference.project_id != self.project_id:
            raise ValueError("circuit evidence identity mismatch")
        if self.evidence_reference.receipt_fingerprint is None:
            raise ValueError("circuit evidence receipt fingerprint is missing")
        if self.status not in {ProposalStatus.CREATED, ProposalStatus.REJECTED, ProposalStatus.APPLIED}:
            raise ValueError("circuit candidate status is invalid")
        if self.status is ProposalStatus.CREATED and self.decision is not None:
            raise ValueError("undecided circuit candidate has a decision")
        if self.status is not ProposalStatus.CREATED:
            if self.decision is None or self.decision.project_id != self.project_id or self.decision.proposal_id != proposal.proposal_id or self.decision.proposal_fingerprint != proposal.fingerprint:
                raise ValueError("circuit decision binding is invalid")
            if self.decision.fingerprint != canonical_fingerprint(self.decision, exclude={"fingerprint"}):
                raise ValueError("circuit decision fingerprint is invalid")
            expected = ProposalStatus.REJECTED if self.status is ProposalStatus.REJECTED else ProposalStatus.APPROVED
            if self.decision.decision is not expected:
                raise ValueError("circuit decision status is invalid")
        return self


class PredesignCircuitCandidate(RuntimeContract):
    """A reviewable design intent, with no ERC or artifact claim."""

    tenant_id: str
    project_id: str
    proposal: ObjectProposal
    source_snapshot: PredesignSourceSnapshot
    idempotency_key: str
    request_fingerprint: str
    conversation_id: str
    turn_id: str
    object_id: str
    base_revision: str | None = None
    base_fingerprint: str | None = None
    created_at: datetime
    status: ProposalStatus = ProposalStatus.CREATED
    decision: ApprovalRecord | None = None

    _ids = field_validator(
        "tenant_id", "project_id", "idempotency_key", "conversation_id", "turn_id", "object_id",
    )(lambda value, info: validate_identifier(value, field=info.field_name))
    _request_fingerprint = field_validator("request_fingerprint")(
        lambda value: validate_fingerprint(value)
    )
    _base_revision = field_validator("base_revision")(
        lambda value: None if value is None else validate_revision(value)
    )
    _base_fingerprint = field_validator("base_fingerprint")(
        lambda value: None if value is None else validate_fingerprint(value)
    )
    _created = field_validator("created_at")(validate_utc_datetime)

    @model_validator(mode="after")
    def _binding(self) -> PredesignCircuitCandidate:
        if ((self.base_revision is None) != (self.base_fingerprint is None)
                or self.source_snapshot.project_id != self.project_id
                or self.source_snapshot.conversation_id != self.conversation_id
                or self.source_snapshot.turn_id != self.turn_id
                or self.proposal.project_id != self.project_id
                or self.proposal.target_object_id != self.object_id
                or self.proposal.target_object_type is not ObjectType.CIRCUIT_INTENT
                or not isinstance(self.proposal.proposed_object, CircuitIntentObject)
                or self.proposal.proposed_object.project_id != self.project_id
                or self.proposal.base_revision != self.base_revision
                or self.proposal.base_fingerprint != self.base_fingerprint
                or self.proposal.context_fingerprint != canonical_fingerprint(self.source_snapshot)
                or self.proposal.fingerprint != canonical_fingerprint(self.proposal, exclude={"fingerprint", "status"})):
            raise ValueError("predesign proposal binding is invalid")
        if self.status is ProposalStatus.CREATED and self.decision is not None:
            raise ValueError("undecided predesign candidate has a decision")
        if self.status not in {ProposalStatus.CREATED, ProposalStatus.REJECTED, ProposalStatus.APPLIED}:
            raise ValueError("predesign candidate status is invalid")
        if self.status is not ProposalStatus.CREATED and (
            self.decision is None or self.decision.project_id != self.project_id
            or self.decision.proposal_id != self.proposal.proposal_id
            or self.decision.proposal_fingerprint != self.proposal.fingerprint
            or self.decision.fingerprint != canonical_fingerprint(self.decision, exclude={"fingerprint"})
            or self.decision.decision is not (
                ProposalStatus.REJECTED if self.status is ProposalStatus.REJECTED else ProposalStatus.APPROVED
            )
        ):
            raise ValueError("predesign decision binding is invalid")
        return self


class SchematicClaimProof(RuntimeContract):
    """Record-bound proof of one canonical parse or ERC receipt only."""

    kind: Literal["CATALOG_PARSE_RECEIPT", "CANONICAL_ERC_RECEIPT", "CANONICAL_XML_READBACK", "REVIEWED_PARAMETER_CLAIM"]
    scope: Literal["SCHEMATIC_PARSE", "SCHEMATIC_ERC", "SCHEMATIC_READBACK", "SCHEMATIC_PARAMETER"]
    tenant_id: str
    project_id: str
    conversation_id: str
    intent_id: str
    intent_revision: str
    intent_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    artifact_fingerprint: str
    operation_id: str
    source_receipt_id: str
    source_receipt_fingerprint: str
    verification_record_id: str
    context_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    currentness_at_recording: Literal["CURRENT"] = "CURRENT"
    fingerprint: str

    _ids = field_validator(
        "tenant_id", "project_id", "conversation_id", "intent_id", "plan_id",
        "operation_id", "source_receipt_id", "verification_record_id",
    )(lambda value, info: validate_identifier(value, field=info.field_name))
    _revision = field_validator("intent_revision")(validate_revision)
    _fps = field_validator(
        "intent_fingerprint", "plan_fingerprint", "artifact_fingerprint",
        "source_receipt_fingerprint", "fingerprint",
    )(lambda value, info: validate_fingerprint(value, field=info.field_name))

    @model_validator(mode="after")
    def _binding(self) -> SchematicClaimProof:
        if (
            self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"})
            or {"CATALOG_PARSE_RECEIPT": "SCHEMATIC_PARSE", "CANONICAL_ERC_RECEIPT": "SCHEMATIC_ERC",
                "CANONICAL_XML_READBACK": "SCHEMATIC_READBACK", "REVIEWED_PARAMETER_CLAIM": "SCHEMATIC_PARAMETER"}[self.kind] != self.scope
            or (self.context_fingerprint is not None and validate_fingerprint(self.context_fingerprint) != self.context_fingerprint)
        ):
            raise ValueError("schematic claim proof binding is invalid")
        return self


class SchematicClaimResolution(RuntimeContract):
    claim_id: str
    status: Literal["PASS", "BLOCKED"] = "BLOCKED"
    reason: Literal[
        "TRUSTED_CANONICAL_PROOF", "NO_TRUSTED_RESOLVER",
        "EVIDENCE_UNAVAILABLE", "EVIDENCE_MISMATCH", "SOURCE_STALE",
    ] = "NO_TRUSTED_RESOLVER"
    evidence_ids: tuple[str, ...] = ()
    proof: SchematicClaimProof | None = Field(default=None, exclude_if=lambda value: value is None)

    _id = field_validator("claim_id")(
        lambda value: validate_identifier(value, field="claim_id")
    )

    @model_validator(mode="after")
    def _binding(self) -> SchematicClaimResolution:
        if (self.status == "PASS") != (self.proof is not None):
            raise ValueError("schematic claim status and proof disagree")
        if (self.status == "PASS") != (self.reason == "TRUSTED_CANONICAL_PROOF"):
            raise ValueError("schematic claim reason is invalid")
        return self


class SchematicVerificationRecord(RuntimeContract):
    """Immutable evidence projection; it never grants an execution capability."""

    verification_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    intent_id: str
    intent_revision: str
    intent_fingerprint: str
    plan_id: str
    plan_fingerprint: str
    artifact_fingerprint: str
    generation_operation_id: str
    generation_receipt_fingerprint: str
    parse_validation_fingerprint: str
    erc_operation_id: str | None = None
    erc_receipt_fingerprint: str | None = None
    erc_status: Literal["PASS", "BLOCKED", "FAILED"]
    erc_evidence_id: str | None = None
    claims: tuple[SchematicClaimResolution, ...]
    profile_id: Literal["S02-I2C-SCHEMATIC"] | None = Field(default=None, exclude_if=lambda value: value is None)
    profile_version: Literal[1, 2] | None = Field(default=None, exclude_if=lambda value: value is None)
    profile_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    required_claim_ids: tuple[str, ...] = Field(default=(), exclude_if=lambda values: not values)
    requirement_review_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    architecture_review_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    selection_approval_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    parse_operation_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    readback_operation_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    readback_receipt_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    parameter_evidence_ids: tuple[str, ...] = Field(default=(), exclude_if=lambda values: not values)
    library_map_evidence_ids: tuple[str, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    library_map_evidence_fingerprints: tuple[str, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    context_fingerprint: str | None = Field(default=None, exclude_if=lambda value: value is None)
    aggregate_status: Literal["BLOCKED", "FAILED", "SCHEMATIC_VERIFIED"]
    created_at: datetime
    fingerprint: str

    _ids = field_validator(
        "verification_id", "tenant_id", "project_id", "conversation_id", "intent_id",
        "plan_id", "generation_operation_id",
    )(lambda value, info: validate_identifier(value, field=info.field_name))
    _revision = field_validator("intent_revision")(validate_revision)
    _fps = field_validator(
        "intent_fingerprint", "plan_fingerprint", "artifact_fingerprint",
        "generation_receipt_fingerprint", "parse_validation_fingerprint", "fingerprint",
    )(lambda value, info: validate_fingerprint(value, field=info.field_name))
    _optional_ids = field_validator("erc_operation_id", "erc_evidence_id")(
        lambda value, info: None if value is None else validate_identifier(value, field=info.field_name)
    )
    _optional_fp = field_validator("erc_receipt_fingerprint")(
        lambda value: None if value is None else validate_fingerprint(value)
    )
    _created = field_validator("created_at")(validate_utc_datetime)

    @field_validator("library_map_evidence_ids", "library_map_evidence_fingerprints", mode="before")
    @classmethod
    def _library_sequences(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _binding(self) -> SchematicVerificationRecord:
        if (self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"})
                or len({item.claim_id for item in self.claims}) != len(self.claims)
                or (self.erc_status == "PASS") != (self.erc_evidence_id is not None)
                or (self.erc_operation_id is None) != (self.erc_receipt_fingerprint is None)
                or (self.erc_status == "FAILED") != (self.aggregate_status == "FAILED")):
            raise ValueError("schematic verification record binding is invalid")
        if self.profile_id is not None:
            from embedded_copilot.engineering_copilot.schematic_evidence import REQUIRED_CLAIM_IDS, PROFILE_FINGERPRINT, PROFILE_FINGERPRINT_V2
            expected_profile = PROFILE_FINGERPRINT_V2 if self.profile_version == 2 else PROFILE_FINGERPRINT
            if (self.profile_version not in {1, 2} or self.profile_fingerprint != expected_profile
                    or self.required_claim_ids != REQUIRED_CLAIM_IDS
                    or tuple(item.claim_id for item in self.claims) != REQUIRED_CLAIM_IDS
                    or not all((self.requirement_review_fingerprint, self.architecture_review_fingerprint,
                                self.selection_approval_fingerprint, self.context_fingerprint))):
                raise ValueError("mandatory profile binding invalid")
            for fp in (self.requirement_review_fingerprint, self.architecture_review_fingerprint,
                       self.selection_approval_fingerprint, self.context_fingerprint):
                validate_fingerprint(fp)
            all_pass = all(item.status == "PASS" for item in self.claims)
            if (self.aggregate_status == "SCHEMATIC_VERIFIED") != all_pass:
                raise ValueError("mandatory claims cannot be omitted")
            if all_pass and not all((self.parse_operation_id, self.readback_operation_id, self.readback_receipt_fingerprint)):
                raise ValueError("canonical XML evidence required")
        elif self.aggregate_status == "SCHEMATIC_VERIFIED" or any((self.profile_version, self.profile_fingerprint, self.required_claim_ids, self.context_fingerprint)):
            raise ValueError("legacy record cannot be upgraded")
        if (self.library_map_evidence_ids is None) != (self.library_map_evidence_fingerprints is None):
            raise ValueError("library map selector pair incomplete")
        if self.profile_version == 2:
            if (self.library_map_evidence_ids is None or len(self.library_map_evidence_ids) != 1
                or len(self.library_map_evidence_fingerprints) != 1 or self.readback_operation_id is None):
                raise ValueError("profile2 requires explicit single library map/readback selector")
            validate_identifier(self.library_map_evidence_ids[0], field="library_map_evidence_ids")
            validate_fingerprint(self.library_map_evidence_fingerprints[0])
        elif self.library_map_evidence_ids is not None:
            raise ValueError("legacy profile cannot carry library map selectors")
        for item in self.claims:
            proof = item.proof
            if proof is None:
                continue
            if (
                proof.tenant_id != self.tenant_id
                or proof.project_id != self.project_id
                or proof.conversation_id != self.conversation_id
                or proof.intent_id != self.intent_id
                or proof.intent_revision != self.intent_revision
                or proof.intent_fingerprint != self.intent_fingerprint
                or proof.plan_id != self.plan_id
                or proof.plan_fingerprint != self.plan_fingerprint
                or proof.artifact_fingerprint != self.artifact_fingerprint
                or proof.verification_record_id != self.verification_id
                or (proof.scope == "SCHEMATIC_PARSE" and (
                    proof.operation_id != (self.generation_operation_id if self.profile_id is None else self.parse_operation_id)
                    or proof.source_receipt_fingerprint != self.parse_validation_fingerprint
                ))
                or (self.profile_id is not None and proof.context_fingerprint != self.context_fingerprint)
                or (proof.scope in {"SCHEMATIC_READBACK", "SCHEMATIC_PARAMETER"} and (
                    self.profile_id is None or proof.operation_id != self.readback_operation_id
                    or proof.source_receipt_fingerprint != self.readback_receipt_fingerprint))
                or (proof.scope == "SCHEMATIC_ERC" and (
                    proof.operation_id != self.erc_operation_id
                    or proof.source_receipt_fingerprint != self.erc_receipt_fingerprint
                    or proof.source_receipt_id != self.erc_evidence_id
                    or self.erc_status != "PASS"
                ))
            ):
                raise ValueError("schematic claim proof subject is invalid")
        return self


class CircuitPublication(RuntimeContract):
    """One durable approved Circuit revision and its complete audit chain."""

    tenant_id: str
    project_id: str
    idempotency_key: str
    request_fingerprint: str
    base_revision: str | None = None
    base_fingerprint: str | None = None
    circuit: CircuitIntentObject
    proposal: ObjectProposal
    approval: ApprovalRecord
    apply_record: ObjectMutationRecord

    _ids = field_validator("tenant_id", "project_id", "idempotency_key")(
        lambda value: validate_identifier(value, field="circuit_publication_identity")
    )
    _request_fp = field_validator("request_fingerprint")(
        lambda value: validate_fingerprint(value)
    )
    _base_fp = field_validator("base_fingerprint")(
        lambda value: None if value is None else validate_fingerprint(value)
    )
    _base_rev = field_validator("base_revision")(
        lambda value: None if value is None else validate_revision(value)
    )

    @model_validator(mode="after")
    def _binding(self) -> CircuitPublication:
        circuit, proposal, approval, applied = self.circuit, self.proposal, self.approval, self.apply_record
        if (self.base_revision is None) != (self.base_fingerprint is None):
            raise ValueError("circuit base identity is incomplete")
        if (
            circuit.project_id != self.project_id
            or circuit.status is not LifecycleStatus.ACTIVE
            or circuit.fingerprint == "sha256:" + "0" * 64
            or proposal.project_id != self.project_id
            or proposal.target_object_id != circuit.object_id
            or proposal.base_revision != self.base_revision
            or proposal.base_fingerprint != self.base_fingerprint
            or proposal.proposed_object.fingerprint != circuit.fingerprint
            or proposal.fingerprint != canonical_fingerprint(proposal, exclude={"fingerprint", "status"})
            or approval.project_id != self.project_id
            or approval.proposal_id != proposal.proposal_id
            or approval.proposal_fingerprint != proposal.fingerprint
            or approval.decision is not ProposalStatus.APPROVED
            or approval.fingerprint != canonical_fingerprint(approval, exclude={"fingerprint"})
            or applied.project_id != self.project_id
            or applied.proposal_id != proposal.proposal_id
            or applied.new_revision != circuit.revision
            or applied.object_fingerprint != circuit.fingerprint
            or applied.fingerprint != canonical_fingerprint(applied, exclude={"fingerprint"})
        ):
            raise ValueError("circuit publication binding is invalid")
        return self


def circuit_decision_record(
    candidate: CircuitProposalCandidate, reviewer: str, decision: str
) -> ApprovalRecord:
    """Create the Catalog's typed record for one explicit review decision."""
    if decision not in {"APPROVE", "REJECT"}:
        raise ValueError("Circuit review decision is invalid")
    proposal = candidate.proposal
    approval_id = "approval-" + hashlib.sha256(
        f"{proposal.proposal_id}|{reviewer}|{decision}".encode("utf-8")
    ).hexdigest()[:24]
    reviewed_at = datetime.now(UTC)
    if decision == "APPROVE":
        return ApprovalRecord.approve(
            approval_id=approval_id, project_id=proposal.project_id,
            proposal_id=proposal.proposal_id,
            proposal_fingerprint=proposal.fingerprint,
            reviewer=reviewer, reviewed_at=reviewed_at,
        )
    draft = ApprovalRecord(
        approval_id=approval_id, project_id=proposal.project_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        reviewer=reviewer, decision=ProposalStatus.REJECTED,
        reviewed_at=reviewed_at, fingerprint="sha256:" + "0" * 64,
    )
    return draft.model_copy(update={
        "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"}),
    })


__all__ = tuple(name for name in globals() if name.startswith(("Project", "Conversation", "Evidence")))
