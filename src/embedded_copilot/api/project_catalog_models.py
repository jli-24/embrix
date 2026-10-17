from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
)
from embedded_copilot.engineering_copilot import (
    AuthorizationDecisionStatus,
    FirmwareSourceProposalDecisionStatus,
    HardwareSelectionDecision,
    HardwareSelectionState,
    PlanReviewStatus,
    SchematicPlanDecision,
    SchematicProposalDecisionStatus,
    SchematicRepairDecisionAction,
)
from embedded_copilot.engineering_copilot.schematic_evidence import (
    LibraryTerminalBindingV2,
    ParameterLocator,
)
from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
    ProjectCoverageV1,
)
from embedded_copilot.engineering_research import (
    FeatureDecisionAction,
    ReferenceProjectDecisionAction,
    RequirementDecisionAction,
)
from embedded_copilot.firmware_engineering.contracts import FirmwareBuildConfiguration
from embedded_copilot.pcb_production.contracts import (
    PCBPhysicalDesignProposal,
    PCBRepairDecisionAction,
)
from embedded_copilot.project_catalog import (
    PCBProposalDecisionStatus,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectDirectoryReference,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.models import (
    SchematicVerificationRecord,
    SourcePublicationBindingV1,
    StandardTargetBindingV2,
)


class ProjectCatalogApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ProjectPreviewInput(ProjectCatalogApiModel):
    name: str
    project_type: ProjectType
    parent_location: ProjectLocationReference | None = None
    location: ProjectLocationReference | None = None
    creation_mode: ProjectCreationMode | None = None
    existing_directory: ProjectDirectoryReference | None = None
    relative_path: str | None = None

    @model_validator(mode="after")
    def _contract(self) -> ProjectPreviewInput:
        controlled_location = self.parent_location or self.location
        if self.parent_location is not None and self.location is not None and self.parent_location != self.location:
            raise ValueError("parent location references do not match")
        if self.relative_path is None and (controlled_location is None or self.creation_mode is None):
            raise ValueError("project creation contract is incomplete")
        return self

    def to_request(self) -> ProjectCreationRequest:
        return ProjectCreationRequest(
            name=self.name,
            project_type=self.project_type,
            parent_location=self.parent_location,
            location=self.location,
            creation_mode=self.creation_mode,
            existing_directory=self.existing_directory,
            relative_path=self.relative_path,
        )


class ProjectCreateInput(ProjectCatalogApiModel):
    preview_id: str
    preview_fingerprint: str
    confirmed_at: datetime


class EDAImportPreviewInput(ProjectCatalogApiModel):
    backend: Literal["KICAD", "JLCEDA_PRO"]
    native_entry_relative_path: str = Field(min_length=1, max_length=512)
    selected_native_entries: list[str] = Field(default_factory=list, max_length=32)
    expected_head_revision_id: str | None
    expected_head_revision_fingerprint: str | None = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _head_pair(self) -> EDAImportPreviewInput:
        if (self.expected_head_revision_id is None) != (self.expected_head_revision_fingerprint is None):
            raise ValueError("expected head selector is incomplete")
        return self


class EDAImportCommitInput(ProjectCatalogApiModel):
    preview_id: str = Field(min_length=1, max_length=128)
    preview_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class NativeMovePositionInput(ProjectCatalogApiModel):
    x_mm: float = Field(ge=-10_000, le=10_000, allow_inf_nan=False)
    y_mm: float = Field(ge=-10_000, le=10_000, allow_inf_nan=False)


class NativeMoveCandidateInput(ProjectCatalogApiModel):
    import_revision_id: str = Field(min_length=1, max_length=128)
    import_revision_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    workspace_target_id: Literal["project-root"]
    target_native_uuid: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    after: NativeMovePositionInput


class NativeMoveCandidateView(ProjectCatalogApiModel):
    project_id: str
    backend: Literal["KICAD"]
    candidate_id: str
    candidate_fingerprint: str
    import_revision_id: str
    import_revision_fingerprint: str
    source_set_fingerprint: str
    manifest_fingerprint: str
    native_entry_relative_path: str
    parser_ruleset_id: Literal["kicad-pcb-footprint-20240108-r1", "kicad-pcb-footprint-20240108-r2"]
    parser_ruleset_fingerprint: str
    supported_subset_id: Literal["kicad-footprint-placement-r1"]
    supported_subset_fingerprint: str
    manifest: list[EDAImportManifestItem]
    issues: list[EDAImportIssueView]
    workspace_target_id: Literal["project-root"]
    workspace_project_fingerprint: str
    workspace_target_fingerprint: str
    target_native_uuid: str
    native_locator: str
    pcb_relative_path: str
    pcb_sha256: str
    rotation_degrees: float
    before: NativeMovePositionInput
    after: NativeMovePositionInput
    edits: list[NativeMoveByteEditView]
    diff_fingerprint: str
    preservation_status: Literal["BLOCKED_UNPROVEN", "BYTE_SPAN_PROVEN"]
    can_request_operation_approval: bool


class NativeMoveMappingReviewInput(ProjectCatalogApiModel):
    candidate_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["ACCEPT_MAPPING", "REJECT_MAPPING"]
    design_intent: str = Field(min_length=1, max_length=512)


class NativeMoveMappingReviewView(ProjectCatalogApiModel):
    mapping_review_id: str
    mapping_review_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    reviewer_id: str
    decision: Literal["ACCEPT_MAPPING", "REJECT_MAPPING"]
    design_intent: str
    created_at: datetime


class DrcBaselineOperationCreateInput(ProjectCatalogApiModel):
    import_revision_id: str = Field(min_length=1, max_length=128)
    import_revision_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    pcb_relative_path: str = Field(min_length=1, max_length=4096)


class NativeMoveOperationCreateInput(ProjectCatalogApiModel):
    candidate_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mapping_review_id: str = Field(min_length=1, max_length=128)
    mapping_review_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EDAImportManifestItem(ProjectCatalogApiModel):
    relative_path: str
    kind: Literal["FILE", "MISSING_DEPENDENCY", "EXTERNAL_DEPENDENCY"]
    byte_size: int | None = None
    sha256: str | None = None
    reference_digest: str | None = None
    discovery_locator: str | None = None


class EDAImportIssueView(ProjectCatalogApiModel):
    kind: Literal["UNKNOWN", "CONFLICT", "LOSS"]
    code: str
    locator: str
    blocks_mutation: bool


class NativeMoveByteEditView(ProjectCatalogApiModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    before_hex: str = Field(pattern=r"^(?:[0-9a-f]{2})+$")
    after_hex: str = Field(pattern=r"^(?:[0-9a-f]{2})+$")


class EDAImportObjectView(ProjectCatalogApiModel):
    object_type: Literal["FOOTPRINT"]
    source_file: str
    native_id: str
    locator: str
    library_id: str
    reference: str | None
    position_x_mm: float
    position_y_mm: float
    rotation_degrees: float


class EDAImportPreviewView(ProjectCatalogApiModel):
    project_coverage: ProjectCoverageV1 | None = None

    @field_validator("project_coverage", mode="before")
    @classmethod
    def _coverage(cls, value):
        if isinstance(value, dict):
            return ProjectCoverageV1.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return value

    @model_serializer(mode="wrap")
    def _omit_absent_coverage(self, handler):
        result = handler(self)
        if self.project_coverage is None:
            result.pop("project_coverage", None)
        return result

    status: Literal["PREVIEW"]
    preview_id: str
    preview_fingerprint: str
    expires_at: datetime
    backend: Literal["KICAD", "JLCEDA_PRO"]
    native_entry_relative_path: str
    native_format_family: str
    native_format_versions: dict[str, str]
    source_set_fingerprint: str
    parser_ruleset_id: str
    supported_subset_id: str
    semantic_completeness: Literal["UNKNOWN", "PARTIAL", "COMPLETE_FOR_DECLARED_SUBSET"]
    manifest: list[EDAImportManifestItem]
    objects: list[EDAImportObjectView]
    issues: list[EDAImportIssueView]
    verified: Literal[False]
    can_modify: Literal[False]


class EDAImportRevisionView(ProjectCatalogApiModel):
    project_coverage: ProjectCoverageV1 | None = None

    @field_validator("project_coverage", mode="before")
    @classmethod
    def _coverage(cls, value):
        if isinstance(value, dict):
            return ProjectCoverageV1.model_validate_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return value

    @model_serializer(mode="wrap")
    def _omit_absent_coverage(self, handler):
        result = handler(self)
        if self.project_coverage is None:
            result.pop("project_coverage", None)
        return result

    status: Literal["IMPORTED_OBSERVATION"] | None = None
    commit_outcome: Literal["CREATED", "REUSED_ACTIVE", "ALREADY_COMMITTED"] | None = None
    import_revision_id: str
    import_revision_fingerprint: str
    active: bool
    created_at: datetime
    backend: Literal["KICAD", "JLCEDA_PRO"]
    native_entry_relative_path: str
    native_format_family: str
    native_format_versions: dict[str, str]
    source_set_fingerprint: str
    parser_ruleset_id: str
    supported_subset_id: str
    semantic_completeness: Literal["UNKNOWN", "PARTIAL", "COMPLETE_FOR_DECLARED_SUBSET"]
    source_currentness: Literal["CURRENT", "STALE", "UNAVAILABLE"]
    observation_currentness: Literal["CURRENT", "STALE", "UNAVAILABLE"]
    manifest: list[EDAImportManifestItem]
    objects: list[EDAImportObjectView]
    issues: list[EDAImportIssueView]
    verified: Literal[False]
    can_modify: Literal[False]


class EDAImportRevisionSummaryView(ProjectCatalogApiModel):
    import_revision_id: str
    import_revision_fingerprint: str
    active: bool
    created_at: datetime
    backend: Literal["KICAD", "JLCEDA_PRO"]
    native_entry_relative_path: str
    source_set_fingerprint: str


class EDAImportRevisionPage(ProjectCatalogApiModel):
    items: list[EDAImportRevisionSummaryView]
    next_cursor: str | None


class ConversationCreateInput(ProjectCatalogApiModel):
    pass


class ConversationResumeInput(ProjectCatalogApiModel):
    pass


class CircuitIntentPreviewInput(ProjectCatalogApiModel):
    conversation_id: str = Field(min_length=1, max_length=256)
    turn_id: str = Field(min_length=1, max_length=256)
    circuit_intent: CircuitIntentSpec
    erc_operation_id: str = Field(min_length=1, max_length=256)
    object_id: str | None = Field(default=None, min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    constraints: tuple[str, ...] = Field(default=(), max_length=256)

    @field_validator("circuit_intent", mode="before")
    @classmethod
    def _circuit_json(cls, value: object) -> CircuitIntentSpec:
        return CircuitIntentSpec.model_validate(value, strict=False)


class PredesignSourceExpectationsInput(ProjectCatalogApiModel):
    requirement_context_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    architecture_id: str = Field(min_length=1, max_length=256)
    architecture_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    hardware_selection_approval_id: str = Field(min_length=1, max_length=256)
    hardware_truth_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PredesignCircuitPreviewInput(ProjectCatalogApiModel):
    purpose: Literal["PREDESIGN_APPROVAL"]
    conversation_id: str = Field(min_length=1, max_length=256)
    turn_id: str = Field(min_length=1, max_length=256)
    circuit_intent: CircuitIntentSpec
    idempotency_key: str = Field(min_length=1, max_length=256)
    object_id: str | None = Field(default=None, min_length=1, max_length=256)
    base_revision: str | None = Field(default=None, pattern=r"^r[1-9][0-9]*$")
    base_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    source_expectations: PredesignSourceExpectationsInput

    @field_validator("circuit_intent", mode="before")
    @classmethod
    def _circuit_json(cls, value: object) -> CircuitIntentSpec:
        return CircuitIntentSpec.model_validate(value, strict=False)

    @model_validator(mode="after")
    def _base(self) -> PredesignCircuitPreviewInput:
        if ((self.object_id is None) != (self.base_revision is None)
                or (self.base_revision is None) != (self.base_fingerprint is None)):
            raise ValueError("predesign base selector is incomplete")
        return self


class CircuitIntentDecisionInput(ProjectCatalogApiModel):
    proposal_id: str = Field(min_length=1, max_length=256)
    proposal_fingerprint: str = Field(min_length=71, max_length=71)
    base_revision: str | None = Field(default=None, min_length=2, max_length=32)
    base_fingerprint: str | None = Field(default=None, min_length=71, max_length=71)
    context_fingerprint: str = Field(min_length=71, max_length=71)
    evidence_receipt_fingerprint: str = Field(min_length=71, max_length=71)
    decision: Literal["APPROVE", "REJECT"]


class PredesignCircuitDecisionInput(ProjectCatalogApiModel):
    purpose: Literal["PREDESIGN_APPROVAL"]
    proposal_id: str = Field(min_length=1, max_length=256)
    proposal_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_revision: str | None = Field(default=None, pattern=r"^r[1-9][0-9]*$")
    base_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVE", "REJECT"]


class CircuitIntentPreviewView(ProjectCatalogApiModel):
    project_id: str
    conversation_id: str
    turn_id: str
    proposal_id: str
    proposal_fingerprint: str
    status: str
    object_id: str
    revision: str
    base_revision: str | None
    base_fingerprint: str | None
    context_fingerprint: str
    source_fingerprint: str
    evidence_receipt_id: str
    evidence_receipt_fingerprint: str
    circuit_intent: CircuitIntentSpec
    created_at: datetime


class PredesignCircuitPreviewView(ProjectCatalogApiModel):
    purpose: Literal["PREDESIGN_APPROVAL"] = "PREDESIGN_APPROVAL"
    project_id: str
    conversation_id: str
    turn_id: str
    proposal_id: str
    proposal_fingerprint: str
    status: str
    object_id: str
    revision: str
    base_revision: str | None
    base_fingerprint: str | None
    source_fingerprint: str
    source_snapshot: dict[str, object]
    completion: Literal["DESIGN_ONLY"] = "DESIGN_ONLY"
    circuit_intent: CircuitIntentSpec
    created_at: datetime


class CircuitIntentDecisionView(ProjectCatalogApiModel):
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    status: Literal["APPLIED", "REJECTED"]
    object_id: str | None = None
    revision: str | None = None
    object_fingerprint: str | None = None
    approval_id: str
    approval_fingerprint: str


class PredesignCircuitDecisionView(CircuitIntentDecisionView):
    purpose: Literal["PREDESIGN_APPROVAL"] = "PREDESIGN_APPROVAL"
    completion: Literal["DESIGN_ONLY"] = "DESIGN_ONLY"


class BrownfieldBuildProposalInput(ProjectCatalogApiModel):
    workspace_fingerprint: str = Field(min_length=71, max_length=71)
    target_platform: str = Field(min_length=1, max_length=128)
    mcu_family: str = Field(min_length=1, max_length=128)
    mcu_exact_part: str = Field(min_length=1, max_length=128)
    source_layout: tuple[str, ...] = Field(min_length=1, max_length=1024)
    configuration: FirmwareBuildConfiguration


class BrownfieldBuildDecisionInput(ProjectCatalogApiModel):
    proposal_fingerprint: str = Field(min_length=71, max_length=71)
    decision: PlanReviewStatus

    @model_validator(mode="after")
    def _terminal(self) -> BrownfieldBuildDecisionInput:
        if self.decision is PlanReviewStatus.PENDING:
            raise ValueError("terminal plan review decision is required")
        return self


class RequirementSuggestionDecisionInput(ProjectCatalogApiModel):
    action: RequirementDecisionAction
    modified_requirement: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _terminal(self) -> RequirementSuggestionDecisionInput:
        if self.action in {
            RequirementDecisionAction.PENDING,
            RequirementDecisionAction.SUPPRESSED,
        }:
            raise ValueError("terminal requirement suggestion decision is required")
        if self.action is RequirementDecisionAction.MODIFY and not self.modified_requirement:
            raise ValueError("modify decision requires modified_requirement")
        if self.action is not RequirementDecisionAction.MODIFY and self.modified_requirement is not None:
            raise ValueError("only modify decision may contain modified_requirement")
        return self


class ReferenceProjectDecisionInput(ProjectCatalogApiModel):
    project_identity: str = Field(min_length=1, max_length=160)
    decision: ReferenceProjectDecisionAction
    selected_features: tuple[str, ...] = Field(default=(), max_length=64)


class FeatureDecisionInput(ProjectCatalogApiModel):
    project_identity: str = Field(min_length=1, max_length=160)
    feature: str = Field(min_length=1, max_length=256)
    action: FeatureDecisionAction
    value: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _decision(self) -> FeatureDecisionInput:
        if self.action is FeatureDecisionAction.MODIFY and not self.value:
            raise ValueError("feature MODIFY requires value")
        if self.action is not FeatureDecisionAction.MODIFY and self.value is not None:
            raise ValueError("only feature MODIFY may contain value")
        return self


class RequirementChangeProposalInput(ProjectCatalogApiModel):
    utterance: str = Field(min_length=1, max_length=8192)


class RequirementFinalConfirmationInput(ProjectCatalogApiModel):
    proposal_fingerprint: str = Field(min_length=71, max_length=71)
    confirmed: bool


class ResearchIntentRefinementInput(ProjectCatalogApiModel):
    product_positioning: str = Field(min_length=1, max_length=1024)
    negative_constraints: tuple[str, ...] = Field(default=(), max_length=64)


class ExecutionAuthorizationDecisionInput(ProjectCatalogApiModel):
    decision: AuthorizationDecisionStatus

    @model_validator(mode="after")
    def _terminal(self) -> ExecutionAuthorizationDecisionInput:
        if self.decision is AuthorizationDecisionStatus.PENDING:
            raise ValueError("terminal authorization decision is required")
        return self


class FirmwareSourceProposalDecisionInput(ProjectCatalogApiModel):
    decision: FirmwareSourceProposalDecisionStatus

    @model_validator(mode="after")
    def _terminal(self) -> FirmwareSourceProposalDecisionInput:
        if self.decision is FirmwareSourceProposalDecisionStatus.PENDING:
            raise ValueError("terminal proposal decision is required")
        return self


class SchematicProposalDecisionInput(ProjectCatalogApiModel):
    decision: SchematicProposalDecisionStatus

    @model_validator(mode="after")
    def _terminal(self) -> SchematicProposalDecisionInput:
        if self.decision is SchematicProposalDecisionStatus.PENDING:
            raise ValueError("terminal schematic proposal decision is required")
        return self


class SchematicRepairDecisionInput(ProjectCatalogApiModel):
    repair_id: str = Field(min_length=1, max_length=256)
    proposal_fingerprint: str = Field(min_length=71, max_length=71)
    artifact_fingerprint: str = Field(min_length=71, max_length=71)
    action: SchematicRepairDecisionAction
    comment: str = Field(default="", max_length=2048)
    edited_proposal_fingerprint: str | None = Field(default=None, min_length=71, max_length=71)

    @model_validator(mode="after")
    def _action_boundary(self) -> SchematicRepairDecisionInput:
        if self.action is SchematicRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is None:
            raise ValueError("EDIT_FIX requires edited_proposal_fingerprint")
        if self.action is not SchematicRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is not None:
            raise ValueError("edited_proposal_fingerprint is only valid for EDIT_FIX")
        if self.action is SchematicRepairDecisionAction.WAIVE_WITH_REASON and not self.comment.strip():
            raise ValueError("WAIVE_WITH_REASON requires a reason")
        return self


class PCBProductionProposalInput(ProjectCatalogApiModel):
    physical_design: PCBPhysicalDesignProposal | None = None
    schematic_artifact_id: str | None = Field(default=None, min_length=1, max_length=256)
    schematic_artifact_fingerprint: str | None = Field(default=None, min_length=71, max_length=71)
    schematic_content_fingerprint: str | None = Field(default=None, min_length=71, max_length=71)
    workspace_identity: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _physical_input(self) -> PCBProductionProposalInput:
        values = (
            self.schematic_artifact_id,
            self.schematic_artifact_fingerprint,
            self.schematic_content_fingerprint,
            self.workspace_identity,
        )
        if self.physical_design is not None and any(value is None for value in values):
            raise ValueError("typed G4 artifact and workspace identity are required with physical design")
        if self.physical_design is None and any(value is not None for value in values):
            raise ValueError("physical design is required with PCB production input")
        return self


class HardwareSelectionProposalInput(ProjectCatalogApiModel):
    pass


class HardwareSelectionChoiceV2Input(ProjectCatalogApiModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot_id: str
    source_id: str
    source_fingerprint: str

    @model_validator(mode="after")
    def _verify(self):
        from embedded_copilot.engineering_runtime.common import (
            validate_fingerprint,
            validate_identifier,
        )
        validate_identifier(self.slot_id, field="slot_id")
        validate_identifier(self.source_id, field="source_id")
        validate_fingerprint(self.source_fingerprint)
        return self


class HardwareSelectionProposalV2Input(ProjectCatalogApiModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[2]
    request_id: str
    expected_head_revision_id: str | None
    expected_head_selection_id: str | None
    expected_head_selection_fingerprint: str | None
    preview_fingerprint: str
    selections: list[HardwareSelectionChoiceV2Input]

    @model_validator(mode="after")
    def _verify(self):
        from embedded_copilot.engineering_runtime.common import (
            validate_fingerprint,
            validate_identifier,
        )
        validate_identifier(self.request_id, field="request_id")
        validate_fingerprint(self.preview_fingerprint)
        if (self.expected_head_selection_id is None) != (self.expected_head_selection_fingerprint is None) or (self.expected_head_revision_id is not None and self.expected_head_selection_id is None):
            raise ValueError("selection head selector incomplete")
        for field in ("expected_head_revision_id", "expected_head_selection_id"):
            if getattr(self, field) is not None:
                validate_identifier(getattr(self,field), field=field)
        if self.expected_head_selection_fingerprint is not None:
            validate_fingerprint(self.expected_head_selection_fingerprint)
        if not self.selections or len({item.slot_id for item in self.selections}) != len(self.selections):
            raise ValueError("selection choices incomplete or repeated")
        return self


class HardwareSelectionProposalV3Input(HardwareSelectionProposalV2Input):
    schema_version: Literal[3]
    parent_choices: list[HardwareSelectionChoiceV2Input] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _parents(self):
        if len({item.slot_id for item in self.parent_choices}) != len(self.parent_choices):
            raise ValueError("parent choices repeated")
        return self


class HardwareSelectionDecisionV2Input(ProjectCatalogApiModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[2]
    revision_id: str
    selection_id: str
    selection_fingerprint: str
    expected_decision_fingerprint: str | None
    decision: Literal["APPROVED", "REJECTED", "REVOKED"]

    @model_validator(mode="after")
    def _verify(self):
        from embedded_copilot.engineering_runtime.common import (
            validate_fingerprint,
            validate_identifier,
        )
        validate_identifier(self.revision_id, field="revision_id")
        validate_identifier(self.selection_id, field="selection_id")
        validate_fingerprint(self.selection_fingerprint)
        if self.expected_decision_fingerprint is not None:
            validate_fingerprint(self.expected_decision_fingerprint)
        if self.decision == "REVOKED" and self.expected_decision_fingerprint is None:
            raise ValueError("revoke requires approved decision selector")
        return self


class HardwareSelectionDecisionInput(ProjectCatalogApiModel):
    decision: HardwareSelectionDecision


class ProjectSupplyRelationInput(ProjectCatalogApiModel):
    power_domain_id: str
    source_component_refs: tuple[str, ...]
    consumer_component_refs: tuple[str, ...]
    nominal_voltage_v: str
    supply_net_ref: str
    ground_net_ref: str
    evidence_refs: tuple[str, ...]


class ProjectSupplyCandidateInput(ProjectCatalogApiModel):
    design_truth_id: str
    design_truth_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selection_id: str
    selection_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    circuit_id: str
    circuit_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_id: str
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    relations: tuple[ProjectSupplyRelationInput, ...]


class ProjectSupplyReviewInput(ProjectCatalogApiModel):
    candidate_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVED", "REJECTED"]


class SchematicPlanProposalInput(ProjectCatalogApiModel):
    circuit_intent_id: str | None = Field(default=None, min_length=1, max_length=256)
    circuit_intent_revision: str | None = Field(default=None, pattern=r"^r[1-9][0-9]*$")
    circuit_intent_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _complete_selector(self) -> SchematicPlanProposalInput:
        fields = (self.circuit_intent_id, self.circuit_intent_revision, self.circuit_intent_fingerprint)
        if any(value is None for value in fields) and any(value is not None for value in fields):
            raise ValueError("Circuit intent selector must include id, revision, and fingerprint")
        return self


class SchematicPlanProposalV2Input(SchematicPlanProposalInput):
    schema_version: Literal[2]
    request_id: str = Field(min_length=1, max_length=128)
    expected_head_revision_id: str | None = Field(max_length=128)
    expected_head_plan_id: str | None = Field(max_length=128)
    expected_head_plan_fingerprint: str | None = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _complete_head(self) -> SchematicPlanProposalV2Input:
        if (self.expected_head_plan_id is None) != (self.expected_head_plan_fingerprint is None):
            raise ValueError("expected Plan head ID and fingerprint must be paired")
        if self.expected_head_revision_id is not None and self.expected_head_plan_id is None:
            raise ValueError("versioned expected head requires a Plan selector")
        return self


class SchematicPlanDecisionInput(ProjectCatalogApiModel):
    decision: SchematicPlanDecision


class SchematicPlanDecisionV2Input(ProjectCatalogApiModel):
    schema_version: Literal[2]
    revision_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: SchematicPlanDecision


class SchematicPlanRevisionView(ProjectCatalogApiModel):
    schema_version: Literal[2]
    revision_id: str
    revision_number: int
    predecessor_revision_id: str | None
    plan_id: str
    plan_fingerprint: str
    is_current: bool
    currentness: Literal["CURRENT", "STALE", "UNAVAILABLE"]
    planning: dict[str, object]
    receipt_references: tuple[dict[str, object], ...] = ()


class SchematicPlanLegacyView(ProjectCatalogApiModel):
    schema_version: Literal[2]
    revision_id: None
    revision_number: Literal[0]
    predecessor_revision_id: None
    plan_id: str
    plan_fingerprint: str
    is_current: Literal[False]
    currentness: Literal["STALE"]
    planning: dict[str, object]
    receipt_references: tuple[dict[str, object], ...]


class SchematicPlanHistoryView(ProjectCatalogApiModel):
    schema_version: Literal[2]
    head: SchematicPlanRevisionView | None
    legacy: SchematicPlanLegacyView | None
    history: tuple[SchematicPlanRevisionView, ...]


class SchematicPlanCurrentnessView(ProjectCatalogApiModel):
    plan_id: str = Field(min_length=1, max_length=256)
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    currentness: Literal["CURRENT", "STALE", "UNAVAILABLE"]


class SchematicEvidenceOperationInput(ProjectCatalogApiModel):
    kind: Literal["PARSE", "READBACK"]
    library_map_evidence_id: str | None = Field(default=None, min_length=1, max_length=160)
    library_map_evidence_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _library_pair(self) -> SchematicEvidenceOperationInput:
        if (self.library_map_evidence_id is None) != (self.library_map_evidence_fingerprint is None):
            raise ValueError("library map selector pair is incomplete")
        if self.kind != "READBACK" and self.library_map_evidence_id is not None:
            raise ValueError("library map selector requires READBACK")
        return self



class StandardApplicabilityInput(ProjectCatalogApiModel):
    document_uri: str = Field(min_length=1, max_length=1024)
    document_revision: str = Field(min_length=1, max_length=1024)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    locator: ParameterLocator
    mode: Literal["I2C_STANDARD"]
    frequency_max_hz: str | None = None
    rise_time_max_s: str | None = None
    cbus_max_f: str | None = None
    target_net_ids: tuple[str, ...] = Field(min_length=1, max_length=128)
    target_components: tuple[StandardTargetBindingV2, ...] = Field(min_length=1, max_length=128)
    requirement_id: str = Field(min_length=1, max_length=256)
    requirement_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1, max_length=256)
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _fact(self) -> StandardApplicabilityInput:
        if all(value is None for value in (
            self.frequency_max_hz, self.rise_time_max_s, self.cbus_max_f
        )):
            raise ValueError("one standard limit is required")
        return self


class StandardApplicabilityReviewInput(ProjectCatalogApiModel):
    evidence_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVED", "REJECTED"]


class LibraryTerminalMapInput(ProjectCatalogApiModel):
    component_instance_id: str = Field(min_length=1, max_length=256)
    source_record_id: str = Field(min_length=1, max_length=256)
    source_record_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1, max_length=256)
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    symbol_library_id: str = Field(min_length=1, max_length=256)
    symbol_uri: str = Field(min_length=1, max_length=1024)
    symbol_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    footprint_library_id: str = Field(min_length=1, max_length=256)
    footprint_uri: str = Field(min_length=1, max_length=1024)
    footprint_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    terminal_bindings: tuple[LibraryTerminalBindingV2, ...] = Field(min_length=1, max_length=128)


class LibraryTerminalMapReviewInput(ProjectCatalogApiModel):
    evidence_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVED", "REJECTED"]


class SchematicParameterReviewInput(ProjectCatalogApiModel):
    evidence_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVED", "REJECTED"]


class SchematicVerificationInput(ProjectCatalogApiModel):
    intent_id: str = Field(min_length=1, max_length=256)
    intent_revision: str = Field(pattern=r"^r[1-9][0-9]*$")
    intent_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_id: str = Field(min_length=1, max_length=256)
    plan_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    generation_operation_id: str = Field(min_length=1, max_length=256)
    erc_operation_id: str | None = Field(default=None, min_length=1, max_length=256)
    parse_operation_id: str | None = Field(default=None, min_length=1, max_length=256)
    readback_operation_id: str | None = Field(default=None, min_length=1, max_length=256)
    parameter_evidence_ids: tuple[str, ...] = Field(default=(), max_length=128)
    library_map_evidence_ids: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=1)
    library_map_evidence_fingerprints: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=1)

    @model_validator(mode="after")
    def _library_selectors(self) -> SchematicVerificationInput:
        if (self.library_map_evidence_ids is None) != (self.library_map_evidence_fingerprints is None):
            raise ValueError("library map selector pair incomplete")
        if self.library_map_evidence_ids is not None:
            if self.readback_operation_id is None or len(self.library_map_evidence_ids) != len(self.library_map_evidence_fingerprints):
                raise ValueError("library map requires explicit readback selector")
            from embedded_copilot.engineering_runtime.common import (
                validate_fingerprint,
                validate_identifier,
            )
            validate_identifier(self.library_map_evidence_ids[0], field="library_map_evidence_ids")
            validate_fingerprint(self.library_map_evidence_fingerprints[0])
        return self


class SchematicVerificationView(ProjectCatalogApiModel):
    record: SchematicVerificationRecord
    currentness: Literal["CURRENT", "STALE"]


class PCBProposalDecisionInput(ProjectCatalogApiModel):
    decision: PCBProposalDecisionStatus

    @model_validator(mode="after")
    def _terminal(self) -> PCBProposalDecisionInput:
        if self.decision is PCBProposalDecisionStatus.PENDING:
            raise ValueError("terminal PCB proposal decision is required")
        return self


class PCBRepairDecisionInput(ProjectCatalogApiModel):
    repair_id: str = Field(min_length=1, max_length=256)
    proposal_fingerprint: str = Field(min_length=71, max_length=71)
    artifact_fingerprint: str = Field(min_length=71, max_length=71)
    action: PCBRepairDecisionAction
    comment: str = Field(default="", max_length=2048)
    edited_proposal_fingerprint: str | None = Field(default=None, min_length=71, max_length=71)

    @model_validator(mode="after")
    def _repair_action(self) -> PCBRepairDecisionInput:
        if self.action.value == "EDIT_FIX" and self.edited_proposal_fingerprint is None:
            raise ValueError("EDIT_FIX requires edited_proposal_fingerprint")
        if self.action.value != "EDIT_FIX" and self.edited_proposal_fingerprint is not None:
            raise ValueError("edited_proposal_fingerprint is only valid for EDIT_FIX")
        if self.action.value == "WAIVE_WITH_REASON" and not self.comment.strip():
            raise ValueError("WAIVE_WITH_REASON requires a reason")
        return self


class ArtifactIntelligenceInput(ProjectCatalogApiModel):
    """Optional in-memory ZIP input; raw bytes are never persisted."""

    zip_base64: str | None = Field(default=None, max_length=33_554_432)

    @model_validator(mode="after")
    def _input(self) -> ArtifactIntelligenceInput:
        if self.zip_base64 is not None and not self.zip_base64.strip():
            raise ValueError("zip_base64 is invalid")
        return self


class ArtifactFileView(ProjectCatalogApiModel):
    name: str
    type: str
    status: str


class ArtifactCoverageView(ProjectCatalogApiModel):
    schematic: str
    pcb: str
    bom: str
    firmware: str
    gerber: str
    documentation: str
    completeness_percent: int


class ArtifactIntelligenceView(ProjectCatalogApiModel):
    files: tuple[ArtifactFileView, ...] = ()
    coverage: ArtifactCoverageView
    available_evidence: tuple[str, ...] = ()
    evidence_review_status: Literal[
        "UNAVAILABLE", "CANDIDATE", "REVIEWING", "APPROVED", "REJECTED", "SUPERSEDED", "INVALIDATED"
    ] = "UNAVAILABLE"
    missing_evidence: tuple[str, ...] = ()


class DesignTruthProjection(ProjectCatalogApiModel):
    design_revision: str
    origin: Literal["CREATE", "REVERSE"]
    confirmed_mapping: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()


class ProjectLocationRegisterInput(ProjectCatalogApiModel):
    path: str
    display_label: str | None = None


def _selection_state_json(value):
    if value is None or isinstance(value, HardwareSelectionState):
        return value
    if type(value) is not dict:
        raise ValueError("selection state must be canonical JSON")
    return HardwareSelectionState.model_validate_json(json.dumps(value,ensure_ascii=False,allow_nan=False))


class SelectionRevisionHeadView(ProjectCatalogApiModel):
    revision_id: str
    revision_number: int = Field(ge=1)
    selection_id: str
    selection_fingerprint: str
    fingerprint: str
    status: Literal["REVIEW", "APPROVED", "REJECTED", "REVOKED"]
    decision_fingerprint: str | None
    currentness: Literal["CURRENT", "STALE"]
    reason: Literal["NON_HEAD", "SOURCE_OR_UPSTREAM_CHANGED"] | None


class SelectionRevisionView(ProjectCatalogApiModel):
    kind: Literal["REVISION"]
    schema_version: Literal[2]
    head: SelectionRevisionHeadView
    selection: HardwareSelectionState | None
    _canonical_selection = field_validator("selection",mode="before")(_selection_state_json)
    source_publications: tuple[SourcePublicationBindingV1, ...]


class SelectionLegacyView(ProjectCatalogApiModel):
    kind: Literal["LEGACY"]
    schema_version: Literal[2]
    expected_head: tuple[str | None, str | None, str | None]
    selection: HardwareSelectionState | None
    _canonical_selection = field_validator("selection",mode="before")(_selection_state_json)
    currentness: Literal["UNVERIFIED"]
    reason: Literal["LEGACY_UNVERSIONED"]


class SelectionPreviewView(ProjectCatalogApiModel):
    kind: Literal["PREVIEW"]
    schema_version: Literal[2]
    project_id: str
    conversation_id: str
    expected_head: tuple[str | None, str | None, str | None]
    conversation_fingerprint: str
    architecture_review_fingerprint: str
    engineering_plan_review_fingerprint: str
    required_slots: tuple[str, ...]
    choices: tuple[HardwareSelectionChoiceV2Input, ...]
    source_publications: tuple[SourcePublicationBindingV1, ...]
    preview_fingerprint: str


class SelectionPreviewV3View(SelectionPreviewView):
    schema_version: Literal[3]
    root_slots: tuple[str, ...]
    selected_parents: tuple[HardwareSelectionChoiceV2Input, ...]


class SelectionHistoryView(ProjectCatalogApiModel):
    kind: Literal["HISTORY"]
    schema_version: Literal[2]
    revisions: tuple[SelectionRevisionView, ...]
