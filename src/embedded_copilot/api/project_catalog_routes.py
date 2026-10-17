# ruff: noqa: B008, BLE001
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from embedded_copilot.api.copilot_execution_models import (
    OperationStatusResponse,
    status_response,
)
from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.api.project_catalog_models import (
    ArtifactCoverageView,
    ArtifactFileView,
    ArtifactIntelligenceInput,
    ArtifactIntelligenceView,
    BrownfieldBuildDecisionInput,
    BrownfieldBuildProposalInput,
    CircuitIntentDecisionInput,
    CircuitIntentDecisionView,
    CircuitIntentPreviewInput,
    CircuitIntentPreviewView,
    ConversationCreateInput,
    ConversationResumeInput,
    DesignTruthProjection,
    DrcBaselineOperationCreateInput,
    EDAImportCommitInput,
    EDAImportPreviewInput,
    EDAImportPreviewView,
    EDAImportRevisionPage,
    EDAImportRevisionView,
    ExecutionAuthorizationDecisionInput,
    FeatureDecisionInput,
    FirmwareSourceProposalDecisionInput,
    HardwareSelectionChoiceV2Input,
    HardwareSelectionDecisionInput,
    HardwareSelectionDecisionV2Input,
    HardwareSelectionProposalInput,
    HardwareSelectionProposalV2Input,
    HardwareSelectionProposalV3Input,
    LibraryTerminalMapInput,
    LibraryTerminalMapReviewInput,
    NativeMoveCandidateInput,
    NativeMoveCandidateView,
    NativeMoveMappingReviewInput,
    NativeMoveMappingReviewView,
    NativeMoveOperationCreateInput,
    PCBProductionProposalInput,
    PCBProposalDecisionInput,
    PCBRepairDecisionInput,
    PredesignCircuitDecisionInput,
    PredesignCircuitDecisionView,
    PredesignCircuitPreviewInput,
    PredesignCircuitPreviewView,
    ProjectCreateInput,
    ProjectLocationRegisterInput,
    ProjectPreviewInput,
    ProjectSupplyCandidateInput,
    ProjectSupplyReviewInput,
    ReferenceProjectDecisionInput,
    RequirementChangeProposalInput,
    RequirementFinalConfirmationInput,
    RequirementSuggestionDecisionInput,
    ResearchIntentRefinementInput,
    SchematicEvidenceOperationInput,
    SchematicParameterReviewInput,
    SchematicPlanCurrentnessView,
    SchematicPlanDecisionInput,
    SchematicPlanDecisionV2Input,
    SchematicPlanHistoryView,
    SchematicPlanLegacyView,
    SchematicPlanProposalInput,
    SchematicPlanProposalV2Input,
    SchematicPlanRevisionView,
    SchematicProposalDecisionInput,
    SchematicRepairDecisionInput,
    SchematicVerificationInput,
    SchematicVerificationView,
    SelectionHistoryView,
    SelectionLegacyView,
    SelectionPreviewV3View,
    SelectionPreviewView,
    SelectionRevisionView,
    StandardApplicabilityInput,
    StandardApplicabilityReviewInput,
)
from embedded_copilot.application.circuit_publication import (
    CircuitDecisionCommand,
    CircuitPreviewCommand,
    CircuitPublicationService,
    PredesignDecisionCommand,
    PredesignPreviewCommand,
)
from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
    EngineeringObjectFlowError,
    EngineeringObjectFlowErrorCode,
)
from embedded_copilot.application.task_model_authority import CatalogTaskAuthoritySource
from embedded_copilot.engineering_copilot import SchematicRepairDecision
from embedded_copilot.engineering_copilot.schematic_evidence import ParameterCandidate
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
)
from embedded_copilot.engineering_review import (
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
)
from embedded_copilot.model_gateway.contracts import (
    EngineeringContextSnapshot,
    ModelMessage,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    InvocationMode,
    InvocationStatus,
    ModelInvocationPayload,
    ModelInvocationRequest,
    ModelInvocationTraceContext,
    TaskAuthorityFailure,
)
from embedded_copilot.project_catalog import (
    ArtifactIntelligenceRejected,
    EvidenceReviewRequired,
    ExecutionAuthorizationDecisionRejected,
    FirmwareSourceGenerationRejected,
    HardwareSelectionRejected,
    PCBProductionRejected,
    ProjectCatalogConflict,
    ProjectCatalogError,
    ProjectCatalogExpired,
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
    ProjectLocationInvalid,
    ProjectLocationPermissionDenied,
    ProjectLocationUnsafe,
    ProjectTargetNotWritable,
    RequirementChangeRejected,
    RequirementSuggestionDecisionRejected,
    SchematicPlanningRejected,
    SchematicProductionRejected,
)
from embedded_copilot.project_catalog.models import (
    CircuitProposalCandidate,
    CircuitPublication,
    PredesignCircuitCandidate,
)


