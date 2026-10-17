"""Model governance contracts for the canonical model gateway.

This module contains policy metadata only.  It never owns a provider client or
performs a model call; selection is a deterministic projection consumed by the
existing gateway boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .contracts import ModelGatewayContract, identifier
from .profiles import ModelCapability, ModelProfile, _ref


def _timestamp(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


class PolicyScope(StrEnum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    STAGE = "STAGE"


class SelectionMode(StrEnum):
    AUTO = "AUTO"
    FIXED_MODEL = "FIXED_MODEL"
    CANDIDATE_GROUP = "CANDIDATE_GROUP"


class EngineeringStage(StrEnum):
    REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"
    TECH_RESEARCH = "TECH_RESEARCH"
    SYSTEM_ARCHITECTURE = "SYSTEM_ARCHITECTURE"
    HARDWARE_SELECTION = "HARDWARE_SELECTION"
    SCHEMATIC_DESIGN = "SCHEMATIC_DESIGN"
    PCB_REVIEW = "PCB_REVIEW"
    FIRMWARE_DEVELOPMENT = "FIRMWARE_DEVELOPMENT"
    DEBUGGING = "DEBUGGING"
    SIMULATION = "SIMULATION"
    DOCUMENTATION = "DOCUMENTATION"


class BoundModelIdentity(ModelGatewayContract):
    """Provider-scoped model identity; model_id alone is never sufficient."""

    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str

    @field_validator("provider_id", "endpoint_id", "model_profile_id", "model_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str:
        return _ref(value, field="credential_ref")

    @classmethod
    def from_profile(cls, profile: ModelProfile, credential_ref: str) -> "BoundModelIdentity":
        return cls(
            provider_id=profile.provider_id,
            endpoint_id=profile.endpoint_id,
            credential_ref=credential_ref,
            model_profile_id=profile.model_profile_id,
            model_id=profile.remote_model_id,
        )


class CandidateSelectionPolicy(StrEnum):
    PRIORITY_ORDER = "PRIORITY_ORDER"


class CandidateSpec(ModelGatewayContract):
    identity: BoundModelIdentity
    priority: int = Field(ge=0)


class CandidateGroup(ModelGatewayContract):
    group_id: str
    stage: EngineeringStage
    candidates: tuple[CandidateSpec, ...]
    selection_mode: SelectionMode = SelectionMode.CANDIDATE_GROUP
    policy: CandidateSelectionPolicy = CandidateSelectionPolicy.PRIORITY_ORDER
    revision: int = Field(ge=1)

    @field_validator("group_id", mode="before")
    @classmethod
    def _group_id(cls, value: object) -> str:
        return identifier(value, field="group_id")

    @model_validator(mode="after")
    def _shape(self) -> CandidateGroup:
        if self.selection_mode is not SelectionMode.CANDIDATE_GROUP or not self.candidates:
            raise ValueError("candidate group shape is invalid")
        priorities = [item.priority for item in self.candidates]
        identities = [
            (
                item.identity.provider_id,
                item.identity.endpoint_id,
                item.identity.credential_ref,
                item.identity.model_profile_id,
                item.identity.model_id,
            )
            for item in self.candidates
        ]
        if len(set(priorities)) != len(priorities):
            raise ValueError("candidate priorities must be unique")
        if len(set(identities)) != len(identities):
            raise ValueError("candidate identities must be unique")
        return self


class CandidateRejection(ModelGatewayContract):
    candidate: BoundModelIdentity
    rejection_reason: str

    @field_validator("rejection_reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return identifier(value, field="rejection_reason")


class CandidateSelectionEvidence(ModelGatewayContract):
    candidate_group_id: str
    candidate_group_revision: int = Field(ge=1)
    candidate_list: tuple[CandidateSpec, ...]
    rejected_candidates: tuple[CandidateRejection, ...]
    selected_candidate: BoundModelIdentity | None
    selection_policy: CandidateSelectionPolicy
    selection_revision: int = Field(default=1, ge=1, le=1)

    @field_validator("candidate_group_id", mode="before")
    @classmethod
    def _group_id(cls, value: object) -> str:
        return identifier(value, field="candidate_group_id")

    @model_validator(mode="after")
    def _binding(self) -> CandidateSelectionEvidence:
        identities = tuple(item.identity for item in self.candidate_list)
        if not identities or any(item.candidate not in identities for item in self.rejected_candidates):
            raise ValueError("candidate selection evidence binding is invalid")
        if self.selected_candidate is not None and (
            self.selected_candidate not in identities
            or self.selected_candidate in (item.candidate for item in self.rejected_candidates)
        ):
            raise ValueError("candidate selection evidence binding is invalid")
        return self


class AutoSelectionEvidence(ModelGatewayContract):
    auto_policy_revision: str
    candidate_source: str
    candidate_list: tuple[CandidateSpec, ...]
    filtered_candidates: tuple[CandidateSpec, ...]
    rejection_reasons: tuple[CandidateRejection, ...]
    selected_candidate: BoundModelIdentity | None
    selection_reason: str

    @model_validator(mode="after")
    def _binding(self) -> AutoSelectionEvidence:
        def key(item: BoundModelIdentity) -> tuple[str, str, str, str, str]:
            return (item.provider_id, item.endpoint_id, item.credential_ref, item.model_profile_id, item.model_id)

        candidates = {key(item.identity) for item in self.candidate_list}
        eligible = {key(item.identity) for item in self.filtered_candidates}
        rejected = {key(item.candidate) for item in self.rejection_reasons}
        if not eligible.issubset(candidates) or not rejected.issubset(candidates) or eligible & rejected:
            raise ValueError("auto selection evidence binding is invalid")
        if eligible | rejected != candidates or self.selected_candidate != (
            self.filtered_candidates[0].identity if self.filtered_candidates else None
        ):
            raise ValueError("auto selection evidence binding is invalid")
        return self


class EngineeringStageDefinition(ModelGatewayContract):
    stage_id: EngineeringStage
    display_name: str
    required_capabilities: tuple[ModelCapability, ...] = (ModelCapability.TEXT,)

    @field_validator("display_name", mode="before")
    @classmethod
    def _display_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError("display_name is invalid")
        return value.strip()

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)


class AllowedModelPolicy(ModelGatewayContract):
    id: str
    scope: PolicyScope
    project_id: str | None = None
    stage_id: EngineeringStage | None = None
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "provider_id", "endpoint_id", "model_profile_id", "model_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str:
        return _ref(value, field="credential_ref")

    @field_validator("project_id", mode="before")
    @classmethod
    def _project(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="project_id")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return _timestamp(value, field=info.field_name)

    @model_validator(mode="after")
    def _scope_binding(self) -> "AllowedModelPolicy":
        if self.scope is PolicyScope.GLOBAL and (self.project_id is not None or self.stage_id is not None):
            raise ValueError("global policy scope binding is invalid")
        if self.scope is PolicyScope.PROJECT and (self.project_id is None or self.stage_id is not None):
            raise ValueError("project policy scope binding is invalid")
        if self.scope is PolicyScope.STAGE and (self.project_id is None or self.stage_id is None):
            raise ValueError("stage policy scope binding is invalid")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self


class StageModelPolicy(ModelGatewayContract):
    id: str
    project_id: str
    stage_id: EngineeringStage
    selection_mode: SelectionMode
    fixed_model: BoundModelIdentity | None = None
    candidate_models: tuple[BoundModelIdentity, ...] = ()
    candidate_group_id: str | None = None
    candidate_group_revision: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("candidate_group_id", mode="before")
    @classmethod
    def _candidate_group_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="candidate_group_id")

    @field_validator("candidate_models", mode="before")
    @classmethod
    def _candidates(cls, value: object) -> tuple[BoundModelIdentity, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("candidate_models must be a tuple")
        return tuple(item if isinstance(item, BoundModelIdentity) else BoundModelIdentity.model_validate(item) for item in value)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return _timestamp(value, field=info.field_name)

    @model_validator(mode="after")
    def _mode_shape(self) -> "StageModelPolicy":
        if self.selection_mode is SelectionMode.CANDIDATE_GROUP:
            if self.fixed_model is not None or self.candidate_models or self.candidate_group_id is None or self.candidate_group_revision is None:
                raise ValueError("candidate group policy requires a group reference only")
        else:
            if self.candidate_group_id is not None or self.candidate_group_revision is not None:
                raise ValueError("non-group policy cannot reference a candidate group")
            _validate_mode_shape(self.selection_mode, self.fixed_model, self.candidate_models)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self


class ProjectDefaultModelPolicy(ModelGatewayContract):
    id: str
    project_id: str
    selection_mode: SelectionMode
    fixed_model: BoundModelIdentity | None = None
    candidate_models: tuple[BoundModelIdentity, ...] = ()
    created_at: datetime
    updated_at: datetime

    @field_validator("id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("candidate_models", mode="before")
    @classmethod
    def _candidates(cls, value: object) -> tuple[BoundModelIdentity, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("candidate_models must be a tuple")
        return tuple(item if isinstance(item, BoundModelIdentity) else BoundModelIdentity.model_validate(item) for item in value)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return _timestamp(value, field=info.field_name)

    @model_validator(mode="after")
    def _mode_shape(self) -> "ProjectDefaultModelPolicy":
        _validate_mode_shape(self.selection_mode, self.fixed_model, self.candidate_models)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self


class ModelSelectionTaskProfile(ModelGatewayContract):
    request_id: str
    project_id: str
    stage: EngineeringStage
    task_type: str
    required_capabilities: tuple[ModelCapability, ...] = (ModelCapability.TEXT,)
    quality_requirement: str = "UNKNOWN"
    cost_preference: str = "NEUTRAL"
    latency_requirement: str = "NEUTRAL"

    @field_validator("request_id", "project_id", "task_type", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _required(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)


class ModelSelectionResult(ModelGatewayContract):
    selected_model: BoundModelIdentity | None = None
    selection_reason: str
    fallback_candidates: tuple[BoundModelIdentity, ...] = ()
    candidate_selection_evidence: CandidateSelectionEvidence | None = None
    auto_selection_evidence: AutoSelectionEvidence | None = None

    @field_validator("selection_reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 128:
            raise ValueError("selection_reason is invalid")
        return value.strip()

    @model_validator(mode="after")
    def _evidence_binding(self) -> ModelSelectionResult:
        if self.candidate_selection_evidence is not None and self.selected_model != self.candidate_selection_evidence.selected_candidate:
            raise ValueError("candidate selection result binding is invalid")
        if self.auto_selection_evidence is not None and self.selected_model != self.auto_selection_evidence.selected_candidate:
            raise ValueError("auto selection result binding is invalid")
        return self


def _validate_mode_shape(
    mode: SelectionMode,
    fixed_model: BoundModelIdentity | None,
    candidates: tuple[BoundModelIdentity, ...],
) -> None:
    if mode is SelectionMode.AUTO and (fixed_model is not None or candidates):
        raise ValueError("auto policy cannot contain models")
    if mode is SelectionMode.FIXED_MODEL and (fixed_model is None or candidates):
        raise ValueError("fixed policy requires exactly one model")
    if mode is SelectionMode.CANDIDATE_GROUP and (fixed_model is not None or len(candidates) < 2):
        raise ValueError("candidate group requires at least two models")
    identities = tuple((item.provider_id, item.endpoint_id, item.credential_ref, item.model_profile_id, item.model_id) for item in candidates)
    if len(set(identities)) != len(identities):
        raise ValueError("candidate models must be unique")


def default_engineering_stage_registry() -> tuple[EngineeringStageDefinition, ...]:
    names = {
        EngineeringStage.REQUIREMENT_ANALYSIS: "Requirement Analysis",
        EngineeringStage.TECH_RESEARCH: "Tech Research",
        EngineeringStage.SYSTEM_ARCHITECTURE: "System Architecture",
        EngineeringStage.HARDWARE_SELECTION: "Hardware Selection",
        EngineeringStage.SCHEMATIC_DESIGN: "Schematic Design",
        EngineeringStage.PCB_REVIEW: "PCB Review",
        EngineeringStage.FIRMWARE_DEVELOPMENT: "Firmware Development",
        EngineeringStage.DEBUGGING: "Debugging",
        EngineeringStage.SIMULATION: "Simulation",
        EngineeringStage.DOCUMENTATION: "Documentation",
    }
    result: list[EngineeringStageDefinition] = []
    for stage, name in names.items():
        required = (ModelCapability.TEXT,)
        if stage is EngineeringStage.PCB_REVIEW:
            required = (ModelCapability.VISION, ModelCapability.REASONING)
        elif stage is EngineeringStage.FIRMWARE_DEVELOPMENT:
            required = (ModelCapability.CODING, ModelCapability.TOOL_CALLING)
        result.append(EngineeringStageDefinition(stage_id=stage, display_name=name, required_capabilities=required))
    return tuple(result)


__all__ = (
    "AllowedModelPolicy",
    "AutoSelectionEvidence",
    "BoundModelIdentity",
    "CandidateGroup",
    "CandidateRejection",
    "CandidateSelectionEvidence",
    "CandidateSelectionPolicy",
    "CandidateSpec",
    "EngineeringStage",
    "EngineeringStageDefinition",
    "ModelSelectionResult",
    "ModelSelectionTaskProfile",
    "PolicyScope",
    "ProjectDefaultModelPolicy",
    "SelectionMode",
    "StageModelPolicy",
    "default_engineering_stage_registry",
)