class ProjectCatalogService(Protocol):
    def locations(self, *, tenant_id: str, requester: str): ...
    def register_location(self, *, path: str, display_label: str | None, tenant_id: str, requester: str): ...
    def location_audit(self, location_id: str, *, tenant_id: str): ...
    def directories(self, location_id: str, *, tenant_id: str, requester: str): ...
    def preview(self, value, *, tenant_id: str, requester: str): ...
    def create(self, preview_id: str, preview_fingerprint: str, *, confirmed_at, tenant_id: str, requester: str): ...
    def projects(self, tenant_id: str): ...
    def get_project(self, project_id: str, *, tenant_id: str): ...
    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str): ...
    def create_eda_import_preview(self, project_id: str, *, tenant_id: str, requester: str, backend: str, native_entry_relative_path: str, selected_native_entries: tuple[str, ...], expected_head_revision_id: str | None, expected_head_revision_fingerprint: str | None): ...
    def commit_eda_import_preview(self, project_id: str, preview_id: str, preview_fingerprint: str, *, tenant_id: str, requester: str): ...
    def eda_import_revision(self, project_id: str, revision_id: str, fingerprint: str, *, tenant_id: str, requester: str): ...
    def eda_import_revisions(self, project_id: str, *, tenant_id: str, requester: str, backend: str, native_entry_relative_path: str, cursor: str | None = None): ...
    def create_native_move_candidate(self, project_id: str, *, tenant_id: str, requester: str, import_revision_id: str, import_revision_fingerprint: str, workspace_target_id: str, target_native_uuid: str, after_x_mm: float, after_y_mm: float): ...
    def native_move_candidate(self, project_id: str, candidate_id: str, candidate_fingerprint: str, *, tenant_id: str, requester: str): ...
    def review_native_move_candidate(self, project_id: str, candidate_id: str, candidate_fingerprint: str, *, tenant_id: str, requester: str, decision: str, design_intent: str): ...
    def native_mapping_review(self, project_id: str, review_id: str, review_fingerprint: str, *, tenant_id: str, requester: str): ...
    def create_native_move_operation(self, project_id: str, candidate_id: str, candidate_fingerprint: str, review_id: str, review_fingerprint: str, *, tenant_id: str, requester: str, execution: object): ...
    def brownfield_build_preview(self, project_id: str, *, tenant_id: str, requester: str): ...
    def brownfield_build_context(self, project_id: str, *, tenant_id: str, requester: str): ...
    def propose_brownfield_build(self, project_id: str, *, tenant_id: str, requester: str, workspace_fingerprint: str, target_platform: str, mcu_family: str, mcu_exact_part: str, source_layout: tuple[str, ...], configuration: object): ...
    def decide_brownfield_build(self, project_id: str, *, tenant_id: str, requester: str, proposal_fingerprint: str, decision: object): ...
    def delete_project(self, project_id: str, *, project_fingerprint: str, tenant_id: str, requester: str): ...
    def context(self, project_id: str, *, tenant_id: str): ...
    def create_conversation(self, project_id: str, *, tenant_id: str, requester: str): ...
    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor: str | None, limit: int): ...
    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def delete_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def resume_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def decide_requirement_suggestion(self, project_id: str, conversation_id: str, suggestion_id: str, action, *, tenant_id: str, requester: str, decided_at: datetime, modified_requirement: str | None = None): ...
    def decide_reference_project(self, project_id: str, conversation_id: str, project_identity: str, decision, *, tenant_id: str, requester: str, decided_at: datetime, selected_features: tuple[str, ...] = ()): ...
    def decide_feature(self, project_id: str, conversation_id: str, project_identity: str, feature: str, action, *, tenant_id: str, requester: str, decided_at: datetime, value: str | None = None): ...
    def propose_requirement_changes(self, project_id: str, conversation_id: str, utterance: str, *, tenant_id: str, requester: str, created_at: datetime): ...
    def confirm_requirement_changes(self, project_id: str, conversation_id: str, proposal_id: str, *, tenant_id: str, requester: str, confirmed: bool, proposal_fingerprint: str, confirmed_at: datetime): ...
    def refine_research_intent(self, project_id: str, conversation_id: str, product_positioning: str, negative_constraints: tuple[str, ...], *, tenant_id: str, requester: str, created_at: datetime): ...
    def scan_artifact_intelligence(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, zip_content: bytes | None = None): ...
    def attach_artifact_evidence_review(self, project_id: str, conversation_id: str, review_id: str, *, tenant_id: str, requester: str): ...
    def build_reverse_design_truth(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, review: object | None = None): ...
    def decide_execution_authorization(self, project_id: str, conversation_id: str, request_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime): ...
    def decide_firmware_source_generation(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime, bridge: object | None): ...
    def create_hardware_selection_proposal(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def create_hardware_model_candidate_proposal(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, candidate_selections: tuple[tuple[str, str, str], ...], authority_source: object, authority_reference: object): ...
    def model_operation_candidate_context(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, operation_id: str, authority_source: object, authority_reference: object): ...
    def decide_hardware_selection(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime): ...
    def create_project_supply_candidate(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, selectors: dict[str, object], relations: tuple[dict[str, object], ...]): ...
    def review_project_supply_candidate(self, project_id: str, conversation_id: str, candidate_id: str, candidate_fingerprint: str, decision: str, *, tenant_id: str, requester: str): ...
    def project_supply(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def create_schematic_plan(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, circuit_intent_selector: tuple[str, str, str] | None = None): ...
    def create_schematic_plan_v2(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, request_id: str, expected_head: tuple[str | None, str | None, str | None], circuit_intent_selector: tuple[str, str, str] | None = None): ...
    def create_schematic_model_candidate_proposal(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, review_items: tuple[tuple[str, tuple[str, ...]], ...], authority_source: object, authority_reference: object, expected_project_supply_fingerprints: tuple[str, str] | None = None): ...
    def decide_schematic_plan(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime): ...
    def decide_schematic_plan_v2(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, revision_id: str, plan_id: str, plan_fingerprint: str, decided_at: datetime): ...
    def schematic_plan_revision_history(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def schematic_plan_revision(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, revision_id: str, plan_id: str, plan_fingerprint: str): ...
    def schematic_plan_legacy_snapshot(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, plan_id: str | None = None, plan_fingerprint: str | None = None): ...
    def schematic_plan_currentness(self, project_id: str, conversation_id: str, plan_id: str, plan_fingerprint: str, *, tenant_id: str, requester: str) -> tuple[str, str, str]: ...
    def record_schematic_verification(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, intent_id: str, intent_revision: str, intent_fingerprint: str, plan_id: str, plan_fingerprint: str, artifact_fingerprint: str, generation_operation_id: str, erc_operation_id: str | None = None, parse_operation_id: str | None = None, readback_operation_id: str | None = None, parameter_evidence_ids: tuple[str, ...] = (), library_map_evidence_ids: tuple[str, ...] | None = None, library_map_evidence_fingerprints: tuple[str, ...] | None = None) -> object: ...
    def create_schematic_evidence_operation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, kind: str, library_map_evidence_id: str | None = None, library_map_evidence_fingerprint: str | None = None) -> object: ...
    def propose_schematic_parameter(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, candidate: ParameterCandidate) -> object: ...
    def review_schematic_parameter(self, project_id: str, evidence_id: str, *, tenant_id: str, requester: str, evidence_fingerprint: str, decision: str) -> object: ...
    def schematic_parameter(self, project_id: str, evidence_id: str, *, tenant_id: str, requester: str) -> tuple[object, str]: ...
    def create_standard_applicability(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, values: dict[str, object]) -> object: ...
    def review_standard_applicability(self, project_id: str, evidence_id: str, *, tenant_id: str, requester: str, evidence_fingerprint: str, decision: str) -> object: ...
    def standard_applicability(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> tuple[tuple[object, str], ...]: ...
    def schematic_verification(self, project_id: str, record_id: str, *, tenant_id: str, requester: str): ...
    def schematic_verifications(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, limit: int = 50): ...
    def decide_schematic_production(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime, bridge: object | None): ...
    def decide_schematic_repair(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, decision: object): ...
    def create_pcb_production_proposal(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str): ...
    def create_pcb_production_input(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, physical_design: object, schematic_artifact_id: str, schematic_artifact_fingerprint: str, schematic_content_fingerprint: str, workspace_identity: str, target_relative_path: str = "hardware/main.kicad_pcb", requested_at: datetime | None = None): ...
    def decide_pcb_production(self, project_id: str, conversation_id: str, decision, *, tenant_id: str, requester: str, decided_at: datetime, bridge: object | None): ...
    def decide_pcb_repair(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str, decision: object): ...


router = APIRouter(prefix="/api/v1/copilot/projects", tags=["copilot-projects"])


def _service(request: Request) -> ProjectCatalogService | None:
    return getattr(request.app.state, "project_catalog_runtime", None)


def _requester(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.requester


def _tenant(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.tenant_id


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "trace_id": request.state.trace_id})


_SCHEMATIC_BLOCKED_REASONS = frozenset({
    "POWER_SOURCE_UNRESOLVED", "POWER_NET_INCOMPLETE",
    "INTERFACE_PROTOCOL_UNRESOLVED", "PIN_BINDING_UNRESOLVED",
})


def _schematic_blocked(request: Request, reason_code: str) -> JSONResponse:
    return JSONResponse(status_code=409, content={
        "status": "BLOCKED", "operation_id": "SCHEMATIC_DESIGN_PROPOSAL",
        "blocking_reasons": [reason_code], "reason_code": reason_code,
        "error": reason_code, "trace_id": request.state.trace_id,
    })


def _map(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, SchematicPlanningBlocked):
        return _error(request, 422, "SCHEMATIC_PLANNING_REJECTED")
    if isinstance(error, EngineeringObjectFlowError):
        if error.code in {EngineeringObjectFlowErrorCode.CONTEXT_UNAVAILABLE, EngineeringObjectFlowErrorCode.EVIDENCE_UNAVAILABLE}:
            return _error(request, 503, f"CIRCUIT_{error.code.value}")
        if error.code is EngineeringObjectFlowErrorCode.STALE_CONTEXT:
            return _error(request, 409, "CIRCUIT_STALE_CONTEXT")
        return _error(request, 422, f"CIRCUIT_{error.code.value}")
    if isinstance(error, OSError):
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    if isinstance(error, ProjectCatalogExpired):
        return _error(request, 410, "PROJECT_PREVIEW_EXPIRED")
    if isinstance(error, EvidenceReviewRequired):
        return _error(request, 422, "EVIDENCE_REVIEW_REQUIRED")
    if isinstance(error, ArtifactIntelligenceRejected):
        return _error(request, 422, "ARTIFACT_INPUT_REJECTED")
    if isinstance(error, ExecutionAuthorizationDecisionRejected):
        return _error(request, 422, "EXECUTION_AUTHORIZATION_DECISION_REJECTED")
    if isinstance(error, FirmwareSourceGenerationRejected):
        return _error(request, 422, "FIRMWARE_SOURCE_GENERATION_REJECTED")
    if isinstance(error, HardwareSelectionRejected):
        return _error(request, 422, "HARDWARE_SELECTION_REJECTED")
    if isinstance(error, SchematicPlanningRejected):
        return _error(request, 422, "SCHEMATIC_PLANNING_REJECTED")
    if isinstance(error, RequirementSuggestionDecisionRejected):
        return _error(request, 422, "REQUIREMENT_SUGGESTION_DECISION_REJECTED")
    if isinstance(error, RequirementChangeRejected):
        return _error(request, 422, "REQUIREMENT_CHANGE_REJECTED")
    if isinstance(error, SchematicProductionRejected):
        return _error(request, 422, "SCHEMATIC_PRODUCTION_REJECTED")
    if isinstance(error, PCBProductionRejected):
        return _error(request, 422, "PCB_PRODUCTION_REJECTED")
    if isinstance(error, ProjectCatalogConflict):
        return _error(request, 409, "PROJECT_CATALOG_CONFLICT")
    if isinstance(error, ProjectLocationInvalid):
        return _error(request, 422, "INVALID_DIRECTORY")
    if isinstance(error, ProjectTargetNotWritable):
        return _error(request, 403, "PROJECT_TARGET_NOT_WRITABLE")
    if isinstance(error, ProjectLocationPermissionDenied):
        return _error(request, 403, "PERMISSION_DENIED")
    if isinstance(error, ProjectLocationUnsafe):
        return _error(request, 422, "UNSAFE_LOCATION")
    if isinstance(error, ProjectCatalogForbidden):
        return _error(request, 403, "PROJECT_CATALOG_FORBIDDEN")
    if isinstance(error, ProjectCatalogNotFound):
        return _error(request, 404, "PROJECT_NOT_FOUND")
    if isinstance(error, ProjectCatalogRejected):
        return _error(request, 422, "PROJECT_CATALOG_REJECTED")
    if isinstance(error, ProjectCatalogUnavailable):
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    if isinstance(error, ProjectCatalogError):
        return _error(request, 400, "PROJECT_CATALOG_REJECTED")
    raise error


def _eda_import_map(request: Request, error: Exception) -> JSONResponse:
    code = str(error)
    if isinstance(error, ProjectCatalogExpired):
        return _error(request, 410, "IMPORT_PREVIEW_EXPIRED")
    if isinstance(error, ProjectCatalogConflict) and code in {
        "IMPORT_HEAD_CHANGED", "SOURCE_SET_CHANGED", "PARSER_RULESET_CHANGED", "IMPORT_PROJECT_CHANGED"
    }:
        return _error(request, 409, code)
    if isinstance(error, ProjectCatalogRejected) and code in {"IMPORT_INPUT_REJECTED", "IMPORT_PREVIEW_INVALID"}:
        return _error(request, 422, code)
    if isinstance(error, ProjectCatalogUnavailable) and code == "IMPORT_SOURCE_UNAVAILABLE":
        return _error(request, 503, code)
    return _map(request, error)


def _eda_import_response(model: type[EDAImportPreviewView | EDAImportRevisionView | EDAImportRevisionPage], value: dict):
    checked = model.model_validate({name: value[name] for name in model.model_fields if name in value})
    if value.get("project_coverage") is not None:
        # Preserve exact nullable fields inside the strict coverage envelope.
        body = checked.model_dump(mode="json")
        for name in ("status", "commit_outcome"):
            if body.get(name) is None:
                body.pop(name, None)
        return JSONResponse(content=body)
    return checked


def _native_move_candidate_response(value: dict) -> NativeMoveCandidateView:
    source, target = value["binding"]["source"], value["binding"]["target"]
    workspace = value["binding"]["workspace"]
    return NativeMoveCandidateView.model_validate({
        "project_id": value["project_id"], "backend": "KICAD",
        "candidate_id": value["candidate_id"], "candidate_fingerprint": value["candidate_fingerprint"],
        "import_revision_id": value["import_revision_id"], "import_revision_fingerprint": value["import_revision_fingerprint"],
        "source_set_fingerprint": source["source_set_fingerprint"], "manifest_fingerprint": source["manifest_fingerprint"],
        "target_native_uuid": target["native_uuid"],
        "native_entry_relative_path": source["native_entry_relative_path"],
        "parser_ruleset_id": source["parser_ruleset_id"], "parser_ruleset_fingerprint": source["parser_ruleset_fingerprint"],
        "supported_subset_id": source["supported_subset_id"], "supported_subset_fingerprint": source["supported_subset_fingerprint"],
        "manifest": source["manifest"], "issues": source["issues"],
        "workspace_target_id": workspace["target_id"], "workspace_project_fingerprint": workspace["project_fingerprint"],
        "workspace_target_fingerprint": workspace["target_fingerprint"],
        "native_locator": target["native_locator"], "before": {"x_mm": target["before_x_mm"], "y_mm": target["before_y_mm"]},
        "pcb_relative_path": target["pcb_relative_path"], "pcb_sha256": target["pcb_sha256"],
        "rotation_degrees": target["rotation_degrees"],
        "after": {"x_mm": target["after_x_mm"], "y_mm": target["after_y_mm"]},
        "edits": target["edits"],
        "diff_fingerprint": target["diff_fingerprint"], "preservation_status": value["binding"]["preservation_status"],
        "can_request_operation_approval": value.get("can_request_operation_approval", False),
    })


def _native_mapping_review_response(value: dict) -> NativeMoveMappingReviewView:
    return NativeMoveMappingReviewView.model_validate({
        **{name: value[name] for name in ("mapping_review_id", "mapping_review_fingerprint", "candidate_id",
                                         "candidate_fingerprint", "reviewer_id", "decision", "created_at")},
        "design_intent": value["binding"]["design_intent"],
    })


def _native_move_map(request: Request, error: Exception) -> JSONResponse:
    code = str(error)
    if isinstance(error, ProjectCatalogExpired) and code == "NATIVE_MAPPING_REVIEW_EXPIRED":
        return _error(request, 410, code)
    if isinstance(error, ProjectCatalogConflict) and code in {"SOURCE_STALE", "WORKSPACE_STALE", "NATIVE_MOVE_REVIEW_CONFLICT"}:
        return _error(request, 409, code)
    if isinstance(error, ProjectCatalogRejected) and code in {"PRESERVATION_UNPROVEN", "NATIVE_MOVE_FORMAT_UNSUPPORTED", "NATIVE_MOVE_INPUT_REJECTED", "NATIVE_MOVE_REVIEW_INVALID", "NATIVE_MAPPING_REVIEW_INVALID", "NATIVE_TARGET_UNAVAILABLE"}:
        return _error(request, 422, code)
    return _map(request, error)


def _circuit_service(request: Request) -> CircuitPublicationService | None:
    injected = getattr(request.app.state, "circuit_publication_service", None)
    if isinstance(injected, CircuitPublicationService):
        return injected
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    chat = getattr(request.app.state, "desktop_copilot_chat_runtime", None)
    execution = getattr(request.app.state, "execution_operation_port", None)
    if catalog is None or chat is None or execution is None:
        return None
    application = getattr(chat, "application_runtime", None)
    if application is None:
        return None
    try:
        return CircuitPublicationService(catalog=catalog, application=application, execution=execution)
    except TypeError:
        return None


def _circuit_map(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, TypeError):
        return _error(request, 503, "CIRCUIT_PUBLICATION_UNAVAILABLE")
    if isinstance(error, ValueError) and not isinstance(error, (ProjectCatalogError, EngineeringObjectFlowError)):
        return _error(request, 422, "CIRCUIT_INPUT_REJECTED")
    return _map(request, error)


def _circuit_preview_view(value: CircuitProposalCandidate | PredesignCircuitCandidate) -> CircuitIntentPreviewView | PredesignCircuitPreviewView:
    proposed = value.proposal.proposed_object
    if isinstance(value, PredesignCircuitCandidate):
        return PredesignCircuitPreviewView(
            project_id=value.project_id, conversation_id=value.conversation_id,
            turn_id=value.turn_id, proposal_id=value.proposal.proposal_id,
            proposal_fingerprint=value.proposal.fingerprint, status=value.status.value,
            object_id=value.object_id, revision=proposed.revision,
            base_revision=value.base_revision, base_fingerprint=value.base_fingerprint,
            source_fingerprint=value.proposal.context_fingerprint,
            source_snapshot=value.source_snapshot.model_dump(mode="json"),
            circuit_intent=CircuitIntentSpec.model_validate(
                {name: getattr(proposed, name) for name in CircuitIntentSpec.model_fields}, strict=False,
            ),
            created_at=value.created_at,
        )
    return CircuitIntentPreviewView(
        project_id=value.project_id, conversation_id=value.conversation_id, turn_id=value.turn_id,
        proposal_id=value.proposal.proposal_id, proposal_fingerprint=value.proposal.fingerprint,
        status=value.status.value, object_id=value.object_id, revision=proposed.revision,
        base_revision=value.base_revision, base_fingerprint=value.base_fingerprint,
        context_fingerprint=value.context_fingerprint, source_fingerprint=value.source_fingerprint,
        evidence_receipt_id=value.evidence_reference.receipt_id,
        evidence_receipt_fingerprint=value.evidence_reference.receipt_fingerprint,
        circuit_intent=CircuitIntentSpec.model_validate(
            {name: getattr(proposed, name) for name in CircuitIntentSpec.model_fields}, strict=False
        ),
        created_at=value.created_at,
    )


def _circuit_decision_view(
    value: CircuitProposalCandidate | PredesignCircuitCandidate | CircuitPublication,
    *, predesign: bool = False,
) -> CircuitIntentDecisionView | PredesignCircuitDecisionView:
    view_type = PredesignCircuitDecisionView if predesign else CircuitIntentDecisionView
    if isinstance(value, CircuitPublication):
        return view_type(
            project_id=value.project_id, proposal_id=value.proposal.proposal_id,
            proposal_fingerprint=value.proposal.fingerprint, status="APPLIED",
            object_id=value.circuit.object_id, revision=value.circuit.revision,
            object_fingerprint=value.circuit.fingerprint,
            approval_id=value.approval.approval_id, approval_fingerprint=value.approval.fingerprint,
        )
    if value.decision is None:
        raise ProjectCatalogRejected("Circuit decision is unavailable")
    return view_type(
        project_id=value.project_id, proposal_id=value.proposal.proposal_id,
        proposal_fingerprint=value.proposal.fingerprint, status="REJECTED",
        approval_id=value.decision.approval_id,
        approval_fingerprint=value.decision.fingerprint,
    )


def _artifact_view(snapshot: object | None) -> ArtifactIntelligenceView:
    if snapshot is None:
        return ArtifactIntelligenceView(
            coverage=ArtifactCoverageView(
                schematic="MISSING", pcb="MISSING", bom="MISSING", firmware="MISSING",
                gerber="MISSING", documentation="MISSING", completeness_percent=0,
            )
        )
    inventory = snapshot.inventory
    bundle = snapshot.engineering_evidence_bundle
    return ArtifactIntelligenceView(
        files=tuple(
            ArtifactFileView(
                name=item.name,
                type=item.type.value,
                status=item.status.value,
            )
            for item in inventory.artifacts
        ),
        coverage=ArtifactCoverageView(
            schematic=snapshot.coverage.schematic_status.value,
            pcb=snapshot.coverage.pcb_status.value,
            bom=snapshot.coverage.bom_status.value,
            firmware=snapshot.coverage.firmware_status.value,
            gerber=snapshot.coverage.gerber_status.value,
            documentation=snapshot.coverage.documentation_status.value,
            completeness_percent=round(snapshot.coverage.completeness_score * 100),
        ),
        available_evidence=()
        if bundle is None
        else tuple(item.claim for item in bundle.evidence),
        evidence_review_status=(
            "UNAVAILABLE" if bundle is None else bundle.status.value
        ),
        missing_evidence=snapshot.coverage.missing_recommendations,
    )


def _truth_view(truth: object) -> DesignTruthProjection:
    component_names = {item.component_id: item.name for item in truth.components}
    def component(value: str) -> str:
        return component_names.get(value, "组件")

    confirmed: list[str] = []
    confirmed.extend(f"组件：{item.name}" for item in truth.components)
    confirmed.extend(
        f"接口：{component(item.source)} → {component(item.target)}（{item.protocol}）"
        for item in truth.interfaces
    )
    confirmed.extend(
        f"信号：{item.name}（{component(item.source)} → {', '.join(component(target) for target in item.targets)}）"
        for item in truth.signals
    )
    confirmed.extend(
        f"引脚：{component(item.component_id)} · {item.pin_name}（{item.function}）"
        for item in truth.pins
    )
    confirmed.extend(f"外设：{item.peripheral}（{item.function}）" for item in truth.peripherals)
    confirmed.extend(f"网络：{item.net_name}（{item.purpose}）" for item in truth.nets)
    confirmed.extend(f"固件：{item.firmware_symbol} → {item.function}" for item in truth.firmware_bindings)
    unavailable: list[str] = []
    if not truth.pins and not truth.peripherals and not truth.nets:
        unavailable.append("暂无已确认硬件映射")
    if not truth.firmware_bindings:
        unavailable.append("没有 Firmware 证据")
    return DesignTruthProjection(
        design_revision=truth.design_revision,
        origin=truth.origin.value,
        confirmed_mapping=tuple(confirmed[:32]),
        unavailable=tuple(unavailable),
    )


@router.get("/locations")
def list_locations(request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.locations(tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.post("/locations", status_code=201)
def register_location(payload: ProjectLocationRegisterInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.register_location(path=payload.path, display_label=payload.display_label, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("/locations/{location_id}/audit")
def audit_location(location_id: str, request: Request, tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.location_audit(location_id, tenant_id=tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get("/locations/{location_id}/directories")
def list_directories(location_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.directories(location_id, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("")
def list_projects(request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        visible_projects = []
        for project in service.projects(tenant_id):
            try:
                visible_projects.append(service.get_project_for_requester(project.project_id, tenant_id=tenant_id, requester=requester))
            except ProjectCatalogForbidden:
                continue
        return tuple(visible_projects)
    except Exception as error:
        return _map(request, error)


@router.post("/previews")
def preview_project(payload: ProjectPreviewInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.preview(payload.to_request(), tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.post("", status_code=202)
def create_project(payload: ProjectCreateInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.create(payload.preview_id, payload.preview_fingerprint, confirmed_at=payload.confirmed_at, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/eda-import-previews", response_model=EDAImportPreviewView)
def preview_eda_import(
    project_id: str, payload: EDAImportPreviewInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        value = service.create_eda_import_preview(
            project_id, tenant_id=identity.tenant_id, requester=identity.requester,
            backend=payload.backend, native_entry_relative_path=payload.native_entry_relative_path,
            selected_native_entries=tuple(payload.selected_native_entries),
            expected_head_revision_id=payload.expected_head_revision_id,
            expected_head_revision_fingerprint=payload.expected_head_revision_fingerprint,
        )
        return _eda_import_response(EDAImportPreviewView, value)
    except Exception as error:
        return _eda_import_map(request, error)


@router.post("/{project_id}/eda-imports", response_model=EDAImportRevisionView)
def commit_eda_import(
    project_id: str, payload: EDAImportCommitInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        value = service.commit_eda_import_preview(
            project_id, payload.preview_id, payload.preview_fingerprint,
            tenant_id=identity.tenant_id, requester=identity.requester,
        )
        return _eda_import_response(EDAImportRevisionView, value)
    except Exception as error:
        return _eda_import_map(request, error)


@router.get("/{project_id}/eda-imports", response_model=EDAImportRevisionPage)
def list_eda_imports(
    project_id: str, request: Request, backend: str, native_entry_relative_path: str,
    cursor: str | None = None, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        value = service.eda_import_revisions(
            project_id, tenant_id=identity.tenant_id, requester=identity.requester,
            backend=backend, native_entry_relative_path=native_entry_relative_path, cursor=cursor,
        )
        return _eda_import_response(EDAImportRevisionPage, value)
    except Exception as error:
        return _eda_import_map(request, error)


@router.get("/{project_id}/eda-imports/{import_revision_id}", response_model=EDAImportRevisionView, response_model_exclude_none=True)
def get_eda_import(
    project_id: str, import_revision_id: str, request: Request,
    fingerprint: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        value = service.eda_import_revision(
            project_id, import_revision_id, fingerprint,
            tenant_id=identity.tenant_id, requester=identity.requester,
        )
        return _eda_import_response(EDAImportRevisionView, value)
    except Exception as error:
        return _eda_import_map(request, error)


@router.post("/{project_id}/eda-native/footprint-move-candidates", response_model=NativeMoveCandidateView)
def create_native_move_candidate(
    project_id: str, payload: NativeMoveCandidateInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        value = service.create_native_move_candidate(
            project_id, tenant_id=identity.tenant_id, requester=identity.requester,
            import_revision_id=payload.import_revision_id,
            import_revision_fingerprint=payload.import_revision_fingerprint,
            workspace_target_id=payload.workspace_target_id, target_native_uuid=payload.target_native_uuid,
            after_x_mm=payload.after.x_mm, after_y_mm=payload.after.y_mm,
        )
        return _native_move_candidate_response(value)
    except Exception as error:
        return _native_move_map(request, error)


@router.get("/{project_id}/eda-native/footprint-move-candidates/{candidate_id}", response_model=NativeMoveCandidateView)
def get_native_move_candidate(
    project_id: str, candidate_id: str, request: Request,
    fingerprint: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _native_move_candidate_response(service.native_move_candidate(
            project_id, candidate_id, fingerprint, tenant_id=identity.tenant_id, requester=identity.requester,
        ))
    except Exception as error:
        return _native_move_map(request, error)


@router.post("/{project_id}/eda-native/footprint-move-candidates/{candidate_id}/mapping-review", response_model=NativeMoveMappingReviewView)
def review_native_move_candidate(
    project_id: str, candidate_id: str, payload: NativeMoveMappingReviewInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _native_mapping_review_response(service.review_native_move_candidate(
            project_id, candidate_id, payload.candidate_fingerprint,
            tenant_id=identity.tenant_id, requester=identity.requester,
            decision=payload.decision, design_intent=payload.design_intent,
        ))
    except Exception as error:
        return _native_move_map(request, error)


@router.get("/{project_id}/eda-native/mapping-reviews/{review_id}", response_model=NativeMoveMappingReviewView)
def get_native_mapping_review(
    project_id: str, review_id: str, request: Request,
    fingerprint: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _native_mapping_review_response(service.native_mapping_review(
            project_id, review_id, fingerprint, tenant_id=identity.tenant_id, requester=identity.requester,
        ))
    except Exception as error:
        return _native_move_map(request, error)


@router.post("/{project_id}/eda-native/drc-baseline-operations", response_model=OperationStatusResponse, status_code=201)
def create_drc_baseline_operation(
    project_id: str, payload: DrcBaselineOperationCreateInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        prepare = getattr(service, "create_drc_baseline_operation", None)
        if not callable(prepare):
            return _error(request, 409, "DRC_BASELINE_UNAVAILABLE")
        status = prepare(project_id, tenant_id=identity.tenant_id, requester=identity.requester,
            import_revision_id=payload.import_revision_id, import_revision_fingerprint=payload.import_revision_fingerprint,
            pcb_relative_path=payload.pcb_relative_path,
            execution=getattr(request.app.state, "execution_operation_port", None))
        return status_response(status, target_id="project-root")
    except ProjectCatalogUnavailable as error:
        if str(error) == "DRC_BASELINE_UNAVAILABLE":
            return _error(request, 409, "DRC_BASELINE_UNAVAILABLE")
        return _map(request, error)
    except ProjectCatalogRejected as error:
        if str(error) == "DRC_BASELINE_INPUT_REJECTED":
            return _error(request, 422, "DRC_BASELINE_INPUT_REJECTED")
        return _map(request, error)
    except Exception as error:
        return _native_move_map(request, error)


@router.post("/{project_id}/eda-native/footprint-move-candidates/{candidate_id}/operations", response_model=OperationStatusResponse, status_code=201)
def create_native_move_operation(
    project_id: str, candidate_id: str, payload: NativeMoveOperationCreateInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        status = service.create_native_move_operation(
            project_id, candidate_id, payload.candidate_fingerprint,
            payload.mapping_review_id, payload.mapping_review_fingerprint,
            tenant_id=identity.tenant_id, requester=identity.requester,
            execution=getattr(request.app.state, "execution_operation_port", None),
        )
        return status_response(status, target_id="project-root")
    except Exception as error:
        return _native_move_map(request, error)


@router.post("/{project_id}/circuit-intents/preview", response_model=CircuitIntentPreviewView | PredesignCircuitPreviewView)
def preview_circuit_intent(
    project_id: str, payload: CircuitIntentPreviewInput | PredesignCircuitPreviewInput, request: Request,
    requester: str = Depends(_requester), tenant_id: str = Depends(_tenant),
):
    service = _circuit_service(request)
    if service is None:
        return _error(request, 503, "CIRCUIT_PUBLICATION_UNAVAILABLE")
    try:
        command = (
            PredesignPreviewCommand.model_validate(payload.model_dump(mode="python"), strict=False)
            if isinstance(payload, PredesignCircuitPreviewInput)
            else CircuitPreviewCommand.model_validate(payload.model_dump(mode="python"), strict=False)
        )
        return _circuit_preview_view(service.preview(
            project_id=project_id, tenant_id=tenant_id, requester=requester, command=command,
        ))
    except Exception as error:
        return _circuit_map(request, error)


@router.post("/{project_id}/circuit-intents/decision", response_model=CircuitIntentDecisionView | PredesignCircuitDecisionView)
def decide_circuit_intent(
    project_id: str, payload: CircuitIntentDecisionInput | PredesignCircuitDecisionInput, request: Request,
    requester: str = Depends(_requester), tenant_id: str = Depends(_tenant),
):
    service = _circuit_service(request)
    if service is None:
        return _error(request, 503, "CIRCUIT_PUBLICATION_UNAVAILABLE")
    try:
        predesign = isinstance(payload, PredesignCircuitDecisionInput)
        command = (
            PredesignDecisionCommand.model_validate(payload.model_dump(mode="python"), strict=False)
            if predesign else CircuitDecisionCommand.model_validate(payload.model_dump(mode="python"), strict=False)
        )
        return _circuit_decision_view(service.decide(
            project_id=project_id, tenant_id=tenant_id, requester=requester, command=command,
        ), predesign=predesign)
    except Exception as error:
        return _circuit_map(request, error)


@router.get("/{project_id}")
def get_project(project_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


def _brownfield_view(context: object) -> dict[str, object]:
    return {
        **context.model_dump(mode="json"),
        "proposal_fingerprint": context.plan.content_fingerprint,
    }


@router.get("/{project_id}/brownfield-build/preview")
def get_brownfield_build_preview(project_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.brownfield_build_preview(project_id, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/brownfield-build")
def get_brownfield_build(project_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _brownfield_view(service.brownfield_build_context(project_id, tenant_id=tenant_id, requester=requester))
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/brownfield-build/proposal", status_code=201)
def propose_brownfield_build(project_id: str, payload: BrownfieldBuildProposalInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _brownfield_view(service.propose_brownfield_build(
            project_id, tenant_id=tenant_id, requester=requester,
            workspace_fingerprint=payload.workspace_fingerprint,
            target_platform=payload.target_platform,
            mcu_family=payload.mcu_family,
            mcu_exact_part=payload.mcu_exact_part,
            source_layout=payload.source_layout,
            configuration=payload.configuration,
        ))
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/brownfield-build/decision")
def decide_brownfield_build(project_id: str, payload: BrownfieldBuildDecisionInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return _brownfield_view(service.decide_brownfield_build(
            project_id, tenant_id=tenant_id, requester=requester,
            proposal_fingerprint=payload.proposal_fingerprint,
            decision=payload.decision,
        ))
    except Exception as error:
        return _map(request, error)


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    request: Request,
    project_fingerprint: str = Header(..., alias="X-Project-Fingerprint"),
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.delete_project(
            project_id,
            project_fingerprint=project_fingerprint,
            tenant_id=tenant_id,
            requester=requester,
        )
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/context")
def get_context(project_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        service.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return service.context(project_id, tenant_id=tenant_id)
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations", status_code=201)
def create_conversation(project_id: str, payload: ConversationCreateInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        service.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return service.create_conversation(project_id, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations")
def list_conversations(project_id: str, request: Request, cursor: str | None = Query(default=None), limit: int = Query(default=20, ge=1, le=100), requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.conversations(project_id, tenant_id=tenant_id, requester=requester, cursor=cursor, limit=limit)
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}")
def get_conversation(project_id: str, conversation_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        detail = service.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if callable(getattr(service,"selection_revision_view",None)):
            revision = service.selection_revision_view(project_id,conversation_id,tenant_id=tenant_id,requester=requester)
            if revision.get("kind") == "REVISION":
                return {**detail.model_dump(mode="json"),"selection_head":revision["head"]}
        return detail
    except Exception as error:
        return _map(request, error)


@router.delete("/{project_id}/conversations/{conversation_id}")
def delete_conversation(project_id: str, conversation_id: str, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        deleted = service.delete_conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        return {"conversation_id": deleted.conversation_id, "deleted": True}
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/resume")
def resume_conversation(project_id: str, conversation_id: str, payload: ConversationResumeInput, request: Request, requester: str = Depends(_requester), tenant_id: str = Depends(_tenant), service: ProjectCatalogService | None = Depends(_service)):
    del payload
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.resume_conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/requirement-suggestions/{suggestion_id}/decision")
def decide_requirement_suggestion_route(
    project_id: str,
    conversation_id: str,
    suggestion_id: str,
    payload: RequirementSuggestionDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_requirement_suggestion(
            project_id,
            conversation_id,
            suggestion_id,
            payload.action,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            modified_requirement=payload.modified_requirement,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/reference-projects/decision")
def decide_reference_project_route(
    project_id: str,
    conversation_id: str,
    payload: ReferenceProjectDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_reference_project(
            project_id,
            conversation_id,
            payload.project_identity,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            selected_features=payload.selected_features,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/feature-decisions")
def decide_feature_route(
    project_id: str,
    conversation_id: str,
    payload: FeatureDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_feature(
            project_id,
            conversation_id,
            payload.project_identity,
            payload.feature,
            payload.action,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            value=payload.value,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/requirement-change-proposals")
def propose_requirement_changes_route(
    project_id: str,
    conversation_id: str,
    payload: RequirementChangeProposalInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.propose_requirement_changes(
            project_id,
            conversation_id,
            payload.utterance,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            created_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/requirement-change-proposals/{proposal_id}/confirmation")
def confirm_requirement_changes_route(
    project_id: str,
    conversation_id: str,
    proposal_id: str,
    payload: RequirementFinalConfirmationInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.confirm_requirement_changes(
            project_id,
            conversation_id,
            proposal_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            confirmed=payload.confirmed,
            proposal_fingerprint=payload.proposal_fingerprint,
            confirmed_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/research-intent-refinement")
def refine_research_intent_route(
    project_id: str,
    conversation_id: str,
    payload: ResearchIntentRefinementInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.refine_research_intent(
            project_id,
            conversation_id,
            payload.product_positioning,
            payload.negative_constraints,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            created_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/execution-authorization/{request_id}/decision")
def decide_execution_authorization_route(
    project_id: str,
    conversation_id: str,
    request_id: str,
    payload: ExecutionAuthorizationDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_execution_authorization(
            project_id,
            conversation_id,
            request_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/firmware-source-generation/decision")
def decide_firmware_source_generation_route(
    project_id: str,
    conversation_id: str,
    payload: FirmwareSourceProposalDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_firmware_source_generation(
            project_id,
            conversation_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            bridge=getattr(request.app.state, "firmware_source_execution_bridge", None),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-production/decision")
def decide_schematic_production_route(
    project_id: str,
    conversation_id: str,
    payload: SchematicProposalDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_schematic_production(
            project_id,
            conversation_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            bridge=getattr(request.app.state, "schematic_execution_bridge", None),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-repair/decision")
def decide_schematic_repair_route(
    project_id: str,
    conversation_id: str,
    payload: SchematicRepairDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    decision_id = "schematic-repair-decision-" + hashlib.sha256(
        "|".join(
            (
                project_id,
                conversation_id,
                payload.repair_id,
                payload.proposal_fingerprint,
                payload.artifact_fingerprint,
                payload.action.value,
                payload.comment,
                payload.edited_proposal_fingerprint or "",
                identity.requester,
            )
        ).encode("utf-8")
    ).hexdigest()[:32]
    try:
        decision = SchematicRepairDecision.create(
            decision_id=decision_id,
            repair_id=payload.repair_id,
            proposal_fingerprint=payload.proposal_fingerprint,
            artifact_fingerprint=payload.artifact_fingerprint,
            action=payload.action,
            reviewer=identity.requester,
            decided_at=datetime.now(UTC),
            comment=payload.comment,
            edited_proposal_fingerprint=payload.edited_proposal_fingerprint,
        )
        return service.decide_schematic_repair(
            project_id,
            conversation_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decision=decision,
        )
    except Exception as error:
        return _map(request, error)


def _parent_choices_query(raw):
    if raw is None:
        return None
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:
                raise ValueError("parent JSON duplicate key")
            value[key]=item
        return value
    items=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError("parent JSON constant")))
    if not isinstance(items,list) or not 0 < len(items) <= 64:
        raise ValueError("parent choices invalid")
    from embedded_copilot.api.project_catalog_models import (
        HardwareSelectionChoiceV2Input,
    )
    choices=tuple(HardwareSelectionChoiceV2Input.model_validate(item) for item in items)
    if len({item.slot_id for item in choices})!=len(choices):
        raise ValueError("parent choices repeated")
    return tuple((item.slot_id,item.source_id,item.source_fingerprint) for item in choices)


@router.get("/{project_id}/conversations/{conversation_id}/hardware-selection/currentness", response_model=SelectionRevisionView | SelectionLegacyView | SelectionPreviewView | SelectionPreviewV3View | SelectionHistoryView)
def hardware_selection_currentness_route(
    project_id: str, conversation_id: str, request: Request,
    view: Literal["CURRENT", "PREVIEW", "HISTORY"] = "CURRENT", revision_id: str | None = None,
    schema_version: int = Query(default=2,ge=2,le=3), parent_choices: str | None = Query(default=None,max_length=16384),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request,503,"PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.selection_revision_view(project_id,conversation_id,
            tenant_id=identity.tenant_id,requester=identity.requester,view=view,revision_id=revision_id,
            schema_version=schema_version,parent_choices=_parent_choices_query(parent_choices))
    except (ValueError,TypeError):
        return _error(request,422,"SELECTION_PARENT_CHOICES_INVALID")
    except Exception as error:
        return _map(request,error)


@router.post("/{project_id}/conversations/{conversation_id}/hardware-selection/proposal")
def create_hardware_selection_proposal_route(
    project_id: str,
    conversation_id: str,
    payload: HardwareSelectionProposalInput | HardwareSelectionProposalV2Input | HardwareSelectionProposalV3Input,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        if isinstance(payload, HardwareSelectionProposalV2Input):
            return service.create_hardware_selection_proposal_v2(project_id, conversation_id,
                tenant_id=identity.tenant_id, requester=identity.requester,
                request_id=payload.request_id,
                expected_head=(payload.expected_head_revision_id, payload.expected_head_selection_id, payload.expected_head_selection_fingerprint),
                preview_fingerprint=payload.preview_fingerprint,
                selections=tuple((item.slot_id,item.source_id,item.source_fingerprint) for item in payload.selections),
                **({"schema_version":3,"parent_choices":tuple((x.slot_id,x.source_id,x.source_fingerprint) for x in payload.parent_choices)} if isinstance(payload,HardwareSelectionProposalV3Input) else {}))
        return service.create_hardware_selection_proposal(
            project_id,
            conversation_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
        )
    except Exception as error:
        return _map(request, error)


class ModelCandidateProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str
    plan_id: str
    phase_id: str
    plan_task_id: str
    engineering_task_id: str | None = None
    stage_id: str | None = None



class HardwareModelCandidateProposalV2Input(ModelCandidateProposalInput):
    schema_version: Literal[2]
    expected_head_revision_id: str | None
    expected_head_selection_id: str | None
    expected_head_selection_fingerprint: str | None
    preview_fingerprint: str

    @model_validator(mode="after")
    def _selector(self):
        # Reuse the same strict selector/identifier validation as deterministic proposals.
        HardwareSelectionProposalV2Input.model_validate({
            "schema_version": 2, "request_id": self.request_id,
            "expected_head_revision_id": self.expected_head_revision_id,
            "expected_head_selection_id": self.expected_head_selection_id,
            "expected_head_selection_fingerprint": self.expected_head_selection_fingerprint,
            "preview_fingerprint": self.preview_fingerprint,
            "selections": [{"slot_id": "selector-validation", "source_id": "selector-validation",
                "source_fingerprint": self.preview_fingerprint}],
        })
        return self

    def revision_input(self):
        return {"expected_head": (self.expected_head_revision_id, self.expected_head_selection_id,
                    self.expected_head_selection_fingerprint), "preview_fingerprint": self.preview_fingerprint}


class HardwareModelCandidateProposalV3Input(HardwareModelCandidateProposalV2Input):
    schema_version: Literal[3]
    parent_choices: list[HardwareSelectionChoiceV2Input] = Field(min_length=1,max_length=64)

    @model_validator(mode="after")
    def _parents(self):
        if len({item.slot_id for item in self.parent_choices})!=len(self.parent_choices):
            raise ValueError("parent choices repeated")
        return self

    def revision_input(self):
        return {**super().revision_input(),"schema_version":3,
            "parent_choices":tuple((x.slot_id,x.source_id,x.source_fingerprint) for x in self.parent_choices)}


def _candidate_items(raw: str | None, operation_id: str):
    if not isinstance(raw, str) or len(raw) > 8192:
        raise ValueError("model candidate output is invalid")
    def _unique_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("model candidate JSON has duplicate keys")
            result[key] = item
        return result
    value = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("model candidate JSON constant is invalid")))
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("model candidate output is invalid")
    key = "selections" if operation_id == "HARDWARE_SELECTION_PROPOSAL" else "review_items"
    items = value.get(key)
    if not isinstance(items, list) or not 0 < len(items) <= 64:
        raise ValueError("model candidate output is invalid")
    if operation_id == "HARDWARE_SELECTION_PROPOSAL":
        if any(not isinstance(item, dict) or set(item) != {"slot_id", "source_id", "source_fingerprint"} or any(not isinstance(item[field], str) or len(item[field]) > 128 for field in item) for item in items):
            raise ValueError("model candidate output is invalid")
        return tuple((item["slot_id"], item["source_id"], item["source_fingerprint"]) for item in items)
    if any(not isinstance(item, dict) or set(item) != {"net_id", "endpoint_binding_ids"} or not isinstance(item["net_id"], str) or len(item["net_id"]) > 128 or not isinstance(item["endpoint_binding_ids"], list) or len(item["endpoint_binding_ids"]) > 64 or any(not isinstance(endpoint, str) or len(endpoint) > 128 for endpoint in item["endpoint_binding_ids"]) for item in items):
        raise ValueError("model candidate output is invalid")
    return tuple((item["net_id"], tuple(item["endpoint_binding_ids"])) for item in items)



@dataclass(frozen=True, slots=True)
class _CurrentSchematicCandidateAuthority:
    """Re-read the exact server preview when Gateway revalidates before dispatch."""

    source: CatalogTaskAuthoritySource
    service: ProjectCatalogService
    project_id: str
    conversation_id: str
    tenant_id: str
    requester: str
    expected_context: str
    operation_id: str = "SCHEMATIC_DESIGN_PROPOSAL"
    selection_revision_input: dict[str, object] | None = None

    def revalidate(self, reference):
        current = self.source.revalidate(reference)
        try:
            latest = self.service.model_operation_candidate_context(
                self.project_id, self.conversation_id,
                tenant_id=self.tenant_id, requester=self.requester,
                operation_id=self.operation_id,
                authority_source=self.source, authority_reference=reference,
                **({} if self.selection_revision_input is None else {"selection_revision_input": self.selection_revision_input}),
            )
            actual = json.dumps(latest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if actual != self.expected_context:
                raise TaskAuthorityFailure("TASK_AUTHORITY_STALE")
        except TaskAuthorityFailure:
            raise
        except Exception as error:
            raise TaskAuthorityFailure("TASK_AUTHORITY_STALE") from error
        return current


def _prepare_model_candidate(project_id: str, conversation_id: str, payload: ModelCandidateProposalInput | HardwareModelCandidateProposalV2Input | HardwareModelCandidateProposalV3Input, request: Request, identity: TrustedDesktopIdentity, service: ProjectCatalogService, operation_id: str) -> tuple:
    """One no-send candidate builder used by consent and model execution."""
    invocation = getattr(request.app.state, "model_invocation_service", None)
    if invocation is None:
        raise ValueError("model invocation unavailable")
    service.get_project_for_requester(project_id, tenant_id=identity.tenant_id, requester=identity.requester)
    service.conversation(project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester)
    source = CatalogTaskAuthoritySource(
        service, tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=project_id, conversation_id=conversation_id, plan_id=payload.plan_id,
        phase_id=payload.phase_id, plan_task_id=payload.plan_task_id, operation_id=operation_id,
    )
    authority, stage = source.resolve_current()
    if payload.engineering_task_id is not None and payload.engineering_task_id != authority.engineering_task_id:
        raise TaskAuthorityFailure("TASK_STAGE_BINDING_MISMATCH")
    if payload.stage_id is not None and payload.stage_id != stage.value:
        raise TaskAuthorityFailure("TASK_STAGE_BINDING_MISMATCH")
    candidate_context = service.model_operation_candidate_context(
        project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester,
        operation_id=operation_id, authority_source=source, authority_reference=authority,
        **({"selection_revision_input": payload.revision_input()} if isinstance(payload, HardwareModelCandidateProposalV2Input) else {}),
    )
    context = EngineeringContextSnapshot.create(
        project_id=project_id, tenant_id=identity.tenant_id,
        task_description=operation_id, memory_evidence=(), graph_evidence=(),
        knowledge_evidence=(), multimodal_evidence=(), conflicts=(),
        confidence=1.0, provenance=(), verification_status="UNVERIFIED",
    )
    from embedded_copilot.model_gateway.invocation.contracts import (
        ModelInvocationGenerationOptions,
    )
    bundle_options = ({"generation_options": ModelInvocationGenerationOptions(max_tokens=2048, service_tier="default", structured_output_schema=(("type", "object"),))}
                      if operation_id == "HARDWARE_SELECTION_PROPOSAL" and getattr(invocation, "counter_bundle_enabled", False) is True else {})
    invocation_request = ModelInvocationRequest(
        **bundle_options,
        request_id=payload.request_id, project_id=project_id, stage_id=stage,
        invocation_mode=InvocationMode.TASK, authority_reference=authority,
        task_type=operation_id,
        payload=ModelInvocationPayload(
            task=json.dumps(candidate_context, ensure_ascii=False, separators=(",", ":")) if bundle_options else operation_id,
            engineering_context=context, conversation_id=conversation_id,
            messages=() if bundle_options else (ModelMessage(role="user", content=json.dumps(candidate_context, ensure_ascii=False, separators=(",", ":"))),),
            system_instruction=(
                'Return only JSON {"selections":[{"slot_id":"...","source_id":"...","source_fingerprint":"sha256:..."}]} with exactly one selection per required slot.'
                if operation_id == "HARDWARE_SELECTION_PROPOSAL" else
                'Return only JSON {"review_items":[{"net_id":"...","endpoint_binding_ids":["..."]}]} using the current nets.'
            ) + " Use only identifiers in the supplied preview. Keep UNRESOLVED values unresolved. This is a candidate for human review; do not approve or execute changes.",
        ),
        trace_context=ModelInvocationTraceContext(trace_id=request.state.trace_id),
    )
    dispatch_authority = source
    if operation_id in {"SCHEMATIC_DESIGN_PROPOSAL", "HARDWARE_SELECTION_PROPOSAL"}:
        dispatch_authority = _CurrentSchematicCandidateAuthority(
            source=source, service=service, project_id=project_id,
            conversation_id=conversation_id, tenant_id=identity.tenant_id,
            requester=identity.requester,
            operation_id=operation_id,
            selection_revision_input=payload.revision_input() if isinstance(payload, HardwareModelCandidateProposalV2Input) else None,
            expected_context=json.dumps(candidate_context, ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":")),
        )
        dispatch_authority.revalidate(authority)
    return invocation_request, dispatch_authority, source, authority, stage, candidate_context

def _invoke_model_candidate(
    project_id: str, conversation_id: str, payload: ModelCandidateProposalInput | HardwareModelCandidateProposalV2Input | HardwareModelCandidateProposalV3Input,
    request: Request, identity: TrustedDesktopIdentity, service: ProjectCatalogService,
    operation_id: str,
):
    invocation = getattr(request.app.state, "model_invocation_service", None)
    if invocation is None:
        return _error(request, 503, "MODEL_INVOCATION_UNAVAILABLE")
    try:
        service.get_project_for_requester(project_id, tenant_id=identity.tenant_id, requester=identity.requester)
        service.conversation(project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester)
    except Exception as error:
        return _map(request, error)
    try:
        invocation_request, dispatch_authority, source, authority, stage, candidate_context = _prepare_model_candidate(
            project_id, conversation_id, payload, request, identity, service, operation_id)
        result = invocation.invoke(invocation_request, authority_source=dispatch_authority)
        if result.status is not InvocationStatus.SUCCESS or result.receipt is None or result.response is None:
            code = result.error_code or "MODEL_CANDIDATE_UNAVAILABLE"
            return _error(request, 404 if code == "TASK_AUTHORITY_OWNER_UNAVAILABLE" else 409 if code.startswith("TASK_") else 503, code)
        candidate = _candidate_items(result.response.structured_output if result.response.structured_output is not None else result.response.content, operation_id)
        source.revalidate(authority)
        dispatch_authority.revalidate(authority)
        if isinstance(payload, HardwareModelCandidateProposalV2Input):
            projected = service.create_hardware_selection_proposal_v2(project_id, conversation_id,
                tenant_id=identity.tenant_id, requester=identity.requester, request_id=payload.request_id,
                expected_head=payload.revision_input()["expected_head"], preview_fingerprint=payload.preview_fingerprint,
                selections=candidate,
                **({"schema_version":3,"parent_choices":payload.revision_input()["parent_choices"]} if isinstance(payload,HardwareModelCandidateProposalV3Input) else {}))
            from embedded_copilot.engineering_copilot import HardwareSelectionState
            proposal = HardwareSelectionState.model_validate_json(json.dumps(projected["selection"])).proposal
            validated_candidate = tuple((item.slot_id, item.source.source_id, item.source.fingerprint) for item in proposal.selected_components)
        elif operation_id == "HARDWARE_SELECTION_PROPOSAL":
            detail, validated_candidate = service.create_hardware_model_candidate_proposal(
                project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester,
                candidate_selections=candidate, authority_source=source, authority_reference=authority,
            )
            proposal = detail.hardware_selection.proposal
        else:
            detail, validated_candidate = service.create_schematic_model_candidate_proposal(
                project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester,
                review_items=candidate, authority_source=source, authority_reference=authority,
                expected_project_supply_fingerprints=(
                    candidate_context["project_supply_candidate_fingerprint"],
                    candidate_context["project_supply_review_fingerprint"],
                ),
            )
            proposal = detail.schematic_planning.proposal
        return {
            "status": "REVIEW" if proposal.status.value == "REVIEW" else proposal.status.value,
            "operation_id": operation_id, "stage_id": stage.value,
            "plan_content_fingerprint": authority.plan_content_fingerprint,
            "attempt_request_id": payload.request_id, "receipt_fingerprint": result.receipt.fingerprint,
            "proposal_fingerprint": proposal.fingerprint,
            "candidate_review_items": validated_candidate,
        }
    except TaskAuthorityFailure as error:
        return _error(request, 404 if error.code == "TASK_AUTHORITY_OWNER_UNAVAILABLE" else 409, error.code)
    except SchematicPlanningBlocked as error:
        if operation_id == "SCHEMATIC_DESIGN_PROPOSAL" and error.reason_code in _SCHEMATIC_BLOCKED_REASONS:
            return _schematic_blocked(request, error.reason_code)
        return _map(request, error)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _error(request, 409, "MODEL_CANDIDATE_REJECTED")
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/hardware-selection/model-candidate-proposal")
def create_hardware_model_candidate_proposal_route(
    project_id: str, conversation_id: str, payload: ModelCandidateProposalInput | HardwareModelCandidateProposalV2Input | HardwareModelCandidateProposalV3Input,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    return _invoke_model_candidate(project_id, conversation_id, payload, request, identity, service, "HARDWARE_SELECTION_PROPOSAL")


@router.post("/{project_id}/conversations/{conversation_id}/schematic-plan/model-candidate-proposal")
def create_schematic_model_candidate_proposal_route(
    project_id: str, conversation_id: str, payload: ModelCandidateProposalInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    return _invoke_model_candidate(project_id, conversation_id, payload, request, identity, service, "SCHEMATIC_DESIGN_PROPOSAL")


@router.post("/{project_id}/conversations/{conversation_id}/hardware-selection/decision")
def decide_hardware_selection_route(
    project_id: str,
    conversation_id: str,
    payload: HardwareSelectionDecisionInput | HardwareSelectionDecisionV2Input,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        if isinstance(payload, HardwareSelectionDecisionV2Input):
            return service.decide_hardware_selection_v2(project_id, conversation_id,
                tenant_id=identity.tenant_id, requester=identity.requester,
                revision_id=payload.revision_id, selection_id=payload.selection_id,
                selection_fingerprint=payload.selection_fingerprint,
                expected_decision_fingerprint=payload.expected_decision_fingerprint,
                decision=payload.decision)
        return service.decide_hardware_selection(
            project_id,
            conversation_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/project-supply")
def create_project_supply_candidate_route(
    project_id: str, conversation_id: str, payload: ProjectSupplyCandidateInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        values = payload.model_dump(mode="python")
        relations = tuple(values.pop("relations"))
        return service.create_project_supply_candidate(
            project_id, conversation_id, tenant_id=identity.tenant_id,
            requester=identity.requester, selectors=values, relations=relations,
        ).model_dump(mode="json")
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/project-supply/{candidate_id}/review")
def review_project_supply_candidate_route(
    project_id: str, conversation_id: str, candidate_id: str, payload: ProjectSupplyReviewInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.review_project_supply_candidate(
            project_id, conversation_id, candidate_id, payload.candidate_fingerprint,
            payload.decision, tenant_id=identity.tenant_id, requester=identity.requester,
        ).model_dump(mode="json")
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}/project-supply")
def project_supply_route(
    project_id: str, conversation_id: str, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.project_supply(
            project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-plan/proposal")
def create_schematic_plan_route(
    project_id: str,
    conversation_id: str,
    payload: SchematicPlanProposalInput | SchematicPlanProposalV2Input,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        selector = None if payload.circuit_intent_id is None else (
            payload.circuit_intent_id,
            payload.circuit_intent_revision,
            payload.circuit_intent_fingerprint,
        )
        if isinstance(payload, SchematicPlanProposalV2Input):
            return service.create_schematic_plan_v2(
                project_id, conversation_id, tenant_id=identity.tenant_id,
                requester=identity.requester, request_id=payload.request_id,
                expected_head=(payload.expected_head_revision_id,
                    payload.expected_head_plan_id, payload.expected_head_plan_fingerprint),
                circuit_intent_selector=selector,
            )
        return service.create_schematic_plan(
            project_id,
            conversation_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            circuit_intent_selector=selector,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-plan/decision")
def decide_schematic_plan_route(
    project_id: str,
    conversation_id: str,
    payload: SchematicPlanDecisionInput | SchematicPlanDecisionV2Input,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        if isinstance(payload, SchematicPlanDecisionV2Input):
            return service.decide_schematic_plan_v2(
                project_id, conversation_id, payload.decision,
                tenant_id=identity.tenant_id, requester=identity.requester,
                revision_id=payload.revision_id, plan_id=payload.plan_id,
                plan_fingerprint=payload.plan_fingerprint, decided_at=datetime.now(UTC),
            )
        return service.decide_schematic_plan(
            project_id,
            conversation_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
        )
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}/schematic-plan/currentness", response_model=SchematicPlanCurrentnessView | SchematicPlanRevisionView | SchematicPlanLegacyView | SchematicPlanHistoryView)
def schematic_plan_currentness_route(
    project_id: str,
    conversation_id: str,
    request: Request,
    plan_id: str | None = Query(default=None, min_length=1, max_length=256),
    plan_fingerprint: str | None = Query(default=None, pattern=r"^sha256:[0-9a-f]{64}$"),
    schema_version: int = Query(default=1, ge=1, le=2),
    revision_id: str | None = Query(default=None, min_length=1, max_length=128),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        if schema_version == 2:
            if revision_id is None:
                if (plan_id is None) != (plan_fingerprint is None):
                    return _error(request, 422, "SCHEMATIC_PLAN_SELECTOR_INVALID")
                if plan_id is not None:
                    legacy = service.schematic_plan_legacy_snapshot(
                        project_id, conversation_id, tenant_id=identity.tenant_id,
                        requester=identity.requester, plan_id=plan_id,
                        plan_fingerprint=plan_fingerprint,
                    )
                    if legacy is None:
                        return _error(request, 404, "SCHEMATIC_PLAN_HISTORY_NOT_FOUND")
                    return legacy
                history = service.schematic_plan_revision_history(
                    project_id, conversation_id, tenant_id=identity.tenant_id,
                    requester=identity.requester,
                )
                legacy = service.schematic_plan_legacy_snapshot(
                    project_id, conversation_id, tenant_id=identity.tenant_id,
                    requester=identity.requester,
                )
                return {"schema_version": 2, "head": next(
                    (item for item in reversed(history) if item["is_current"]), None,
                ), "legacy": legacy, "history": history}
            if plan_id is None or plan_fingerprint is None:
                return _error(request, 422, "SCHEMATIC_PLAN_SELECTOR_INVALID")
            return service.schematic_plan_revision(project_id, conversation_id,
                tenant_id=identity.tenant_id, requester=identity.requester,
                revision_id=revision_id, plan_id=plan_id,
                plan_fingerprint=plan_fingerprint)
        if revision_id is not None or plan_id is None or plan_fingerprint is None:
            return _error(request, 422, "SCHEMATIC_PLAN_SELECTOR_INVALID")
        current_id, current_fingerprint, currentness = service.schematic_plan_currentness(
            project_id, conversation_id, plan_id, plan_fingerprint,
            tenant_id=identity.tenant_id, requester=identity.requester,
        )
        return {
            "plan_id": current_id,
            "plan_fingerprint": current_fingerprint,
            "currentness": currentness,
        }
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-evidence/operations")
def schematic_evidence_operation_route(project_id: str, conversation_id: str, payload: SchematicEvidenceOperationInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity), service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        selectors = {} if payload.library_map_evidence_id is None else dict(library_map_evidence_id=payload.library_map_evidence_id,
            library_map_evidence_fingerprint=payload.library_map_evidence_fingerprint)
        return service.create_schematic_evidence_operation(project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester, kind=payload.kind, **selectors)
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/standard-applicability")
def standard_applicability_proposal_route(
    project_id: str, conversation_id: str, payload: StandardApplicabilityInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.create_standard_applicability(
            project_id, conversation_id, tenant_id=identity.tenant_id,
            requester=identity.requester, values=payload.model_dump(mode="python"),
        )
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}/standard-applicability")
def standard_applicability_list_route(
    project_id: str, conversation_id: str, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return [
            {"evidence": state.evidence, "review": state.review, "currentness": currentness}
            for state, currentness in service.standard_applicability(
                project_id, conversation_id, tenant_id=identity.tenant_id,
                requester=identity.requester,
            )
        ]
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/standard-applicability/{evidence_id}/review")
def standard_applicability_review_route(
    project_id: str, evidence_id: str, payload: StandardApplicabilityReviewInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.review_standard_applicability(
            project_id, evidence_id, tenant_id=identity.tenant_id,
            requester=identity.requester, **payload.model_dump(),
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/library-terminal-maps")
def library_terminal_map_proposal_route(project_id: str, conversation_id: str,
    payload: LibraryTerminalMapInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.create_library_terminal_map(project_id, conversation_id,
            tenant_id=identity.tenant_id, requester=identity.requester,
            values=payload.model_dump(mode="python"))
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}/library-terminal-maps")
def library_terminal_map_list_route(project_id: str, conversation_id: str, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return [{"evidence": state.evidence, "review": state.review, "currentness": currentness}
            for state, currentness in service.library_terminal_maps(project_id, conversation_id,
                tenant_id=identity.tenant_id, requester=identity.requester)]
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/library-terminal-maps/{evidence_id}/review")
def library_terminal_map_review_route(project_id: str, evidence_id: str,
    payload: LibraryTerminalMapReviewInput, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.review_library_terminal_map(project_id, evidence_id,
            tenant_id=identity.tenant_id, requester=identity.requester, **payload.model_dump())
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-parameters")
def schematic_parameter_proposal_route(project_id: str, conversation_id: str, payload: ParameterCandidate,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity), service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.propose_schematic_parameter(project_id, conversation_id, tenant_id=identity.tenant_id, requester=identity.requester, candidate=payload)
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/schematic-parameters/{evidence_id}/review")
def schematic_parameter_review_route(project_id: str, evidence_id: str, payload: SchematicParameterReviewInput,
    request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity), service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.review_schematic_parameter(project_id, evidence_id, tenant_id=identity.tenant_id, requester=identity.requester, **payload.model_dump())
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/schematic-parameters/{evidence_id}")
def schematic_parameter_read_route(project_id: str, evidence_id: str, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity), service: ProjectCatalogService | None = Depends(_service)) -> object:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        evidence, currentness = service.schematic_parameter(project_id, evidence_id, tenant_id=identity.tenant_id, requester=identity.requester)
        return {"evidence": evidence, "currentness": currentness}
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/schematic-verifications", response_model=SchematicVerificationView)
def record_schematic_verification_route(
    project_id: str, conversation_id: str, payload: SchematicVerificationInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        selectors = payload.model_dump()
        if payload.library_map_evidence_ids is None:
            selectors.pop("library_map_evidence_ids")
            selectors.pop("library_map_evidence_fingerprints")
        record = service.record_schematic_verification(
            project_id, conversation_id, tenant_id=identity.tenant_id,
            requester=identity.requester, **selectors,
        )
        return {"record": record, "currentness": "CURRENT"}
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/schematic-verifications/{verification_id}", response_model=SchematicVerificationView)
def schematic_verification_route(
    project_id: str, verification_id: str, request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        record, currentness = service.schematic_verification(
            project_id, verification_id, tenant_id=identity.tenant_id,
            requester=identity.requester,
        )
        return {"record": record, "currentness": currentness}
    except Exception as error:
        return _map(request, error)


@router.get("/{project_id}/conversations/{conversation_id}/schematic-verifications", response_model=list[SchematicVerificationView])
def schematic_verifications_route(
    project_id: str, conversation_id: str, request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return [
            {"record": record, "currentness": currentness}
            for record, currentness in service.schematic_verifications(
                project_id, conversation_id, tenant_id=identity.tenant_id,
                requester=identity.requester, limit=limit,
            )
        ]
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/pcb-production/proposal")
def create_pcb_production_proposal_route(
    project_id: str,
    conversation_id: str,
    payload: PCBProductionProposalInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        if payload.physical_design is not None:
            service.create_pcb_production_input(
                project_id,
                conversation_id,
                tenant_id=identity.tenant_id,
                requester=identity.requester,
                physical_design=payload.physical_design,
                schematic_artifact_id=payload.schematic_artifact_id,
                schematic_artifact_fingerprint=payload.schematic_artifact_fingerprint,
                schematic_content_fingerprint=payload.schematic_content_fingerprint,
                workspace_identity=payload.workspace_identity,
            )
        return service.create_pcb_production_proposal(
            project_id,
            conversation_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/pcb-production/repair-decision")
def decide_pcb_repair_route(
    project_id: str,
    conversation_id: str,
    payload: PCBRepairDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        from embedded_copilot.pcb_production.contracts import PCBRepairDecision

        decision = PCBRepairDecision.create(
            decision_id=f"pcb-repair-decision-api-{uuid4().hex}",
            repair_id=payload.repair_id,
            proposal_fingerprint=payload.proposal_fingerprint,
            artifact_fingerprint=payload.artifact_fingerprint,
            action=payload.action,
            reviewer=identity.requester,
            decided_at=datetime.now(UTC),
            comment=payload.comment,
            edited_proposal_fingerprint=payload.edited_proposal_fingerprint,
        )
        return service.decide_pcb_repair(
            project_id,
            conversation_id,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decision=decision,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/{project_id}/conversations/{conversation_id}/pcb-production/decision")
def decide_pcb_production_route(
    project_id: str,
    conversation_id: str,
    payload: PCBProposalDecisionInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    service: ProjectCatalogService | None = Depends(_service),
):
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        return service.decide_pcb_production(
            project_id,
            conversation_id,
            payload.decision,
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            decided_at=datetime.now(UTC),
            bridge=getattr(request.app.state, "pcb_execution_bridge", None),
        )
    except Exception as error:
        return _map(request, error)


@router.get(
    "/{project_id}/conversations/{conversation_id}/artifact-intelligence",
    response_model=ArtifactIntelligenceView,
)
def get_artifact_intelligence(
    project_id: str,
    conversation_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: ProjectCatalogService | None = Depends(_service),
) -> ArtifactIntelligenceView | JSONResponse:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        detail = service.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        return _artifact_view(detail.artifact_intelligence_snapshot)
    except Exception as error:
        return _map(request, error)


@router.post(
    "/{project_id}/conversations/{conversation_id}/artifact-intelligence",
    response_model=ArtifactIntelligenceView,
)
def post_artifact_intelligence(
    project_id: str,
    conversation_id: str,
    payload: ArtifactIntelligenceInput,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: ProjectCatalogService | None = Depends(_service),
) -> ArtifactIntelligenceView | JSONResponse:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    zip_content: bytes | None = None
    if payload.zip_base64 is not None:
        try:
            zip_content = base64.b64decode(payload.zip_base64, validate=True)
        except (binascii.Error, ValueError):
            return _error(request, 422, "ARTIFACT_INPUT_REJECTED")
        if not zip_content:
            return _error(request, 422, "ARTIFACT_INPUT_REJECTED")
    try:
        detail = service.scan_artifact_intelligence(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
            zip_content=zip_content,
        )
        bundle = detail.engineering_evidence_bundle
        snapshot = detail.artifact_intelligence_snapshot
        if bundle is not None and snapshot is not None and snapshot.review_id is None:
            port = getattr(request.app.state, "copilot_review_port", None)
            create_review = getattr(port, "create_review", None) if port is not None else None
            if callable(create_review):
                review_request = ReviewRequest.create(
                    review_id=f"review-{bundle.bundle_id}-{uuid4().hex[:16]}",
                    project_id=project_id,
                    target_reference=ReviewTargetReference.create(
                        target_type=ReviewTargetType.ENGINEERING_EVIDENCE,
                        target_id=bundle.bundle_id,
                        target_fingerprint=bundle.bundle_fingerprint,
                    ),
                    requester=requester,
                    created_at=datetime.now(UTC),
                )
                try:
                    stored_review = create_review(
                        tenant_id=tenant_id,
                        requester=requester,
                        request=review_request,
                    )
                except Exception:
                    return _error(request, 422, "REVIEW_REJECTED")
                detail = service.attach_artifact_evidence_review(
                    project_id,
                    conversation_id,
                    getattr(stored_review, "review_id", review_request.review_id),
                    tenant_id=tenant_id,
                    requester=requester,
                )
        return _artifact_view(detail.artifact_intelligence_snapshot)
    except Exception as error:
        return _map(request, error)


@router.post(
    "/{project_id}/conversations/{conversation_id}/artifact-intelligence/design-truth",
    response_model=DesignTruthProjection,
)
def post_reverse_design_truth(
    project_id: str,
    conversation_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: ProjectCatalogService | None = Depends(_service),
) -> DesignTruthProjection | JSONResponse:
    if service is None:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        detail = service.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        snapshot = detail.artifact_intelligence_snapshot
        if snapshot is None or snapshot.review_id is None:
            return _error(request, 422, "EVIDENCE_REVIEW_REQUIRED")
        port = getattr(request.app.state, "copilot_review_port", None)
        get_review = getattr(port, "get_review", None) if port is not None else None
        if not callable(get_review):
            return _error(request, 503, "REVIEW_UNAVAILABLE")
        review = get_review(
            tenant_id=tenant_id,
            project_id=project_id,
            review_id=snapshot.review_id,
        )
        if review is None:
            return _error(request, 422, "EVIDENCE_REVIEW_REQUIRED")
        decision = getattr(review, "decision", None)
        if getattr(getattr(decision, "decision", None), "value", None) != "APPROVE":
            return _error(request, 422, "EVIDENCE_REVIEW_REQUIRED")
        truth = service.build_reverse_design_truth(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
            review=review,
        )
        return _truth_view(truth)
    except Exception as error:
        return _map(request, error)
