from __future__ import annotations

import copy
import errno
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

from embedded_copilot.application import ApplicationRoutingContext
from embedded_copilot.desktop_copilot_chat import (
    ChatContextReference,
    ChatProjectReference,
    ChatReferenceKind,
    ChatSessionOpenRequest,
)
from embedded_copilot.desktop_copilot_chat.response_contract import (
    resolve_response_type,
)
from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    ArtifactIntelligenceService,
    AuthorizationDecisionStatus,
    DesignTruth,
    DesignSource,
    DesignTruthBuilder,
    DesignTruthOrigin,
    EngineeringEvidenceStatus,
    EngineeringPlan,
    EngineeringPlanPhase,
    EngineeringTask,
    EngineeringWorkflow,
    FirmwareSourceGenerationState,
    FirmwareSourceGenerationStatus,
    FirmwareSourceProposalDecisionStatus,
    HardwareSelectionDecision,
    HardwareSelectionService,
    HardwareSelectionState,
    HardwareSelectionStatus,
    LearningMode,
    PlanOutput,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
    SchematicErcCorrelation,
    SchematicErcCorrelationStatus,
    SchematicErcEngineeringStatus,
    SchematicErcGateStatus,
    SchematicErcIssueClassification,
    SchematicErcIssueStatus,
    SchematicErcReceipt,
    SchematicErcResult,
    SchematicGenerationReceipt,
    SchematicGenerationStatus,
    SchematicParseValidationEvidence,
    SchematicPlanDecision,
    SchematicPlanningService,
    SchematicPlanningState,
    SchematicPlanningStatus,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposalDecisionStatus,
    SchematicRepairApplicationReceipt,
    SchematicRepairApplicationStatus,
    SchematicRepairAssessment,
    SchematicRepairDecision,
    SchematicRepairDecisionAction,
    SchematicRepairProposal,
    TeachingContext,
    approve_engineering_plan,
    assess_schematic_repair,
    create_firmware_source_generation_state,
    create_schematic_production_state,
    decide_execution_authorization_request,
    decide_firmware_source_proposal,
    decide_schematic_proposal,
    evaluate_authorization,
    evaluate_capabilities,
    evaluate_schematic_readiness,
    generate_engineering_workflow,
    generate_execution_intent,
    generate_execution_requests,
    generate_firmware_source_proposal,
    generate_firmware_source_request,
    generate_firmware_spec,
    generate_hardware_design_spec,
    generate_implementation_spec,
    generate_knowledge_model,
    generate_schematic_production_request,
    generate_schematic_proposal,
    generate_simulation_spec,
    generate_verification_spec,
    is_firmware_source_generation_request,
    is_schematic_generation_request,
    transition_engineering_evidence,
    transition_engineering_plan,
    validate_execution_authorization,
)
from embedded_copilot.engineering_copilot.schematic_claim_rules import (
    ClaimRuleResult,
    resolve_schematic_claim,
)
from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanningBlocked, _project_supply_net_options
from embedded_copilot.engineering_copilot.schematic_evidence import LibraryTerminalBindingV2, decimal_text
from embedded_copilot.engineering_copilot.teaching_service import (
    generate_teaching_context as project_generate_teaching_context,
)
from embedded_copilot.engineering_memory.contracts import ApprovedEngineeringMemory
from embedded_copilot.engineering_pcb import (
    DecouplingPreventEvaluator,
    DecouplingRelationshipResolver,
    DecouplingVerifier,
    DFTRequirement,
    DifferentialPairPreventEvaluator,
    DifferentialPairVerifier,
    DifferentialRouteCandidate,
    EDAMappingCandidate,
    EngineeringApplicabilityDecision,
    EngineeringCheck,
    EngineeringEvidenceRequirement,
    FootprintSymbolPinConsistencyVerifier,
    HumanApprovalBinding,
    ManufacturerProfile,
    MappingPreventEvaluator,
    PCBArtifactReference,
    PCBArtifactType,
    PCBConstraintCompiler,
    PCBDifferentialPairProjection,
    PCBDifferentialPairRule,
    PCBEngineeringProjector,
    PCBEngineeringVerificationService,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBManufacturingMeasurements,
    PCBPlacementCandidate,
    PCBPowerLoopRule,
    PCBPowerStageEvidence,
    PCBRouteCandidate,
    PCBSourceFormat,
    PowerLayoutCandidate,
    PowerLoopPreventEvaluator,
    PowerLoopVerifier,
    ReturnPathPreventEvaluator,
    ReturnPathVerifier,
    build_jlcpcb_dfm_inputs,
    build_mapping_input_from_existing,
    mark_decoupling_stale,
    mark_differential_pair_stale,
    mark_mapping_stale,
    mark_power_loop_stale,
    mark_return_path_stale,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
    merge_requirement_context,
)
from embedded_copilot.engineering_research import (
    EngineeringResearchService,
    EngineeringResearchSnapshot,
    FeatureDecision,
    FeatureDecisionAction,
    GitHubResearchSource,
    OSHWHubResearchSource,
    ReferenceProjectDecision,
    ReferenceProjectDecisionAction,
    RequirementChangeProposal,
    RequirementChangeStatus,
    RequirementDecisionAction,
    RequirementFinalConfirmation,
    RequirementSuggestionDecision,
    ResearchIntentRefinement,
    ResearchSourceType,
    UnavailableResearchSource,
    VendorReferenceSource,
    apply_requirement_suggestion_decision,
    is_requirement_change_feedback,
)
from embedded_copilot.engineering_runtime.common import canonical_fingerprint
from embedded_copilot.engineering_runtime.objects.models import CircuitIntentObject
from embedded_copilot.engineering_runtime.proposals import ApprovalRecord
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.firmware_engineering.models import (
    canonical_fingerprint as firmware_canonical_fingerprint,
)
from embedded_copilot.input import AttachmentType, UserAttachment
from embedded_copilot.pcb.parser import KiCadPCBParser, RootedPCBSourceResolver
from embedded_copilot.pcb_production import (
    PCBDrcReceipt,
    PCBDrcStatus,
    PCBEngineeringStatus,
    PCBGenerationReceipt,
    PCBParseValidationEvidence,
    PCBParseValidationStatus,
    PCBPhysicalDesignProposal,
    PCBProductionRequest,
    PCBProductionState,
    PCBProductionStatus,
    PCBProposalDecisionStatus,
    create_pcb_production_state,
    decide_pcb_proposal,
)

from .brownfield_build import (
    BrownfieldBuildContext,
    BrownfieldFirmwareSpec,
    BrownfieldImplementationSpec,
)
from .exceptions import (
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
    ProjectTargetNotWritable,
    RequirementChangeRejected,
    RequirementSuggestionDecisionRejected,
    SchematicPlanningRejected,
    SchematicProductionRejected,
)
from .source_publication import PublishedComponentSourcePort
from .firmware_production import (
    FirmwareBuildArtifactProjection,
    FirmwareBuildReceiptProjection,
    FirmwareProductionState,
    FirmwareProductionStatus,
    artifact_projection,
    firmware_binding_fingerprint,
    firmware_production_id,
)
from .models import (
    CircuitProposalCandidate,
    CircuitPublication,
    ConversationDetail,
    ConversationPage,
    ConversationSessionView,
    ConversationSummary,
    ConversationTurn,
    ConversationTurnStatus,
    PredesignCircuitCandidate,
    ProjectSupplyCandidateV2,
    ProjectSupplyCandidateV3,
    SourcePublicationBindingV1,
    ProjectSupplyRelationV2,
    ProjectSupplyReviewV2,
    ProjectSupplyStateV2,
    ProjectSupplyStateV3,
    LibraryTerminalMapEvidenceV2,
    LibraryTerminalMapReviewV2,
    ReviewedLibraryTerminalMapV2,
    ReviewedStandardApplicabilityV2,
    StandardApplicabilityEvidenceV2,
    StandardApplicabilityReviewV2,
    ProjectCapability,
    ProjectContextGroup,
    ProjectContextReference,
    ProjectContextView,
    ProjectCreationMode,
    ProjectCreationPreview,
    ProjectCreationRequest,
    ProjectDeletionResult,
    ProjectDirectoryPage,
    ProjectDirectoryReference,
    ProjectDirectoryView,
    ProjectInitializationStatus,
    ProjectLegacyMode,
    ProjectLocationReference,
    ProjectLocationView,
    ProjectType,
    ProjectView,
    SchematicClaimProof,
    SchematicClaimResolution,
    SchematicVerificationRecord,
    predesign_source_snapshot,
)

_logger = logging.getLogger(__name__)


def _create_diagnostic_reason(error: Exception | None) -> str:
    if error is None:
        return "-"
    if isinstance(error, ProjectTargetNotWritable):
        return "PROJECT_TARGET_NOT_WRITABLE"
    if isinstance(error, OSError):
        error_name = errno.errorcode.get(error.errno, "UNKNOWN")
        return f"OS_ERROR_{error_name}"
    if isinstance(error, ProjectCatalogConflict):
        return "PROJECT_CATALOG_CONFLICT"
    if isinstance(error, ProjectCatalogForbidden):
        return "PROJECT_CATALOG_FORBIDDEN"
    if isinstance(error, ProjectCatalogExpired):
        return "PROJECT_PREVIEW_EXPIRED"
    if isinstance(error, ProjectCatalogRejected):
        return "PROJECT_CATALOG_REJECTED"
    if isinstance(error, ProjectCatalogUnavailable):
        return "PROJECT_CATALOG_UNAVAILABLE"
    return "PROJECT_CREATE_BOUNDARY_FAILED"


def _workflow_sources_match(
    workflow: EngineeringWorkflow,
    plan: EngineeringPlan,
    architecture: object,
) -> bool:
    return (
        workflow.project_id == plan.project_id
        and workflow.plan_id == plan.plan_id
        and workflow.architecture_id == getattr(architecture, "architecture_id", None)
        and any(
            item.source_type == "ENGINEERING_PLAN"
            and item.source_id == plan.plan_id
            and item.source_fingerprint == plan.fingerprint
            for item in workflow.provenance
        )
        and any(
            item.source_type == "ARCHITECTURE_DESIGN"
            and item.source_id == architecture.architecture_id
            and item.source_fingerprint == architecture.fingerprint
            for item in workflow.provenance
        )
    )


def _capability_state(
    implementation_spec,
    *,
    conversation_id: str,
    requested_by: str,
    current_authorization=None,
):
    if implementation_spec is None:
        return None, (), None
    try:
        evaluation = evaluate_capabilities(implementation_spec)
        intents = tuple(
            generate_execution_intent(task) for task in implementation_spec.tasks
        )
        requests = generate_execution_requests(
            evaluation,
            intents,
            conversation_id=conversation_id,
            requested_by=requested_by,
        )
        pending = evaluate_authorization(evaluation, intents, requests)
        if current_authorization is not None:
            try:
                return evaluation, intents, validate_execution_authorization(
                    evaluation, intents, current_authorization
                )
            except (TypeError, ValueError):
                pass
        return evaluation, intents, pending
    except (TypeError, ValueError):
        return None, (), None


def _firmware_source_state(
    *,
    tenant_id: str,
    firmware_spec,
    implementation_spec,
    execution_intents,
    execution_authorization,
    existing,
    command: str | None,
    source_request_id: str | None = None,
    trace_id: str | None = None,
):
    if existing is not None and execution_authorization is not None:
        request = existing.request
        if (
            request.authorization_id == execution_authorization.authorization_id
            and request.authorization_fingerprint == execution_authorization.fingerprint
            and firmware_spec is not None
            and request.firmware_spec_fingerprint == firmware_spec.fingerprint
            and implementation_spec is not None
            and request.implementation_fingerprint == implementation_spec.fingerprint
        ):
            return existing
    if (
        command is None
        or not is_firmware_source_generation_request(command)
        or firmware_spec is None
        or implementation_spec is None
        or execution_authorization is None
    ):
        return None
    candidates = tuple(
        request
        for request, decision in zip(
            execution_authorization.requests,
            execution_authorization.decisions,
            strict=True,
        )
        if request.capability_id == "FIRMWARE_CODE_GENERATION"
        and request.status.value == "AUTHORIZED"
        and decision.decision is AuthorizationDecisionStatus.APPROVED
    )
    if len(candidates) != 1:
        return None
    authorization_request = candidates[0]
    intent = next(
        (
            item
            for item in execution_intents
            if item.intent_id == authorization_request.intent_id
        ),
        None,
    )
    if intent is None or len(intent.source_task_refs) != 1:
        return None
    task = next(
        (
            item
            for item in implementation_spec.tasks
            if item.task_id == intent.source_task_refs[0]
        ),
        None,
    )
    if task is None:
        return None
    modules = tuple(item for item in firmware_spec.modules if item.module_id in task.source_refs)
    if len(modules) != 1:
        return None
    try:
        request = generate_firmware_source_request(
            firmware_spec,
            implementation_spec,
            task,
            modules[0],
            intent,
            execution_authorization,
            source_request_id=source_request_id,
            trace_id=trace_id,
            authorization_request_id=authorization_request.request_id,
            tenant_id=tenant_id,
        )
        proposal = generate_firmware_source_proposal(request, modules[0])
        return create_firmware_source_generation_state(request, proposal)
    except (TypeError, ValueError):
        return None


def _pcb_authority_fingerprint(detail: ConversationDetail) -> tuple[str | None, ...]:
    production = detail.pcb_production
    schematic = detail.schematic_production
    planning = detail.schematic_planning
    selection = detail.hardware_selection
    return (
        None if detail.design_truth is None else detail.design_truth.fingerprint,
        None if detail.hardware_design_spec is None else detail.hardware_design_spec.fingerprint,
        None if detail.implementation_spec is None else detail.implementation_spec.fingerprint,
        None if detail.execution_authorization is None else detail.execution_authorization.fingerprint,
        None if planning is None or planning.approved_plan is None else planning.approved_plan.fingerprint,
        None if schematic is None or schematic.generation_receipt is None else schematic.generation_receipt.fingerprint,
        None if schematic is None or schematic.erc_receipt is None else schematic.erc_receipt.fingerprint,
        None if selection is None else selection.proposal.fingerprint,
        None if production is None else production.request.fingerprint,
    )


def _pcb_foundation_authority_fingerprint(detail: ConversationDetail) -> tuple[str | None, ...]:
    production = detail.pcb_production
    receipt = None if production is None else production.generation_receipt
    selection = detail.hardware_selection
    hardware_truth = None if selection is None else selection.hardware_truth
    return (
        None if detail.design_truth is None else detail.design_truth.fingerprint,
        None if hardware_truth is None else hardware_truth.fingerprint,
        None if receipt is None else receipt.fingerprint,
        None if receipt is None else receipt.artifact_fingerprint,
        None if detail.pcb_engineering_state is None else detail.pcb_engineering_state.fingerprint,
        None if detail.pcb_constraint_set is None else detail.pcb_constraint_set.fingerprint,
        None if detail.pcb_verification_plan is None else detail.pcb_verification_plan.fingerprint,
    )


def _pcb_physics_plan_values(plan: object) -> dict[str, object]:
    values = plan.model_dump(mode="python", exclude={"fingerprint"})
    for field in (
        "engineering_required_verifiers",
        "engineering_optional_verifiers",
        "engineering_not_applicable_verifiers",
        "engineering_unknown_applicability",
        "engineering_evidence_fingerprint",
    ):
        values.pop(field, None)
    return values


def _pcb_engineering_verifiers(plan: object) -> set[str]:
    return {
        *plan.engineering_required_verifiers,
        *plan.engineering_optional_verifiers,
        *plan.engineering_not_applicable_verifiers,
        *plan.engineering_unknown_applicability,
    }


def _pcb_manufacturer_profile_fingerprint(detail: ConversationDetail) -> str | None:
    profile = detail.pcb_manufacturer_profile
    return None if profile is None else profile.fingerprint


def _rebind_pcb_physics_results(detail: ConversationDetail, plan_fingerprint: str) -> ConversationDetail:
    update: dict[str, object] = {}
    for field in (
        "pcb_decoupling_verification",
        "pcb_return_path_verification",
        "pcb_power_loop_verification",
        "pcb_differential_pair_verification",
        "pcb_mapping_verification",
    ):
        result = getattr(detail, field)
        if result is None or plan_fingerprint in result.compared_fingerprints:
            continue
        values = result.model_dump(mode="python", exclude={"fingerprint"})
        values["compared_fingerprints"] = tuple(
            dict.fromkeys((*result.compared_fingerprints, plan_fingerprint))
        )
        update[field] = type(result).create(**values)
    return detail.model_copy(update=update) if update else detail


def _mark_pcb_foundation_stale(detail: ConversationDetail, *, reason: str) -> ConversationDetail:
    state = detail.pcb_engineering_state
    constraint_set = detail.pcb_constraint_set
    plan = detail.pcb_verification_plan
    affected = tuple(
        dict.fromkeys(
            item
            for source in (state, constraint_set, plan)
            if source is not None
            for item in source.affected_objects
        )
    ) or ("pcb-engineering-state",)
    reverification = tuple(
        dict.fromkeys(
            item
            for source in (state, constraint_set, plan)
            if source is not None
            for item in source.required_reverification
        )
    ) or ("reproject_pcb_engineering_state", "recompile_pcb_constraints", "rebuild_pcb_verification_plan")
    update: dict[str, object] = {
        "fingerprint": "sha256:" + "0" * 64,
        # A foundation change invalidates the final closure projection as a
        # whole.  Removing the old gate is safer than exposing a stale
        # READY_FOR_FAB decision that could be mistaken for current evidence.
        "pcb_engineering_verification_summary": None,
        "pcb_ready_for_fab": None,
        "pcb_ready_for_fab_approval": None,
        "pcb_manufacturing_measurements": None,
    }
    stale_state = None
    if state is not None:
        stale_state = state.mark_stale(
            reason=reason,
            affected_objects=affected,
            required_reverification=reverification,
        )
        update["pcb_engineering_state"] = stale_state
    stale_constraints = None
    if constraint_set is not None:
        constraint_values = constraint_set.model_dump(mode="python", exclude={"fingerprint"})
        if stale_state is not None:
            constraint_values["state_fingerprint"] = stale_state.fingerprint
        stale_constraints = type(constraint_set).create(**constraint_values).mark_stale(
            reason=reason,
            affected_objects=affected,
            required_reverification=reverification,
        )
        update["pcb_constraint_set"] = stale_constraints
    if plan is not None:
        plan_values = plan.model_dump(mode="python", exclude={"fingerprint"})
        if stale_state is not None:
            plan_values["pcb_state_fingerprint"] = stale_state.fingerprint
        if stale_constraints is not None:
            plan_values["constraint_set_fingerprint"] = stale_constraints.fingerprint
        update["pcb_verification_plan"] = type(plan).create(**plan_values).mark_stale(
            reason=reason,
            affected_objects=affected,
            required_reverification=reverification,
        )
    if detail.pcb_decoupling_verification is not None and not detail.pcb_decoupling_verification.stale:
        update["pcb_decoupling_verification"] = mark_decoupling_stale(
            detail.pcb_decoupling_verification,
            reason=reason,
            affected_objects=affected,
            required_reverification=("reverify_decoupling",),
        )
    if detail.pcb_return_path_verification is not None and not detail.pcb_return_path_verification.stale:
        update["pcb_return_path_verification"] = mark_return_path_stale(
            detail.pcb_return_path_verification,
            reason=reason,
            affected_objects=affected,
        )
    if detail.pcb_power_loop_verification is not None and not detail.pcb_power_loop_verification.stale:
        update["pcb_power_loop_verification"] = mark_power_loop_stale(
            detail.pcb_power_loop_verification,
            reason=reason,
            affected_objects=affected,
        )
    if detail.pcb_differential_pair_verification is not None and not detail.pcb_differential_pair_verification.stale:
        update["pcb_differential_pair_verification"] = mark_differential_pair_stale(
            detail.pcb_differential_pair_verification,
            reason=reason,
            affected_objects=affected,
        )
    if detail.pcb_mapping_verification is not None and not detail.pcb_mapping_verification.stale:
        update["pcb_mapping_verification"] = mark_mapping_stale(
            detail.pcb_mapping_verification,
            reason=reason,
            affected_objects=affected,
        )
    return detail.model_copy(update=update)


def _mark_pcb_production_stale(existing: PCBProductionState, *, reason: str) -> PCBProductionState:
    if existing.status is PCBProductionStatus.STALE and existing.stale_reason == reason:
        return existing
    receipt = existing.generation_receipt
    if receipt is None:
        raise ValueError("generated PCB receipt is unavailable")
    stale_receipt = PCBGenerationReceipt.create(
        **{
            **receipt.model_dump(mode="python", exclude={"fingerprint", "status", "validation_status", "parse_validation"}),
            "status": "STALE",
            "validation_status": PCBParseValidationStatus.FAILED,
        }
    )
    return PCBProductionState.create(
        production_id=existing.production_id,
        project_id=existing.project_id,
        conversation_id=existing.conversation_id,
        request=existing.request,
        proposal=existing.proposal,
        decision=existing.decision,
        operation_id=existing.operation_id,
        operation_fingerprint=existing.operation_fingerprint,
        status=PCBProductionStatus.STALE,
        stale_reason=reason,
        generation_receipt=stale_receipt,
    )


def _mark_schematic_production_stale(existing: SchematicProductionState) -> SchematicProductionState:
    receipt = existing.generation_receipt
    if receipt is None:
        raise ValueError("generated schematic receipt is unavailable")
    if receipt.status is SchematicGenerationStatus.STALE and existing.status is SchematicProductionStatus.STALE:
        return existing
    stale_receipt = SchematicGenerationReceipt.create(
        **{
            **receipt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": SchematicGenerationStatus.STALE,
        }
    )
    stale_erc_receipt = None
    if existing.erc_receipt is not None:
        current_result = existing.erc_receipt.result
        stale_issues = tuple(
            type(issue).create(
                **{
                    **issue.model_dump(mode="python", exclude={"fingerprint"}),
                    "status": SchematicErcIssueStatus.STALE,
                }
            )
            for issue in current_result.issues
        )
        stale_correlations = tuple(
            type(correlation).create(
                **{
                    **correlation.model_dump(mode="python", exclude={"fingerprint"}),
                    "status": SchematicErcCorrelationStatus.STALE,
                }
            )
            for correlation in current_result.correlations
        )
        stale_result = SchematicErcResult.create(
            **{
                **current_result.model_dump(
                    mode="python",
                    exclude={"fingerprint", "issues", "correlations", "engineering_status"},
                ),
                "issues": stale_issues,
                "correlations": stale_correlations,
                "engineering_status": SchematicErcEngineeringStatus.BLOCKED,
            }
        )
        stale_erc_receipt = SchematicErcReceipt.create(
            **{
                **existing.erc_receipt.model_dump(mode="python", exclude={"fingerprint", "result", "engineering_status"}),
                "result": stale_result,
                "engineering_status": SchematicErcEngineeringStatus.BLOCKED,
            }
        )
    stale_repair_receipts = tuple(
        receipt
        if receipt.status is SchematicRepairApplicationStatus.STALE
        else SchematicRepairApplicationReceipt.create(
            **{
                **receipt.model_dump(mode="python", exclude={"fingerprint"}),
                "status": SchematicRepairApplicationStatus.STALE,
            }
        )
        for receipt in existing.repair_application_receipts
    )
    return SchematicProductionState.create(
        production_id=existing.production_id,
        project_id=existing.project_id,
        conversation_id=existing.conversation_id,
        request=existing.request,
        proposal=existing.proposal,
        decision=existing.decision,
        operation_id=existing.operation_id,
        operation_fingerprint=existing.operation_fingerprint,
        status=SchematicProductionStatus.STALE,
        generation_receipt=stale_receipt,
        erc_receipt=stale_erc_receipt,
        erc_gate_status=(None if stale_erc_receipt is None else SchematicErcGateStatus.BLOCKED),
        erc_assessment=None,
        repair_proposals=existing.repair_proposals,
        repair_decisions=existing.repair_decisions,
        repair_application_receipts=stale_repair_receipts,
    )


def _schematic_production_state(
    *,
    design_truth,
    hardware_design,
    implementation_spec,
    execution_intents,
    execution_authorization,
    existing,
    command: str | None,
    conversation_id: str,
    requester: str,
    schematic_planning=None,
    source_request_id: str | None = None,
    trace_id: str | None = None,
):
    approved_plan = None
    if (
        schematic_planning is not None
        and schematic_planning.status is SchematicPlanningStatus.APPROVED
    ):
        approved_plan = schematic_planning.approved_plan
    if existing is not None and existing.generation_receipt is not None:
        receipt_plan_fingerprint = existing.generation_receipt.schematic_plan_fingerprint
        if approved_plan is None or receipt_plan_fingerprint != approved_plan.fingerprint:
            try:
                return _mark_schematic_production_stale(existing)
            except (TypeError, ValueError):
                return None
    if existing is not None:
        # Re-derive the complete request/proposal before preserving a queued
        # state. This catches hardware, intent, task and authorization drift,
        # rather than comparing only a subset of upstream fingerprints.
        try:
            if (
                design_truth is not None
                and hardware_design is not None
                and implementation_spec is not None
                and execution_authorization is not None
            ):
                current_readiness = evaluate_schematic_readiness(
                    design_truth,
                    hardware_design,
                    implementation_spec,
                    approved_schematic_plan=approved_plan,
                )
                if current_readiness.status.value == "READY":
                    current_request = generate_schematic_production_request(
                        design_truth=design_truth,
                        hardware_design=hardware_design,
                        implementation=implementation_spec,
                        authorization=execution_authorization,
                        readiness=current_readiness,
                        execution_intents=execution_intents,
                        conversation_id=conversation_id,
                        requester=requester,
                        source_request_id=existing.request.source_request_id,
                        trace_id=existing.request.trace_id,
                        approved_schematic_plan=approved_plan,
                    )
                    current_proposal = generate_schematic_proposal(current_request, design_truth)
                    if (
                        current_request.fingerprint == existing.request.fingerprint
                        and current_proposal.fingerprint == existing.proposal.fingerprint
                    ):
                        return existing
        except (TypeError, ValueError):
            pass
    if command is None or not is_schematic_generation_request(command) or design_truth is None or hardware_design is None or implementation_spec is None or execution_authorization is None or approved_plan is None:
        return None
    readiness = evaluate_schematic_readiness(
        design_truth,
        hardware_design,
        implementation_spec,
        approved_schematic_plan=approved_plan,
    )
    if readiness.status.value != "READY":
        return None
    try:
        request = generate_schematic_production_request(
            design_truth=design_truth,
            hardware_design=hardware_design,
            implementation=implementation_spec,
            authorization=execution_authorization,
            readiness=readiness,
            execution_intents=execution_intents,
            conversation_id=conversation_id,
            requester=requester,
            source_request_id=source_request_id,
            trace_id=trace_id,
            approved_schematic_plan=approved_plan,
        )
        proposal = generate_schematic_proposal(request, design_truth)
        return create_schematic_production_state(request, proposal)
    except (TypeError, ValueError):
        return None
from .location_service import ProjectLocationService
from .store import SQLiteProjectCatalogRepository


class ProjectClockPort(Protocol):
    def now(self) -> datetime: ...


class BrownfieldWorkspaceSnapshotSource(Protocol):
    def snapshot(self, *, tenant_id: str, project_id: str): ...


class SystemProjectClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_IGNORED = {".embedded-copilot", ".git", "build", "out", "dist", "node_modules"}
_FIRMWARE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".ino", ".s", ".S"}
_HARDWARE_SUFFIXES = {".kicad_pcb", ".kicad_sch", ".pcb", ".sch", ".brd"}
_DOCUMENT_SUFFIXES = {".md", ".pdf", ".txt", ".rst"}
_WORKBENCH_SUFFIXES = _FIRMWARE_SUFFIXES | _HARDWARE_SUFFIXES | _DOCUMENT_SUFFIXES | {".kicad_pro", ".csv", ".yaml", ".yml", ".toml", ".json"}
_TEST_TOKENS = {"test", "tests", "hil", "validation"}
_LOCATION_ID = "default-project-root"
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class ProjectCatalogRuntime:
    def __init__(
        self,
        *,
        project_root: Path | None,
        state_database: Path,
        metadata_directory: Path | None = None,
        location_label: str = "Embedded Projects",
        clock: ProjectClockPort | None = None,
        initialize_async: bool = True,
        chat_runtime: object | None = None,
        approved_memory_store: object | None = None,
        knowledge_manifest_store: object | None = None,
        v63_memory_governance: object | None = None,
        location_allow_list: tuple[Path, ...] = (),
        production_mode: bool = False,
        pcb_production_service: object | None = None,
        hardware_component_source: object | None = None,
        research_service: EngineeringResearchService | None = None,
    ) -> None:
        root = None if project_root is None else Path(project_root)
        if root is not None and (not root.exists() or not root.is_dir() or root.is_symlink()):
            raise ProjectCatalogUnavailable("project root is unavailable")
        self._root = None if root is None else root.resolve(strict=True)
        database = Path(state_database)
        database_target = database.resolve(strict=False)
        metadata_target = (Path(metadata_directory) if metadata_directory is not None else database.parent / ".embedded-copilot" / "projects").resolve(strict=False)
        if self._root is not None and (
            database_target == self._root
            or database_target.is_relative_to(self._root)
            or metadata_target == self._root
            or metadata_target.is_relative_to(self._root)
        ):
            raise ProjectCatalogUnavailable("desktop data directory must be outside the project root")
        self._repository = SQLiteProjectCatalogRepository(database_target)
        self._brownfield_workspace: BrownfieldWorkspaceSnapshotSource | None = None
        self._drc_baseline_collector = None
        self._native_move_setup = None
        self._location_service = ProjectLocationService(
            database=database_target,
            default_root=self._root,
            metadata_directory=metadata_target,
            default_label=location_label,
            allow_list=location_allow_list,
            purge_invalid_persisted=production_mode,
            production_mode=production_mode,
        )
        self._metadata_directory = metadata_target
        self._location_label = location_label.strip() or "Embedded Projects"
        self._clock = clock or SystemProjectClock()
        self._async = initialize_async
        self._chat = chat_runtime
        self._approved_memory = approved_memory_store
        self._knowledge_manifest = knowledge_manifest_store
        self._v63_memory_governance = v63_memory_governance
        self._pcb_production_service = pcb_production_service
        self._hardware_component_source = hardware_component_source
        self._source_publication_host = None
        self._schematic_evidence_execution = None
        self._schematic_parameter_port = None
        self._research_service = research_service or self._default_research_service()
        self._artifact_intelligence = ArtifactIntelligenceService()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="project-context") if initialize_async else None

    def set_chat_runtime(self, runtime: object | None) -> None:
        self._chat = runtime

    def set_workspace_snapshot_source(self, source: BrownfieldWorkspaceSnapshotSource | None) -> None:
        if source is not None and not callable(getattr(source, "snapshot", None)):
            raise TypeError("workspace snapshot source is invalid")
        self._brownfield_workspace = source

    def set_drc_baseline_collector(self, collector: object | None) -> None:
        from embedded_copilot.execution_worker.drc_baseline import PcbDrcBaselineCollector
        if collector is not None and (not isinstance(collector, PcbDrcBaselineCollector) or collector.workspace is not self._brownfield_workspace):
            raise TypeError("canonical baseline composition required")
        self._drc_baseline_collector = collector

    def set_native_move_production_setup(self, setup: object | None) -> None:
        from embedded_copilot.execution_worker.native_move import (
            NativeMoveProductionSetup,
        )

        if setup is not None and (not isinstance(setup, NativeMoveProductionSetup) or setup.workspace is not self._brownfield_workspace):
            raise TypeError("native move production setup is not trusted Workspace composition")
        self._native_move_setup = setup

    def set_knowledge_manifest_store(self, store: object | None) -> None:
        if store is not None and not callable(getattr(store, "tombstone_project", None)):
            raise TypeError("knowledge manifest store is invalid")
        self._knowledge_manifest = store

    def set_v63_memory_governance(self, service: object | None) -> None:
        if service is not None and not callable(getattr(service, "tombstone_project", None)):
            raise TypeError("v6.3 tombstone service is invalid")
        self._v63_memory_governance = service

    def set_pcb_production_service(self, service: object | None) -> None:
        if service is not None and not callable(getattr(service, "create_proposal", None)):
            raise TypeError("PCB production service is invalid")
        self._pcb_production_service = service

    def set_source_publication_host(self, host):
        from .source_publication_host import CatalogSourcePublicationHost
        if type(host) is not CatalogSourcePublicationHost or host._catalog is not self or host._repository is not self._repository:
            raise ProjectCatalogUnavailable("source publication host binding is invalid")
        self._source_publication_host = host

    def hardware_component_source_for(self, detail: ConversationDetail):
        if self._source_publication_host is not None:
            # No fallback: a host-bound project requires its qualified publication.
            return self._source_publication_host.source_for(tenant_id=detail.tenant_id,
                project_id=detail.project_id, requester=detail.requester)
        return self._hardware_component_source

    def set_hardware_component_source(self, source: object | None) -> None:
        if source is not None and not callable(getattr(source, "search", None)):
            raise TypeError("hardware component source is invalid")
        self._hardware_component_source = source

    @property
    def research_service(self) -> EngineeringResearchService:
        return self._research_service

    @staticmethod
    def _default_research_service() -> EngineeringResearchService:
        live_enabled = os.getenv("EMBRIX_G0R_ENABLE_LIVE_SOURCES", "").casefold() == "true"
        sources = (
            (GitHubResearchSource(), OSHWHubResearchSource(live=True), VendorReferenceSource(live=True))
            if live_enabled
            else tuple(
                UnavailableResearchSource(source_type, "LIVE_SOURCE_DISABLED")
                for source_type in (
                    ResearchSourceType.GITHUB,
                    ResearchSourceType.OSHWHUB,
                    ResearchSourceType.VENDOR_REFERENCE,
                )
            )
        )
        return EngineeringResearchService(sources=sources)

    def set_research_service(self, service: EngineeringResearchService) -> None:
        if not isinstance(service, EngineeringResearchService):
            raise TypeError("research service is invalid")
        self._research_service = service

    def start(self) -> None:
        if self._executor is not None:
            for project in self._repository.pending_projects():
                self._executor.submit(self._initialize, project.project_id)

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProjectCatalogUnavailable("project clock is invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _record_create_stage(
        stage: str,
        status: str,
        *,
        target: Path | None = None,
        write_attempted: bool = False,
        project_root_writable: bool | None = None,
        error: Exception | None = None,
    ) -> None:
        try:
            target_exists = bool(target is not None and target.exists())
        except (OSError, RuntimeError, ValueError):
            target_exists = False
        _logger.info(
            "project_catalog_create_diagnostic",
            extra={
                "event_name": "project_catalog_create_diagnostic",
                "stage": stage,
                "status": status,
                "exception_type": "-" if error is None else type(error).__name__,
                "reason_code": _create_diagnostic_reason(error),
                "target_exists": target_exists,
                "write_attempted": bool(write_attempted),
                "project_root_writable": project_root_writable,
            },
        )

    @staticmethod
    def _project_root_writable(root: Path) -> bool:
        try:
            return os.access(root, os.R_OK | os.W_OK | os.X_OK)
        except (OSError, RuntimeError, ValueError):
            return False

    def _relative(self, value: str, *, root: Path | None = None) -> tuple[str, Path]:
        active_root = self._root if root is None else root
        if active_root is None:
            raise ProjectCatalogRejected("project root is unavailable")
        normalized = value.replace("\\", "/").strip("/")
        pure = PurePosixPath(normalized)
        if (
            not normalized
            or value.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", value)
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.parts[0].casefold() == ".embedded-copilot"
        ):
            raise ProjectCatalogRejected("project relative path is invalid")
        target = active_root.joinpath(*pure.parts)
        current = active_root
        for part in pure.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ProjectCatalogRejected("project path contains a symbolic link")
        try:
            resolved = target.resolve(strict=False)
            resolved.relative_to(active_root)
        except (OSError, ValueError) as error:
            raise ProjectCatalogRejected("project path escapes the configured root") from error
        return pure.as_posix(), target

    @staticmethod
    def _safe_directory_name(value: str) -> str:
        name = unicodedata.normalize("NFC", value).strip()
        if (
            not name
            or len(name) > 96
            or name in {".", ".."}
            or any(character in name for character in '/\\<>:"|?*')
            or any(unicodedata.category(character).startswith("C") for character in name)
            or name.endswith((" ", "."))
            or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise ProjectCatalogRejected("project name cannot be used as a directory name")
        return name

    def _location(self, *, tenant_id: str = "*") -> ProjectLocationView:
        return self._location_service.view(_LOCATION_ID, tenant_id=tenant_id)

    def locations(self, *, tenant_id: str, requester: str) -> tuple[ProjectLocationView, ...]:
        return self._location_service.list(tenant_id=tenant_id, requester=requester)

    def register_location(self, *, path: str, display_label: str | None, tenant_id: str, requester: str) -> ProjectLocationView:
        return self._location_service.register(path=path, display_label=display_label, tenant_id=tenant_id, requester=requester)

    def location_audit(self, location_id: str, *, tenant_id: str) -> dict[str, str]:
        return self._location_service.audit_status(location_id, tenant_id=tenant_id)

    def _validate_location(self, reference: ProjectLocationReference) -> ProjectLocationView:
        location = self._location()
        if reference.location_id != location.location_id or reference.fingerprint != location.fingerprint:
            raise ProjectCatalogRejected("project location reference is invalid")
        return location

    def _validate_location_for_tenant(self, reference: ProjectLocationReference, *, tenant_id: str) -> ProjectLocationView:
        location = self._location_service.view(reference.location_id, tenant_id=tenant_id)
        if reference.fingerprint != location.fingerprint:
            raise ProjectCatalogRejected("project location reference is invalid")
        return location

    @staticmethod
    def _directory_id(name: str, location_id: str = _LOCATION_ID) -> str:
        digest = hashlib.sha256(f"{location_id}|{unicodedata.normalize('NFC', name)}".encode()).hexdigest()
        return f"directory-{digest[:24]}"

    def _directory_views(self, location_id: str, tenant_id: str) -> tuple[ProjectDirectoryView, ...]:
        root = self._location_service.resolve(location_id, tenant_id=tenant_id)
        registered = self._repository.registered_relative_paths(location_id)
        values: list[ProjectDirectoryView] = []
        for entry in sorted(os.scandir(root), key=lambda item: item.name.casefold()):
            if entry.name == ".embedded-copilot" or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in registered:
                continue
            stat = entry.stat(follow_symlinks=False)
            marker = hashlib.sha256(f"{entry.name}|{stat.st_dev}|{stat.st_ino}|{stat.st_mtime_ns}".encode()).hexdigest()
            values.append(ProjectDirectoryView(directory_id=self._directory_id(entry.name, location_id), display_name=entry.name, fingerprint=f"sha256:{marker}"))
        return tuple(values)

    def directories(self, location_id: str, *, tenant_id: str, requester: str) -> ProjectDirectoryPage:
        del requester
        return ProjectDirectoryPage(items=self._directory_views(location_id, tenant_id))

    def _validate_directory(self, reference: ProjectDirectoryReference, tenant_id: str, location_id: str) -> ProjectDirectoryView:
        match = next((item for item in self._directory_views(location_id, tenant_id) if item.directory_id == reference.directory_id), None)
        if match is None or match.fingerprint != reference.fingerprint:
            raise ProjectCatalogRejected("project directory reference is invalid")
        return match

    def preview(self, request: ProjectCreationRequest, *, tenant_id: str, requester: str) -> ProjectCreationPreview:
        checked = ProjectCreationRequest.model_validate(request.model_dump(mode="python"))
        if checked.creation_mode is None:
            relative, target = self._relative(checked.relative_path or "")
            creation_mode = ProjectCreationMode.ATTACH_EXISTING if checked.project_type is ProjectType.EXISTING_FIRMWARE else ProjectCreationMode.CREATE_NEW
            location = self._location(tenant_id=tenant_id)
            directory_name = PurePosixPath(relative).name
            existing_directory = None
        else:
            location = self._validate_location_for_tenant(checked.parent_location or checked.location, tenant_id=tenant_id)
            location_root = self._location_service.resolve(location.location_id, tenant_id=tenant_id)
            creation_mode = checked.creation_mode
            if creation_mode is ProjectCreationMode.CREATE_NEW:
                if not self._project_root_writable(location_root):
                    raise ProjectTargetNotWritable("project target root is not writable")
                directory_name = self._safe_directory_name(checked.name)
                relative, target = self._relative(directory_name, root=location_root)
                existing_directory = None
            else:
                existing_directory = self._validate_directory(checked.existing_directory, tenant_id, location.location_id)
                directory_name = existing_directory.display_name
                relative, target = self._relative(directory_name, root=location_root)
        if creation_mode is ProjectCreationMode.ATTACH_EXISTING and (not target.exists() or not target.is_dir()):
            raise ProjectCatalogRejected("existing firmware directory was not found")
        if creation_mode is ProjectCreationMode.CREATE_NEW and target.exists():
            raise ProjectCatalogConflict("project directory already exists")
        now = self._now()
        preview = ProjectCreationPreview(
            preview_id=f"preview-{uuid4()}",
            tenant_id=tenant_id,
            requester=requester,
            name=checked.name,
            location=location,
            existing_directory=existing_directory,
            directory_name=directory_name,
            creation_mode=creation_mode,
            relative_path=relative,
            project_type=checked.project_type,
            mode=ProjectLegacyMode.CREATE if creation_mode is ProjectCreationMode.CREATE_NEW else ProjectLegacyMode.ATTACH,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            write_summary=(
                ("创建项目目录", "保存受控私有元数据")
                if creation_mode is ProjectCreationMode.CREATE_NEW
                else ("建立 Project metadata binding", "只读扫描现有工程")
            ),
        )
        try:
            self._repository.save_preview(preview)
        except ProjectCatalogConflict:
            raise
        except Exception as error:
            raise ProjectCatalogUnavailable("project preview store is unavailable") from error
        return preview

    def create(self, preview_id: str, preview_fingerprint: str, *, confirmed_at: datetime, tenant_id: str, requester: str) -> ProjectView:
        self._record_create_stage("C0_CREATE_ENTER", "PASS")
        stored = self._repository.preview(preview_id)
        if stored is None:
            raise ProjectCatalogRejected("project preview was not found")
        preview, consumed, stored_fingerprint = stored
        if consumed:
            raise ProjectCatalogConflict("project preview was already consumed")
        if preview.tenant_id != tenant_id or preview.requester != requester:
            raise ProjectCatalogForbidden("project preview identity mismatch")
        if stored_fingerprint != preview_fingerprint:
            raise ProjectCatalogRejected("project preview fingerprint mismatch")
        if confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None:
            raise ProjectCatalogRejected("project confirmation time is invalid")
        confirmation = confirmed_at.astimezone(UTC)
        if confirmation < preview.created_at or confirmation > preview.expires_at:
            raise ProjectCatalogExpired("project preview expired")
        legacy_preview = stored_fingerprint != preview.fingerprint
        if not legacy_preview:
            self._validate_location_for_tenant(ProjectLocationReference(location_id=preview.location.location_id, fingerprint=preview.location.fingerprint), tenant_id=tenant_id)
        target: Path | None = None
        project_root_writable: bool | None = None
        try:
            project_root = self._location_service.resolve(preview.location.location_id, tenant_id=tenant_id)
            project_root_writable = self._project_root_writable(project_root)
            relative, target = self._relative(preview.relative_path, root=project_root)
            target_exists = target.exists()
            if preview.creation_mode is ProjectCreationMode.CREATE_NEW and target_exists:
                raise ProjectCatalogConflict("project directory already exists")
        except Exception as error:
            self._record_create_stage(
                "C1_TARGET_PREPARE",
                "FAIL",
                target=target,
                project_root_writable=project_root_writable,
                error=error,
            )
            raise
        self._record_create_stage(
            "C1_TARGET_PREPARE",
            "PASS",
            target=target,
            project_root_writable=project_root_writable,
        )
        created_directory = False
        if preview.creation_mode is ProjectCreationMode.CREATE_NEW:
            if not project_root_writable:
                error = ProjectTargetNotWritable("project target root is not writable")
                self._record_create_stage(
                    "C2_TARGET_DIR_CREATE",
                    "FAIL",
                    target=target,
                    project_root_writable=False,
                    error=error,
                )
                raise error
            try:
                target.mkdir(parents=True, exist_ok=False)
            except PermissionError as error:
                failure = ProjectTargetNotWritable("project target root is not writable")
                self._record_create_stage(
                    "C2_TARGET_DIR_CREATE",
                    "FAIL",
                    target=target,
                    project_root_writable=True,
                    write_attempted=True,
                    error=failure,
                )
                raise failure from error
            except Exception as error:
                self._record_create_stage(
                    "C2_TARGET_DIR_CREATE",
                    "FAIL",
                    target=target,
                    project_root_writable=project_root_writable,
                    write_attempted=True,
                    error=error,
                )
                raise
            created_directory = True
            self._record_create_stage(
                "C2_TARGET_DIR_CREATE",
                "PASS",
                target=target,
                project_root_writable=project_root_writable,
                write_attempted=True,
            )
        else:
            if preview.existing_directory is not None:
                self._validate_directory(ProjectDirectoryReference(directory_id=preview.existing_directory.directory_id, fingerprint=preview.existing_directory.fingerprint), tenant_id, preview.location.location_id)
            if not target.exists() or not target.is_dir() or target.is_symlink():
                raise ProjectCatalogRejected("existing firmware directory was not found")
            self._record_create_stage(
                "C2_TARGET_DIR_CREATE",
                "PASS",
                target=target,
                project_root_writable=project_root_writable,
            )
        now = self._now()
        project = ProjectView(
            project_id=f"project-{uuid4()}",
            tenant_id=tenant_id,
            name=preview.name,
            location_id=preview.location.location_id,
            directory_name=preview.directory_name,
            creation_mode=preview.creation_mode,
            relative_path=relative,
            project_type=preview.project_type,
            initialization_status=ProjectInitializationStatus.PENDING,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        try:
            self._write_sidecar(project)
        except Exception as error:
            self._record_create_stage(
                "C3_SIDECAR_WRITE",
                "FAIL",
                target=target,
                project_root_writable=project_root_writable,
                write_attempted=True,
                error=error,
            )
            sidecar = self._metadata_directory / f"{project.project_id}.json"
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
            if created_directory:
                try:
                    target.rmdir()
                except OSError:
                    pass
            raise
        self._record_create_stage(
            "C3_SIDECAR_WRITE",
            "PASS",
            target=target,
            project_root_writable=project_root_writable,
            write_attempted=True,
        )
        try:
            self._repository.commit_project(preview.preview_id, stored_fingerprint, project, requester=requester)
        except Exception as error:
            self._record_create_stage(
                "C4_PROJECT_RECORD_WRITE",
                "FAIL",
                target=target,
                project_root_writable=project_root_writable,
                write_attempted=True,
                error=error,
            )
            self._record_create_stage(
                "C5_COMMIT_COMPLETE",
                "FAIL",
                target=target,
                project_root_writable=project_root_writable,
                error=error,
            )
            sidecar = self._metadata_directory / f"{project.project_id}.json"
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
            if created_directory:
                try:
                    target.rmdir()
                except OSError:
                    pass
            raise
        self._record_create_stage(
            "C4_PROJECT_RECORD_WRITE",
            "PASS",
            target=target,
            project_root_writable=project_root_writable,
            write_attempted=True,
        )
        self._record_create_stage(
            "C5_COMMIT_COMPLETE",
            "PASS",
            target=target,
            project_root_writable=project_root_writable,
        )
        if self._executor is not None:
            initializing = self._mark_initializing(project)
            self._executor.submit(self._initialize, project.project_id)
            return initializing
        return self._initialize(project.project_id)

    def _write_sidecar(self, project: ProjectView) -> None:
        directory = self._metadata_directory
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{project.project_id}.json"
        temporary = directory / f".{project.project_id}.tmp"
        payload = {
            "schema": "embedded-copilot.project.v1",
            "project_id": project.project_id,
            "tenant_id": project.tenant_id,
            "name": project.name,
            "location_id": project.location_id,
            "directory_name": project.directory_name,
            "creation_mode": project.creation_mode.value,
            "relative_path": project.relative_path,
            "project_type": project.project_type.value,
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(target)

    def _initialize(self, project_id: str) -> ProjectView:
        project = self._repository.project(project_id)
        if project is None:
            raise ProjectCatalogRejected("project was not found")
        initializing = self._mark_initializing(project)
        try:
            context, metadata = self._scan(initializing)
            self._repository.save_context(context)
            ready = initializing.model_copy(update={
                "initialization_status": ProjectInitializationStatus.READY,
                "platform": metadata.get("platform"),
                "version": metadata.get("version"),
                "branch": metadata.get("branch"),
                "updated_at": context.generated_at,
                "last_activity_at": context.generated_at,
                "failure_code": None,
                "fingerprint": "sha256:" + "0" * 64,
            })
            ready = ProjectView.model_validate(ready.model_dump(mode="python"))
            self._repository.update_project(ready)
            return ready
        except Exception:  # noqa: BLE001 - initialization failures are projected as a fixed safe state
            failed = initializing.model_copy(update={"initialization_status": ProjectInitializationStatus.FAILED, "failure_code": "CONTEXT_INITIALIZATION_FAILED", "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
            failed = ProjectView.model_validate(failed.model_dump(mode="python"))
            self._repository.update_project(failed)
            return failed

    def _mark_initializing(self, project: ProjectView) -> ProjectView:
        if project.initialization_status is ProjectInitializationStatus.INITIALIZING:
            return project
        initializing = project.model_copy(update={"initialization_status": ProjectInitializationStatus.INITIALIZING, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        initializing = ProjectView.model_validate(initializing.model_dump(mode="python"))
        self._repository.update_project(initializing)
        return initializing

    def _scan(self, project: ProjectView) -> tuple[ProjectContextView, dict[str, str]]:
        project_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
        _, root = self._relative(project.relative_path, root=project_root)
        refs: dict[str, list[ProjectContextReference]] = {"Firmware": [], "Hardware": [], "Documents": [], "Tests": []}
        count = 0
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > 8:
                continue
            for entry in sorted(os.scandir(directory), key=lambda item: item.name.casefold()):
                if entry.name in _IGNORED or entry.is_symlink():
                    continue
                count += 1
                if count > 5000:
                    raise ProjectCatalogUnavailable("project scan limit exceeded")
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append((path, depth + 1))
                    continue
                category = self._category(path, root)
                if category is None:
                    continue
                relative = path.relative_to(root).as_posix()
                digest = hashlib.sha256(f"{project.project_id}|{relative}".encode()).hexdigest()
                refs[category].append(ProjectContextReference(
                    reference_id=f"file-{digest[:24]}",
                    category=category,
                    display_label=path.name,
                    source_fingerprint=f"sha256:{digest}",
                ))
        groups = tuple(ProjectContextGroup(category=category, references=tuple(values)) for category, values in refs.items() if values)
        generated = self._now()
        context = ProjectContextView(
            project_id=project.project_id,
            status=ProjectInitializationStatus.READY,
            groups=groups,
            evidence_sources=(),
            capabilities=(
                ProjectCapability(capability="Project Files", status="AVAILABLE"),
                ProjectCapability(capability="HIL", status="UNAVAILABLE"),
                ProjectCapability(capability="EDA", status="UNAVAILABLE"),
                ProjectCapability(capability="Knowledge", status="UNAVAILABLE"),
            ),
            generated_at=generated,
        )
        return context, self._metadata(root, project.project_type)

    @staticmethod
    def _category(path: Path, root: Path) -> str | None:
        suffix = path.suffix.lower()
        tokens = {part.casefold() for part in path.relative_to(root).parts[:-1]}
        if tokens & _TEST_TOKENS:
            return "Tests"
        if suffix in _FIRMWARE_SUFFIXES or path.name in {"CMakeLists.txt", "sdkconfig", "platformio.ini"}:
            return "Firmware"
        if suffix in _HARDWARE_SUFFIXES:
            return "Hardware"
        if suffix in _DOCUMENT_SUFFIXES:
            return "Documents"
        return None

    @staticmethod
    def _metadata(root: Path, project_type: ProjectType) -> dict[str, str]:
        result: dict[str, str] = {
            "platform": {
                ProjectType.ESP_IDF: "ESP-IDF",
                ProjectType.STM32: "STM32",
                ProjectType.ARDUINO: "Arduino",
                ProjectType.GENERIC_FIRMWARE: "Generic Firmware",
                ProjectType.EXISTING_FIRMWARE: "Existing Firmware",
            }[project_type]
        }
        head = root / ".git" / "HEAD"
        if head.is_file() and not head.is_symlink() and head.stat().st_size <= 128 * 1024:
            value = head.read_text(encoding="utf-8", errors="replace").strip()
            if value.startswith("ref: refs/heads/"):
                result["branch"] = value.removeprefix("ref: refs/heads/")[:128]
        sdkconfig = root / "sdkconfig"
        if sdkconfig.is_file() and not sdkconfig.is_symlink() and sdkconfig.stat().st_size <= 128 * 1024:
            content = sdkconfig.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'^CONFIG_IDF_TARGET="?([A-Za-z0-9_-]+)"?$', content, re.MULTILINE)
            if match:
                result["platform"] = match.group(1).upper().replace("ESP32S3", "ESP32-S3")
        version = root / "version.txt"
        if version.is_file() and not version.is_symlink() and version.stat().st_size <= 128 * 1024:
            value = version.read_text(encoding="utf-8", errors="replace").strip()
            if value and len(value) <= 128 and "\x00" not in value:
                result["version"] = value
        return result

    def get_project(self, project_id: str, *, tenant_id: str) -> ProjectView:
        value = self._repository.project(project_id)
        if value is None:
            raise ProjectCatalogNotFound("project was not found")
        if value.tenant_id != tenant_id:
            raise ProjectCatalogForbidden("project identity mismatch")
        return value

    def get_project_for_requester(
        self, project_id: str, *, tenant_id: str, requester: str
    ) -> ProjectView:
        project = self.get_project(project_id, tenant_id=tenant_id)
        if self._repository.project_owner(project_id) != requester:
            raise ProjectCatalogForbidden("project requester mismatch")
        return project

    @staticmethod
    def _eda_import_fingerprint(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _eda_import_source(self, project: ProjectView, native_entry: str, backend: str) -> dict[str, object]:
        location_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
        _, bound_root = self._relative(project.relative_path, root=location_root)
        cursor = location_root
        for part in PurePosixPath(project.relative_path).parts:
            cursor = cursor / part
            if cursor.exists() and self._artifact_intelligence._eda_import_reparse(cursor):
                raise ProjectCatalogUnavailable("IMPORT_SOURCE_UNAVAILABLE")
        if not bound_root.is_dir() or bound_root.is_symlink():
            raise ProjectCatalogUnavailable("IMPORT_SOURCE_UNAVAILABLE")
        try:
            return self._artifact_intelligence.scan_eda_import_directory(
                root=bound_root.resolve(strict=True), native_entry_relative_path=native_entry, backend=backend
            )
        except ValueError as error:
            code = str(error)
            if code == "SOURCE_SET_CHANGED":
                raise ProjectCatalogConflict(code) from error
            if code == "IMPORT_INPUT_REJECTED":
                raise ProjectCatalogRejected(code) from error
            raise ProjectCatalogUnavailable("IMPORT_SOURCE_UNAVAILABLE") from error
        except OSError as error:
            raise ProjectCatalogUnavailable("IMPORT_SOURCE_UNAVAILABLE") from error

    def _eda_import_entry_missing(self, project: ProjectView, native_entry: str) -> bool:
        try:
            location_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
            _, bound_root = self._relative(project.relative_path, root=location_root)
            return bound_root.is_dir() and not bound_root.joinpath(*native_entry.split("/")).exists()
        except (OSError, ProjectCatalogRejected, ProjectCatalogUnavailable):
            return False

    def _eda_import_material(
        self, project: ProjectView, *, tenant_id: str, requester: str, backend: str,
        native_entry: str, selected: tuple[str, ...], binding: str,
    ) -> dict[str, object]:
        from embedded_copilot.engineering_eda.adapters.jlceda_pro_v3 import (
            observe_jlceda_entry,
        )
        from embedded_copilot.pcb.parser.kicad import observe_kicad_pcb_bytes

        scan = self._eda_import_source(project, native_entry, backend)
        manifest = scan["manifest"]
        payloads = scan["payloads"]
        assert isinstance(manifest, list) and isinstance(payloads, dict)
        source_files = {item["relative_path"] for item in manifest if item["kind"] == "FILE"}
        scope = str(scan["scope_root_relative_path"])
        for path in selected:
            try:
                normalized = self._artifact_intelligence._eda_import_relative(path)
            except ValueError as error:
                raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED") from error
            if normalized not in source_files or (scope != "." and not normalized.startswith(scope + "/")):
                raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED")
        objects: list[dict[str, object]] = []
        issues: list[dict[str, object]] = list(scan["closure_issues"])
        versions: dict[str, str] = {}
        if backend == "JLCEDA_PRO":
            observed = observe_jlceda_entry(native_entry, payloads[native_entry])
            versions[native_entry] = observed.native_format_version
            issues.extend({"kind": item.kind, "code": item.code, "locator": native_entry, "blocks_mutation": item.blocks_mapping} for item in observed.issues)
            ruleset = "jlceda-index-inventory-r2"
            subset = "opaque-inventory-r1"
            family = "JLCEDA_EPRJ3_DIRECTORY"
        else:
            if native_entry.endswith(".kicad_pcb"):
                boards = (native_entry,)
            else:
                expected_board = native_entry.removesuffix(".kicad_pro") + ".kicad_pcb"
                boards = (expected_board,) if expected_board in source_files else ()
            boards = tuple(dict.fromkeys((*boards, *(path for path in selected if path.endswith(".kicad_pcb")))))
            for board in boards:
                if board not in payloads:
                    raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED")
                observed = observe_kicad_pcb_bytes(board, payloads[board])
                versions[board] = observed.native_format_version
                issues.extend({"kind": item.kind, "code": item.code, "locator": board + item.locator, "blocks_mutation": item.blocks_mapping} for item in observed.issues)
                objects.extend({"object_type": "FOOTPRINT", "source_file": board, **asdict(item)} for item in observed.footprints)
            if native_entry.endswith(".kicad_pro"):
                versions[native_entry] = "UNKNOWN"
                issues.append({"kind": "UNKNOWN", "code": "PROJECT_FORMAT_UNPARSED", "locator": native_entry, "blocks_mutation": True})
            for schematic in sorted(path for path in payloads if path.endswith(".kicad_sch")):
                versions[schematic] = "UNKNOWN"
                issues.append({"kind": "UNKNOWN", "code": "SCHEMATIC_SEMANTICS_UNSUPPORTED", "locator": schematic, "blocks_mutation": True})
            supported_board = any(version == "20240108" for version in versions.values())
            ruleset = "kicad-pcb-footprint-20240108-r2" if supported_board else "inventory-only-r1"
            subset = "kicad-footprint-placement-r1" if supported_board else "opaque-inventory-r1"
            family = "KICAD_PROJECT_DIRECTORY"
        coverage = None
        if backend == "KICAD":
            from embedded_copilot.engineering_eda.kicad_project_coverage import (
                parse_kicad_project_coverage,
            )
            from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
                CoverageSourceFile,
            )
            coverage = parse_kicad_project_coverage(
                native_entry_relative_path=native_entry, source_set_fingerprint=str(scan["source_set_fingerprint"]),
                payloads=payloads, expected_files=tuple(CoverageSourceFile(
                    relative_path=str(item["relative_path"]), byte_size=item["byte_size"], sha256=str(item["sha256"]))
                    for item in manifest if item["kind"] == "FILE"))
            if coverage is not None:
                ruleset = "kicad-project-source-20240108-r2"
                subset = "kicad-project-source-coverage-r1"
        for item in manifest:
            if item["kind"] == "MISSING_DEPENDENCY":
                issues.append({"kind": "LOSS", "code": "MISSING_DEPENDENCY", "locator": item["relative_path"], "blocks_mutation": True})
            elif item["kind"] == "FILE" and item["relative_path"] not in {native_entry, *versions}:
                issues.append({"kind": "UNKNOWN", "code": "DISCOVERED_UNPARSED", "locator": item["relative_path"], "blocks_mutation": True})
        if backend == "KICAD" and native_entry.endswith(".kicad_pcb"):
            issues.append({"kind": "UNKNOWN", "code": "STANDALONE_BOARD", "locator": native_entry, "blocks_mutation": True})
        issues.sort(key=lambda item: (str(item["locator"]), str(item["code"])))
        objects.sort(key=lambda item: (str(item["source_file"]), str(item["locator"])))
        completeness = "UNKNOWN" if not objects else "PARTIAL" if issues else "COMPLETE_FOR_DECLARED_SUBSET"
        rule_fp = self._eda_import_fingerprint({"id": ruleset, "semantic_rules": ("native-version-gated-observation-v2-reject-ambiguous-footprint-singletons" if ruleset in {"kicad-pcb-footprint-20240108-r2", "kicad-project-source-20240108-r2"} else "native-version-gated-observation-v1")})
        subset_fp = self._eda_import_fingerprint({"id": subset, "objects": "footprint-uuid-placement-only" if subset == "kicad-footprint-placement-r1" else "none"})
        payload: dict[str, object] = {
            "schema": "eda-import-revision-v1", "tenant_id": tenant_id, "project_id": project.project_id,
            "requester": requester, "project_binding_fingerprint": binding, "backend": backend,
            "native_entry_relative_path": native_entry, "native_format_family": family,
            "native_format_versions": versions, "source_set_fingerprint": scan["source_set_fingerprint"],
            "scope_policy_id": scan["scope_policy_id"], "scope_root_relative_path": scan["scope_root_relative_path"],
            "manifest": manifest, "parser_ruleset_id": ruleset, "parser_ruleset_fingerprint": rule_fp,
            "supported_subset_id": subset, "supported_subset_fingerprint": subset_fp,
            "objects": objects, "issues": issues, "semantic_completeness": completeness,
            "verified": False, "can_modify": False,
        }
        if coverage is not None:
            payload["project_coverage"] = coverage.model_dump(mode="json")
        payload["import_revision_fingerprint"] = self._eda_import_fingerprint(payload)
        return payload

    def create_eda_import_preview(
        self, project_id: str, *, tenant_id: str, requester: str, backend: str,
        native_entry_relative_path: str, selected_native_entries: tuple[str, ...],
        expected_head_revision_id: str | None, expected_head_revision_fingerprint: str | None,
    ) -> dict[str, object]:
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        if backend not in {"KICAD", "JLCEDA_PRO"} or (expected_head_revision_id is None) != (expected_head_revision_fingerprint is None):
            raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED")
        try:
            native_entry = self._artifact_intelligence._eda_import_relative(native_entry_relative_path)
        except ValueError as error:
            raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED") from error
        binding = self._repository.eda_import_project_binding_fingerprint(project_id, tenant_id=tenant_id, requester=requester)
        revision = self._eda_import_material(project, tenant_id=tenant_id, requester=requester, backend=backend,
                                             native_entry=native_entry, selected=selected_native_entries, binding=binding)
        now = self._now()
        candidate: dict[str, object] = {
            "preview_id": f"eda-preview-{uuid4()}", "tenant_id": tenant_id, "project_id": project_id,
            "requester": requester, "backend": backend, "native_entry_relative_path": native_entry,
            "project_binding_fingerprint": binding, "expected_head_revision_id": expected_head_revision_id,
            "expected_head_revision_fingerprint": expected_head_revision_fingerprint,
            "selected_native_entries": list(selected_native_entries),
            "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(),
            "revision": revision,
        }
        candidate["preview_fingerprint"] = self._eda_import_fingerprint(candidate)
        self._repository.save_eda_import_preview(candidate)
        return {"status": "PREVIEW", "preview_id": candidate["preview_id"], "preview_fingerprint": candidate["preview_fingerprint"],
                "expires_at": candidate["expires_at"], "backend": backend, "native_entry_relative_path": native_entry,
                "native_format_family": revision["native_format_family"], "native_format_versions": revision["native_format_versions"],
                "source_set_fingerprint": revision["source_set_fingerprint"], "parser_ruleset_id": revision["parser_ruleset_id"],
                "supported_subset_id": revision["supported_subset_id"], "semantic_completeness": revision["semantic_completeness"],
                "manifest": revision["manifest"], "objects": revision["objects"], "issues": revision["issues"],
                "verified": False, "can_modify": False,
                **({"project_coverage": revision["project_coverage"]} if "project_coverage" in revision else {})}

    def commit_eda_import_preview(
        self, project_id: str, preview_id: str, preview_fingerprint: str, *, tenant_id: str, requester: str
    ) -> dict[str, object]:
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        candidate = self._repository.eda_import_preview(project_id, preview_id, tenant_id=tenant_id, requester=requester)
        if candidate is None or candidate.get("preview_fingerprint") != preview_fingerprint:
            raise ProjectCatalogRejected("IMPORT_PREVIEW_INVALID")
        binding = self._repository.eda_import_project_binding_fingerprint(project_id, tenant_id=tenant_id, requester=requester)
        if candidate["project_binding_fingerprint"] != binding:
            raise ProjectCatalogConflict("IMPORT_PROJECT_CHANGED")
        if candidate.get("status") != "COMMITTED":
            try:
                expires_at = datetime.fromisoformat(str(candidate["expires_at"]))
            except ValueError as error:
                raise ProjectCatalogUnavailable("IMPORT_PREVIEW_INVALID") from error
            if expires_at <= self._now():
                raise ProjectCatalogExpired("IMPORT_PREVIEW_EXPIRED")
            revision = candidate["revision"]
            assert isinstance(revision, dict)
            try:
                current = self._eda_import_material(
                    project, tenant_id=tenant_id, requester=requester, backend=str(candidate["backend"]),
                    native_entry=str(candidate["native_entry_relative_path"]),
                    selected=tuple(candidate.get("selected_native_entries", ())), binding=binding,
                )
            except ProjectCatalogUnavailable as error:
                if self._eda_import_entry_missing(project, str(candidate["native_entry_relative_path"])):
                    raise ProjectCatalogConflict("SOURCE_SET_CHANGED") from error
                raise
            if current["source_set_fingerprint"] != revision["source_set_fingerprint"]:
                raise ProjectCatalogConflict("SOURCE_SET_CHANGED")
            if (current["parser_ruleset_fingerprint"], current["supported_subset_fingerprint"]) != (
                revision["parser_ruleset_fingerprint"], revision["supported_subset_fingerprint"]
            ) or current["import_revision_fingerprint"] != revision["import_revision_fingerprint"]:
                raise ProjectCatalogConflict("PARSER_RULESET_CHANGED")
        stored, outcome = self._repository.commit_eda_import_preview(
            project_id, preview_id, preview_fingerprint, tenant_id=tenant_id, requester=requester,
            now=self._now(), current_project_fingerprint=binding,
        )
        projected = self._eda_import_projected(project, stored)
        return {"status": "IMPORTED_OBSERVATION", **projected, "commit_outcome": outcome}

    def _eda_import_projected(self, project: ProjectView, stored: dict[str, object]) -> dict[str, object]:
        result = dict(stored)
        result.pop("schema", None)
        try:
            current = self._eda_import_source(project, str(stored["native_entry_relative_path"]), str(stored["backend"]))
            source_state = "CURRENT" if current["source_set_fingerprint"] == stored["source_set_fingerprint"] else "STALE"
            if source_state == "CURRENT" and any(item["kind"] == "EXTERNAL_DEPENDENCY" for item in current["manifest"]):
                source_state = "UNAVAILABLE"
        except (ProjectCatalogRejected, ProjectCatalogConflict, ProjectCatalogUnavailable):
            source_state = "UNAVAILABLE"
            if self._eda_import_entry_missing(project, str(stored["native_entry_relative_path"])):
                source_state = "STALE"
        observed_board = stored["backend"] == "KICAD" and "20240108" in stored.get("native_format_versions", {}).values()
        expected_rules = (
            "jlceda-index-inventory-r2" if stored["backend"] == "JLCEDA_PRO"
            else "kicad-pcb-footprint-20240108-r2" if observed_board
            else "inventory-only-r1"
        )
        expected_subset = "kicad-footprint-placement-r1" if observed_board else "opaque-inventory-r1"
        if "project_coverage" in stored:
            expected_rules = "kicad-project-source-20240108-r2"
            expected_subset = "kicad-project-source-coverage-r1"
        expected_rule_fp = self._eda_import_fingerprint({"id": expected_rules, "semantic_rules": ("native-version-gated-observation-v2-reject-ambiguous-footprint-singletons" if expected_rules in {"kicad-pcb-footprint-20240108-r2", "kicad-project-source-20240108-r2"} else "native-version-gated-observation-v1")})
        expected_subset_fp = self._eda_import_fingerprint({"id": expected_subset, "objects": "footprint-uuid-placement-only" if expected_subset == "kicad-footprint-placement-r1" else "none"})
        rules_state = "CURRENT" if (
            stored["parser_ruleset_id"] == expected_rules
            and stored["parser_ruleset_fingerprint"] == expected_rule_fp
            and stored["supported_subset_id"] == expected_subset
            and stored["supported_subset_fingerprint"] == expected_subset_fp
        ) else "STALE"
        if "project_coverage" in stored and source_state == "CURRENT":
            from embedded_copilot.engineering_eda.kicad_project_coverage import (
                validate_project_coverage_source,
            )
            from embedded_copilot.engineering_eda.kicad_project_coverage_models import (
                CoverageSourceFile,
                ProjectCoverageV1,
            )
            try:
                coverage = ProjectCoverageV1.model_validate_json(json.dumps(stored["project_coverage"], ensure_ascii=False))
                validate_project_coverage_source(coverage, current["payloads"], tuple(
                    CoverageSourceFile(relative_path=str(item["relative_path"]),
                        byte_size=item["byte_size"], sha256=str(item["sha256"]))
                    for item in current["manifest"] if item["kind"] == "FILE"))
            except (TypeError, ValueError):
                rules_state = "STALE"
        result.update({"source_currentness": source_state,
                       "observation_currentness": "CURRENT" if source_state == rules_state == "CURRENT" else "UNAVAILABLE" if source_state == "UNAVAILABLE" else "STALE",
                       "verified": False, "can_modify": False})
        return result

    def eda_import_revision(
        self, project_id: str, revision_id: str, fingerprint: str, *, tenant_id: str, requester: str
    ) -> dict[str, object]:
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        stored = self._repository.eda_import_revision(project_id, revision_id, fingerprint, tenant_id=tenant_id, requester=requester)
        if stored is None:
            raise ProjectCatalogNotFound("IMPORT_REVISION_NOT_FOUND")
        return self._eda_import_projected(project, stored)

    def eda_import_revisions(
        self, project_id: str, *, tenant_id: str, requester: str, backend: str,
        native_entry_relative_path: str, cursor: str | None = None,
    ) -> dict[str, object]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        try:
            entry = self._artifact_intelligence._eda_import_relative(native_entry_relative_path)
            offset = 0 if cursor is None else int(cursor)
            if offset < 0 or backend not in {"KICAD", "JLCEDA_PRO"}:
                raise ValueError("invalid")
        except (ValueError, TypeError) as error:
            raise ProjectCatalogRejected("IMPORT_INPUT_REJECTED") from error
        values = self._repository.eda_import_revisions(project_id, backend, entry, tenant_id=tenant_id, requester=requester)
        selected = values[offset:offset + 20]
        return {"items": tuple({key: item[key] for key in (
            "import_revision_id", "import_revision_fingerprint", "active", "created_at", "backend",
            "native_entry_relative_path", "source_set_fingerprint") } for item in selected),
                "next_cursor": str(offset + 20) if offset + 20 < len(values) else None}

    def _drc_baseline_material(self, project_id: str, revision_id: str, revision_fp: str,
                               pcb_path: str, *, tenant_id: str, requester: str) -> dict:
        from embedded_copilot.execution_worker.drc_baseline import PcbDrcBaselineCollector
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        collector = self._drc_baseline_collector
        if not isinstance(collector, PcbDrcBaselineCollector) or collector.workspace is not self._brownfield_workspace or not collector.available():
            raise ProjectCatalogUnavailable("DRC_BASELINE_UNAVAILABLE")
        revision = self.eda_import_revision(project_id, revision_id, revision_fp, tenant_id=tenant_id, requester=requester)
        if (revision["backend"] != "KICAD" or revision["active"] is not True
            or revision["source_currentness"] != "CURRENT" or revision["observation_currentness"] != "CURRENT"):
            raise ProjectCatalogConflict("SOURCE_STALE")
        frozen_revision = self._repository.eda_import_revision(project_id, revision_id, revision_fp, tenant_id=tenant_id, requester=requester)
        scan = self._eda_import_source(project, str(revision["native_entry_relative_path"]), "KICAD")
        manifest = scan["manifest"]
        if (scan["source_set_fingerprint"] != revision["source_set_fingerprint"] or manifest != revision["manifest"]
            or not isinstance(manifest, list) or any(item["kind"] != "FILE" for item in manifest)):
            raise ProjectCatalogConflict("SOURCE_STALE")
        try:
            normalized = self._artifact_intelligence._eda_import_relative(pcb_path)
        except (TypeError, ValueError) as error:
            raise ProjectCatalogRejected("DRC_BASELINE_INPUT_REJECTED") from error
        pcb = [item for item in manifest if item["relative_path"] == normalized]
        if normalized != pcb_path or not normalized.endswith(".kicad_pcb") or len(pcb) != 1:
            raise ProjectCatalogRejected("DRC_BASELINE_INPUT_REJECTED")
        snapshot = collector.workspace.snapshot(tenant_id=tenant_id, project_id=project_id, target_id="project-root")
        selected = [item for item in snapshot.files if item.relative_file_reference == pcb_path]
        if len(selected) != 1 or selected[0].content_fingerprint != "sha256:"+pcb[0]["sha256"]:
            raise ProjectCatalogConflict("WORKSPACE_STALE")
        native_entry = str(revision["native_entry_relative_path"])
        prl_path = str(PurePosixPath(native_entry if native_entry.endswith(".kicad_pro") else pcb_path).with_suffix(".kicad_prl"))
        prl = next((item for item in snapshot.files if item.relative_file_reference == prl_path), None)
        # Stable snapshot identity is derived from the actual full frozen Workspace.
        material = {"target_id": snapshot.target_id, "target_fingerprint": snapshot.target_fingerprint,
            "workspace_snapshot_id": self._eda_import_fingerprint({"project_fingerprint": snapshot.project_fingerprint,"target_fingerprint": snapshot.target_fingerprint}),
            "workspace_fingerprint": snapshot.project_fingerprint,
            "import_revision_id": revision_id, "import_revision_fingerprint": revision_fp,
            "source_set_fingerprint": revision["source_set_fingerprint"],
            "manifest_fingerprint": self._eda_import_fingerprint({"manifest": manifest}),
            **{key:revision[key] for key in ("parser_ruleset_id","parser_ruleset_fingerprint","supported_subset_id","supported_subset_fingerprint")},
            "native_entry_relative_path": native_entry, "pcb_relative_path": pcb_path, "pcb_sha256": selected[0].content_fingerprint,
            "prl_relative_path": prl_path, "prl_before_sha256": None if prl is None else prl.content_fingerprint,
            "collector_policy": collector.policy}
        # All slow closure/source/Workspace reads precede final canonical rows.
        if (self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester) != project
            or self._repository.eda_import_revision(project_id, revision_id, revision_fp, tenant_id=tenant_id, requester=requester) != frozen_revision
            or frozen_revision is None or frozen_revision.get("active") is not True):
            raise ProjectCatalogConflict("SOURCE_STALE")
        return material

    def create_drc_baseline_operation(self, project_id: str, *, tenant_id: str, requester: str,
                                     import_revision_id: str, import_revision_fingerprint: str,
                                     pcb_relative_path: str, execution: object):
        from uuid import uuid4
        from embedded_copilot.execution_worker.contracts import PcbDrcBaselineOperation, OperationRequest, OperationType, drc_baseline_proposal_fingerprint
        from embedded_copilot.execution_worker.service import ExecutionOperationService
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        if not isinstance(execution, ExecutionOperationService):
            raise ProjectCatalogUnavailable("DRC_BASELINE_UNAVAILABLE")
        first = self._drc_baseline_material(project_id, import_revision_id, import_revision_fingerprint, pcb_relative_path, tenant_id=tenant_id, requester=requester)
        second = self._drc_baseline_material(project_id, import_revision_id, import_revision_fingerprint, pcb_relative_path, tenant_id=tenant_id, requester=requester)
        if first != second:
            raise ProjectCatalogConflict("SOURCE_STALE")
        now = self._now()
        values = dict(first, tenant_id=tenant_id, project_id=project_id, requester_id=requester,
            operation_type=OperationType.KICAD_PCB_DRC_BASELINE, operation_id="drc-baseline-"+uuid4().hex,
            proposal_id="drc-baseline-proposal-"+uuid4().hex, nonce=uuid4().hex,
            created_at=now, expires_at=now+timedelta(minutes=15))
        values["proposal_fingerprint"] = drc_baseline_proposal_fingerprint(values)
        operation = PcbDrcBaselineOperation(**values)
        if not self.revalidate_drc_baseline_operation(operation):
            raise ProjectCatalogConflict("SOURCE_STALE")
        execution.create_operation(OperationRequest(operation=operation))
        return execution.get_status(tenant_id=tenant_id, project_id=project_id, operation_id=operation.operation_id)

    def revalidate_drc_baseline_operation(self, operation: object) -> bool:
        from embedded_copilot.execution_worker.contracts import PcbDrcBaselineOperation
        if not isinstance(operation, PcbDrcBaselineOperation) or self._now() >= operation.expires_at:
            return False
        try:
            material = self._drc_baseline_material(operation.project_id, operation.import_revision_id,
                operation.import_revision_fingerprint, operation.pcb_relative_path,
                tenant_id=operation.tenant_id, requester=operation.requester_id)
            return all(getattr(operation, key) == value for key, value in material.items()) and self._now() < operation.expires_at
        except (ProjectCatalogError, AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            return False

    @staticmethod
    def _native_move_limited_source_issues(source: dict[str, object]) -> bool:
        entry = source.get("native_entry_relative_path")
        issues = source.get("issues")
        if not isinstance(entry, str) or not entry.casefold().endswith(".kicad_pcb") or not isinstance(issues, list):
            return False
        return all(
            not item.get("blocks_mutation")
            or (
                item.get("kind") == "UNKNOWN"
                and item.get("code") == "STANDALONE_BOARD"
                and item.get("locator") == entry
            )
            for item in issues if isinstance(item, dict)
        ) and all(isinstance(item, dict) for item in issues)

    def _native_move_current(self, project: ProjectView, candidate: dict[str, object], *,
                             tenant_id: str, requester: str) -> dict[str, object]:
        source = candidate["binding"]["source"]
        assert isinstance(source, dict)
        revision = self.eda_import_revision(
            project.project_id, str(source["import_revision_id"]), str(source["import_revision_fingerprint"]),
            tenant_id=tenant_id, requester=requester,
        )
        if not revision["active"] or revision["source_currentness"] != "CURRENT" or revision["observation_currentness"] != "CURRENT":
            raise ProjectCatalogConflict("SOURCE_STALE")
        scan = self._eda_import_source(project, str(source["native_entry_relative_path"]), "KICAD")
        if scan["source_set_fingerprint"] != source["source_set_fingerprint"] or scan["manifest"] != source["manifest"]:
            raise ProjectCatalogConflict("SOURCE_STALE")
        workspace = candidate["binding"]["workspace"]
        assert isinstance(workspace, dict)
        if self._brownfield_workspace is None:
            raise ProjectCatalogUnavailable("WORKSPACE_SNAPSHOT_UNAVAILABLE")
        snapshot = self._brownfield_workspace.snapshot(tenant_id=tenant_id, project_id=project.project_id)
        if (snapshot.tenant_id, snapshot.project_id, snapshot.target_id,
            snapshot.project_fingerprint, snapshot.target_fingerprint) != (
            tenant_id, project.project_id, workspace["target_id"],
            workspace["project_fingerprint"], workspace["target_fingerprint"],
        ):
            raise ProjectCatalogConflict("WORKSPACE_STALE")
        frozen = candidate["binding"].get("production_frozen")
        if frozen is not None:
            if not self._native_move_limited_source_issues(source):
                raise ProjectCatalogRejected("PRESERVATION_UNPROVEN")
            from embedded_copilot.engineering_workspace_apply import WorkspaceSnapshot
            from embedded_copilot.execution_worker.native_move import (
                NativeMoveProductionSetup,
            )

            setup = self._native_move_setup
            if not isinstance(setup, NativeMoveProductionSetup) or not setup.available() or not isinstance(snapshot, WorkspaceSnapshot):
                raise ProjectCatalogUnavailable("NATIVE_MOVE_UNAVAILABLE")
            captured = WorkspaceSnapshot.model_validate(frozen["workspace_snapshot"])
            if (captured.tenant_id, captured.project_id, captured.target_id, captured.project_fingerprint,
                captured.target_fingerprint, captured.files) != (
                snapshot.tenant_id, snapshot.project_id, snapshot.target_id, snapshot.project_fingerprint,
                snapshot.target_fingerprint, snapshot.files,
            ):
                raise ProjectCatalogConflict("WORKSPACE_STALE")
            if (
                frozen["native_reader_policy"] != setup.reader_policy.model_dump(mode="json")
                or frozen["drc_policy"] != setup.drc_policy.model_dump(mode="json")
                or frozen["side_effect_policy"] != setup.side_effect_policy.model_dump(mode="json")
                or setup.drc_policy.baseline_report.tenant_id != tenant_id
                or setup.drc_policy.baseline_report.project_id != project.project_id
            ):
                raise ProjectCatalogUnavailable("NATIVE_MOVE_UNAVAILABLE")
        return {"revision": revision, "scan": scan}

    def create_native_move_candidate(
        self, project_id: str, *, tenant_id: str, requester: str, import_revision_id: str,
        import_revision_fingerprint: str, workspace_target_id: str, target_native_uuid: str,
        after_x_mm: float, after_y_mm: float,
    ) -> dict[str, object]:
        from embedded_copilot.pcb.parser.kicad import KiCadByteSpanRejected, stage_kicad_footprint_xy_bytes

        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        if workspace_target_id != "project-root" or not all(
            isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) <= 10_000
            for value in (after_x_mm, after_y_mm)
        ):
            raise ProjectCatalogRejected("NATIVE_MOVE_INPUT_REJECTED")
        revision = self.eda_import_revision(project_id, import_revision_id, import_revision_fingerprint,
                                            tenant_id=tenant_id, requester=requester)
        if (
            revision["backend"] != "KICAD"
            or revision["parser_ruleset_id"] not in {"kicad-pcb-footprint-20240108-r1", "kicad-pcb-footprint-20240108-r2"}
            or revision["supported_subset_id"] != "kicad-footprint-placement-r1"
        ):
            raise ProjectCatalogRejected("NATIVE_MOVE_FORMAT_UNSUPPORTED")
        if revision["parser_ruleset_id"] != "kicad-pcb-footprint-20240108-r2" or not revision["active"] or revision["source_currentness"] != "CURRENT" or revision["observation_currentness"] != "CURRENT":
            raise ProjectCatalogConflict("SOURCE_STALE")
        matches = [item for item in revision["objects"] if item["object_type"] == "FOOTPRINT" and item["native_id"] == target_native_uuid]
        if len(matches) != 1:
            raise ProjectCatalogRejected("NATIVE_TARGET_UNAVAILABLE")
        target = matches[0]
        pcb_path = str(target["source_file"])
        manifest = revision["manifest"]
        pcb_items = [item for item in manifest if item["kind"] == "FILE" and item["relative_path"] == pcb_path]
        if len(pcb_items) != 1 or pcb_items[0]["sha256"] is None:
            raise ProjectCatalogRejected("NATIVE_TARGET_UNAVAILABLE")
        scan = self._eda_import_source(project, str(revision["native_entry_relative_path"]), "KICAD")
        if scan["source_set_fingerprint"] != revision["source_set_fingerprint"] or scan["manifest"] != manifest:
            raise ProjectCatalogConflict("SOURCE_STALE")
        payload = scan["payloads"].get(pcb_path)
        if not isinstance(payload, bytes):
            raise ProjectCatalogConflict("SOURCE_STALE")
        try:
            staged = stage_kicad_footprint_xy_bytes(
                payload, target_native_uuid,
                expected_old=(target["position_x_mm"], target["position_y_mm"]),
                new=(after_x_mm, after_y_mm), expected_source_sha256=pcb_items[0]["sha256"],
            )
        except KiCadByteSpanRejected as error:
            raise ProjectCatalogRejected("NATIVE_STAGE_" + error.code) from error
        if staged.staged_bytes == payload:
            raise ProjectCatalogRejected("NATIVE_MOVE_INPUT_REJECTED")
        if self._brownfield_workspace is None:
            raise ProjectCatalogUnavailable("WORKSPACE_SNAPSHOT_UNAVAILABLE")
        snapshot = self._brownfield_workspace.snapshot(tenant_id=tenant_id, project_id=project_id)
        if (snapshot.tenant_id, snapshot.project_id, snapshot.target_id) != (tenant_id, project_id, workspace_target_id):
            raise ProjectCatalogRejected("WORKSPACE_IDENTITY_MISMATCH")
        confirmed = self._eda_import_source(project, str(revision["native_entry_relative_path"]), "KICAD")
        if confirmed["source_set_fingerprint"] != revision["source_set_fingerprint"] or confirmed["manifest"] != manifest:
            raise ProjectCatalogConflict("SOURCE_STALE")
        final_snapshot = self._brownfield_workspace.snapshot(tenant_id=tenant_id, project_id=project_id)
        if (final_snapshot.tenant_id, final_snapshot.project_id, final_snapshot.target_id,
            final_snapshot.project_fingerprint, final_snapshot.target_fingerprint) != (
            snapshot.tenant_id, snapshot.project_id, snapshot.target_id,
            snapshot.project_fingerprint, snapshot.target_fingerprint,
        ):
            raise ProjectCatalogConflict("WORKSPACE_STALE")
        edits = [{"start": edit.start, "end": edit.end, "before_hex": edit.before.hex(), "after_hex": edit.after.hex()}
                 for edit in staged.edits]
        source = {key: revision[key] for key in (
            "import_revision_id", "import_revision_fingerprint", "source_set_fingerprint",
            "native_entry_relative_path", "parser_ruleset_id", "parser_ruleset_fingerprint",
            "supported_subset_id", "supported_subset_fingerprint", "manifest", "issues",
        )}
        source["manifest_fingerprint"] = self._eda_import_fingerprint({"manifest": manifest})
        target_binding = {
            "native_uuid": target_native_uuid, "native_locator": target["locator"], "pcb_relative_path": pcb_path,
            "pcb_sha256": "sha256:" + pcb_items[0]["sha256"], "before_x_mm": target["position_x_mm"],
            "before_y_mm": target["position_y_mm"], "after_x_mm": float(after_x_mm), "after_y_mm": float(after_y_mm),
            "rotation_degrees": target["rotation_degrees"], "edits": edits,
        }
        target_binding["diff_fingerprint"] = self._eda_import_fingerprint(target_binding)
        production_frozen = None
        setup = self._native_move_setup
        if setup is not None:
            from embedded_copilot.engineering_workspace_apply import WorkspaceSnapshot
            from embedded_copilot.execution_worker.native_move import (
                NativeMoveProductionSetup,
            )

            if not isinstance(setup, NativeMoveProductionSetup) or not isinstance(snapshot, WorkspaceSnapshot) or not setup.available():
                raise ProjectCatalogUnavailable("NATIVE_MOVE_UNAVAILABLE")
            if setup.drc_policy.baseline_report.tenant_id != tenant_id or setup.drc_policy.baseline_report.project_id != project_id:
                raise ProjectCatalogRejected("NATIVE_MOVE_INPUT_REJECTED")
            if not self._native_move_limited_source_issues(source):
                raise ProjectCatalogRejected("PRESERVATION_UNPROVEN")
            from embedded_copilot.execution_worker.native_move import (
                expected_limited_observation,
            )
            try:
                expected_limited_observation(payload, staged.staged_bytes, target_native_uuid, reader_id=setup.reader_policy.reader_id)
            except (UnicodeError, ValueError, TypeError) as error:
                raise ProjectCatalogRejected("PRESERVATION_UNPROVEN") from error
            source_file = next((item for item in snapshot.files if item.relative_file_reference == pcb_path), None)
            if source_file is None or source_file.content_fingerprint != target_binding["pcb_sha256"]:
                raise ProjectCatalogConflict("WORKSPACE_STALE")
            prl = next((item for item in snapshot.files if item.relative_file_reference == setup.side_effect_policy.prl_relative_path), None)
            if (None if prl is None else prl.content_fingerprint) != setup.side_effect_policy.prl_before_sha256:
                raise ProjectCatalogConflict("WORKSPACE_STALE")
            try:
                staged_content = staged.staged_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ProjectCatalogRejected("PRESERVATION_UNPROVEN") from error
            if staged_content.encode("utf-8") != staged.staged_bytes:
                raise ProjectCatalogRejected("PRESERVATION_UNPROVEN")
            production_frozen = {
                "workspace_snapshot": snapshot.model_dump(mode="json"),
                "native_reader_policy": setup.reader_policy.model_dump(mode="json"),
                "drc_policy": setup.drc_policy.model_dump(mode="json"),
                "side_effect_policy": setup.side_effect_policy.model_dump(mode="json"),
                "staged_content": staged_content,
                "staged_sha256": "sha256:" + hashlib.sha256(staged.staged_bytes).hexdigest(),
            }
        binding = {
            "schema": "native-footprint-move-candidate-v2" if production_frozen is not None else "native-footprint-move-candidate-v1",
            "tenant_id": tenant_id, "project_id": project_id,
            "requester": requester, "import_revision_id": import_revision_id,
            "import_revision_fingerprint": import_revision_fingerprint, "source": source, "target": target_binding,
            "workspace": {"target_id": workspace_target_id, "project_fingerprint": snapshot.project_fingerprint,
                          "target_fingerprint": snapshot.target_fingerprint},
            "preservation_status": "BYTE_SPAN_PROVEN" if production_frozen is not None else "BLOCKED_UNPROVEN",
            "can_request_operation_approval": False,
        }
        if production_frozen is not None:
            binding["production_frozen"] = production_frozen
        candidate: dict[str, object] = {
            "candidate_id": f"native-move-candidate-{uuid4()}", "candidate_fingerprint": self._eda_import_fingerprint(binding),
            "tenant_id": tenant_id, "project_id": project_id, "requester": requester,
            "import_revision_id": import_revision_id, "import_revision_fingerprint": import_revision_fingerprint,
            "created_at": self._now().isoformat(), "binding": binding,
        }
        return self._repository.save_eda_native_move_candidate(candidate, tenant_id=tenant_id, requester=requester)

    def native_move_candidate(self, project_id: str, candidate_id: str, candidate_fingerprint: str, *,
                              tenant_id: str, requester: str) -> dict[str, object]:
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        candidate = self._repository.eda_native_move_candidate(project_id, candidate_id, candidate_fingerprint,
                                                                  tenant_id=tenant_id, requester=requester)
        if candidate is None:
            raise ProjectCatalogNotFound("NATIVE_MOVE_CANDIDATE_NOT_FOUND")
        self._native_move_current(project, candidate, tenant_id=tenant_id, requester=requester)
        projected = dict(candidate)
        frozen = candidate["binding"].get("production_frozen")
        if frozen is not None:
            review = self._repository.eda_native_mapping_review_for_candidate(
                project_id, candidate_id, tenant_id=tenant_id, requester=requester,
            )
            projected["can_request_operation_approval"] = bool(
                review is not None and review["decision"] == "ACCEPT_MAPPING"
                and self._now() < datetime.fromisoformat(review["created_at"]) + timedelta(minutes=10)
            )
        return projected

    def review_native_move_candidate(self, project_id: str, candidate_id: str, candidate_fingerprint: str, *,
                                     tenant_id: str, requester: str, decision: str, design_intent: str) -> dict[str, object]:
        candidate = self.native_move_candidate(project_id, candidate_id, candidate_fingerprint,
                                               tenant_id=tenant_id, requester=requester)
        if decision not in {"ACCEPT_MAPPING", "REJECT_MAPPING"} or not isinstance(design_intent, str) or not design_intent.strip() or len(design_intent) > 512 or "\x00" in design_intent:
            raise ProjectCatalogRejected("NATIVE_MOVE_REVIEW_INVALID")
        binding = {"schema": "native-mapping-review-v1", "tenant_id": tenant_id, "project_id": project_id,
                   "candidate_id": candidate_id, "candidate_fingerprint": candidate_fingerprint,
                   "reviewer_id": requester, "decision": decision, "design_intent": design_intent.strip()}
        existing = self._repository.eda_native_mapping_review_for_candidate(
            project_id, candidate_id, tenant_id=tenant_id, requester=requester,
        )
        if existing is not None:
            if existing["binding"] != binding:
                raise ProjectCatalogConflict("NATIVE_MOVE_REVIEW_CONFLICT")
            return existing
        review: dict[str, object] = {
            "mapping_review_id": f"native-mapping-review-{uuid4()}",
            "mapping_review_fingerprint": self._eda_import_fingerprint(binding),
            "tenant_id": tenant_id, "project_id": project_id,
            "candidate_id": candidate_id, "candidate_fingerprint": candidate_fingerprint,
            "reviewer_id": requester, "decision": decision, "created_at": self._now().isoformat(),
            "binding": binding,
        }
        try:
            return self._repository.save_eda_native_mapping_review(review, tenant_id=tenant_id, requester=requester)
        except ProjectCatalogConflict:
            existing = self._repository.eda_native_mapping_review_for_candidate(
                project_id, candidate_id, tenant_id=tenant_id, requester=requester,
            )
            if existing is not None and existing["binding"] == binding:
                return existing
            raise

    def native_mapping_review(self, project_id: str, review_id: str, review_fingerprint: str, *,
                              tenant_id: str, requester: str) -> dict[str, object]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        review = self._repository.eda_native_mapping_review(
            project_id, review_id, review_fingerprint, tenant_id=tenant_id, requester=requester,
        )
        if review is None:
            raise ProjectCatalogNotFound("NATIVE_MAPPING_REVIEW_NOT_FOUND")
        return review

    def create_native_move_operation(self, project_id: str, candidate_id: str, candidate_fingerprint: str,
                                     review_id: str, review_fingerprint: str, *, tenant_id: str,
                                     requester: str, execution: object) -> object:
        candidate = self.native_move_candidate(project_id, candidate_id, candidate_fingerprint,
                                               tenant_id=tenant_id, requester=requester)
        review = self.native_mapping_review(project_id, review_id, review_fingerprint,
                                            tenant_id=tenant_id, requester=requester)
        if (review["candidate_id"], review["candidate_fingerprint"], review["decision"]) != (
            candidate_id, candidate_fingerprint, "ACCEPT_MAPPING"
        ):
            raise ProjectCatalogRejected("NATIVE_MAPPING_REVIEW_INVALID")
        if self._now() >= datetime.fromisoformat(str(review["created_at"])) + timedelta(minutes=10):
            raise ProjectCatalogExpired("NATIVE_MAPPING_REVIEW_EXPIRED")
        frozen = candidate["binding"].get("production_frozen")
        if frozen is None:
            raise ProjectCatalogRejected("PRESERVATION_UNPROVEN")
        from embedded_copilot.engineering_workspace_apply import (
            ApplyOperation,
            ChangeIntent,
            ChangeType,
            WorkspaceSnapshot,
        )
        from embedded_copilot.engineering_workspace_apply.contracts import (
            deterministic_id,
        )
        from embedded_copilot.execution_worker.contracts import (
            KiCadNativeFootprintMoveOperation,
            NativeMoveProductionContextV1,
            OperationRequest,
            native_move_operation_id,
            native_move_operation_nonce,
        )
        from embedded_copilot.execution_worker.native_move import (
            NativeMoveProductionSetup,
            ReviewedNativeMoveProposal,
        )
        from embedded_copilot.execution_worker.service import ExecutionOperationService

        setup = self._native_move_setup
        if not isinstance(setup, NativeMoveProductionSetup) or not setup.available() or not isinstance(execution, ExecutionOperationService):
            raise ProjectCatalogUnavailable("NATIVE_MOVE_UNAVAILABLE")
        source = candidate["binding"]["source"]
        target = candidate["binding"]["target"]
        snapshot = WorkspaceSnapshot.model_validate(frozen["workspace_snapshot"])
        reviewed_at = datetime.fromisoformat(review["created_at"])
        proposal = ReviewedNativeMoveProposal(
            proposal_id=candidate_id, proposal_fingerprint=candidate_fingerprint,
            tenant_id=tenant_id, project_id=project_id, requester_id=requester,
            validation_fingerprint=review_fingerprint, evidence_fingerprint=target["diff_fingerprint"],
        )
        intent = ChangeIntent(
            relative_file_reference=target["pcb_relative_path"], change_type=ChangeType.MODIFY_FILE,
            before_fingerprint=target["pcb_sha256"], after_fingerprint=frozen["staged_sha256"],
            safe_preview=f"Move native footprint {target['native_uuid']} to ({target['after_x_mm']}, {target['after_y_mm']}) mm.",
            reason="Accepted native footprint mapping and byte-span placement change.",
            source_proposal_reference=candidate_id, after_content=frozen["staged_content"],
        )
        change_key = deterministic_id(
            "v65-change-key", candidate_fingerprint, snapshot.target_fingerprint,
            self._eda_import_fingerprint(intent.model_dump(mode="json")),
        )
        change_id = deterministic_id("v65-changeset", change_key)
        changeset = setup.workspace.get_changeset(tenant_id, project_id, change_id)
        if changeset is None:
            changeset = setup.workspace.create_changeset(
                proposal=proposal, snapshot=snapshot, intents=(intent,), operation=ApplyOperation.WORKSPACE_APPLY,
            )
        if (
            changeset.changeset_id != change_id or changeset.proposal_id != candidate_id
            or changeset.proposal_fingerprint != candidate_fingerprint
            or changeset.requester_id != requester or len(changeset.entries) != 1
            or changeset.entries[0].after_content != frozen["staged_content"]
            or changeset.entries[0].after_fingerprint != frozen["staged_sha256"]
            or not setup.staging_current(changeset) or self._now() >= changeset.expires_at
        ):
            raise ProjectCatalogConflict("WORKSPACE_STALE")
        context = NativeMoveProductionContextV1(
            workspace_snapshot=snapshot, changeset=changeset,
            native_reader_policy=setup.reader_policy, drc_policy=setup.drc_policy,
            side_effect_policy=setup.side_effect_policy,
        )
        operation = KiCadNativeFootprintMoveOperation(
            operation_id=native_move_operation_id(
                tenant_id, project_id, candidate_id, candidate_fingerprint, review_id, review_fingerprint, context.fingerprint,
            ),
            tenant_id=tenant_id, project_id=project_id, requester_id=requester,
            target_id=snapshot.target_id, target_fingerprint=snapshot.target_fingerprint,
            workspace_snapshot_id="workspace-snapshot-" + hashlib.sha256(snapshot.fingerprint.encode("utf-8")).hexdigest()[:24],
            workspace_fingerprint=snapshot.project_fingerprint,
            proposal_id=candidate_id, proposal_fingerprint=candidate_fingerprint,
            created_at=reviewed_at, expires_at=reviewed_at + timedelta(minutes=10),
            nonce=native_move_operation_nonce(review_id, review_fingerprint, context.fingerprint),
            mapping_review_id=review_id, mapping_review_fingerprint=review_fingerprint,
            mapping_reviewed_at=reviewed_at,
            import_revision_id=source["import_revision_id"], import_revision_fingerprint=source["import_revision_fingerprint"],
            source_set_fingerprint=source["source_set_fingerprint"], manifest_fingerprint=source["manifest_fingerprint"],
            parser_ruleset_id=source["parser_ruleset_id"], parser_ruleset_fingerprint=source["parser_ruleset_fingerprint"],
            supported_subset_id=source["supported_subset_id"], supported_subset_fingerprint=source["supported_subset_fingerprint"],
            native_entry_relative_path=source["native_entry_relative_path"], pcb_relative_path=target["pcb_relative_path"],
            pcb_sha256=target["pcb_sha256"], native_uuid=target["native_uuid"], native_locator=target["native_locator"],
            before_x_mm=target["before_x_mm"], before_y_mm=target["before_y_mm"],
            after_x_mm=target["after_x_mm"], after_y_mm=target["after_y_mm"],
            rotation_degrees=target["rotation_degrees"], diff_fingerprint=target["diff_fingerprint"],
            production_context=context,
        )
        execution.create_operation(OperationRequest(operation=operation))
        return execution.get_status(tenant_id=tenant_id, project_id=project_id, operation_id=operation.operation_id)

    def revalidate_native_move_operation(self, operation: object) -> bool:
        """Recheck exact Owner, C1, Review, Workspace and frozen production binding."""
        from embedded_copilot.execution_worker.contracts import (
            KiCadNativeFootprintMoveOperation, native_move_operation_id,
        )

        if not isinstance(operation, KiCadNativeFootprintMoveOperation):
            return False
        try:
            candidate = self.native_move_candidate(
                operation.project_id, operation.proposal_id, operation.proposal_fingerprint,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            review = self.native_mapping_review(
                operation.project_id, operation.mapping_review_id, operation.mapping_review_fingerprint,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            source, target, workspace = (candidate["binding"][name] for name in ("source", "target", "workspace"))
            assert isinstance(source, dict) and isinstance(target, dict) and isinstance(workspace, dict)
            context = operation.production_context
            context_fp = None if context is None else context.fingerprint
            snapshot_fp = workspace["project_fingerprint"] if context is None else context.workspace_snapshot.fingerprint
            expected_snapshot_id = "workspace-snapshot-" + hashlib.sha256(snapshot_fp.encode("utf-8")).hexdigest()[:24]
            basic = (
                review["candidate_id"] == candidate["candidate_id"]
                and review["candidate_fingerprint"] == candidate["candidate_fingerprint"]
                and review["decision"] == "ACCEPT_MAPPING"
                and self._now() < datetime.fromisoformat(review["created_at"]) + timedelta(minutes=10)
                and operation.operation_id == native_move_operation_id(
                    operation.tenant_id, operation.project_id, candidate["candidate_id"],
                    candidate["candidate_fingerprint"], review["mapping_review_id"], review["mapping_review_fingerprint"],
                    context_fp,
                )
                and operation.mapping_reviewed_at == datetime.fromisoformat(review["created_at"])
                and operation.import_revision_id == source["import_revision_id"]
                and operation.import_revision_fingerprint == source["import_revision_fingerprint"]
                and operation.source_set_fingerprint == source["source_set_fingerprint"]
                and operation.manifest_fingerprint == source["manifest_fingerprint"]
                and operation.parser_ruleset_id == source["parser_ruleset_id"]
                and operation.parser_ruleset_fingerprint == source["parser_ruleset_fingerprint"]
                and operation.supported_subset_id == source["supported_subset_id"]
                and operation.supported_subset_fingerprint == source["supported_subset_fingerprint"]
                and operation.native_entry_relative_path == source["native_entry_relative_path"]
                and operation.pcb_relative_path == target["pcb_relative_path"]
                and operation.pcb_sha256 == target["pcb_sha256"]
                and operation.native_uuid == target["native_uuid"]
                and operation.native_locator == target["native_locator"]
                and (operation.before_x_mm, operation.before_y_mm) == (target["before_x_mm"], target["before_y_mm"])
                and (operation.after_x_mm, operation.after_y_mm) == (target["after_x_mm"], target["after_y_mm"])
                and operation.rotation_degrees == target["rotation_degrees"]
                and operation.diff_fingerprint == target["diff_fingerprint"]
                and operation.target_id == workspace["target_id"]
                and operation.target_fingerprint == workspace["target_fingerprint"]
                and operation.workspace_fingerprint == workspace["project_fingerprint"]
                and operation.workspace_snapshot_id == expected_snapshot_id
            )
            if not basic:
                return False
            frozen = candidate["binding"].get("production_frozen")
            if context is None:
                return frozen is None
            from embedded_copilot.execution_worker.native_move import (
                NativeMoveProductionSetup,
            )

            setup = self._native_move_setup
            if frozen is None or not isinstance(setup, NativeMoveProductionSetup) or not setup.available():
                return False
            if self._now() >= operation.expires_at or self._now() >= context.changeset.expires_at:
                return False
            return not (
                context.workspace_snapshot.model_dump(mode="json") != frozen["workspace_snapshot"]
                or context.native_reader_policy.model_dump(mode="json") != frozen["native_reader_policy"]
                or context.drc_policy.model_dump(mode="json") != frozen["drc_policy"]
                or context.side_effect_policy.model_dump(mode="json") != frozen["side_effect_policy"]
                or context.changeset.entries[0].after_content != frozen["staged_content"]
                or context.changeset.entries[0].after_fingerprint != frozen["staged_sha256"]
                or not setup.staging_current(context.changeset)
            )
        except (ProjectCatalogError, KeyError, TypeError, ValueError, AssertionError, sqlite3.Error):
            return False

    def _native_move_readback_binding(self, operation: object) -> tuple[object, object, object, object] | None:
        """Freeze the canonical owner, candidate, review, and active revision rows."""
        from embedded_copilot.execution_worker.contracts import (
            KiCadNativeFootprintMoveOperation,
        )

        if not isinstance(operation, KiCadNativeFootprintMoveOperation):
            return None
        try:
            project = self.get_project_for_requester(
                operation.project_id, tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            candidate = self._repository.eda_native_move_candidate(
                operation.project_id, operation.proposal_id, operation.proposal_fingerprint,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            review = self._repository.eda_native_mapping_review(
                operation.project_id, operation.mapping_review_id, operation.mapping_review_fingerprint,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            revision = self._repository.eda_import_revision(
                operation.project_id, operation.import_revision_id, operation.import_revision_fingerprint,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            if (
                candidate is None or review is None or revision is None
                or revision.get("active") is not True
                or review.get("decision") != "ACCEPT_MAPPING"
                or review.get("reviewer_id") != operation.requester_id
            ):
                return None
            return copy.deepcopy((project, candidate, review, revision))
        except Exception:  # noqa: BLE001 - a missing canonical row fails closed
            return None

    def revalidate_native_move_readback(self, operation: object, approval: object) -> bool:
        """Revalidate canonical C2-R identity after the approved PCB bytes have been applied."""
        from embedded_copilot.engineering_workspace_apply.models import (
            ApprovalGrantStatus,
            ValidationStatus,
            WorkspaceFile,
            WorkspaceTarget,
            deterministic_id,
        )
        from embedded_copilot.engineering_workspace_apply.models import (
            OperationStatus as WorkspaceOperationStatus,
        )
        from embedded_copilot.execution_worker.contracts import (
            ApprovalStatus,
            KiCadNativeFootprintMoveOperation,
            OperationApprovalGrant,
            native_move_operation_id,
        )
        from embedded_copilot.execution_worker.native_move import (
            NativeMoveProductionSetup,
            _manifest_allowed,
            _sha,
        )

        if not isinstance(operation, KiCadNativeFootprintMoveOperation) or not isinstance(approval, OperationApprovalGrant):
            return False
        context = operation.production_context
        setup = self._native_move_setup
        if context is None or not isinstance(setup, NativeMoveProductionSetup):
            return False
        try:
            binding = self._native_move_readback_binding(operation)
            if binding is None or not setup.available():
                return False
            project, candidate, review, revision = binding
            source, target, frozen_workspace = (candidate["binding"][key] for key in ("source", "target", "workspace"))
            frozen = candidate["binding"].get("production_frozen")
            if frozen is None:
                return False
            if not self._native_move_limited_source_issues(source):
                return False
            if (
                revision["import_revision_id"] != source["import_revision_id"]
                or revision["import_revision_fingerprint"] != source["import_revision_fingerprint"]
                or revision["source_set_fingerprint"] != source["source_set_fingerprint"]
                or revision["manifest"] != source["manifest"]
                or revision["issues"] != source["issues"]
                or review["candidate_id"] != candidate["candidate_id"]
                or review["candidate_fingerprint"] != candidate["candidate_fingerprint"]
                or review["decision"] != "ACCEPT_MAPPING"
                or review["reviewer_id"] != operation.requester_id
                or self._now() >= datetime.fromisoformat(review["created_at"]) + timedelta(minutes=10)
                or operation.mapping_reviewed_at != datetime.fromisoformat(review["created_at"])
                or operation.operation_id != native_move_operation_id(
                    operation.tenant_id, operation.project_id, candidate["candidate_id"],
                    candidate["candidate_fingerprint"], review["mapping_review_id"],
                    review["mapping_review_fingerprint"], context.fingerprint,
                )
                or (operation.tenant_id, operation.project_id, operation.requester_id) !=
                   (candidate["tenant_id"], candidate["project_id"], candidate["requester"])
                or (operation.import_revision_id, operation.import_revision_fingerprint,
                    operation.source_set_fingerprint, operation.manifest_fingerprint,
                    operation.parser_ruleset_id, operation.parser_ruleset_fingerprint,
                    operation.supported_subset_id, operation.supported_subset_fingerprint,
                    operation.native_entry_relative_path) !=
                   tuple(source[key] for key in (
                       "import_revision_id", "import_revision_fingerprint", "source_set_fingerprint",
                       "manifest_fingerprint", "parser_ruleset_id", "parser_ruleset_fingerprint",
                       "supported_subset_id", "supported_subset_fingerprint", "native_entry_relative_path",
                   ))
                or (operation.pcb_relative_path, operation.pcb_sha256, operation.native_uuid,
                    operation.native_locator, operation.before_x_mm, operation.before_y_mm,
                    operation.after_x_mm, operation.after_y_mm, operation.rotation_degrees,
                    operation.diff_fingerprint) != tuple(target[key] for key in (
                        "pcb_relative_path", "pcb_sha256", "native_uuid", "native_locator",
                        "before_x_mm", "before_y_mm", "after_x_mm", "after_y_mm",
                        "rotation_degrees", "diff_fingerprint",
                    ))
                or (operation.target_id, operation.target_fingerprint, operation.workspace_fingerprint) !=
                   (frozen_workspace["target_id"], frozen_workspace["target_fingerprint"], frozen_workspace["project_fingerprint"])
                or context.workspace_snapshot.model_dump(mode="json") != frozen["workspace_snapshot"]
                or context.native_reader_policy.model_dump(mode="json") != frozen["native_reader_policy"]
                or context.drc_policy.model_dump(mode="json") != frozen["drc_policy"]
                or context.side_effect_policy.model_dump(mode="json") != frozen["side_effect_policy"]
                or context.changeset.entries[0].after_content != frozen["staged_content"]
                or context.changeset.entries[0].after_fingerprint != frozen["staged_sha256"]
                or not setup.staging_current(context.changeset)
            ):
                return False
            if (
                approval.status is not ApprovalStatus.CONSUMED
                or approval.operation_id != operation.operation_id
                or approval.operation_fingerprint != operation.fingerprint
                or approval.reviewer_id != operation.requester_id
                or self._now() >= min(operation.expires_at, approval.expires_at)
            ):
                return False
            workspace = setup.workspace
            changeset = context.changeset
            receipt = workspace.get_receipt(operation.project_id, operation.operation_id)
            if receipt is None or (
                receipt.operation_id, receipt.project_id, receipt.changeset_id,
                receipt.changeset_fingerprint, receipt.before_project_fingerprint,
                receipt.status, receipt.validation_status,
            ) != (
                operation.operation_id, operation.project_id, changeset.changeset_id,
                changeset.fingerprint, context.workspace_snapshot.project_fingerprint,
                WorkspaceOperationStatus.APPLIED, ValidationStatus.PASSED,
            ):
                return False
            nonce = "native-" + _sha((operation.operation_id + approval.fingerprint).encode())[7:31]
            grant_id = deterministic_id("v65-approval", changeset.fingerprint, approval.reviewer_id, nonce)
            grant = workspace.get_approval(operation.tenant_id, operation.project_id, grant_id)
            if grant is None or (
                grant.status is not ApprovalGrantStatus.CONSUMED
                or grant.nonce != nonce or grant.reviewer_id != approval.reviewer_id
                or not workspace._grant_matches(changeset, grant)
                or grant.issued_at > receipt.created_at or grant.expires_at < receipt.created_at
                or grant.expires_at > min(operation.expires_at, approval.expires_at)
            ):
                return False
            entry = changeset.entries[0]
            frozen_snapshot = context.workspace_snapshot
            if len(changeset.entries) != 1 or entry.relative_file_reference != operation.pcb_relative_path:
                return False
            expected_files = tuple(
                WorkspaceFile(relative_file_reference=item.relative_file_reference, exists=True,
                              content_fingerprint=entry.after_fingerprint,
                              size=len(entry.after_content.encode("utf-8")))
                if item.relative_file_reference == operation.pcb_relative_path else item
                for item in frozen_snapshot.files
            )
            if workspace._project_fingerprint(expected_files) != receipt.after_project_fingerprint:
                return False
            root = workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root
            board = workspace._safe_path(root, operation.pcb_relative_path)
            if not board.is_file() or _sha(board.read_bytes()) != entry.after_fingerprint:
                return False
            before_prl_path = workspace._safe_path(workspace._stage_path(changeset.changeset_id),
                                                   context.side_effect_policy.prl_relative_path)
            before_prl = before_prl_path.read_bytes() if before_prl_path.is_file() else None
            before_prl_fp = None if before_prl is None else _sha(before_prl)
            if before_prl_fp != context.side_effect_policy.prl_before_sha256:
                return False
            current = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id,
                                         target_id=operation.target_id)
            from embedded_copilot.engineering_workspace_apply.service import (
                _payload_fingerprint,
            )

            after_target = WorkspaceTarget(
                tenant_id=frozen_snapshot.tenant_id, project_id=frozen_snapshot.project_id,
                target_id=frozen_snapshot.target_id,
                project_fingerprint=receipt.after_project_fingerprint,
            )
            expected_target_fp = _payload_fingerprint({
                "target": after_target.fingerprint,
                "files": [item.model_dump(mode="json") for item in expected_files],
            })
            if (current.tenant_id, current.project_id, current.target_id, current.target_fingerprint) != (
                frozen_snapshot.tenant_id, frozen_snapshot.project_id, frozen_snapshot.target_id,
                expected_target_fp,
            ):
                return False
            if not _manifest_allowed(frozen_snapshot, current, operation.pcb_relative_path,
                                     entry.after_fingerprint, context.side_effect_policy, root, before_prl):
                return False
            source_scan = self._eda_import_source(project, source["native_entry_relative_path"], "KICAD")
            location_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
            _, source_root = self._relative(project.relative_path, root=location_root)
            if source_root.resolve(strict=True) != root.resolve(strict=True):
                return (source_scan["source_set_fingerprint"] == source["source_set_fingerprint"]
                        and source_scan["manifest"] == source["manifest"]
                        and self._native_move_readback_binding(operation) == binding)
            expected_manifest = [dict(item) for item in source["manifest"]]
            pcb_items = [item for item in expected_manifest if item["relative_path"] == operation.pcb_relative_path
                         and item["kind"] == "FILE"]
            if len(pcb_items) != 1:
                return False
            pcb_items[0]["sha256"] = entry.after_fingerprint.removeprefix("sha256:")
            pcb_items[0]["byte_size"] = len(entry.after_content.encode("utf-8"))
            if source_scan["manifest"] != expected_manifest:
                return False
            observed = self._eda_import_material(
                project, tenant_id=operation.tenant_id, requester=operation.requester_id,
                backend="KICAD", native_entry=source["native_entry_relative_path"], selected=(),
                binding=revision["project_binding_fingerprint"],
            )
            return (
                observed["manifest"] == expected_manifest
                and observed["issues"] == source["issues"]
                and (observed["parser_ruleset_id"], observed["parser_ruleset_fingerprint"],
                     observed["supported_subset_id"], observed["supported_subset_fingerprint"],
                     observed["native_format_versions"]) ==
                    (revision["parser_ruleset_id"], revision["parser_ruleset_fingerprint"],
                     revision["supported_subset_id"], revision["supported_subset_fingerprint"],
                     revision["native_format_versions"])
                and self._native_move_readback_binding(operation) == binding
            )
        except Exception:  # noqa: BLE001 - post-apply canonical evidence must fail closed
            return False

    def save_circuit_proposal(self, candidate: CircuitProposalCandidate, *, tenant_id: str, requester: str) -> CircuitProposalCandidate:
        self.get_project_for_requester(candidate.project_id, tenant_id=tenant_id, requester=requester)
        if candidate.tenant_id != tenant_id:
            raise ProjectCatalogForbidden("circuit tenant mismatch")
        return self._repository.save_circuit_proposal(candidate, requester=requester)

    def circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str) -> CircuitProposalCandidate:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.circuit_proposal(project_id, proposal_id, tenant_id=tenant_id, requester=requester)

    def circuit_proposal_by_idempotency(self, project_id: str, idempotency_key: str, *, tenant_id: str, requester: str) -> CircuitProposalCandidate | None:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.circuit_proposal_by_idempotency(project_id, idempotency_key, tenant_id=tenant_id, requester=requester)

    def decide_circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str, proposal_fingerprint: str, decision: ApprovalRecord, publication: CircuitPublication | None = None) -> CircuitProposalCandidate | CircuitPublication:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.decide_circuit_proposal(project_id, proposal_id, tenant_id=tenant_id, requester=requester, proposal_fingerprint=proposal_fingerprint, decision=decision, publication=publication)

    def save_predesign_proposal(self, candidate: PredesignCircuitCandidate, *, tenant_id: str, requester: str) -> PredesignCircuitCandidate:
        self.get_project_for_requester(candidate.project_id, tenant_id=tenant_id, requester=requester)
        if candidate.tenant_id != tenant_id:
            raise ProjectCatalogForbidden("predesign tenant mismatch")
        return self._repository.save_predesign_proposal(candidate, requester=requester)

    def predesign_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str) -> PredesignCircuitCandidate:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.predesign_proposal(project_id, proposal_id, tenant_id=tenant_id, requester=requester)

    def predesign_proposal_by_idempotency(self, project_id: str, key: str, *, tenant_id: str, requester: str) -> PredesignCircuitCandidate | None:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.predesign_proposal_by_idempotency(project_id, key, tenant_id=tenant_id, requester=requester)

    def decide_predesign_proposal(
        self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str,
        proposal_fingerprint: str, decision: ApprovalRecord,
        publication: CircuitPublication | None = None,
    ) -> PredesignCircuitCandidate | CircuitPublication:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.decide_predesign_proposal(
            project_id, proposal_id, tenant_id=tenant_id, requester=requester,
            proposal_fingerprint=proposal_fingerprint, decision=decision, publication=publication,
        )

    def current_circuit_intents(self, project_id: str, *, tenant_id: str, requester: str) -> tuple[CircuitIntentObject, ...]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.current_circuit_intents(project_id, tenant_id=tenant_id, requester=requester)

    def circuit_intent_revision(self, project_id: str, object_id: str, revision: str, fingerprint: str, *, tenant_id: str, requester: str) -> CircuitIntentObject:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return self._repository.circuit_intent_revision(project_id, object_id, revision, fingerprint, tenant_id=tenant_id, requester=requester)

    def circuit_source_current(self, project_id: str, object_id: str, revision: str, *, tenant_id: str, requester: str) -> bool:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        predesign = self._repository.predesign_published_candidate(
            project_id, object_id, revision, tenant_id=tenant_id, requester=requester,
        )
        if predesign is not None:
            if predesign.status.value != "APPLIED":
                return False
            try:
                detail = self.conversation(
                    project_id, predesign.conversation_id,
                    tenant_id=tenant_id, requester=requester,
                )
                return predesign_source_snapshot(detail, predesign.turn_id) == predesign.source_snapshot
            except (ProjectCatalogError, TypeError, ValueError):
                return False
        if not self._repository.circuit_source_current(
            project_id, object_id, revision, tenant_id=tenant_id, requester=requester,
        ):
            return False
        candidate = self._repository.circuit_published_candidate(
            project_id, object_id, revision, tenant_id=tenant_id, requester=requester,
        )
        if (candidate is None or candidate.project_id != project_id
                or candidate.tenant_id != tenant_id or candidate.object_id != object_id
                or candidate.proposal.proposed_object.revision != revision
                or candidate.status.value != "APPLIED"):
            return False
        reference = candidate.evidence_reference
        if (reference.scope != "SCHEMATIC_ERC"
                or reference.source != "KICAD_EXECUTION_WORKER"
                or reference.execution_result != "ERC_PASS"):
            return False
        try:
            if (reference.artifact_ref is None or reference.artifact_fingerprint is None
                    or reference.receipt_fingerprint is None
                    or reference.source_fingerprint != reference.artifact_fingerprint):
                return False
            detail = self.conversation(project_id, candidate.conversation_id, tenant_id=tenant_id, requester=requester)
            production = detail.schematic_production
            generation = None if production is None else production.generation_receipt
            erc = None if production is None else production.erc_receipt
            if (production is None or production.status is not SchematicProductionStatus.GENERATED
                    or production.project_id != project_id
                    or production.conversation_id != candidate.conversation_id
                    or generation is None or generation.status is not SchematicGenerationStatus.SUCCESS
                    or generation.validation_status != "PARSE_VERIFIED"
                    or generation.operation_id != production.operation_id
                    or generation.artifact_path != reference.artifact_ref
                    or generation.artifact_fingerprint != reference.artifact_fingerprint
                    or erc is None or erc.operation_id != reference.receipt_id
                    or erc.project_id != project_id
                    or erc.conversation_id != candidate.conversation_id
                    or erc.engineering_status != "PASS"
                    or erc.operation_receipt_fingerprint != reference.receipt_fingerprint
                    or erc.artifact_path != reference.artifact_ref
                    or erc.artifact_fingerprint != reference.artifact_fingerprint
                    or erc.result.status != "ERC_PASS"
                    or erc.result.receipt_ref != generation.operation_receipt_fingerprint
                    or erc.result.schematic_artifact_ref != reference.artifact_ref
                    or erc.result.schematic_content_fingerprint != reference.source_fingerprint):
                return False
            return self._current_schematic_bytes(
                project_id, reference.artifact_ref, reference.artifact_fingerprint, tenant_id=tenant_id,
            )
        except (ProjectCatalogError, OSError, TypeError, ValueError):
            return False

    def require_current_circuit_binding(
        self, project_id: str, binding: object | None, *, tenant_id: str, requester: str
    ) -> None:
        """Reject an explicit Circuit dependency that no longer names the active Owner revision."""
        if binding is None:
            return
        if isinstance(binding, CircuitIntentObject):
            object_id = binding.object_id
            revision = binding.revision
            fingerprint = binding.fingerprint
        else:
            object_id = getattr(binding, "circuit_intent_id", None)
            if object_id is None:
                return
            revision = getattr(binding, "circuit_intent_revision", None)
            fingerprint = getattr(binding, "circuit_intent_fingerprint", None)
        if not isinstance(object_id, str) or not isinstance(revision, str) or not isinstance(fingerprint, str):
            raise ProjectCatalogConflict("Circuit source binding is incomplete")
        try:
            self.circuit_intent_revision(
                project_id, object_id, revision, fingerprint,
                tenant_id=tenant_id, requester=requester,
            )
            if not self.circuit_source_current(
                project_id, object_id, revision, tenant_id=tenant_id, requester=requester,
            ):
                raise ProjectCatalogConflict("Circuit source changed")
        except (ProjectCatalogUnavailable, ProjectCatalogConflict) as error:
            raise ProjectCatalogConflict("Circuit source binding is stale") from error

    def set_schematic_evidence_context(self, *, execution: object, parameter_port: object | None = None) -> None:
        self._schematic_evidence_execution = execution
        execution.set_schematic_evidence_authorizer(self.revalidate_schematic_evidence_operation)
        self._schematic_parameter_port = parameter_port

    def _canonical_schematic_sources(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str):
        from embedded_copilot.execution_worker.contracts import OperationStatus, SchematicWriteOperation
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        plan = None if detail.schematic_planning is None else detail.schematic_planning.approved_plan
        production = detail.schematic_production
        generation = None if production is None else production.generation_receipt
        execution = self._schematic_evidence_execution
        if detail.hardware_selection is not None:
            self._require_current_model_selection(detail.hardware_selection, detail=detail)
        if plan is None or generation is None or execution is None or plan.circuit_intent_id is None:
            raise ProjectCatalogUnavailable("canonical schematic source unavailable")
        if (generation.schematic_plan_id != plan.plan_id or generation.schematic_plan_fingerprint != plan.fingerprint
                or self._schematic_plan_source_currentness(detail, plan, project_id=project_id, tenant_id=tenant_id, requester=requester) != "CURRENT"):
            raise ProjectCatalogConflict("canonical schematic plan is stale")
        source = execution.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=generation.operation_id)
        receipt = execution.get_receipt(tenant_id=tenant_id, project_id=project_id, operation_id=generation.operation_id)
        if (not isinstance(source, SchematicWriteOperation) or receipt is None or receipt.status is not OperationStatus.SUCCEEDED
                or receipt.fingerprint != generation.operation_receipt_fingerprint
                or source.fingerprint != generation.operation_fingerprint
                or source.conversation_id != conversation_id or source.requester_id != requester
                or source.content_fingerprint != generation.artifact_fingerprint
                or not receipt.workspace_mutations or receipt.workspace_apply_receipt_fingerprint != generation.workspace_apply_receipt_fingerprint
                or not self._current_schematic_bytes(project_id, generation.artifact_path, generation.artifact_fingerprint, tenant_id=tenant_id)):
            raise ProjectCatalogConflict("canonical schematic write evidence is stale")
        return detail, plan, generation, source, receipt

    def read_schematic_operation_input(self, operation: object) -> bytes:
        if not self.revalidate_schematic_evidence_operation(operation):
            raise ProjectCatalogConflict("schematic evidence input is stale")
        root = self.knowledge_snapshot_root(operation.project_id, tenant_id=operation.tenant_id)
        _, target = self._relative(operation.artifact_path, root=root)
        content = target.read_bytes()
        if "sha256:" + hashlib.sha256(content).hexdigest() != operation.artifact_fingerprint:
            raise ProjectCatalogConflict("schematic input bytes changed")
        return content

    def revalidate_schematic_evidence_operation(self, operation: object) -> bool:
        from embedded_copilot.execution_worker.contracts import SchematicParseOperation, SchematicErcOperation, SchematicReadBackOperation
        if isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is not None:
            try:
                parse = self.schematic_xml_receipt(operation.project_id, operation.source_parse_operation_id,
                    tenant_id=operation.tenant_id, requester=operation.requester_id, kind="PARSE")
                source = self._schematic_evidence_execution.get_operation(tenant_id=operation.tenant_id,
                    project_id=operation.project_id, operation_id=operation.source_parse_operation_id)
                return bool(type(source) is SchematicParseOperation and source.conversation_id == operation.conversation_id
                    and parse.fingerprint == operation.source_parse_receipt_fingerprint
                    and parse.status.value == "SUCCEEDED" and parse.schematic_parse_result.parse_status == "PASS"
                    and source.source_write_operation_id == operation.source_operation_id
                    and source.source_write_receipt_fingerprint == operation.source_operation_receipt_fingerprint
                    and source.artifact_fingerprint == operation.schematic_artifact_fingerprint
                    and source.artifact_path == operation.target_relative_path
                    and source.target_id == operation.target_id and source.target_fingerprint == operation.target_fingerprint
                    and source.workspace_snapshot_id == operation.workspace_snapshot_id and source.workspace_fingerprint == operation.workspace_fingerprint
                    and source.schematic_plan_fingerprint == operation.proposal_fingerprint)
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                return False
        if not isinstance(operation, SchematicParseOperation):
            return False
        try:
            checked = type(operation).model_validate(operation.model_dump(mode="python"))
            _, plan, generation, source, receipt = self._canonical_schematic_sources(
                checked.project_id, checked.conversation_id, tenant_id=checked.tenant_id, requester=checked.requester_id,
            )
            if isinstance(checked, SchematicReadBackOperation) and checked.library_map_evidence_id is not None:
                self._library_map_operation_state(checked, require_registered=False)
            return bool(checked.source_write_operation_id == source.operation_id
                        and checked.source_write_receipt_fingerprint == receipt.fingerprint
                        and checked.schematic_plan_id == plan.plan_id and checked.schematic_plan_fingerprint == plan.fingerprint
                        and checked.target_id == source.target_id and checked.target_fingerprint == source.target_fingerprint
                        and checked.workspace_snapshot_id == source.workspace_snapshot_id and checked.workspace_fingerprint == source.workspace_fingerprint
                        and checked.artifact_path == generation.artifact_path and checked.artifact_fingerprint == generation.artifact_fingerprint)
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def create_schematic_evidence_operation(self, project_id: str, conversation_id: str, *, tenant_id: str,
                                           requester: str, kind: str, library_map_evidence_id: str | None = None,
                                           library_map_evidence_fingerprint: str | None = None):
        from embedded_copilot.execution_worker.contracts import OperationRequest, SchematicParseOperation, SchematicReadBackOperation
        if kind not in {"PARSE", "READBACK"}:
            raise ProjectCatalogRejected("schematic evidence operation kind is invalid")
        _, plan, generation, source, receipt = self._canonical_schematic_sources(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester,
        )
        if (library_map_evidence_id is None) != (library_map_evidence_fingerprint is None) or (kind != "READBACK" and library_map_evidence_id is not None):
            raise ProjectCatalogRejected("library map selector pair is invalid")
        identity_material = source.operation_id + receipt.fingerprint + plan.fingerprint
        selectors = {}
        if library_map_evidence_id is not None:
            selectors = dict(library_map_evidence_id=library_map_evidence_id, library_map_evidence_fingerprint=library_map_evidence_fingerprint)
            identity_material += json.dumps(selectors, sort_keys=True, separators=(",", ":"))
        op_id = "schematic-" + kind.lower() + "-" + hashlib.sha256(identity_material.encode()).hexdigest()[:24]
        execution = self._schematic_evidence_execution
        existing = execution.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=op_id)
        model = SchematicParseOperation if kind == "PARSE" else SchematicReadBackOperation
        if existing is not None:
            if type(existing) is not model or not self.revalidate_schematic_evidence_operation(existing):
                raise ProjectCatalogConflict("schematic evidence operation conflict")
            return existing
        now = self._now()
        operation = model(operation_id=op_id, tenant_id=tenant_id, project_id=project_id, requester_id=requester,
                          target_id=source.target_id, target_fingerprint=source.target_fingerprint,
                          workspace_snapshot_id=source.workspace_snapshot_id, workspace_fingerprint=source.workspace_fingerprint,
                          proposal_id=plan.plan_id, proposal_fingerprint=plan.fingerprint,
                          created_at=now, expires_at=now + timedelta(minutes=15), nonce=op_id + "-nonce",
                          conversation_id=conversation_id, source_write_operation_id=source.operation_id,
                          source_write_receipt_fingerprint=receipt.fingerprint, schematic_plan_id=plan.plan_id,
                          schematic_plan_fingerprint=plan.fingerprint, artifact_path=generation.artifact_path,
                          artifact_fingerprint=generation.artifact_fingerprint, **selectors)
        if selectors:
            self._library_map_operation_state(operation, require_registered=False)
        execution.create_operation(OperationRequest(operation=operation))
        return operation

    def record_schematic_xml_receipt(self, *, operation: object, receipt: object):
        from embedded_copilot.execution_worker.contracts import OperationReceipt, SchematicParseOperation, SchematicReadBackOperation
        if not isinstance(operation, SchematicParseOperation) or not self.revalidate_schematic_evidence_operation(operation):
            raise ProjectCatalogConflict("schematic XML receipt source is stale")
        checked = OperationReceipt.model_validate(receipt.model_dump(mode="python"))
        execution = self._schematic_evidence_execution
        persisted = execution.get_receipt(tenant_id=operation.tenant_id, project_id=operation.project_id, operation_id=operation.operation_id)
        stored_operation = execution.get_operation(tenant_id=operation.tenant_id, project_id=operation.project_id, operation_id=operation.operation_id)
        result = checked.schematic_readback_result if isinstance(operation, SchematicReadBackOperation) else checked.schematic_parse_result
        if (persisted != checked or stored_operation != operation or result is None
                or checked.tenant_id != operation.tenant_id or checked.project_id != operation.project_id
                or checked.operation_id != operation.operation_id or checked.operation_type != operation.operation_type
                or result.source_write_operation_id != operation.source_write_operation_id
                or result.source_write_receipt_fingerprint != operation.source_write_receipt_fingerprint
                or result.fingerprint_before != operation.artifact_fingerprint
                or result.netlist_artifact.tenant_id != operation.tenant_id
                or result.netlist_artifact.build_operation_id != operation.operation_id):
            raise ProjectCatalogConflict("schematic XML canonical receipt mismatch")
        if execution.artifact_store is None:
            raise ProjectCatalogUnavailable("schematic XML artifact store unavailable")
        execution.artifact_store.read(result.netlist_artifact)
        detail = self.conversation(operation.project_id, operation.conversation_id, tenant_id=operation.tenant_id, requester=operation.requester_id)
        self._repository.save_schematic_evidence(project_id=operation.project_id, tenant_id=operation.tenant_id,
            conversation_id=operation.conversation_id, requester=operation.requester_id,
            kind="READBACK" if isinstance(operation, SchematicReadBackOperation) else "PARSE",
            evidence_id=operation.operation_id, body=checked.model_dump_json(), expected_conversation_fingerprint=detail.fingerprint)
        return checked

    def schematic_xml_receipt(self, project_id: str, operation_id: str, *, tenant_id: str, requester: str, kind: str):
        from embedded_copilot.execution_worker.contracts import OperationReceipt
        body = self._repository.schematic_evidence(project_id=project_id, tenant_id=tenant_id, requester=requester, kind=kind, evidence_id=operation_id)
        if body is None:
            raise ProjectCatalogUnavailable("schematic XML evidence unavailable")
        receipt = OperationReceipt.model_validate_json(body, strict=False)
        operation = self._schematic_evidence_execution.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
        return self.record_schematic_xml_receipt(operation=operation, receipt=receipt)

    def create_schematic_erc_operation(self, parse_operation: object, parse_receipt: object):
        from embedded_copilot.execution_worker.contracts import OperationRequest, SchematicErcOperation, SchematicParseOperation
        if type(parse_operation) is not SchematicParseOperation:
            raise ProjectCatalogRejected("ERC requires independent parse")
        receipt = self.record_schematic_xml_receipt(operation=parse_operation, receipt=parse_receipt)
        if receipt.status.value != "SUCCEEDED" or receipt.schematic_parse_result.parse_status != "PASS":
            raise ProjectCatalogRejected("ERC requires a successful parse receipt")
        op_id = "schematic-erc-" + hashlib.sha256((parse_operation.fingerprint + receipt.fingerprint).encode()).hexdigest()[:24]
        execution = self._schematic_evidence_execution
        existing = execution.get_operation(tenant_id=parse_operation.tenant_id, project_id=parse_operation.project_id, operation_id=op_id)
        if existing is not None:
            if not self.revalidate_schematic_evidence_operation(existing):
                raise ProjectCatalogConflict("ERC source is stale")
            return existing
        now = self._now()
        operation = SchematicErcOperation(operation_id=op_id, tenant_id=parse_operation.tenant_id,
            project_id=parse_operation.project_id, requester_id=parse_operation.requester_id,
            target_id=parse_operation.target_id, target_fingerprint=parse_operation.target_fingerprint,
            workspace_snapshot_id=parse_operation.workspace_snapshot_id, workspace_fingerprint=parse_operation.workspace_fingerprint,
            proposal_id=parse_operation.schematic_plan_id, proposal_fingerprint=parse_operation.schematic_plan_fingerprint,
            created_at=now, expires_at=now + timedelta(minutes=15), nonce=op_id + "-nonce",
            conversation_id=parse_operation.conversation_id, schematic_artifact_ref=parse_operation.artifact_path,
            schematic_artifact_fingerprint=parse_operation.artifact_fingerprint,
            source_operation_id=parse_operation.source_write_operation_id,
            source_operation_receipt_fingerprint=parse_operation.source_write_receipt_fingerprint,
            target_relative_path=parse_operation.artifact_path, source_parse_operation_id=parse_operation.operation_id,
            source_parse_receipt_fingerprint=receipt.fingerprint)
        return execution.create_operation(OperationRequest(operation=operation))

    def reconcile_schematic_terminal_receipts(self) -> tuple[str, ...]:
        """Idempotent projection repair, with no approval issuance or tool replay."""
        from embedded_copilot.execution_worker.contracts import OperationType
        execution = self._schematic_evidence_execution
        if execution is None:
            return ()
        failed = []
        rows = execution.store.terminal_schematic_receipts()
        order = {OperationType.KICAD_SCHEMATIC_WRITE: 0, OperationType.KICAD_SCHEMATIC_PARSE: 1,
                 OperationType.KICAD_SCHEMATIC_READ_BACK: 2, OperationType.KICAD_SCHEMATIC_ERC: 3}
        for operation, receipt in sorted(rows, key=lambda item: (order[item[0].operation_type], item[1].created_at)):
            try:
                if operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE:
                    if receipt.status.value != "SUCCEEDED":
                        continue
                    detail = self.conversation(operation.project_id, operation.conversation_id, tenant_id=operation.tenant_id, requester=operation.requester_id)
                    prior = None if detail.schematic_production is None else detail.schematic_production.generation_receipt
                    if prior is None:
                        self.record_schematic_generation(operation=operation, receipt=receipt, external_validation_status="UNAVAILABLE")
                    elif prior.operation_id != operation.operation_id or prior.operation_receipt_fingerprint != receipt.fingerprint:
                        continue
                    self.create_schematic_evidence_operation(operation.project_id, operation.conversation_id,
                        tenant_id=operation.tenant_id, requester=operation.requester_id, kind="PARSE")
                elif operation.operation_type in {OperationType.KICAD_SCHEMATIC_PARSE, OperationType.KICAD_SCHEMATIC_READ_BACK}:
                    self.record_schematic_xml_receipt(operation=operation, receipt=receipt)
                    if operation.operation_type is OperationType.KICAD_SCHEMATIC_PARSE and receipt.status.value == "SUCCEEDED":
                        self.create_schematic_erc_operation(operation, receipt)
                else:
                    self.record_schematic_erc_validation(operation=operation, receipt=receipt)
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                failed.append(operation.operation_id)
        return tuple(failed)

    def _parameter_source_current(self, candidate: object) -> bool:
        from embedded_copilot.engineering_copilot.schematic_evidence import ParameterCandidate
        try:
            candidate = ParameterCandidate.model_validate(candidate.model_dump(mode="python"))
            if self._schematic_parameter_port is None:
                return False
            returned = self._schematic_parameter_port.read_parameter(candidate)
            return ParameterCandidate.model_validate(returned.model_dump(mode="python")) == candidate
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def propose_schematic_parameter(self, project_id: str, conversation_id: str, *, tenant_id: str,
                                    requester: str, candidate: object):
        from embedded_copilot.engineering_copilot.schematic_evidence import ParameterCandidate
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        candidate = ParameterCandidate.model_validate(candidate.model_dump(mode="python"))
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        selection = detail.hardware_selection
        if selection is None or selection.status is not HardwareSelectionStatus.APPROVED or selection.approval is None:
            raise ProjectCatalogUnavailable("parameter selection unavailable")
        self._require_current_model_selection(selection, detail=detail)
        sources = [item.source for item in selection.proposal.selected_components]
        if ((candidate.tenant_id, candidate.project_id, candidate.conversation_id) != (tenant_id, project_id, conversation_id)
                or not any((s.source_id, s.fingerprint, s.manufacturer, s.mpn, s.package) ==
                           (candidate.source_record_id, candidate.source_record_fingerprint, candidate.manufacturer, candidate.mpn, candidate.package) for s in sources)
                or not self._parameter_source_current(candidate)):
            raise ProjectCatalogRejected("parameter source or exact part unavailable")
        self._repository.save_schematic_evidence(project_id=project_id, tenant_id=tenant_id, conversation_id=conversation_id,
            requester=requester, kind="PARAMETER", evidence_id=candidate.evidence_id, body=candidate.model_dump_json(),
            expected_conversation_fingerprint=detail.fingerprint)
        return candidate

    def review_schematic_parameter(self, project_id: str, evidence_id: str, *, tenant_id: str,
                                   requester: str, evidence_fingerprint: str, decision: str):
        from embedded_copilot.engineering_copilot.schematic_evidence import ParameterCandidate, ParameterReview
        body = self._repository.schematic_evidence(project_id=project_id, tenant_id=tenant_id, requester=requester,
                                                  kind="PARAMETER", evidence_id=evidence_id)
        if body is None:
            raise ProjectCatalogUnavailable("parameter candidate unavailable")
        candidate = ParameterCandidate.model_validate_json(body, strict=False)
        if candidate.fingerprint != evidence_fingerprint or not self._parameter_source_current(candidate):
            raise ProjectCatalogConflict("parameter review source is stale")
        self.propose_schematic_parameter(project_id, candidate.conversation_id, tenant_id=tenant_id, requester=requester, candidate=candidate)
        prior = self._repository.schematic_evidence(project_id=project_id, tenant_id=tenant_id, requester=requester, kind="PARAMETER_REVIEW", evidence_id=evidence_id)
        if prior is not None:
            reviewed = ParameterReview.model_validate_json(prior, strict=False)
            if reviewed.evidence_fingerprint != candidate.fingerprint or reviewed.decision != decision:
                raise ProjectCatalogConflict("parameter review decision conflict")
            return reviewed
        reviewed = ParameterReview(review_id="parameter-review-" + hashlib.sha256((project_id + candidate.fingerprint).encode()).hexdigest()[:24],
                                   tenant_id=tenant_id, project_id=project_id, evidence_id=evidence_id,
                                   evidence_fingerprint=candidate.fingerprint, decision=decision, reviewer=requester, decided_at=self._now())
        detail = self.conversation(project_id, candidate.conversation_id, tenant_id=tenant_id, requester=requester)
        self._repository.save_schematic_evidence(project_id=project_id, tenant_id=tenant_id, conversation_id=candidate.conversation_id,
            requester=requester, kind="PARAMETER_REVIEW", evidence_id=evidence_id, body=reviewed.model_dump_json(),
            expected_conversation_fingerprint=detail.fingerprint)
        return reviewed

    def schematic_parameter(self, project_id: str, evidence_id: str, *, tenant_id: str, requester: str):
        from embedded_copilot.engineering_copilot.schematic_evidence import ParameterCandidate, ParameterReview, ReviewedParameterEvidenceV1
        candidate_body = self._repository.schematic_evidence(project_id=project_id, tenant_id=tenant_id, requester=requester, kind="PARAMETER", evidence_id=evidence_id)
        review_body = self._repository.schematic_evidence(project_id=project_id, tenant_id=tenant_id, requester=requester, kind="PARAMETER_REVIEW", evidence_id=evidence_id)
        if candidate_body is None or review_body is None:
            raise ProjectCatalogUnavailable("reviewed parameter unavailable")
        value = ReviewedParameterEvidenceV1(candidate=ParameterCandidate.model_validate_json(candidate_body, strict=False),
                                             review=ParameterReview.model_validate_json(review_body, strict=False))
        detail = self.conversation(project_id, value.candidate.conversation_id, tenant_id=tenant_id, requester=requester)
        if detail.hardware_selection is None or detail.hardware_selection.status is not HardwareSelectionStatus.APPROVED:
            return value, "STALE"
        try:
            self._require_current_model_selection(detail.hardware_selection, detail=detail)
        except (OSError, RuntimeError, TypeError, ValueError):
            return value, "STALE"
        sources = [] if detail.hardware_selection is None else [item.source for item in detail.hardware_selection.proposal.selected_components]
        current = self._parameter_source_current(value.candidate) and any(s.source_id == value.candidate.source_record_id and s.fingerprint == value.candidate.source_record_fingerprint for s in sources)
        return value, "CURRENT" if current else "STALE"

    def _schematic_verification_sources(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
        intent_id: str, intent_revision: str, intent_fingerprint: str,
        plan_id: str, plan_fingerprint: str, artifact_fingerprint: str,
        generation_operation_id: str, erc_operation_id: str | None,
    ) -> tuple[object, object, object, object, object | None]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        intent = self.circuit_intent_revision(
            project_id, intent_id, intent_revision, intent_fingerprint,
            tenant_id=tenant_id, requester=requester,
        )
        if not self.circuit_source_current(
            project_id, intent_id, intent_revision, tenant_id=tenant_id, requester=requester,
        ):
            raise ProjectCatalogConflict("verification intent source is stale")
        if isinstance(intent, CircuitIntentObject):
            try:
                intent = CircuitIntentObject.model_validate(intent.model_dump(mode="python"))
            except (TypeError, ValueError) as error:
                raise ProjectCatalogConflict("verification intent is invalid") from error
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester,
        )
        if isinstance(detail, ConversationDetail):
            try:
                detail = ConversationDetail.model_validate(detail.model_dump(mode="python"), strict=False)
            except (TypeError, ValueError) as error:
                raise ProjectCatalogConflict("verification Owner source is invalid") from error
        planning = detail.schematic_planning
        plan = None if planning is None else planning.approved_plan
        if (plan is None or plan.status.value != "APPROVED"
                or plan.project_id != project_id or plan.conversation_id != conversation_id
                or plan.plan_id != plan_id or plan.fingerprint != plan_fingerprint
                or plan.circuit_intent_id != intent_id
                or plan.circuit_intent_revision != intent_revision
                or plan.circuit_intent_fingerprint != intent_fingerprint):
            raise ProjectCatalogConflict("approved schematic plan binding is stale")
        if self._schematic_plan_source_currentness(detail, plan,
                project_id=project_id, tenant_id=tenant_id, requester=requester) != "CURRENT":
            raise ProjectCatalogConflict("approved schematic source publication is stale")
        production = detail.schematic_production
        generation = None if production is None else production.generation_receipt
        if (production is None or production.status.value != "GENERATED"
                or production.project_id != project_id
                or production.conversation_id != conversation_id
                or production.operation_id != generation_operation_id
                or generation is None or generation.status.value != "SUCCESS"
                or generation.operation_id != generation_operation_id
                or generation.project_id != project_id
                or generation.conversation_id != conversation_id
                or generation.schematic_plan_id != plan_id
                or generation.schematic_plan_fingerprint != plan_fingerprint
                or generation.artifact_fingerprint != artifact_fingerprint):
            raise ProjectCatalogConflict("generation receipt plan or artifact binding is stale")
        parse = generation.parse_validation
        if (generation.validation_status != "PARSE_VERIFIED" or parse is None
                or parse.validation_status != "PARSE_VERIFIED"
                or not parse.artifact_unchanged
                or parse.project_id != project_id
                or parse.conversation_id != conversation_id
                or parse.operation_id != generation_operation_id
                or parse.artifact_path != generation.artifact_path
                or parse.artifact_fingerprint != artifact_fingerprint):
            raise ProjectCatalogConflict("parse receipt is unavailable or stale")
        if not self._current_schematic_bytes(
            project_id, generation.artifact_path, artifact_fingerprint, tenant_id=tenant_id,
        ):
            raise ProjectCatalogConflict("schematic artifact bytes are stale")
        erc = None
        if erc_operation_id is not None:
            erc = production.erc_receipt
            if (erc is None or erc.operation_id != erc_operation_id
                    or erc.project_id != project_id or erc.conversation_id != conversation_id
                    or erc.schematic_plan_fingerprint != plan_fingerprint
                    or erc.artifact_path != generation.artifact_path
                    or erc.artifact_fingerprint != artifact_fingerprint
                    or erc.result.schematic_artifact_ref != generation.artifact_path
                    or erc.result.schematic_content_fingerprint != artifact_fingerprint):
                raise ProjectCatalogConflict("ERC receipt plan or artifact binding is stale")
        if self._schematic_plan_source_currentness(detail, plan,
                project_id=project_id, tenant_id=tenant_id, requester=requester) != "CURRENT":
            raise ProjectCatalogConflict("approved schematic source changed during verification")
        return intent, detail, generation, parse, erc

    def record_schematic_verification(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
        intent_id: str, intent_revision: str, intent_fingerprint: str,
        plan_id: str, plan_fingerprint: str, artifact_fingerprint: str,
        generation_operation_id: str, erc_operation_id: str | None = None,
        parse_operation_id: str | None = None, readback_operation_id: str | None = None,
        parameter_evidence_ids: tuple[str, ...] = (),
        library_map_evidence_ids: tuple[str, ...] | None = None,
        library_map_evidence_fingerprints: tuple[str, ...] | None = None,
    ) -> SchematicVerificationRecord:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        plan = None if detail.schematic_planning is None else detail.schematic_planning.approved_plan
        applicable = plan is not None and any(item.mpn.upper() == "SHTC3" for item in getattr(plan, "component_instances", ()))
        if applicable or parse_operation_id is not None or readback_operation_id is not None or parameter_evidence_ids or library_map_evidence_ids is not None:
            return self._record_s02_verification(project_id, conversation_id, tenant_id=tenant_id, requester=requester,
                intent_id=intent_id, intent_revision=intent_revision, intent_fingerprint=intent_fingerprint,
                plan_id=plan_id, plan_fingerprint=plan_fingerprint, artifact_fingerprint=artifact_fingerprint,
                generation_operation_id=generation_operation_id, erc_operation_id=erc_operation_id,
                parse_operation_id=parse_operation_id, readback_operation_id=readback_operation_id,
                parameter_evidence_ids=parameter_evidence_ids, library_map_evidence_ids=library_map_evidence_ids,
                library_map_evidence_fingerprints=library_map_evidence_fingerprints)

        intent, detail, generation, parse, erc = self._schematic_verification_sources(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester,
            intent_id=intent_id, intent_revision=intent_revision,
            intent_fingerprint=intent_fingerprint, plan_id=plan_id,
            plan_fingerprint=plan_fingerprint, artifact_fingerprint=artifact_fingerprint,
            generation_operation_id=generation_operation_id, erc_operation_id=erc_operation_id,
        )
        erc_status = "BLOCKED"
        erc_evidence_id = None
        if erc is not None:
            if (erc.result.status.value == "ERC_PASS"
                    and erc.engineering_status.value == "PASS"
                    and erc.tool_execution_status == "SUCCEEDED"):
                erc_status = "PASS"
                erc_evidence_id = erc.receipt_id
            else:
                erc_status = "FAILED"
        rule_results = self._schematic_claim_rules(
            intent, detail, generation, parse, erc,
            project_id=project_id, conversation_id=conversation_id,
            artifact_fingerprint=artifact_fingerprint,
        )
        source_material = "|".join((
            project_id, intent_id, intent_revision, intent_fingerprint,
            plan_id, plan_fingerprint, artifact_fingerprint,
            generation_operation_id, generation.fingerprint, parse.fingerprint,
            "" if erc_operation_id is None else erc_operation_id,
            "" if erc is None else erc.fingerprint,
        ))
        if any(
            result.status != "BLOCKED" or result.reason != "NO_TRUSTED_RESOLVER"
            or result.proof is not None for _, result in rule_results
        ):
            claim_material = [
                {
                    "claim_id": claim.verification_id, "status": result.status,
                    "reason": result.reason,
                    "proof": None if result.proof is None else {
                        "scope": result.proof.proof_scope,
                        "source_receipt_id": result.proof.source_receipt_id,
                        "source_receipt_fingerprint": result.proof.source_receipt_fingerprint,
                    },
                }
                for claim, result in rule_results
            ]
            source_material += "|" + hashlib.sha256(json.dumps(
                claim_material, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
        record_id = "schematic-verification-" + hashlib.sha256(source_material.encode()).hexdigest()[:24]
        claim_results = self._schematic_claim_resolutions(
            rule_results, record_id=record_id, tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id,
            intent_id=intent_id, intent_revision=intent_revision,
            intent_fingerprint=intent_fingerprint, plan_id=plan_id,
            plan_fingerprint=plan_fingerprint,
            artifact_fingerprint=artifact_fingerprint,
            generation_operation_id=generation_operation_id,
            erc_operation_id=erc_operation_id,
        )
        draft = SchematicVerificationRecord.model_construct(
            verification_id=record_id, tenant_id=tenant_id, project_id=project_id,
            conversation_id=conversation_id, intent_id=intent_id,
            intent_revision=intent_revision, intent_fingerprint=intent_fingerprint,
            plan_id=plan_id, plan_fingerprint=plan_fingerprint,
            artifact_fingerprint=artifact_fingerprint,
            generation_operation_id=generation_operation_id,
            generation_receipt_fingerprint=generation.fingerprint,
            parse_validation_fingerprint=parse.fingerprint,
            erc_operation_id=erc_operation_id,
            erc_receipt_fingerprint=None if erc is None else erc.fingerprint,
            erc_status=erc_status, erc_evidence_id=erc_evidence_id,
            claims=claim_results,
            aggregate_status="FAILED" if erc_status == "FAILED" else "BLOCKED",
            created_at=self._now(), fingerprint="sha256:" + "0" * 64,
        )
        record = SchematicVerificationRecord.model_validate({
            **draft.model_dump(mode="python"),
            "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"}),
        })
        return self._repository.save_schematic_verification(
            record, requester=requester, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=self._approved_source_heads(detail),
        )

    def _s02_verification_material(self, project_id, conversation_id, *, tenant_id, requester, intent_id,
        intent_revision, intent_fingerprint, plan_id, plan_fingerprint, artifact_fingerprint,
        generation_operation_id, erc_operation_id, parse_operation_id, readback_operation_id, parameter_evidence_ids,
        library_map_evidence_ids=None, library_map_evidence_fingerprints=None):
        from decimal import Decimal
        from embedded_copilot.engineering_copilot.schematic_evidence import (ParameterConditions, parse_xml_netlist,
            compare_plan_xml, decimal_text, resolve_calculation_inputs, finite_rc_decoupling)
        detail, plan, generation, source, write = self._canonical_schematic_sources(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        intent = self.circuit_intent_revision(project_id, intent_id, intent_revision, intent_fingerprint,
            tenant_id=tenant_id, requester=requester)
        if (not self.circuit_source_current(project_id, intent_id, intent_revision, tenant_id=tenant_id, requester=requester)
            or (plan.circuit_intent_id, plan.circuit_intent_revision, plan.circuit_intent_fingerprint) != (intent_id, intent_revision, intent_fingerprint)
            or (plan.plan_id, plan.fingerprint, generation.artifact_fingerprint, source.operation_id) !=
                (plan_id, plan_fingerprint, artifact_fingerprint, generation_operation_id)
            or detail.engineering_plan_review is None or detail.architecture_review is None
            or detail.engineering_plan_review.status.value != "APPROVED" or detail.architecture_review.status.value != "APPROVED"):
            raise ProjectCatalogConflict("verification approved sources mismatch")
        if ((library_map_evidence_ids is None) != (library_map_evidence_fingerprints is None)
            or (library_map_evidence_ids is not None and (len(library_map_evidence_ids) != 1
                or len(library_map_evidence_fingerprints) != 1 or readback_operation_id is None))):
            raise ProjectCatalogRejected("library map requires explicit single pair and readback operation")
        library_material = None
        passed = [False] * 8
        receipts = {}
        for kind, operation_id in (("PARSE", parse_operation_id), ("READBACK", readback_operation_id)):
            if operation_id is None:
                continue
            try:
                receipt = self.schematic_xml_receipt(project_id, operation_id, tenant_id=tenant_id, requester=requester, kind=kind)
                op = self._schematic_evidence_execution.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
                result = receipt.schematic_parse_result if kind == "PARSE" else receipt.schematic_readback_result
                if op.conversation_id != conversation_id or result is None or receipt.status.value != "SUCCEEDED" or result.parse_status != "PASS":
                    continue
                blob = self._schematic_evidence_execution.artifact_store.read(result.netlist_artifact)
                observation = parse_xml_netlist(blob, require_properties=kind == "READBACK")
                receipts[kind] = receipt
                if kind == "PARSE":
                    passed[6] = True
                else:
                    passed[1] = passed[5] = compare_plan_xml(plan, detail.schematic_production, detail.hardware_selection, observation)
                    if library_map_evidence_ids is not None:
                        library_material, library_ok = self._library_verification_observation(
                            op, receipt, blob, library_map_evidence_ids, library_map_evidence_fingerprints)
                        passed[5] = bool(passed[5] and library_ok)
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                continue
        if library_map_evidence_ids is not None and library_material is None:
            passed[5] = False
        erc = None
        if erc_operation_id is not None:
            try:
                execution = self._schematic_evidence_execution
                erc_op = execution.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=erc_operation_id)
                erc_receipt = execution.get_receipt(tenant_id=tenant_id, project_id=project_id, operation_id=erc_operation_id)
                if (erc_op is not None and erc_op.source_parse_operation_id == parse_operation_id
                    and self.revalidate_schematic_evidence_operation(erc_op) and erc_receipt is not None):
                    erc = erc_receipt
                    passed[7] = bool(erc.status.value == "SUCCEEDED" and erc.schematic_erc_result is not None
                        and erc.schematic_erc_result.status.value == "ERC_PASS"
                        and erc.schematic_erc_result.engineering_status.value == "PASS"
                        and erc.tool_execution_status == "SUCCEEDED")
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                pass
        reviewed = []
        if len(parameter_evidence_ids) != len(set(parameter_evidence_ids)):
            raise ProjectCatalogRejected("duplicate parameter selector")
        for evidence_id in sorted(parameter_evidence_ids):
            value, current = self.schematic_parameter(project_id, evidence_id, tenant_id=tenant_id, requester=requester)
            if current != "CURRENT" or value.candidate.conversation_id != conversation_id:
                raise ProjectCatalogConflict("parameter evidence stale")
            reviewed.append(value)
        selected = detail.hardware_selection.proposal.selected_components
        parts_ok = bool(passed[5] and reviewed)
        for component in plan.component_instances:
            source_record = next((item.source for item in selected if item.slot_id == component.hardware_slot_id), None)
            parameters = tuple(item.candidate for item in reviewed if item.candidate.source_record_id == getattr(source_record, "source_id", None))
            try:
                if source_record is None or self._schematic_parameter_port.read_component_source(source_record, parameters) != source_record:
                    parts_ok = False
                facts = {pin.number: pin for pin in source_record.pin_facts}
                for binding in plan.pin_bindings:
                    if binding.component_id == component.instance_id:
                        fact = facts.get(binding.pin_number)
                        if fact is None or fact.name != binding.pin_name or fact.function != binding.function or fact.pad_number != binding.pad_number:
                            parts_ok = False
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                parts_ok = False
        passed[0] = parts_ok
        support = plan.supporting_connections
        fulfilled = [unit for connection in support for unit in connection.fulfillments]
        passed[3] = bool(passed[0] and passed[1] and support and fulfilled
            and all(connection.status.value == "RESOLVED" and connection.fulfillments for connection in support)
            and len({unit.support_instance_id for unit in fulfilled}) == len(fulfilled))
        fields = {field.category: field.value for field in detail.requirement_context.standard_fields if field.status.value == "CONFIRMED"}
        conditions_ok = False
        try:
            conditions = ParameterConditions(voltage_min=decimal_text(fields["S02_VOLTAGE_MIN_V"]),
                voltage_max=decimal_text(fields["S02_VOLTAGE_MAX_V"]), temperature_min=decimal_text(fields["S02_TEMPERATURE_MIN_C"]),
                temperature_max=decimal_text(fields["S02_TEMPERATURE_MAX_C"]), mode=fields["S02_INTERFACE_MODE"], package="condition")
            conditions_ok = conditions.mode != "UNKNOWN"
        except (KeyError, TypeError, ValueError):
            conditions = None
        applicable = bool(any(item.mpn.upper() == "SHTC3" for item in plan.component_instances)
            and any(bus.protocol.upper() in {"I2C", "I²C"} for bus in plan.interfaces))
        consumers = {component_id for domain in plan.power_domains if domain.name.casefold() != "gnd" for component_id in domain.consumer_component_ids}
        voltage_ok = bool(conditions_ok and consumers)
        for component_id in consumers:
            component = next((item for item in plan.component_instances if item.instance_id == component_id), None)
            source_record = None if component is None else next((item.source for item in selected if item.slot_id == component.hardware_slot_id), None)
            parameters = [item.candidate for item in reviewed if item.candidate.source_record_id == getattr(source_record, "source_id", None)]
            minimum = [item for item in parameters if item.parameter_key == "SUPPLY_MIN" and item.bound_kind in {"MIN", "EXACT"}]
            maximum = [item for item in parameters if item.parameter_key == "SUPPLY_MAX" and item.bound_kind in {"MAX", "EXACT"}]
            if conditions is None or len(minimum) != 1 or len(maximum) != 1:
                voltage_ok = False
                continue
            need = conditions.model_copy(update={"package": source_record.package})
            if (not minimum[0].conditions.covers(need) or not maximum[0].conditions.covers(need)
                or Decimal(minimum[0].value_decimal) > Decimal(conditions.voltage_min)
                or Decimal(maximum[0].value_decimal) < Decimal(conditions.voltage_max)):
                voltage_ok = False
        passed[2] = bool(passed[1] and voltage_ok)
        calculations = getattr(intent, "calculations", ())
        calculations_ok = bool(conditions_ok and calculations
            and {unit.support_instance_id for unit in fulfilled} <= {item.component_instance_id for item in calculations})
        instances = {item.instance_id: item for item in plan.component_instances}
        slot_sources = {item.slot_id: item.source for item in selected}
        reviewed_by_id = {item.candidate.evidence_id: item.candidate for item in reviewed}
        standard_ids = {
            item.source_id for calculation in calculations for item in calculation.input_sources
            if item.source_kind == "ENGINEERING_EVIDENCE" and item.source_id not in reviewed_by_id
        }
        standard_by_id = {
            item.evidence.evidence_id: item for item in (detail.standard_applicability or ())
            if item.evidence.evidence_id in standard_ids
        }
        current_standards = []
        for state in standard_by_id.values():
            self._standard_applicability_checked(detail, state.evidence)
            if state.review is not None and state.review.decision == "APPROVED":
                current_standards.append(state)
        for calculation in getattr(intent, "calculations", ()):
            owner = next((unit for unit in fulfilled if unit.support_instance_id == calculation.component_instance_id), None)
            if owner is None:
                calculations_ok = False
            for input_source in calculation.input_sources:
                if input_source.source_kind != "ENGINEERING_EVIDENCE":
                    continue
                instance = instances.get(input_source.component_instance_id)
                standard = standard_by_id.get(input_source.source_id)
                if standard is not None:
                    targets = {item.component_instance_id for item in standard.evidence.target_components}
                    if (owner is None or instance is None or instance.instance_id not in targets
                        or instance.hardware_slot_id != owner.parent_slot_id
                        or standard not in current_standards):
                        calculations_ok = False
                    continue
                parameter = reviewed_by_id.get(input_source.source_id)
                bound_source = None if instance is None else slot_sources.get(instance.hardware_slot_id)
                if (owner is None or parameter is None or bound_source is None
                    or parameter.source_record_id != bound_source.source_id or parameter.source_record_fingerprint != bound_source.fingerprint):
                    calculations_ok = False
                    continue
                if input_source.input_name in {"r_ohm", "r_tolerance", "c_effective_f"}:
                    required_role = "DECOUPLING" if input_source.input_name == "c_effective_f" else "I2C_PULLUPS"
                    sibling = next((unit for unit in fulfilled if unit.support_instance_id == instance.instance_id), None)
                    if sibling is None or sibling.role != required_role or sibling.parent_slot_id != owner.parent_slot_id:
                        calculations_ok = False
                    if owner.role == required_role and instance.instance_id != owner.support_instance_id:
                        calculations_ok = False
                elif instance.hardware_slot_id != owner.parent_slot_id:
                    calculations_ok = False
            values = resolve_calculation_inputs(calculation, detail.requirement_context, tuple(reviewed),
                reviewed_standards=tuple(current_standards))
            if values is None or calculation.equation != "S02_RC_DECOUPLING_V1" or not finite_rc_decoupling(values):
                calculations_ok = False
        for value in reviewed:
            if conditions is None or not value.candidate.conditions.covers(conditions.model_copy(update={"package": value.candidate.package})):
                calculations_ok = False
        passed[4] = bool(passed[3] and calculations_ok)
        if not applicable:
            passed = [False] * 8
        material = {"rule_version": 1, "intent": intent.fingerprint, "plan": plan.fingerprint,
            "write": write.fingerprint, "generation": generation.fingerprint, "artifact": artifact_fingerprint,
            "reviews": [detail.engineering_plan_review.fingerprint, detail.architecture_review.fingerprint, detail.hardware_selection.approval.fingerprint],
            "parameters": [value.fingerprint for value in reviewed], "receipts": {kind: receipt.fingerprint for kind, receipt in receipts.items()},
            "erc": None if erc is None else erc.fingerprint, "passed": passed}
        if standard_ids:
            material["standard_applicability"] = {
                "selected_ids": sorted(standard_ids),
                "records": [
                    {"evidence": state.evidence.fingerprint,
                     "review": None if state.review is None else state.review.fingerprint,
                     "targets": [item.model_dump(mode="json") for item in state.evidence.target_components],
                     "nets": state.evidence.target_net_ids}
                    for _identity, state in sorted(standard_by_id.items())
                ],
            }
        if library_map_evidence_ids is not None:
            material["rule_version"] = 2
            material["library_terminal_map"] = {
                "selected_ids": list(library_map_evidence_ids), "selected_fingerprints": list(library_map_evidence_fingerprints),
                "observation": library_material}
        context_fp = "sha256:" + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return detail, generation, receipts, erc, tuple(passed), context_fp

    def _record_s02_verification(self, project_id, conversation_id, **selectors):
        from embedded_copilot.engineering_copilot.schematic_evidence import PROFILE_ID, PROFILE_FINGERPRINT, REQUIRED_CLAIM_IDS, PROFILE_FINGERPRINT_V2
        detail, generation, receipts, erc, passed, context_fp = self._s02_verification_material(project_id, conversation_id, **selectors)
        record_id = "schematic-verification-" + hashlib.sha256(context_fp.encode()).hexdigest()[:24]
        claims = []
        for index, claim_id in enumerate(REQUIRED_CLAIM_IDS):
            proof = None
            if passed[index]:
                scope = "SCHEMATIC_PARSE" if index == 6 else "SCHEMATIC_ERC" if index == 7 else "SCHEMATIC_PARAMETER" if index in {0, 2, 3, 4} else "SCHEMATIC_READBACK"
                receipt = erc if index == 7 else receipts["PARSE"] if index == 6 else receipts["READBACK"]
                kind = {"SCHEMATIC_PARSE": "CATALOG_PARSE_RECEIPT", "SCHEMATIC_ERC": "CANONICAL_ERC_RECEIPT",
                        "SCHEMATIC_PARAMETER": "REVIEWED_PARAMETER_CLAIM", "SCHEMATIC_READBACK": "CANONICAL_XML_READBACK"}[scope]
                data = dict(kind=kind, scope=scope, tenant_id=selectors["tenant_id"], project_id=project_id,
                    conversation_id=conversation_id, intent_id=selectors["intent_id"], intent_revision=selectors["intent_revision"],
                    intent_fingerprint=selectors["intent_fingerprint"], plan_id=selectors["plan_id"], plan_fingerprint=selectors["plan_fingerprint"],
                    artifact_fingerprint=selectors["artifact_fingerprint"], operation_id=receipt.operation_id,
                    source_receipt_id=receipt.operation_id, source_receipt_fingerprint=receipt.fingerprint,
                    verification_record_id=record_id, context_fingerprint=context_fp)
                draft = SchematicClaimProof.model_construct(**data, fingerprint="sha256:" + "0" * 64)
                proof = SchematicClaimProof.model_validate({**draft.model_dump(mode="python"), "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})})
            claims.append(SchematicClaimResolution(claim_id=claim_id, status="PASS" if proof else "BLOCKED",
                reason="TRUSTED_CANONICAL_PROOF" if proof else "EVIDENCE_UNAVAILABLE", proof=proof))
        erc_status = "PASS" if passed[7] else "FAILED" if erc is not None and (erc.status.value == "FAILED" or erc.schematic_erc_result.status.value == "ERC_FAIL") else "BLOCKED"
        data = dict(verification_id=record_id, tenant_id=selectors["tenant_id"], project_id=project_id, conversation_id=conversation_id,
            intent_id=selectors["intent_id"], intent_revision=selectors["intent_revision"], intent_fingerprint=selectors["intent_fingerprint"],
            plan_id=selectors["plan_id"], plan_fingerprint=selectors["plan_fingerprint"], artifact_fingerprint=selectors["artifact_fingerprint"],
            generation_operation_id=selectors["generation_operation_id"], generation_receipt_fingerprint=generation.fingerprint,
            parse_validation_fingerprint=receipts["PARSE"].fingerprint if "PARSE" in receipts else selectors["artifact_fingerprint"],
            erc_operation_id=None if erc is None else erc.operation_id, erc_receipt_fingerprint=None if erc is None else erc.fingerprint,
            erc_status=erc_status, erc_evidence_id=erc.operation_id if passed[7] else None,
            claims=tuple(claims), aggregate_status="SCHEMATIC_VERIFIED" if all(passed) else "FAILED" if erc_status == "FAILED" else "BLOCKED",
            profile_id=PROFILE_ID, profile_version=2 if selectors.get("library_map_evidence_ids") is not None else 1,
            profile_fingerprint=PROFILE_FINGERPRINT_V2 if selectors.get("library_map_evidence_ids") is not None else PROFILE_FINGERPRINT,
            required_claim_ids=REQUIRED_CLAIM_IDS,
            requirement_review_fingerprint=detail.engineering_plan_review.fingerprint, architecture_review_fingerprint=detail.architecture_review.fingerprint,
            selection_approval_fingerprint=detail.hardware_selection.approval.fingerprint, parse_operation_id=selectors["parse_operation_id"],
            readback_operation_id=selectors["readback_operation_id"], readback_receipt_fingerprint=None if "READBACK" not in receipts else receipts["READBACK"].fingerprint,
            parameter_evidence_ids=tuple(sorted(selectors["parameter_evidence_ids"])), context_fingerprint=context_fp, created_at=self._now())
        if selectors.get("library_map_evidence_ids") is not None:
            data.update(library_map_evidence_ids=tuple(selectors["library_map_evidence_ids"]),
                library_map_evidence_fingerprints=tuple(selectors["library_map_evidence_fingerprints"]))
        draft = SchematicVerificationRecord.model_construct(**data, fingerprint="sha256:" + "0" * 64)
        record = SchematicVerificationRecord.model_validate({**draft.model_dump(mode="python"), "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})})
        # ERC projection can have changed conversation identity; obtain the latest CAS token.
        current = self.conversation(project_id, conversation_id, tenant_id=selectors["tenant_id"], requester=selectors["requester"])
        return self._repository.save_schematic_verification(
            record, requester=selectors["requester"],
            expected_conversation_fingerprint=current.fingerprint,
            expected_source_publications=self._approved_source_heads(current),
        )

    @staticmethod
    def _schematic_claim_rules(
        intent: object, detail: object, generation: object, parse: object, erc: object | None,
        *, project_id: str, conversation_id: str, artifact_fingerprint: str,
    ) -> tuple[tuple[object, ClaimRuleResult], ...]:
        plan = detail.schematic_planning.approved_plan
        return tuple(
            (
                claim,
                resolve_schematic_claim(
                    claim, intent=intent, plan=plan, generation=generation,
                    parse=parse, erc=erc, project_id=project_id,
                    conversation_id=conversation_id,
                    artifact_fingerprint=artifact_fingerprint, source_current=True,
                ),
            )
            for claim in intent.verification_requirements
        )

    @staticmethod
    def _schematic_claim_resolutions(
        rule_results: tuple[tuple[object, ClaimRuleResult], ...], *,
        record_id: str, tenant_id: str, project_id: str, conversation_id: str,
        intent_id: str, intent_revision: str, intent_fingerprint: str,
        plan_id: str, plan_fingerprint: str, artifact_fingerprint: str,
        generation_operation_id: str, erc_operation_id: str | None,
    ) -> tuple[SchematicClaimResolution, ...]:
        resolutions = []
        for claim, result in rule_results:
            proof = None
            if result.status == "PASS" and result.proof is not None:
                scope = result.proof.proof_scope
                if scope == "SCHEMATIC_ERC" and erc_operation_id is None:
                    raise ProjectCatalogConflict("ERC claim proof has no operation")
                draft_proof = SchematicClaimProof.model_construct(
                    kind=("CATALOG_PARSE_RECEIPT" if scope == "SCHEMATIC_PARSE"
                          else "CANONICAL_ERC_RECEIPT"),
                    scope=scope, tenant_id=tenant_id, project_id=project_id,
                    conversation_id=conversation_id, intent_id=intent_id,
                    intent_revision=intent_revision, intent_fingerprint=intent_fingerprint,
                    plan_id=plan_id, plan_fingerprint=plan_fingerprint,
                    artifact_fingerprint=artifact_fingerprint,
                    operation_id=(generation_operation_id if scope == "SCHEMATIC_PARSE"
                                  else erc_operation_id),
                    source_receipt_id=result.proof.source_receipt_id,
                    source_receipt_fingerprint=result.proof.source_receipt_fingerprint,
                    verification_record_id=record_id, currentness_at_recording="CURRENT",
                    fingerprint="sha256:" + "0" * 64,
                )
                proof = SchematicClaimProof.model_validate({
                    **draft_proof.model_dump(mode="python"),
                    "fingerprint": canonical_fingerprint(draft_proof, exclude={"fingerprint"}),
                })
            resolutions.append(SchematicClaimResolution(
                claim_id=claim.verification_id, status=result.status,
                reason=result.reason, proof=proof,
            ))
        return tuple(resolutions)

    def schematic_verification(
        self, project_id: str, record_id: str, *, tenant_id: str, requester: str,
    ) -> tuple[SchematicVerificationRecord, str]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        record = self._repository.schematic_verification(
            project_id, record_id, tenant_id=tenant_id, requester=requester,
        )
        if record.profile_id is not None:
            try:
                material = self._s02_verification_material(project_id, record.conversation_id, tenant_id=tenant_id, requester=requester,
                    intent_id=record.intent_id, intent_revision=record.intent_revision, intent_fingerprint=record.intent_fingerprint,
                    plan_id=record.plan_id, plan_fingerprint=record.plan_fingerprint, artifact_fingerprint=record.artifact_fingerprint,
                    generation_operation_id=record.generation_operation_id, erc_operation_id=record.erc_operation_id,
                    parse_operation_id=record.parse_operation_id, readback_operation_id=record.readback_operation_id,
                    parameter_evidence_ids=record.parameter_evidence_ids,
                    library_map_evidence_ids=record.library_map_evidence_ids,
                    library_map_evidence_fingerprints=record.library_map_evidence_fingerprints)
                return record, "CURRENT" if material[-1] == record.context_fingerprint else "STALE"
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                return record, "STALE"
        currentness = "STALE"
        try:
            intent, detail, generation, parse, erc = self._schematic_verification_sources(
                project_id, record.conversation_id, tenant_id=tenant_id, requester=requester,
                intent_id=record.intent_id, intent_revision=record.intent_revision,
                intent_fingerprint=record.intent_fingerprint, plan_id=record.plan_id,
                plan_fingerprint=record.plan_fingerprint,
                artifact_fingerprint=record.artifact_fingerprint,
                generation_operation_id=record.generation_operation_id,
                erc_operation_id=record.erc_operation_id,
            )
            if (generation.fingerprint == record.generation_receipt_fingerprint
                    and parse.fingerprint == record.parse_validation_fingerprint
                    and (None if erc is None else erc.fingerprint) == record.erc_receipt_fingerprint):
                rule_results = self._schematic_claim_rules(
                    intent, detail, generation, parse, erc,
                    project_id=project_id, conversation_id=record.conversation_id,
                    artifact_fingerprint=record.artifact_fingerprint,
                )
                current_claims = self._schematic_claim_resolutions(
                    rule_results, record_id=record.verification_id,
                    tenant_id=tenant_id, project_id=project_id,
                    conversation_id=record.conversation_id,
                    intent_id=record.intent_id,
                    intent_revision=record.intent_revision,
                    intent_fingerprint=record.intent_fingerprint,
                    plan_id=record.plan_id, plan_fingerprint=record.plan_fingerprint,
                    artifact_fingerprint=record.artifact_fingerprint,
                    generation_operation_id=record.generation_operation_id,
                    erc_operation_id=record.erc_operation_id,
                )
                if current_claims == record.claims:
                    currentness = "CURRENT"
        except (ProjectCatalogError, TypeError, ValueError, OSError):
            pass
        return record, currentness

    def schematic_verifications(
        self, project_id: str, conversation_id: str, *, tenant_id: str,
        requester: str, limit: int = 50,
    ) -> tuple[tuple[SchematicVerificationRecord, str], ...]:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        identities = self._repository.schematic_verifications(
            project_id, conversation_id, tenant_id=tenant_id,
            requester=requester, limit=limit,
        )
        return tuple(
            self.schematic_verification(
                project_id, record_id, tenant_id=tenant_id, requester=requester,
            )
            for record_id in identities
        )

    def generic_pcb_write_target_allowed(self, operation: object) -> bool:
        from embedded_copilot.execution_worker.contracts import PCBWriteOperation
        if not isinstance(operation, PCBWriteOperation):
            return True
        try:
            return not self._repository.has_imported_pcb_target(
                operation.project_id, operation.target_relative_path,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
        except (ProjectCatalogError, TypeError, ValueError, OSError):
            return False

    def revalidate_circuit_bound_operation(self, operation: object) -> bool:
        """Read the current Catalog Owner before a queued design operation has side effects."""
        from embedded_copilot.execution_worker.contracts import (
            PcbDrcOperation,
            PCBWriteOperation,
            SchematicParseOperation,
            SchematicErcOperation,
            SchematicWriteOperation,
        )

        if isinstance(operation, SchematicParseOperation) or (isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is not None):
            return self.revalidate_schematic_evidence_operation(operation)
        if not isinstance(operation, (SchematicWriteOperation, SchematicErcOperation, PCBWriteOperation, PcbDrcOperation)):
            return True
        if isinstance(operation, PCBWriteOperation) and not self.generic_pcb_write_target_allowed(operation):
            return False
        try:
            detail = self.conversation(
                operation.project_id, operation.conversation_id,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            if isinstance(operation, (SchematicWriteOperation, SchematicErcOperation)):
                planning = detail.schematic_planning
                binding = None if planning is None else (planning.approved_plan or planning.proposal)
            else:
                pcb_state = detail.pcb_engineering_state
                binding = None if pcb_state is None else pcb_state.intent_source_binding
            self.require_current_circuit_binding(
                operation.project_id, binding,
                tenant_id=operation.tenant_id, requester=operation.requester_id,
            )
            if isinstance(operation, SchematicWriteOperation):
                production = detail.schematic_production
                if (
                    production is None
                    or production.status is not SchematicProductionStatus.QUEUED
                    or production.operation_id != operation.operation_id
                    or production.operation_fingerprint != operation.fingerprint
                    or production.request.request_id != operation.schematic_request_id
                    or production.request.fingerprint != operation.schematic_request_fingerprint
                    or production.proposal.proposal_id != operation.proposal_id
                    or production.proposal.fingerprint != operation.proposal_fingerprint
                ):
                    return False
                if production.request.schematic_plan_id is not None:
                    plan = None if planning is None else planning.approved_plan
                    selection = detail.hardware_selection
                    if (
                        plan is None or selection is None
                        or production.request.schematic_plan_id != plan.plan_id
                        or production.request.schematic_plan_fingerprint != plan.fingerprint
                    ):
                        return False
                    self._require_current_model_selection(selection, detail=detail)
                    if self._schematic_plan_source_currentness(
                        detail, plan, project_id=operation.project_id,
                        tenant_id=operation.tenant_id, requester=operation.requester_id,
                    ) != "CURRENT":
                        return False
            if (
                isinstance(operation, SchematicErcOperation)
                and binding is not None
                and getattr(binding, "circuit_intent_id", None) is not None
            ):
                production = detail.schematic_production
                generation = None if production is None else production.generation_receipt
                if (
                    production is None
                    or production.status is not SchematicProductionStatus.GENERATED
                    or generation is None
                    or generation.operation_id != operation.source_operation_id
                    or generation.operation_receipt_fingerprint
                    != operation.source_operation_receipt_fingerprint
                    or generation.artifact_fingerprint != operation.schematic_artifact_fingerprint
                    or generation.artifact_path != operation.target_relative_path
                ):
                    return False
            return True
        except (ProjectCatalogError, TypeError, ValueError, OSError):
            return False

    def _brownfield_snapshot(self, project_id: str, *, tenant_id: str, requester: str):
        project = self.get_project(project_id, tenant_id=tenant_id)
        if self._repository.project_owner(project_id) != requester:
            raise ProjectCatalogForbidden("project requester mismatch")
        if (
            project.creation_mode is not ProjectCreationMode.ATTACH_EXISTING
            or project.initialization_status is not ProjectInitializationStatus.READY
        ):
            raise ProjectCatalogRejected("attached project is not ready")
        if self._brownfield_workspace is None:
            raise ProjectCatalogUnavailable("workspace snapshot source is unavailable")
        snapshot = self._brownfield_workspace.snapshot(tenant_id=tenant_id, project_id=project_id)
        if snapshot.tenant_id != tenant_id or snapshot.project_id != project_id:
            raise ProjectCatalogRejected("workspace snapshot identity mismatch")
        return project, snapshot

    def brownfield_build_preview(self, project_id: str, *, tenant_id: str, requester: str) -> dict[str, object]:
        project, snapshot = self._brownfield_snapshot(project_id, tenant_id=tenant_id, requester=requester)
        return {
            "project_id": project_id,
            "project_fingerprint": project.fingerprint,
            "workspace_fingerprint": snapshot.project_fingerprint,
            "workspace_target_id": snapshot.target_id,
            "workspace_target_fingerprint": snapshot.target_fingerprint,
            "source_files": tuple(item.relative_file_reference for item in snapshot.files if item.exists),
        }

    def brownfield_build_context(
        self, project_id: str, *, tenant_id: str, requester: str
    ) -> BrownfieldBuildContext:
        project, snapshot = self._brownfield_snapshot(project_id, tenant_id=tenant_id, requester=requester)
        context = self._repository.brownfield_build_context(project_id)
        if context is None:
            raise ProjectCatalogNotFound("brownfield build context was not found")
        if context.tenant_id != tenant_id or context.requester != requester:
            raise ProjectCatalogForbidden("brownfield build context identity mismatch")
        if (
            context.project_fingerprint != project.fingerprint
            or context.workspace_fingerprint != snapshot.project_fingerprint
            or context.workspace_target_id != snapshot.target_id
            or context.workspace_target_fingerprint != snapshot.target_fingerprint
        ):
            raise ProjectCatalogConflict("brownfield build context is stale")
        return context

    def propose_brownfield_build(
        self,
        project_id: str,
        *,
        tenant_id: str,
        requester: str,
        workspace_fingerprint: str,
        target_platform: str,
        mcu_family: str,
        mcu_exact_part: str,
        source_layout: tuple[str, ...],
        configuration: FirmwareBuildConfiguration,
    ) -> BrownfieldBuildContext:
        project, snapshot = self._brownfield_snapshot(project_id, tenant_id=tenant_id, requester=requester)
        if snapshot.project_fingerprint != workspace_fingerprint:
            raise ProjectCatalogConflict("workspace snapshot changed")
        checked_configuration = FirmwareBuildConfiguration.model_validate(configuration.model_dump(mode="python"))
        if checked_configuration.target_kind != mcu_exact_part:
            raise ProjectCatalogRejected("build target and configuration mismatch")
        files = {item.relative_file_reference: item for item in snapshot.files if item.exists}
        if not source_layout or len(source_layout) != len(set(source_layout)):
            raise ProjectCatalogRejected("source layout is invalid")
        if any(path not in files or files[path].size > 16 * 1024 * 1024 for path in source_layout):
            raise ProjectCatalogRejected("source layout is not in the current workspace")
        for path in (
            checked_configuration.cmake_project,
            checked_configuration.cmake_toolchain_file,
            checked_configuration.startup_source,
            checked_configuration.system_source,
        ):
            if path not in files:
                raise ProjectCatalogRejected("build configuration file is not in the current workspace")
        selected = tuple((path, files[path].content_fingerprint) for path in source_layout)
        revision = "sha256:" + hashlib.sha256(
            json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        configuration_fingerprint = firmware_canonical_fingerprint(checked_configuration)
        configuration_id = "build-config-" + configuration_fingerprint[7:39]
        firmware_spec = BrownfieldFirmwareSpec(
            firmware_spec_id="brownfield-firmware-" + uuid4().hex,
            project_id=project_id,
            workspace_fingerprint=snapshot.project_fingerprint,
            source_revision=revision,
            target_platform=target_platform,
            mcu_family=mcu_family,
            mcu_exact_part=mcu_exact_part,
            source_layout=source_layout,
            build_system=checked_configuration.build_system,
            toolchain_family=checked_configuration.toolchain_id,
        )
        implementation_spec = BrownfieldImplementationSpec(
            implementation_id="brownfield-implementation-" + uuid4().hex,
            project_id=project_id,
            firmware_spec_id=firmware_spec.firmware_spec_id,
            firmware_spec_fingerprint=firmware_spec.fingerprint,
            build_configuration_id=configuration_id,
            build_configuration_fingerprint=configuration_fingerprint,
        )
        context_material = (
            project.fingerprint, snapshot.project_fingerprint, snapshot.target_fingerprint,
            firmware_spec.fingerprint, implementation_spec.fingerprint,
        )
        context_fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(context_material, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        plan_id = "brownfield-build-plan-" + uuid4().hex
        phase_id = "brownfield-build-phase-" + uuid4().hex
        plan = EngineeringPlan.create(
            plan_id=plan_id,
            requirement_id="brownfield-build-confirmation-" + context_fingerprint[7:39],
            project_id=project_id,
            context_fingerprint=context_fingerprint,
            tasks=(EngineeringTask.create(
                task_id="brownfield-build-task-" + uuid4().hex,
                title="构建当前已有工程",
                description="对已确认源码快照执行受控固件构建",
                risk="构建需要独立审批",
            ),),
            title="已有工程固件构建计划",
            objective="构建已接入工程的当前源码快照",
            status=PlanStatus.PROPOSED,
            phases=(EngineeringPlanPhase.create(
                phase_id=phase_id,
                plan_id=plan_id,
                project_id=project_id,
                name="Firmware Build",
                description="对已确认的已有工程源码快照构建固件 Artifact",
                outputs=(PlanOutput.create(
                    output_id="brownfield-build-output-" + uuid4().hex,
                    phase_id=phase_id,
                    plan_id=plan_id,
                    project_id=project_id,
                    name="Firmware Artifact",
                    output_type="BUILD_ARTIFACT",
                ),),
            ),),
            confidence=0.0,
        )
        previous = self._repository.brownfield_build_context(project_id)
        now = self._now()
        context = BrownfieldBuildContext(
            project_id=project_id,
            tenant_id=tenant_id,
            requester=requester,
            project_fingerprint=project.fingerprint,
            workspace_target_id=snapshot.target_id,
            workspace_target_fingerprint=snapshot.target_fingerprint,
            workspace_fingerprint=snapshot.project_fingerprint,
            plan=plan,
            firmware_spec=firmware_spec,
            implementation_spec=implementation_spec,
            configuration=checked_configuration,
            created_at=now,
            updated_at=now,
        )
        current_project, current_snapshot = self._brownfield_snapshot(project_id, tenant_id=tenant_id, requester=requester)
        if (
            current_project.fingerprint != project.fingerprint
            or current_snapshot.project_fingerprint != snapshot.project_fingerprint
            or current_snapshot.target_fingerprint != snapshot.target_fingerprint
        ):
            raise ProjectCatalogConflict("brownfield build context changed before proposal")
        self._repository.save_brownfield_build_context(
            context, previous_fingerprint=None if previous is None else previous.fingerprint
        )
        return context

    def decide_brownfield_build(
        self,
        project_id: str,
        *,
        tenant_id: str,
        requester: str,
        proposal_fingerprint: str,
        decision: PlanReviewStatus,
    ) -> BrownfieldBuildContext:
        context = self.brownfield_build_context(project_id, tenant_id=tenant_id, requester=requester)
        if context.plan.status is not PlanStatus.PROPOSED or context.review is not None:
            raise ProjectCatalogConflict("brownfield build proposal was already reviewed")
        if proposal_fingerprint != context.plan.content_fingerprint:
            raise ProjectCatalogConflict("brownfield build proposal changed")
        project, snapshot = self._brownfield_snapshot(project_id, tenant_id=tenant_id, requester=requester)
        if (
            project.fingerprint != context.project_fingerprint
            or snapshot.project_fingerprint != context.workspace_fingerprint
            or snapshot.target_id != context.workspace_target_id
            or snapshot.target_fingerprint != context.workspace_target_fingerprint
        ):
            raise ProjectCatalogConflict("brownfield build proposal is stale")
        now = self._now()
        if decision is PlanReviewStatus.APPROVED:
            plan, review = approve_engineering_plan(context.plan, reviewer=requester, decided_at=now)
        elif decision is PlanReviewStatus.REJECTED:
            plan = transition_engineering_plan(context.plan, PlanStatus.REJECTED)
            review = PlanReviewDecision.create(
                plan_id=plan.plan_id, project_id=project_id,
                proposal_fingerprint=context.plan.content_fingerprint,
                status=PlanReviewStatus.REJECTED, reviewer=requester, decided_at=now,
            )
        else:
            raise ProjectCatalogRejected("terminal plan review decision is required")
        updated = BrownfieldBuildContext.model_validate(
            context.model_copy(update={
                "plan": plan, "review": review, "updated_at": now,
                "fingerprint": "sha256:" + "0" * 64,
            }).model_dump(mode="python"), strict=False,
        )
        self._repository.save_brownfield_build_context(updated, previous_fingerprint=context.fingerprint)
        return updated

    def delete_project(
        self,
        project_id: str,
        *,
        project_fingerprint: str,
        tenant_id: str,
        requester: str,
    ) -> ProjectDeletionResult:
        project = self._repository.project(project_id)
        if project is None:
            raise ProjectCatalogNotFound("project was not found")
        if project.tenant_id != tenant_id:
            raise ProjectCatalogForbidden("project identity mismatch")
        # v6.3 has an independent SQLite transaction. It is deliberately
        # committed and verified before the Catalog transaction; the two
        # databases are not presented as one atomic transaction.
        if self._v63_memory_governance is not None:
            tombstone = getattr(self._v63_memory_governance, "tombstone_project", None)
            inaccessible = getattr(self._v63_memory_governance, "is_project_inaccessible", None)
            if not callable(tombstone) or not callable(inaccessible):
                raise ProjectCatalogUnavailable("v6.3 tombstone service is unavailable")
            try:
                tombstone(tenant_id, project_id)
                if not inaccessible(tenant_id, project_id):
                    raise ProjectCatalogUnavailable("v6.3 tombstone verification failed")
            except ProjectCatalogUnavailable:
                raise
            except Exception as error:
                raise ProjectCatalogUnavailable("v6.3 tombstone is unavailable") from error
        if self._knowledge_manifest is not None:
            tombstone = getattr(self._knowledge_manifest, "tombstone_project", None)
            if not callable(tombstone):
                raise ProjectCatalogUnavailable("knowledge tombstone service is unavailable")
            try:
                tombstone(tenant=tenant_id, project_id=project_id, requester=requester)
            except Exception as error:
                raise ProjectCatalogUnavailable("knowledge tombstone is unavailable") from error
        deleted_at = self._now()
        stored = self._repository.delete_project(
            project_id,
            tenant_id=tenant_id,
            requester=requester,
            project_fingerprint=project_fingerprint,
            deleted_at=deleted_at.isoformat(),
        )
        if stored is None:
            raise ProjectCatalogNotFound("project was not found")
        sidecar = self._metadata_directory / f"{project_id}.json"
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as error:
            raise ProjectCatalogUnavailable("project metadata cleanup is unavailable") from error
        return ProjectDeletionResult(
            project_id=project_id,
            project_fingerprint=project.fingerprint,
            deleted_at=deleted_at,
        )

    def projects(self, tenant_id: str) -> tuple[ProjectView, ...]:
        return self._repository.projects(tenant_id)

    def context(self, project_id: str, *, tenant_id: str) -> ProjectContextView:
        project = self.get_project(project_id, tenant_id=tenant_id)
        value = self._repository.context(project.project_id)
        if value is not None:
            return value
        return ProjectContextView(
            project_id=project.project_id,
            status=project.initialization_status,
            groups=(), evidence_sources=(), capabilities=(), generated_at=project.updated_at,
        )

    def workbench_resources(self, project_id: str, *, tenant_id: str, limit: int = 500) -> tuple[tuple[dict[str, object], ...], bool]:
        """Return bounded, metadata-only resources for the Desktop workbench."""
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("workbench resource limit is invalid")
        project = self.get_project(project_id, tenant_id=tenant_id)
        project_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
        _, root = self._relative(project.relative_path, root=project_root)
        files: list[tuple[str, Path, str]] = []
        scanned = 0
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > 8:
                continue
            for entry in sorted(os.scandir(directory), key=lambda item: item.name.casefold()):
                if entry.name in _IGNORED or entry.name.startswith(".") or entry.is_symlink():
                    continue
                scanned += 1
                if scanned > 5000:
                    raise ProjectCatalogUnavailable("project scan limit exceeded")
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append((path, depth + 1))
                    continue
                relative = path.relative_to(root).as_posix()
                category = self._workbench_category(path, root)
                if category is not None:
                    files.append((relative, path, category))
        directories: set[str] = set()
        for relative, _, _ in files:
            parts = relative.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                directories.add("/".join(parts[:index]))
        nodes: list[dict[str, object]] = []
        for relative in sorted(directories, key=lambda value: (value.count("/"), value.casefold())):
            parent = relative.rsplit("/", 1)[0] if "/" in relative else None
            digest = hashlib.sha256(f"{project_id}|{relative}".encode()).hexdigest()
            nodes.append({"resource_id": f"resource-{digest[:24]}", "parent_id": self._resource_id(project_id, parent), "name": relative.rsplit("/", 1)[-1], "relative_path": relative, "node_type": "DIRECTORY", "category": "FOLDER"})
        for relative, _, category in sorted(files, key=lambda item: item[0].casefold()):
            parent = relative.rsplit("/", 1)[0] if "/" in relative else None
            digest = hashlib.sha256(f"{project_id}|{relative}".encode()).hexdigest()
            nodes.append({"resource_id": f"resource-{digest[:24]}", "parent_id": self._resource_id(project_id, parent), "name": relative.rsplit("/", 1)[-1], "relative_path": relative, "node_type": "FILE", "category": category, "language": self._workbench_language(relative), "source_fingerprint": f"sha256:{digest}"})
        truncated = len(nodes) > limit
        return tuple(nodes[:limit]), truncated

    @staticmethod
    def _resource_id(project_id: str, relative: str | None) -> str | None:
        if relative is None:
            return None
        digest = hashlib.sha256(f"{project_id}|{relative}".encode()).hexdigest()
        return f"resource-{digest[:24]}"

    @staticmethod
    def _workbench_category(path: Path, root: Path) -> str | None:
        suffix = path.suffix.lower()
        tokens = {part.casefold() for part in path.relative_to(root).parts[:-1]}
        if tokens & _TEST_TOKENS:
            return "TEST"
        if suffix in _FIRMWARE_SUFFIXES or path.name in {"CMakeLists.txt", "sdkconfig", "platformio.ini"}:
            return "FIRMWARE"
        if suffix in _HARDWARE_SUFFIXES or suffix == ".kicad_pro":
            return "HARDWARE"
        if suffix in _DOCUMENT_SUFFIXES:
            return "DOCUMENT"
        if suffix in {".yaml", ".yml", ".toml", ".json"}:
            return "CONFIG"
        if suffix == ".csv":
            return "DATA"
        return None

    @staticmethod
    def _workbench_language(relative: str) -> str | None:
        suffix = Path(relative).suffix.lower()
        return {".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".ino": "Arduino", ".s": "ASM", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".json": "JSON", ".md": "Markdown"}.get(suffix)

    def knowledge_snapshot_root(
        self,
        project_id: str,
        *,
        tenant_id: str | None = None,
    ) -> Path:
        """Resolve the existing project-scoped knowledge snapshot root.

        This method only resolves the persisted project binding and validates
        the resulting directory. It deliberately does not scan the directory
        or infer evidence from its contents.
        """
        project = self._repository.project(project_id)
        if project is None:
            raise ProjectCatalogRejected("project was not found")
        if tenant_id is not None and project.tenant_id != tenant_id:
            raise ProjectCatalogForbidden("project identity mismatch")
        if project.initialization_status is not ProjectInitializationStatus.READY:
            raise ProjectCatalogUnavailable("project snapshot is not ready")
        project_root = self._location_service.resolve(project.location_id, tenant_id=project.tenant_id)
        _, root = self._relative(project.relative_path, root=project_root)
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise ProjectCatalogUnavailable("project snapshot root is unavailable")
        resolved = root.resolve(strict=True)
        if not resolved.is_relative_to(project_root) or resolved == project_root:
            raise ProjectCatalogRejected("project snapshot root is invalid")
        return resolved

    def project(self, tenant_id: str, project_id: str) -> ChatProjectReference:
        value = self.get_project(project_id, tenant_id=tenant_id)
        return ChatProjectReference(
            tenant_id=tenant_id,
            project_id=value.project_id,
            project_name_reference=value.name,
            workspace_reference=f"project:{value.project_id}",
        )

    def references(self, tenant_id: str, project_id: str) -> tuple[ChatContextReference, ...]:
        context = self.context(project_id, tenant_id=tenant_id)
        project_references = tuple(
            ChatContextReference(
                tenant_id=tenant_id,
                project_id=project_id,
                reference_id=item.reference_id,
                reference_kind=ChatReferenceKind.WORKSPACE,
                source_fingerprint=item.source_fingerprint,
                display_label=f"{group.category} · {item.display_label}",
            )
            for group in context.groups for item in group.references
        )
        if self._approved_memory is None:
            return project_references
        try:
            raw_memories = self._approved_memory.list(project_id)
            memories = tuple(
                ApprovedEngineeringMemory.model_validate(item.model_dump(mode="python"))
                for item in raw_memories
                if getattr(item, "status", None) == "APPROVED"
            )
        except Exception as error:
            raise ProjectCatalogUnavailable("approved memory projection is unavailable") from error
        if any(item.project_id != project_id or item.status != "APPROVED" for item in memories):
            raise ProjectCatalogRejected("approved memory projection mismatch")
        memory_references = tuple(
            ChatContextReference(
                tenant_id=tenant_id,
                project_id=project_id,
                reference_id=item.memory_id,
                reference_kind=ChatReferenceKind.MEMORY,
                source_fingerprint=item.fingerprint,
                display_label=item.summary[:128],
            )
            for item in memories
        )
        return project_references + memory_references

    def create_conversation(self, project_id: str, *, tenant_id: str, requester: str) -> ConversationSessionView:
        project = self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        now = self._now()
        conversation_id = f"conversation-{uuid4()}"
        requirement_context = None
        if project.creation_mode is ProjectCreationMode.CREATE_NEW:
            requirement_context = merge_requirement_context(
                None,
                project_id=project.project_id,
                conversation_id=conversation_id,
                turn_id=f"{conversation_id}-initial",
                utterance="",
                project_type=project.project_type.value,
                lifecycle=RequirementLifecycle.DISCOVERY_STARTED,
            )
        detail = ConversationDetail(
            conversation_id=conversation_id, project_id=project_id, tenant_id=tenant_id,
            requester=requester, title="新对话", turns=(), created_at=now, updated_at=now,
            requirement_context=requirement_context,
        )
        self._repository.save_conversation(detail)
        return self._open_conversation_session(detail)

    def resume_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> ConversationSessionView:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        return self._open_conversation_session(detail)

    def _open_conversation_session(self, detail: ConversationDetail) -> ConversationSessionView:
        session_id = f"session-{uuid4()}"
        available = self._chat is not None
        if self._chat is not None:
            self._chat.open_session(ChatSessionOpenRequest(
                session_id=session_id, tenant_id=detail.tenant_id, project_id=detail.project_id,
                requester=detail.requester, expires_at=self._now() + timedelta(hours=1),
            ))
        self._repository.bind_session(session_id, detail.conversation_id)
        return ConversationSessionView(conversation=detail, session_id=session_id, chat_available=available)

    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> ConversationDetail:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        value = self._repository.conversation(conversation_id)
        if value is None or value.project_id != project_id:
            raise ProjectCatalogNotFound("conversation was not found")
        if value.tenant_id != tenant_id or value.requester != requester:
            raise ProjectCatalogForbidden("conversation identity mismatch")
        return value

    def persist_conversation_detail(self, detail: ConversationDetail) -> ConversationDetail:
        """Persist a fully validated canonical engineering conversation state.

        This is deliberately the same repository path used by the normal
        project-catalog turn transitions; callers cannot persist a different
        tenant/project identity or bypass ConversationDetail validation.
        """

        existing = self._repository.conversation(detail.conversation_id)
        incoming = detail
        if existing is not None and existing.firmware_production is not None and existing.firmware_production.status is not FirmwareProductionStatus.STALE:
            candidate = ConversationDetail.model_validate(
                detail.model_copy(
                    update={"firmware_production": None, "fingerprint": "sha256:" + "0" * 64}
                ).model_dump(mode="python"),
                strict=False,
            )
            existing_authority = (
                existing.firmware_production.firmware_plan_fingerprint,
                existing.firmware_production.design_truth_fingerprint,
                existing.firmware_production.implementation_fingerprint,
                existing.firmware_production.build_configuration_fingerprint,
            )
            candidate_authority = (
                None if candidate.firmware_spec is None else candidate.firmware_spec.fingerprint,
                None if candidate.design_truth is None else candidate.design_truth.fingerprint,
                None if candidate.implementation_spec is None else candidate.implementation_spec.fingerprint,
                None if candidate.build_configuration is None else firmware_canonical_fingerprint(candidate.build_configuration),
            )
            if existing_authority != candidate_authority:
                stale = existing.firmware_production.mark_stale(
                    reason="upstream firmware plan or engineering truth changed",
                    updated_at=detail.updated_at,
                )
                incoming = detail.model_copy(
                    update={"firmware_production": stale, "fingerprint": "sha256:" + "0" * 64}
                )
        if (
            existing is not None
            and existing.pcb_manufacturer_profile is not None
            and _pcb_manufacturer_profile_fingerprint(existing)
            != _pcb_manufacturer_profile_fingerprint(incoming)
        ):
            # A source/profile change invalidates the old DFM projection. The
            # new profile remains persisted, but the summary and gate must be
            # rebuilt through close_pcb_engineering_verification.
            incoming = incoming.model_copy(
                update={
                    "pcb_engineering_verification_summary": None,
                    "pcb_ready_for_fab": None,
                    "pcb_ready_for_fab_approval": None,
                    "pcb_manufacturing_measurements": None,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
        if (
            existing is not None
            and any(
                value is not None
                for value in (
                    existing.pcb_engineering_state,
                    existing.pcb_constraint_set,
                    existing.pcb_verification_plan,
                )
            )
            and (
                incoming.pcb_engineering_state is None
                or incoming.pcb_constraint_set is None
                or incoming.pcb_verification_plan is None
                or _pcb_foundation_authority_fingerprint(existing)
                != _pcb_foundation_authority_fingerprint(incoming)
            )
        ):
            # Validate the incoming stale transition without allowing an old
            # final summary/gate to fail closed in the wrong order.  The
            # later stale branch replaces the foundation and B results with
            # their marked-stale projections.
            incoming = incoming.model_copy(
                update={
                    "pcb_engineering_verification_summary": None,
                    "pcb_ready_for_fab": None,
                    "pcb_ready_for_fab_approval": None,
                    "pcb_manufacturing_measurements": None,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
        checked = ConversationDetail.model_validate(incoming.model_dump(mode="python"), strict=False)
        if existing is not None and (
            existing.project_id != checked.project_id
            or existing.tenant_id != checked.tenant_id
            or existing.requester != checked.requester
        ):
            raise ProjectCatalogForbidden("conversation identity mismatch")
        if (
            existing is not None
            and existing.pcb_production is not None
            and existing.pcb_production.status is not PCBProductionStatus.STALE
            and (
                checked.pcb_production is None
                or _pcb_authority_fingerprint(existing) != _pcb_authority_fingerprint(checked)
            )
        ):
            try:
                stale = _mark_pcb_production_stale(
                    existing.pcb_production,
                    reason="upstream G4 or hardware authority fingerprint changed",
                )
            except (TypeError, ValueError) as error:
                raise PCBProductionRejected("PCB production could not be invalidated safely") from error
            checked = checked.model_copy(
                update={
                    "pcb_production": stale,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
        if (
            existing is not None
            and any(
                value is not None
                for value in (
                    existing.pcb_engineering_state,
                    existing.pcb_constraint_set,
                    existing.pcb_verification_plan,
                )
            )
            and (
                checked.pcb_engineering_state is None
                or checked.pcb_constraint_set is None
                or checked.pcb_verification_plan is None
                or _pcb_foundation_authority_fingerprint(existing)
                != _pcb_foundation_authority_fingerprint(checked)
            )
            and any(
                value is not None
                and value.status.value != "STALE"
                for value in (
                    existing.pcb_engineering_state,
                    existing.pcb_constraint_set,
                    existing.pcb_verification_plan,
                )
            )
        ):
            stale_foundation = _mark_pcb_foundation_stale(
                existing,
                reason="G5 PCB artifact or upstream hardware/design truth changed",
            )
            checked = checked.model_copy(
                update={
                    "pcb_engineering_state": stale_foundation.pcb_engineering_state,
                    "pcb_constraint_set": stale_foundation.pcb_constraint_set,
                    "pcb_verification_plan": stale_foundation.pcb_verification_plan,
                    "pcb_decoupling_verification": stale_foundation.pcb_decoupling_verification,
                    "pcb_return_path_verification": stale_foundation.pcb_return_path_verification,
                    "pcb_power_loop_verification": stale_foundation.pcb_power_loop_verification,
                    "pcb_differential_pair_verification": stale_foundation.pcb_differential_pair_verification,
                    "pcb_mapping_verification": stale_foundation.pcb_mapping_verification,
                    "pcb_component_mapping_relationships": stale_foundation.pcb_component_mapping_relationships,
                    "pcb_pin_mapping_relationships": stale_foundation.pcb_pin_mapping_relationships,
                    "pcb_pad_mapping_relationships": stale_foundation.pcb_pad_mapping_relationships,
                    "pcb_engineering_verification_summary": stale_foundation.pcb_engineering_verification_summary,
                    "pcb_ready_for_fab": stale_foundation.pcb_ready_for_fab,
                    "pcb_ready_for_fab_approval": stale_foundation.pcb_ready_for_fab_approval,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(
                checked.model_dump(mode="python"),
                strict=False,
            )
        self._repository.save_conversation(checked)
        return checked

    def refresh_pcb_engineering_state(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        project_requirements: tuple[PCBFoundationEvidence, ...] = (),
        datasheet_evidence: tuple[PCBFoundationEvidence, ...] = (),
        interface_evidence: tuple[PCBFoundationEvidence, ...] = (),
        engineering_knowledge: tuple[PCBFoundationEvidence, ...] = (),
        power_stage_evidence: tuple[PCBPowerStageEvidence, ...] = (),
        power_loop_rules: tuple[PCBPowerLoopRule, ...] = (),
        differential_pairs: tuple[PCBDifferentialPairProjection, ...] = (),
        differential_pair_rules: tuple[PCBDifferentialPairRule, ...] = (),
        differential_reference_requirement: str | None = None,
        differential_impedance_ohms: float | None = None,
    ) -> ConversationDetail:
        """Project the current, parse-verified G5 board into G7-A state.

        This is a read-only projection boundary. It consumes the persisted G5
        receipt and the existing bounded KiCad parser; it does not write or
        mutate the PCB artifact, DesignTruth, HardwareTruth, or any execution
        state.
        """

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        production = detail.pcb_production
        receipt = None if production is None else production.generation_receipt
        if (
            production is None
            or receipt is None
            or receipt.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED
            or production.status is PCBProductionStatus.STALE
        ):
            raise PCBProductionRejected(
                "G7-A requires a current parse-verified G5 PCB generation receipt"
            )
        try:
            root = self.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
            normalized, target = self._relative(receipt.artifact_path, root=root)
            if (
                normalized != receipt.artifact_path
                or target.is_symlink()
                or not target.is_file()
                or target.stat().st_size <= 0
            ):
                raise ValueError("G5 PCB artifact is unavailable")
            content = target.read_bytes()
            content_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
            if content_fingerprint != receipt.artifact_fingerprint:
                raise ValueError("G5 PCB artifact fingerprint is stale")
            attachment = UserAttachment(
                id="g5-pcb",
                filename=Path(normalized).name,
                media_type=AttachmentType.EDA,
                content_type="application/x-kicad-pcb",
                size_bytes=len(content),
                metadata={"category": "eda", "format": "kicad_pcb"},
            )
            parsed = KiCadPCBParser(
                RootedPCBSourceResolver(root, {"g5-pcb": normalized})
            ).parse(attachment)
            artifact = PCBArtifactReference.create(
                project_id=detail.project_id,
                artifact_id=receipt.artifact.artifact_id,
                artifact_type=PCBArtifactType.BOARD,
                source_format=PCBSourceFormat.KICAD_PCB,
                source_reference=f"artifact://{receipt.artifact.artifact_id}",
                filename=Path(normalized).name,
                byte_size=len(content),
                content_fingerprint=content_fingerprint,
                captured_at=receipt.created_at,
            )

            def evidence(
                suffix: str,
                source_type: PCBFoundationEvidenceSource,
                source_id: str,
                source_fingerprint: str,
                claim: str,
            ) -> PCBFoundationEvidence:
                return PCBFoundationEvidence.create(
                    evidence_id=f"pcb-evidence-{suffix}",
                    project_id=detail.project_id,
                    source_type=source_type,
                    source_id=source_id,
                    source_fingerprint=source_fingerprint,
                    claim=claim,
                    locator=f"pcb://{source_id}",
                )

            evidence_refs = [
                evidence(
                    "artifact-" + receipt.artifact.artifact_id,
                    PCBFoundationEvidenceSource.ARTIFACT,
                    receipt.artifact.artifact_id,
                    content_fingerprint,
                    "Observed from the current G5 PCB artifact.",
                ),
                evidence(
                    "g5-receipt-" + receipt.receipt_id,
                    PCBFoundationEvidenceSource.G5_RECEIPT,
                    receipt.receipt_id,
                    receipt.fingerprint,
                    "Bound to the persisted G5 PCB generation and parse receipt.",
                ),
            ]
            if detail.design_truth is not None:
                evidence_refs.append(
                    evidence(
                        "design-truth-" + detail.design_truth.truth_id,
                        PCBFoundationEvidenceSource.DESIGN_TRUTH,
                        detail.design_truth.truth_id,
                        detail.design_truth.fingerprint,
                        "Existing approved DesignTruth used only for identity and connectivity comparison.",
                    )
                )
            hardware_truth = (
                None
                if detail.hardware_selection is None
                else detail.hardware_selection.hardware_truth
            )
            if hardware_truth is not None:
                evidence_refs.append(
                    evidence(
                        "hardware-truth-" + hardware_truth.truth_id,
                        PCBFoundationEvidenceSource.HARDWARE_TRUTH,
                        hardware_truth.truth_id,
                        hardware_truth.fingerprint,
                        "Existing approved HardwareTruth used only as PCB context evidence.",
                    )
                )
            state = PCBEngineeringProjector().project(
                project_id=detail.project_id,
                revision=content_fingerprint,
                source_artifact=artifact,
                parsed=parsed,
                evidence_refs=tuple(evidence_refs),
                g5_request=production.request,
                design_truth=detail.design_truth,
                hardware_truth=hardware_truth,
                generation_receipt=receipt,
                differential_pairs=differential_pairs,
            )
            compiler = PCBConstraintCompiler()
            constraint_set = compiler.compile(
                state,
                project_requirements=project_requirements,
                datasheet_evidence=datasheet_evidence,
                interface_evidence=interface_evidence,
                engineering_knowledge=engineering_knowledge,
                power_stage_evidence=power_stage_evidence,
                power_loop_rules=power_loop_rules,
                differential_pair_rules=differential_pair_rules,
                differential_reference_requirement=differential_reference_requirement,
                differential_impedance_ohms=differential_impedance_ohms,
            )
            state = DecouplingRelationshipResolver().resolve_state(
                state,
                constraint_set,
                design_truth=detail.design_truth,
                hardware_truth=hardware_truth,
            )
            constraint_set = compiler.compile(
                state,
                project_requirements=project_requirements,
                datasheet_evidence=datasheet_evidence,
                interface_evidence=interface_evidence,
                engineering_knowledge=engineering_knowledge,
                power_stage_evidence=power_stage_evidence,
                power_loop_rules=power_loop_rules,
                differential_pair_rules=differential_pair_rules,
                differential_reference_requirement=differential_reference_requirement,
                differential_impedance_ohms=differential_impedance_ohms,
            )
            plan = compiler.build_plan(state, constraint_set)
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            raise PCBProductionRejected("G7-A PCB engineering projection failed") from error
        next_decoupling = detail.pcb_decoupling_verification
        if next_decoupling is not None and not next_decoupling.stale:
            current_inputs = {state.fingerprint, constraint_set.fingerprint, plan.fingerprint}
            if not current_inputs.issubset(set(next_decoupling.compared_fingerprints)):
                next_decoupling = mark_decoupling_stale(
                    next_decoupling,
                    reason="G7-A PCB state, constraint set, or verification plan changed",
                    affected_objects=tuple(
                        dict.fromkeys(
                            item
                            for relation in state.decoupling_relations
                            for item in (
                                relation.load_component_id,
                                relation.load_power_pin_id,
                                relation.load_pad_id,
                                relation.capacitor_component_id,
                                relation.capacitor_power_pad_id,
                                relation.capacitor_return_pad_id,
                                relation.power_net_id,
                                relation.return_net_id,
                            )
                            if item is not None
                        )
                    ),
                )
        next_return_path = detail.pcb_return_path_verification
        if next_return_path is not None and not next_return_path.stale:
            current_inputs = {state.fingerprint, constraint_set.fingerprint, plan.fingerprint}
            if not current_inputs.issubset(set(next_return_path.compared_fingerprints)):
                return_affected = tuple(
                    dict.fromkeys(
                        (
                            *state.affected_objects,
                            *constraint_set.affected_objects,
                            *plan.affected_objects,
                            *(item.segment_id for item in state.routing),
                            *(item.route_id for item in state.route_paths),
                            *(item.plane_id for item in state.planes),
                            *(item.keepout_id for item in state.keepouts),
                            *(item.via_id for item in state.vias),
                        )
                    )
                ) or ("pcb-return-path",)
                next_return_path = mark_return_path_stale(
                    next_return_path,
                    reason="G7-A PCB state, constraint set, or verification plan changed",
                    affected_objects=return_affected,
                )
        next_power_loop = detail.pcb_power_loop_verification
        if next_power_loop is not None and not next_power_loop.stale:
            current_inputs = {state.fingerprint, constraint_set.fingerprint, plan.fingerprint}
            if not current_inputs.issubset(set(next_power_loop.compared_fingerprints)):
                power_affected = tuple(
                    dict.fromkeys(
                        (
                            *state.affected_objects,
                            *constraint_set.affected_objects,
                            *plan.affected_objects,
                            *(item.component_id for item in state.components),
                            *(item.net_id for item in state.nets),
                            *(item.segment_id for item in state.routing),
                            *(item.route_id for item in state.route_paths),
                            *(item.via_id for item in state.vias),
                        )
                    )
                ) or ("pcb-power-loop",)
                next_power_loop = mark_power_loop_stale(
                    next_power_loop,
                    reason="G7-A PCB state, constraint set, or verification plan changed",
                    affected_objects=power_affected,
                )
        next_differential_pair = detail.pcb_differential_pair_verification
        if next_differential_pair is not None and not next_differential_pair.stale:
            differential_inputs = {state.fingerprint, constraint_set.fingerprint, plan.fingerprint}
            dependency = detail.pcb_return_path_verification
            current_dependency_fingerprints = (
                () if dependency is None else (dependency.fingerprint,)
            )
            differential_inputs.update(current_dependency_fingerprints)
            previous_dependency_fingerprints = set(detail.pcb_differential_pair_dependency_fingerprints)
            dependency_changed = previous_dependency_fingerprints != set(current_dependency_fingerprints)
            if dependency_changed or not differential_inputs.issubset(set(next_differential_pair.compared_fingerprints)):
                differential_affected = tuple(
                    dict.fromkeys(
                        (
                            *(item.pair_id for item in state.differential_pairs),
                            *(item.positive_net for item in state.differential_pairs),
                            *(item.negative_net for item in state.differential_pairs),
                            *(item.interface_id for item in state.differential_pairs if item.interface_id is not None),
                            *(item.segment_id for item in state.routing),
                            *(item.route_id for item in state.route_paths),
                            *(item.via_id for item in state.vias),
                            *(item.constraint_id for item in constraint_set.constraints if item.constraint_type.value == "DIFFERENTIAL_PAIR"),
                            *(item.fingerprint for item in (dependency,) if item is not None),
                        )
                    )
                ) or ("pcb-differential-pair",)
                next_differential_pair = mark_differential_pair_stale(
                    next_differential_pair,
                    reason="G7-A PCB state, constraint set, verification plan, or ReturnPath dependency changed",
                    affected_objects=differential_affected,
                )
        plan_changed = (
            detail.pcb_verification_plan is None
            or detail.pcb_verification_plan.fingerprint != plan.fingerprint
        )
        updated = detail.model_copy(
            update={
                "pcb_engineering_state": state,
                "pcb_constraint_set": constraint_set,
                "pcb_verification_plan": plan,
                "pcb_decoupling_verification": next_decoupling,
                "pcb_return_path_verification": next_return_path,
                "pcb_power_loop_verification": next_power_loop,
                "pcb_differential_pair_verification": next_differential_pair,
                # A closure summary and Ready-for-Fab gate are bound to the
                # exact plan fingerprint.  Clear them when deterministic
                # plan adjudication changes that fingerprint; Final Closure
                # will rebuild them after the required verifiers are rerun.
                "pcb_engineering_verification_summary": (
                    None if plan_changed else detail.pcb_engineering_verification_summary
                ),
                "pcb_ready_for_fab": None if plan_changed else detail.pcb_ready_for_fab,
                "pcb_ready_for_fab_approval": (
                    None if plan_changed else detail.pcb_ready_for_fab_approval
                ),
                "pcb_manufacturing_measurements": (
                    None if plan_changed else detail.pcb_manufacturing_measurements
                ),
                "updated_at": receipt.created_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(
            updated.model_dump(mode="python"),
            strict=False,
        )
        self._repository.save_conversation(checked)
        return checked

    def verify_pcb_decoupling(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        """Run the deterministic verifier over persisted G7-A state only."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Decoupling verification requires current G7-A state")
        detailed = DecouplingVerifier().verify_detailed(state, constraint_set, plan)
        updated = detail.model_copy(
            update={
                "pcb_decoupling_verification": detailed.result,
                "pcb_decoupling_measurements": detailed.measurements,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def evaluate_pcb_decoupling_prevent(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        candidate: PCBPlacementCandidate,
    ):
        """Evaluate a candidate placement without mutating the PCB or catalog."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Decoupling Prevent requires current G7-A state")
        checked_candidate = PCBPlacementCandidate.model_validate(candidate.model_dump(mode="python"), strict=False)
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise PCBProductionRejected("Decoupling Prevent candidate is stale")
        return DecouplingPreventEvaluator().evaluate(state, constraint_set, checked_candidate, plan)

    def verify_pcb_return_path(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        """Run ReturnPathVerifier over persisted G7-A state only."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Return-path verification requires current G7-A state")
        detailed = ReturnPathVerifier().verify_detailed(state, constraint_set, plan)
        updated = detail.model_copy(
            update={
                "pcb_return_path_verification": detailed.result,
                "pcb_return_path_measurements": detailed.measurements,
                "pcb_return_path_relationships": detailed.relationships,
                "pcb_return_path_discontinuities": detailed.discontinuities,
                "pcb_return_path_transitions": detailed.transitions,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def evaluate_pcb_return_path_prevent(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        candidate: PCBRouteCandidate,
    ):
        """Evaluate a candidate route without mutating PCB artifacts or catalog state."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Return-path Prevent requires current G7-A state")
        checked_candidate = PCBRouteCandidate.model_validate(candidate.model_dump(mode="python"), strict=False)
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise PCBProductionRejected("Return-path Prevent candidate is stale")
        return ReturnPathPreventEvaluator().evaluate(state, constraint_set, checked_candidate, plan)

    def verify_pcb_power_loop(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        """Run PowerLoopVerifier over persisted G7-A state only."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Power-loop verification requires current G7-A state")
        detailed = PowerLoopVerifier().verify_detailed(state, constraint_set, plan)
        updated = detail.model_copy(
            update={
                "pcb_power_loop_verification": detailed.result,
                "pcb_power_stage_relationships": detailed.power_stages,
                "pcb_power_loop_relationships": detailed.loops,
                "pcb_power_loop_measurements": detailed.measurements,
                "pcb_power_switch_nodes": detailed.switch_nodes,
                "pcb_power_feedback_relationships": detailed.feedback,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def evaluate_pcb_power_loop_prevent(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        candidate: PowerLayoutCandidate,
    ):
        """Evaluate a candidate power layout without mutating PCB artifacts or catalog state."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Power-loop Prevent requires current G7-A state")
        checked_candidate = PowerLayoutCandidate.model_validate(candidate.model_dump(mode="python"), strict=False)
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise PCBProductionRejected("Power-loop Prevent candidate is stale")
        return PowerLoopPreventEvaluator().evaluate(state, constraint_set, checked_candidate, plan)

    def verify_pcb_differential_pair(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        """Run DifferentialPairVerifier over persisted G7-A state only."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Differential-pair verification requires current G7-A state")
        detailed = DifferentialPairVerifier().verify_detailed(
            state,
            constraint_set,
            plan,
            return_path_result=detail.pcb_return_path_verification,
        )
        updated = detail.model_copy(
            update={
                "pcb_differential_pair_verification": detailed.result,
                "pcb_differential_pair_relationships": detailed.relationships,
                "pcb_differential_pair_measurements": detailed.measurements,
                "pcb_differential_pair_dependency_fingerprints": detailed.dependency_fingerprints,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def evaluate_pcb_differential_pair_prevent(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        candidate: DifferentialRouteCandidate,
    ):
        """Evaluate a differential-route candidate without mutating PCB or catalog state."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("Differential-pair Prevent requires current G7-A state")
        checked_candidate = DifferentialRouteCandidate.model_validate(candidate.model_dump(mode="python"), strict=False)
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise PCBProductionRejected("Differential-pair Prevent candidate is stale")
        return DifferentialPairPreventEvaluator().evaluate(
            state,
            constraint_set,
            checked_candidate,
            plan,
            return_path_result=detail.pcb_return_path_verification,
        )

    def verify_pcb_mapping(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        """Verify existing G4/G5 mapping authority against the current G7-A projection."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        request = detail.pcb_production_request
        if state is None or request is None:
            raise PCBProductionRejected("Footprint/symbol/pin verification requires current G5 and G7-A state")
        hardware_selection = detail.hardware_selection
        hardware_truth = None if hardware_selection is None else hardware_selection.hardware_truth
        planning = detail.schematic_planning
        approved_plan = None if planning is None else planning.approved_plan
        try:
            input_value = build_mapping_input_from_existing(
                project_id=project_id,
                state=state,
                production_request=request,
                design_truth=detail.design_truth,
                hardware_truth=hardware_truth,
                approved_plan=approved_plan,
            )
            detailed = FootprintSymbolPinConsistencyVerifier().verify(input_value)
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            raise PCBProductionRejected("Footprint/symbol/pin mapping verification failed closed") from error
        updated = detail.model_copy(
            update={
                "pcb_mapping_verification": detailed.result,
                "pcb_component_mapping_relationships": detailed.component_relationships,
                "pcb_pin_mapping_relationships": detailed.pin_relationships,
                "pcb_pad_mapping_relationships": detailed.pad_relationships,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def close_pcb_engineering_verification(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        engineering_evidence: tuple[EngineeringEvidenceRequirement, ...] = (),
        applicability: tuple[EngineeringApplicabilityDecision, ...] = (),
        checks: tuple[EngineeringCheck, ...] = (),
        manufacturer_profile: ManufacturerProfile | None = None,
        manufacturer_measurements: PCBManufacturingMeasurements | None = None,
        dft_requirements: tuple[DFTRequirement, ...] = (),
        approval: HumanApprovalBinding | None = None,
        erc_status: str = "UNKNOWN",
        drc_status: str = "UNKNOWN",
        hw_fw_consistency_status: str = "UNKNOWN",
        reverified_verifiers: tuple[str, ...] = (),
        canonical_executed: bool = False,
    ) -> ConversationDetail:
        """Run G7 Final Closure over persisted G7-A/B state and save only projections."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        if approval is not None:
            if approval.project_id is not None and approval.project_id != project_id:
                raise PCBProductionRejected("human approval project identity mismatch")
            if approval.conversation_id is not None and approval.conversation_id != conversation_id:
                raise PCBProductionRejected("human approval conversation identity mismatch")
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        if state is None or constraint_set is None or plan is None:
            raise PCBProductionRejected("G7 Final Closure requires current G7-A state")
        if state.status.value == "STALE" or constraint_set.status.value == "STALE" or plan.status.value == "STALE":
            raise PCBProductionRejected("G7 Final Closure requires current, non-stale G7-A inputs")

        effective_profile = manufacturer_profile or detail.pcb_manufacturer_profile
        effective_evidence = tuple(engineering_evidence)
        effective_applicability = tuple(applicability)
        effective_checks = tuple(checks)
        effective_measurements = manufacturer_measurements or detail.pcb_manufacturing_measurements
        if effective_measurements is not None:
            if effective_profile is None:
                raise PCBProductionRejected("JLCPCB DFM measurements require a persisted manufacturer profile")
            if (
                effective_measurements.project_id != state.project_id
                or effective_measurements.artifact_fingerprint != state.source_artifact_fingerprint
            ):
                raise PCBProductionRejected("JLCPCB DFM measurements are stale for the current PCB artifact")
            actual_layer_count = len(state.board.copper_layers)
            if (
                effective_measurements.layer_count is not None
                and actual_layer_count > 0
                and effective_measurements.layer_count != actual_layer_count
            ):
                raise PCBProductionRejected("JLCPCB DFM layer measurement does not match the current PCB state")
            receipt = None if detail.pcb_production is None else detail.pcb_production.generation_receipt
            if receipt is not None:
                try:
                    root = self.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
                    normalized, target = self._relative(receipt.artifact_path, root=root)
                    if normalized != receipt.artifact_path or target.is_symlink() or not target.is_file():
                        raise ValueError("current PCB artifact is unavailable")
                    measured = PCBManufacturingMeasurements.from_artifact(state, target)
                except (OSError, TypeError, ValueError, RuntimeError) as error:
                    raise PCBProductionRejected("current PCB copper-to-edge measurement failed") from error
                if (
                    effective_measurements.min_copper_to_edge_mm is not None
                    and effective_measurements.min_copper_to_edge_mm != measured.min_copper_to_edge_mm
                ):
                    raise PCBProductionRejected("supplied copper-to-edge measurement does not match the current PCB artifact")
                measured_values = measured.model_dump(mode="python", exclude={"fingerprint"})
                measured_values["outer_copper_weight"] = effective_measurements.outer_copper_weight
                effective_measurements = PCBManufacturingMeasurements.create(**measured_values)
            try:
                generated_evidence, generated_applicability, generated_checks = build_jlcpcb_dfm_inputs(
                    effective_profile,
                    effective_measurements,
                )
            except (TypeError, ValueError) as error:
                raise PCBProductionRejected("JLCPCB DFM input projection was rejected") from error
            # This entry point is the canonical manufacturer/DFM update. Keep
            # the change set DFM-only so unrelated engineering applicability
            # cannot force a mechanical B1-B5 rerun.
            effective_evidence = generated_evidence
            effective_applicability = (generated_applicability,)
            effective_checks = generated_checks

        previous_results = tuple(
            item
            for item in (
                detail.pcb_decoupling_verification,
                detail.pcb_return_path_verification,
                detail.pcb_power_loop_verification,
                detail.pcb_differential_pair_verification,
                detail.pcb_mapping_verification,
            )
            if item is not None
        )
        closure_service = PCBEngineeringVerificationService()
        candidate_plan = closure_service.extend_plan(
            plan,
            applicability=effective_applicability,
            evidence=effective_evidence,
        )
        # An engineering-only extension changes the shared plan fingerprint,
        # but it is not a dependency of existing G7-B physics results.  Only
        # a change to the PCB/constraint/physics plan lineage clears and
        # regenerates the verifiers that already had a result.
        new_engineering_verifiers = _pcb_engineering_verifiers(candidate_plan).difference(
            _pcb_engineering_verifiers(plan)
        )
        physics_plan_changed = (
            _pcb_physics_plan_values(candidate_plan) != _pcb_physics_plan_values(plan)
            or not new_engineering_verifiers <= {"DFM"}
        )
        if candidate_plan.fingerprint != plan.fingerprint and not physics_plan_changed:
            detail = _rebind_pcb_physics_results(detail, candidate_plan.fingerprint)
        if candidate_plan.fingerprint != plan.fingerprint and physics_plan_changed and previous_results:
            cleared = detail.model_copy(
                update={
                    "pcb_verification_plan": candidate_plan,
                    "pcb_decoupling_verification": None,
                    "pcb_return_path_verification": None,
                    "pcb_power_loop_verification": None,
                    "pcb_differential_pair_verification": None,
                    "pcb_mapping_verification": None,
                    "pcb_engineering_verification_summary": None,
                    "pcb_ready_for_fab": None,
                    "pcb_engineering_evidence": tuple(effective_evidence),
                    "updated_at": self._now(),
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked_cleared = ConversationDetail.model_validate(
                cleared.model_dump(mode="python"), strict=False
            )
            self._repository.save_conversation(checked_cleared)
            existing_verifiers = {
                item.verifier
                for item in previous_results
            }
            if "DECOUPLING" in existing_verifiers or "DecouplingVerifier" in existing_verifiers:
                detail = self.verify_pcb_decoupling(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
            if "RETURN_PATH" in existing_verifiers or "ReturnPathVerifier" in existing_verifiers:
                detail = self.verify_pcb_return_path(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
            if "POWER_LOOP" in existing_verifiers or "PowerLoopVerifier" in existing_verifiers:
                detail = self.verify_pcb_power_loop(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
            if "DIFFERENTIAL_PAIR" in existing_verifiers or "DifferentialPairVerifier" in existing_verifiers:
                detail = self.verify_pcb_differential_pair(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
            if "FOOTPRINT_SYMBOL_PIN_CONSISTENCY" in existing_verifiers or "FootprintPinConsistencyVerifier" in existing_verifiers:
                detail = self.verify_pcb_mapping(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
            detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        requested = {item.casefold() for item in reverified_verifiers}
        if requested & {"decouplingverifier", "decoupling", "g7-b1"}:
            detail = self.verify_pcb_decoupling(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if requested & {"returnpathverifier", "return_path", "return path", "g7-b2"}:
            detail = self.verify_pcb_return_path(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if requested & {"powerloopverifier", "power_loop", "g7-b3"}:
            detail = self.verify_pcb_power_loop(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if requested & {"differentialpairverifier", "differential_pair", "g7-b4"}:
            detail = self.verify_pcb_differential_pair(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if requested & {"footprintpinconsistencyverifier", "mapping", "g7-b5"}:
            detail = self.verify_pcb_mapping(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if requested:
            detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        state = detail.pcb_engineering_state
        constraint_set = detail.pcb_constraint_set
        plan = detail.pcb_verification_plan
        assert state is not None and constraint_set is not None and plan is not None
        existing_results = tuple(
            item
            for item in (
                detail.pcb_decoupling_verification,
                detail.pcb_return_path_verification,
                detail.pcb_power_loop_verification,
                detail.pcb_differential_pair_verification,
                detail.pcb_mapping_verification,
            )
            if item is not None
        )
        closure = closure_service.close(
            state,
            constraint_set,
            plan,
            existing_results=existing_results,
            previous_results=previous_results,
            evidence=effective_evidence,
            applicability=effective_applicability,
            checks=effective_checks,
            manufacturer_profile=effective_profile,
            dft_requirements=dft_requirements,
            approval=approval,
            artifact_fingerprint=state.source_artifact_fingerprint,
            erc_status=erc_status,
            drc_status=drc_status,
            hw_fw_consistency_status=hw_fw_consistency_status,
            reverified_verifiers=reverified_verifiers,
            canonical_executed=canonical_executed,
        )
        updated = detail.model_copy(
            update={
                "pcb_verification_plan": closure.verification_plan,
                "pcb_engineering_evidence": tuple(effective_evidence),
                "pcb_manufacturer_profile": effective_profile,
                "pcb_manufacturing_measurements": effective_measurements,
                "pcb_engineering_verification_summary": closure.summary,
                "pcb_ready_for_fab": closure.ready_for_fab,
                "pcb_ready_for_fab_approval": closure.approval,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def evaluate_pcb_mapping_prevent(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        candidate: EDAMappingCandidate,
    ):
        """Evaluate a candidate mapping read-only against the same approved authority."""

        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        state = detail.pcb_engineering_state
        request = detail.pcb_production_request
        if state is None or request is None:
            raise PCBProductionRejected("Footprint/symbol/pin Prevent requires current G5 and G7-A state")
        hardware_selection = detail.hardware_selection
        hardware_truth = None if hardware_selection is None else hardware_selection.hardware_truth
        planning = detail.schematic_planning
        approved_plan = None if planning is None else planning.approved_plan
        try:
            input_value = build_mapping_input_from_existing(
                project_id=project_id,
                state=state,
                production_request=request,
                design_truth=detail.design_truth,
                hardware_truth=hardware_truth,
                approved_plan=approved_plan,
            )
            checked_candidate = EDAMappingCandidate.model_validate(candidate.model_dump(mode="python"), strict=False)
            return MappingPreventEvaluator().evaluate(input_value, checked_candidate)
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            raise PCBProductionRejected("Footprint/symbol/pin Prevent failed closed") from error

    def delete_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> ConversationDetail:
        """Remove an owned conversation from persistence without touching files."""
        # Resolve first so a cross-project or cross-requester id returns the
        # same stable ownership errors as the other conversation operations.
        self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        deleted = self._repository.delete_conversation(
            conversation_id,
            project_id=project_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        if deleted is None:
            raise ProjectCatalogNotFound("conversation was not found")
        return deleted

    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor: str | None = None, limit: int = 20) -> ConversationPage:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        values = self._repository.conversations(project_id, tenant_id, requester)
        try:
            offset = 0 if cursor is None else int(cursor)
        except ValueError as error:
            raise ProjectCatalogRejected("conversation cursor is invalid") from error
        if offset < 0:
            raise ProjectCatalogRejected("conversation cursor is invalid")
        selected = values[offset: offset + limit]
        items = tuple(ConversationSummary(
            conversation_id=item.conversation_id, project_id=item.project_id, title=item.title,
            turn_count=len(item.turns), created_at=item.created_at, updated_at=item.updated_at,
        ) for item in selected)
        next_cursor = str(offset + limit) if offset + limit < len(values) else None
        return ConversationPage(items=items, next_cursor=next_cursor)

    def conversation_for_session(self, session_id: str) -> ConversationDetail | None:
        return self._repository.conversation_for_session(session_id)

    def scan_artifact_intelligence(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        zip_content: bytes | None = None,
        source_operation_id: str | None = None,
        source_proposal_fingerprint: str | None = None,
    ) -> ConversationDetail:
        """Run deterministic read-only extraction and persist its snapshot.

        ZIP input remains in memory. The repository receives only typed
        inventory/evidence metadata and never the uploaded bytes.
        """

        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        try:
            if zip_content is not None:
                if type(zip_content) is not bytes or not zip_content:
                    raise ArtifactIntelligenceRejected("artifact ZIP is invalid")
                snapshot = self._artifact_intelligence.scan_zip(
                    zip_content,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    source_operation_id=source_operation_id,
                    source_proposal_fingerprint=source_proposal_fingerprint,
                )
            else:
                snapshot = self._artifact_intelligence.scan_directory(
                    root=self.knowledge_snapshot_root(project_id, tenant_id=tenant_id),
                    tenant_id=tenant_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    source_operation_id=source_operation_id,
                    source_proposal_fingerprint=source_proposal_fingerprint,
                )
        except ArtifactIntelligenceRejected:
            raise
        except (OSError, ValueError) as error:
            raise ArtifactIntelligenceRejected("artifact intelligence input was rejected") from error

        if detail.design_truth is not None:
            previous_bundle = detail.engineering_evidence_bundle
            next_bundle = snapshot.engineering_evidence_bundle
            if previous_bundle is None or next_bundle is None or (
                previous_bundle.bundle_fingerprint != next_bundle.bundle_fingerprint
            ):
                raise ProjectCatalogConflict("artifact evidence conflicts with existing design truth")
        previous_snapshot = detail.artifact_intelligence_snapshot
        if previous_snapshot is not None:
            previous_bundle = previous_snapshot.engineering_evidence_bundle
            next_bundle = snapshot.engineering_evidence_bundle
            same_bundle = (
                previous_bundle is None
                and next_bundle is None
                or previous_bundle is not None
                and next_bundle is not None
                and previous_bundle.bundle_fingerprint == next_bundle.bundle_fingerprint
            )
            if previous_snapshot.inventory.fingerprint == snapshot.inventory.fingerprint and same_bundle:
                # Re-submitting the same artifact content must not reset a
                # reviewing/approved evidence lifecycle or its review link.
                return detail
        updated = detail.model_copy(
            update={
                "artifact_intelligence_snapshot": snapshot,
                "engineering_evidence_bundle": snapshot.engineering_evidence_bundle,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def decide_requirement_suggestion(
        self,
        project_id: str,
        conversation_id: str,
        suggestion_id: str,
        action: RequirementDecisionAction,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        modified_requirement: str | None = None,
    ) -> ConversationDetail:
        """Persist one explicit G0R decision through the existing conversation store."""

        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        snapshot = detail.research_snapshot
        if snapshot is None:
            raise RequirementSuggestionDecisionRejected(
                "research suggestion snapshot is unavailable"
            )
        if not isinstance(action, RequirementDecisionAction) or action in {
            RequirementDecisionAction.PENDING,
            RequirementDecisionAction.SUPPRESSED,
        }:
            raise RequirementSuggestionDecisionRejected(
                "terminal requirement suggestion decision is required"
            )
        try:
            decision = RequirementSuggestionDecision(
                suggestion_id=suggestion_id,
                project_id=project_id,
                conversation_id=conversation_id,
                action=action,
                decided_by=requester,
                decided_at=decided_at,
                modified_requirement=modified_requirement,
            )
            next_snapshot = EngineeringResearchService.decide_suggestion(
                snapshot, decision
            )
            next_requirement = detail.requirement_context
            if action in {
                RequirementDecisionAction.ACCEPT,
                RequirementDecisionAction.MODIFY,
            }:
                if next_requirement is None:
                    raise ValueError("requirement context is unavailable")
                suggestion = next(
                    item
                    for item in snapshot.suggestions
                    if item.suggestion_id == suggestion_id
                )
                next_requirement = apply_requirement_suggestion_decision(
                    next_requirement,
                    suggestion,
                    action=action,
                    decided_by=requester,
                    modified_requirement=modified_requirement,
                )
            existing = next(
                (
                    item
                    for item in detail.requirement_suggestion_decisions
                    if item.suggestion_id == suggestion_id
                ),
                None,
            )
            if existing is not None:
                if existing.fingerprint != decision.fingerprint:
                    raise ValueError("requirement suggestion decision is immutable")
                return detail
            decisions = (*detail.requirement_suggestion_decisions, decision)
            updated = detail.model_copy(
                update={
                    "requirement_context": next_requirement,
                    "research_snapshot": next_snapshot,
                    "requirement_suggestion_decisions": decisions,
                    "updated_at": decided_at,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(
                updated.model_dump(mode="python")
            )
        except (TypeError, ValueError, StopIteration) as error:
            raise RequirementSuggestionDecisionRejected(
                "requirement suggestion decision rejected"
            ) from error
        self._repository.save_conversation(checked)
        return checked

    def decide_reference_project(
        self,
        project_id: str,
        conversation_id: str,
        project_identity: str,
        decision: ReferenceProjectDecisionAction,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        selected_features: tuple[str, ...] = (),
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        snapshot = detail.research_snapshot
        if snapshot is None:
            raise RequirementChangeRejected("research snapshot is unavailable")
        try:
            next_snapshot = EngineeringResearchService.decide_reference_project(
                snapshot,
                project_identity=project_identity,
                decision=decision,
                decided_by=requester,
                decided_at=decided_at,
                selected_features=selected_features,
            )
            project = next(item for item in snapshot.evidence_pack.projects if item.project_identity == project_identity)
            event = ReferenceProjectDecision(
                decision_id=f"reference-decision-{uuid4().hex}",
                project_id=project_id,
                conversation_id=conversation_id,
                project_identity=project_identity,
                research_fingerprint=snapshot.evidence_pack.fingerprint,
                reference_fingerprint=project.fingerprint,
                decision=decision,
                decided_by=requester,
                decided_at=decided_at,
                selected_features=selected_features,
            )
            updated = detail.model_copy(
                update={
                    "research_snapshot": next_snapshot,
                    "reference_project_decisions": (*detail.reference_project_decisions, event),
                    "updated_at": decided_at,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        except (TypeError, ValueError, StopIteration) as error:
            raise RequirementChangeRejected("reference project decision rejected") from error
        self._repository.save_conversation(checked)
        return checked

    def decide_feature(
        self,
        project_id: str,
        conversation_id: str,
        project_identity: str,
        feature: str,
        action: FeatureDecisionAction,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        value: str | None = None,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        snapshot = detail.research_snapshot
        if snapshot is None:
            raise RequirementChangeRejected("research snapshot is unavailable")
        try:
            project = next(item for item in snapshot.evidence_pack.projects if item.project_identity == project_identity)
            event = FeatureDecision(
                decision_id=f"feature-decision-{uuid4().hex}",
                project_id=project_id,
                conversation_id=conversation_id,
                project_identity=project_identity,
                feature=feature,
                research_fingerprint=snapshot.evidence_pack.fingerprint,
                reference_fingerprint=project.fingerprint,
                action=action,
                value=value,
                decided_by=requester,
                decided_at=decided_at,
            )
            next_snapshot = EngineeringResearchService.decide_feature(snapshot, event)
            next_proposal = None
            if detail.requirement_context is not None:
                next_proposal = self._research_service.proposal_from_feature_decision(
                    detail.requirement_context,
                    next_snapshot,
                    event,
                )
            updated = detail.model_copy(
                update={
                    "research_snapshot": next_snapshot,
                    "feature_decisions": (*detail.feature_decisions, event),
                    "requirement_change_proposal": next_proposal or detail.requirement_change_proposal,
                    "requirement_final_confirmation": None if next_proposal is not None else detail.requirement_final_confirmation,
                    "updated_at": decided_at,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        except (TypeError, ValueError, StopIteration) as error:
            raise RequirementChangeRejected("feature decision rejected") from error
        self._repository.save_conversation(checked)
        return checked

    def propose_requirement_changes(
        self,
        project_id: str,
        conversation_id: str,
        utterance: str,
        *,
        tenant_id: str,
        requester: str,
        created_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if detail.requirement_context is None:
            raise RequirementChangeRejected("requirement context is unavailable")
        try:
            proposal = self._research_service.interpret_requirement_feedback(
                detail.requirement_context,
                utterance,
                research_snapshot=detail.research_snapshot,
                decided_by=requester,
                created_at=created_at,
            )
            updated = detail.model_copy(
                update={
                    "requirement_change_proposal": proposal,
                    "requirement_final_confirmation": None,
                    "updated_at": created_at,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise RequirementChangeRejected("requirement change proposal rejected") from error
        self._repository.save_conversation(checked)
        return checked

    def confirm_requirement_changes(
        self,
        project_id: str,
        conversation_id: str,
        proposal_id: str,
        *,
        tenant_id: str,
        requester: str,
        confirmed: bool,
        proposal_fingerprint: str,
        confirmed_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        proposal = detail.requirement_change_proposal
        context = detail.requirement_context
        if proposal is None or context is None or proposal.proposal_id != proposal_id or proposal.fingerprint != proposal_fingerprint:
            raise RequirementChangeRejected("requirement change proposal is unavailable or stale")
        confirmation = RequirementFinalConfirmation(
            confirmation_id=f"requirement-confirmation-{uuid4().hex}",
            proposal_id=proposal.proposal_id,
            project_id=project_id,
            conversation_id=conversation_id,
            proposal_fingerprint=proposal.fingerprint,
            confirmed=confirmed,
            confirmed_by=requester,
            confirmed_at=confirmed_at,
        )
        if not confirmed:
            self._repository.save_conversation(detail)
            return detail
        try:
            next_context = self._research_service.commit_requirement_change(context, proposal, confirmation)
            next_proposal = RequirementChangeProposal.model_validate(
                proposal.model_copy(update={"status": RequirementChangeStatus.CONFIRMED, "fingerprint": "sha256:" + "0" * 64}).model_dump(mode="python"),
                strict=False,
            )
            stored_confirmation = RequirementFinalConfirmation.model_validate(
                confirmation.model_copy(update={"proposal_fingerprint": next_proposal.fingerprint, "fingerprint": "sha256:" + "0" * 64}).model_dump(mode="python"),
                strict=False,
            )
            updated = detail.model_copy(
                update={
                    "requirement_context": next_context,
                    "requirement_change_proposal": next_proposal,
                    "requirement_final_confirmation": stored_confirmation,
                    "updated_at": confirmed_at,
                    "fingerprint": "sha256:" + "0" * 64,
                }
            )
            checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            stale = RequirementChangeProposal.model_validate(
                proposal.model_copy(update={"status": RequirementChangeStatus.STALE, "fingerprint": "sha256:" + "0" * 64}).model_dump(mode="python"),
                strict=False,
            )
            stale_detail = ConversationDetail.model_validate(
                detail.model_copy(update={"requirement_change_proposal": stale, "updated_at": confirmed_at, "fingerprint": "sha256:" + "0" * 64}).model_dump(mode="python")
            )
            self._repository.save_conversation(stale_detail)
            raise RequirementChangeRejected("requirement change confirmation rejected") from error
        self._repository.save_conversation(checked)
        return checked

    def refine_research_intent(
        self,
        project_id: str,
        conversation_id: str,
        product_positioning: str,
        negative_constraints: tuple[str, ...],
        *,
        tenant_id: str,
        requester: str,
        created_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        try:
            refinement = ResearchIntentRefinement(
                refinement_id=f"research-refinement-{uuid4().hex}",
                project_id=project_id,
                conversation_id=conversation_id,
                product_positioning=product_positioning,
                negative_constraints=negative_constraints,
                created_by=requester,
                created_at=created_at,
            )
            updated = detail.model_copy(update={"research_intent_refinement": refinement, "updated_at": created_at, "fingerprint": "sha256:" + "0" * 64})
            checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise RequirementChangeRejected("research intent refinement rejected") from error
        self._repository.save_conversation(checked)
        return checked

    def decide_execution_authorization(
        self,
        project_id: str,
        conversation_id: str,
        request_id: str,
        decision: AuthorizationDecisionStatus,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        if (
            detail.capability_evaluation is None
            or not detail.execution_intents
            or detail.execution_authorization is None
        ):
            raise ExecutionAuthorizationDecisionRejected("execution authorization is unavailable")
        try:
            authorization = decide_execution_authorization_request(
                detail.capability_evaluation,
                detail.execution_intents,
                detail.execution_authorization,
                request_id=request_id,
                decision=decision,
                reviewer=requester,
                decided_at=decided_at,
            )
        except (TypeError, ValueError) as error:
            raise ExecutionAuthorizationDecisionRejected("execution authorization decision rejected") from error
        generation = detail.firmware_source_generation
        if generation is not None and (
            generation.request.authorization_id != authorization.authorization_id
            or generation.request.authorization_fingerprint != authorization.fingerprint
        ):
            generation = None
        updated = detail.model_copy(
            update={
                "execution_authorization": authorization,
                "firmware_source_generation": generation,
                "updated_at": decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def decide_firmware_source_generation(
        self,
        project_id: str,
        conversation_id: str,
        decision: FirmwareSourceProposalDecisionStatus,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        bridge: object | None,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        generation = detail.firmware_source_generation
        if generation is None or generation.status is not FirmwareSourceGenerationStatus.AWAITING_APPROVAL:
            raise FirmwareSourceGenerationRejected("firmware source proposal is unavailable")
        try:
            proposal_decision = decide_firmware_source_proposal(
                generation.proposal,
                decision=decision,
                reviewer=requester,
                decided_at=decided_at,
            )
        except (TypeError, ValueError) as error:
            raise FirmwareSourceGenerationRejected("firmware source proposal decision rejected") from error
        if proposal_decision.decision is FirmwareSourceProposalDecisionStatus.REJECTED:
            next_generation = FirmwareSourceGenerationState.create(
                generation_id=generation.generation_id,
                project_id=generation.project_id,
                conversation_id=generation.conversation_id,
                request=generation.request,
                proposal=generation.proposal,
                decision=proposal_decision,
                operation_id=None,
                operation_fingerprint=None,
                status=FirmwareSourceGenerationStatus.REJECTED,
            )
        else:
            enqueue = getattr(bridge, "approve_and_enqueue", None)
            if not callable(enqueue):
                raise FirmwareSourceGenerationRejected("firmware source execution bridge is unavailable")
            try:
                operation_request = enqueue(
                    generation_request=generation.request,
                    proposal=generation.proposal,
                    decision=proposal_decision,
                )
            except (TypeError, ValueError, OSError, RuntimeError) as error:
                raise FirmwareSourceGenerationRejected("firmware source operation rejected") from error
            operation = operation_request.operation
            next_generation = FirmwareSourceGenerationState.create(
                generation_id=generation.generation_id,
                project_id=generation.project_id,
                conversation_id=generation.conversation_id,
                request=generation.request,
                proposal=generation.proposal,
                decision=proposal_decision,
                operation_id=operation.operation_id,
                operation_fingerprint=operation.fingerprint,
                status=FirmwareSourceGenerationStatus.QUEUED,
            )
        updated = detail.model_copy(
            update={
                "firmware_source_generation": next_generation,
                "updated_at": decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def decide_schematic_production(
        self,
        project_id: str,
        conversation_id: str,
        decision: SchematicProposalDecisionStatus,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        bridge: object | None,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        planning = detail.schematic_planning
        self.require_current_circuit_binding(
            project_id, None if planning is None else (planning.approved_plan or planning.proposal),
            tenant_id=tenant_id, requester=requester,
        )
        production = detail.schematic_production
        if production is None or production.status is not SchematicProductionStatus.AWAITING_APPROVAL:
            raise SchematicProductionRejected("schematic proposal is unavailable")
        publish = None
        try:
            proposal_decision = decide_schematic_proposal(
                production.proposal,
                decision=decision,
                reviewer=requester,
                decided_at=decided_at,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic proposal decision rejected") from error
        if proposal_decision.decision is SchematicProposalDecisionStatus.REJECTED:
            next_state = SchematicProductionState.create(
                production_id=production.production_id,
                project_id=production.project_id,
                conversation_id=production.conversation_id,
                request=production.request,
                proposal=production.proposal,
                decision=proposal_decision,
                status=SchematicProductionStatus.REJECTED,
            )
        else:
            enqueue = getattr(bridge, "approve_and_enqueue", None)
            if not callable(enqueue):
                raise SchematicProductionRejected("schematic execution bridge is unavailable")
            prepare = getattr(bridge, "prepare_approved", None)
            prepared_publish = getattr(bridge, "enqueue_prepared", None)
            if callable(prepare) and callable(prepared_publish):
                enqueue = prepare
                publish = prepared_publish
            try:
                approved_plan = None if planning is None else planning.approved_plan
                selection = None
                if production.request.schematic_plan_id is not None:
                    selection = detail.hardware_selection
                    if approved_plan is None or selection is None:
                        raise ValueError("approved schematic property context is unavailable")
                    self._require_current_model_selection(selection, detail=detail)
                    if self._schematic_plan_source_currentness(
                        detail, approved_plan, project_id=project_id,
                        tenant_id=tenant_id, requester=requester,
                    ) != "CURRENT":
                        raise ValueError("approved schematic property context is stale")
                else:
                    approved_plan = None
                operation_request = enqueue(
                    production_request=production.request,
                    proposal=production.proposal,
                    decision=proposal_decision,
                    tenant_id=tenant_id,
                    approved_schematic_plan=approved_plan,
                    hardware_selection=selection,
                )
            except (ProjectCatalogError, TypeError, ValueError, OSError, RuntimeError) as error:
                raise SchematicProductionRejected("schematic operation rejected") from error
            operation = operation_request.operation
            next_state = SchematicProductionState.create(
                production_id=production.production_id,
                project_id=production.project_id,
                conversation_id=production.conversation_id,
                request=production.request,
                proposal=production.proposal,
                decision=proposal_decision,
                operation_id=operation.operation_id,
                operation_fingerprint=operation.fingerprint,
                status=SchematicProductionStatus.QUEUED,
            )
        updated = detail.model_copy(update={"schematic_production": next_state, "updated_at": decided_at, "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        private = any(item.symbol_binding == "SHTC3_EP:SHTC3_EP" for item in production.proposal.components) or (
            planning is not None and planning.approved_plan is not None and any(
                item.symbol == "SHTC3_EP:SHTC3_EP" for item in planning.approved_plan.component_instances))
        if private:
            self._repository.save_conversation_if_fingerprint(
                checked, expected_conversation_fingerprint=detail.fingerprint)
        else:
            self._repository.save_conversation(checked)
        if publish is not None:
            try:
                publish(operation_request=operation_request, decision=proposal_decision)
            except (ProjectCatalogError, TypeError, ValueError, OSError, RuntimeError) as error:
                # Keep the durable binding and immutable prepared operation for
                # explicit recovery; never erase it or silently replay a write.
                raise SchematicProductionRejected("schematic queue publication interrupted") from error
            return self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        return checked

    def record_firmware_build(
        self,
        *,
        operation: object,
        receipt: object,
    ) -> ConversationDetail:
        """Persist one typed firmware build receipt into the project catalog.

        This method is a projection boundary only.  The ExecutionWorker owns
        the workspace write and the compiler invocation; the catalog records
        their typed result and never re-runs or infers a build.
        """

        from embedded_copilot.execution_worker.contracts import (
            BuildOperation,
            OperationReceipt,
            OperationStatus,
            OperationType,
        )

        try:
            checked_operation = BuildOperation.model_validate(
                operation.model_dump(mode="python") if isinstance(operation, BuildOperation) else operation,
                strict=False,
            )
            checked_receipt = OperationReceipt.model_validate(
                receipt.model_dump(mode="python") if isinstance(receipt, OperationReceipt) else receipt,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise ProjectCatalogRejected("firmware build receipt is invalid") from error
        request = checked_operation.firmware_build_request
        evidence = checked_receipt.build_evidence
        build_result = checked_receipt.build_result
        if (
            checked_operation.operation_type is not OperationType.BUILD
            or checked_receipt.operation_type is not OperationType.BUILD
            or checked_receipt.operation_id != checked_operation.operation_id
            or checked_receipt.tenant_id != checked_operation.tenant_id
            or checked_receipt.project_id != checked_operation.project_id
            or checked_receipt.status not in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}
            or request is None
            or request.conversation_id is None
            or build_result is None
            or evidence is None
        ):
            raise ProjectCatalogRejected("firmware build receipt is incomplete")
        detail = self.conversation(
            checked_operation.project_id,
            request.conversation_id,
            tenant_id=checked_operation.tenant_id,
            requester=checked_operation.requester_id,
        )
        specification = detail.firmware_spec
        truth = detail.design_truth
        implementation = detail.implementation_spec
        build_configuration = detail.build_configuration
        if specification is None or truth is None or implementation is None or build_configuration is None:
            raise ProjectCatalogRejected("firmware build requires complete approved engineering context")
        if (
            specification.firmware_spec_id != request.firmware_spec_id
            or specification.fingerprint != request.firmware_spec_fingerprint
            or specification.project_id != detail.project_id
            or specification.conversation_id != detail.conversation_id
            or truth.truth_id != specification.design_truth_id
            or truth.fingerprint != specification.design_truth_fingerprint
            or implementation.implementation_id != request.implementation_id
            or implementation.fingerprint != request.implementation_fingerprint
            or implementation.design_truth_id != truth.truth_id
            or implementation.design_truth_fingerprint != truth.fingerprint
            or implementation.build_configuration_id is None
            or request.configuration is None
            or request.configuration != build_configuration
        ):
            raise ProjectCatalogRejected("firmware build receipt is stale or unbound")
        expected_binding_fingerprint = firmware_binding_fingerprint(specification)
        source_fingerprints = tuple(item.content_fingerprint for item in request.source_artifacts)
        if evidence.source_fingerprints and evidence.source_fingerprints != source_fingerprints:
            raise ProjectCatalogRejected("firmware build source evidence mismatch")
        artifact_ids = tuple(dict.fromkeys((*evidence.output_artifact_ids, *tuple(item.reference for item in build_result.outputs))))
        result_status = evidence.result_status
        receipt_id = build_result.receipt_reference
        try:
            build_log = self.knowledge_snapshot_root(
                checked_operation.project_id,
                tenant_id=checked_operation.tenant_id,
            ) / ".embedded-copilot-state" / "build" / checked_operation.operation_id / "build.log"
            if build_log.is_file() and not build_log.is_symlink():
                receipt_id = "build-receipt-" + hashlib.sha256(build_log.read_bytes()).hexdigest()[:32]
        except (OSError, RuntimeError):
            pass
        if not receipt_id:
            receipt_id = "build-receipt-" + checked_operation.operation_id
        log_artifact_id = evidence.log_artifact_id or build_result.log_artifact_reference
        projected_artifacts = [
            artifact_projection(item, build_receipt_id=receipt_id)
            for item in build_result.outputs
        ]
        if log_artifact_id and not any(item.artifact_id == log_artifact_id for item in projected_artifacts):
            try:
                build_log = self.knowledge_snapshot_root(
                    checked_operation.project_id,
                    tenant_id=checked_operation.tenant_id,
                ) / ".embedded-copilot-state" / "build" / checked_operation.operation_id / "build.log"
                if build_log.is_file() and not build_log.is_symlink():
                    content = build_log.read_bytes()
                    projected_artifacts.append(
                        FirmwareBuildArtifactProjection(
                            artifact_id=log_artifact_id,
                            artifact_type="LOG",
                            relative_path=".embedded-copilot-state/build/" + checked_operation.operation_id + "/build.log",
                            content_fingerprint="sha256:" + hashlib.sha256(content).hexdigest(),
                            size_bytes=len(content),
                            build_receipt_id=receipt_id,
                        )
                    )
            except (OSError, RuntimeError, ValueError):
                pass
        existing = detail.firmware_production
        if existing is not None and existing.build_receipt.operation_id == checked_operation.operation_id:
            if existing.build_receipt.operation_receipt_fingerprint == checked_receipt.fingerprint:
                return detail
            raise ProjectCatalogConflict("firmware build receipt identity conflict")
        now = checked_receipt.created_at
        build_receipt = FirmwareBuildReceiptProjection(
            receipt_id=receipt_id,
            project_id=detail.project_id,
            conversation_id=detail.conversation_id,
            operation_id=checked_operation.operation_id,
            operation_receipt_fingerprint=checked_receipt.fingerprint,
            request_fingerprint=request.fingerprint,
            firmware_spec_id=specification.firmware_spec_id,
            firmware_plan_fingerprint=specification.fingerprint,
            hw_fw_binding_fingerprint=expected_binding_fingerprint,
            design_truth_id=truth.truth_id,
            design_truth_fingerprint=truth.fingerprint,
            implementation_id=implementation.implementation_id,
            implementation_fingerprint=implementation.fingerprint,
            build_configuration_id=implementation.build_configuration_id,
            build_configuration_fingerprint=firmware_canonical_fingerprint(build_configuration),
            source_fingerprints=source_fingerprints,
            status=result_status,
            toolchain_id=evidence.toolchain_id,
            toolchain_version=evidence.toolchain_version,
            build_system=evidence.build_system or build_configuration.build_system,
            artifact_ids=artifact_ids,
            log_artifact_id=log_artifact_id,
            raw_evidence_artifact_id=evidence.raw_evidence_artifact_id or log_artifact_id,
            configure_exit_code=evidence.cmake_configure_exit_code,
            compiler_exit_code=evidence.compiler_exit_code,
            assembler_exit_code=evidence.assembler_exit_code,
            linker_exit_code=evidence.linker_exit_code,
            objcopy_exit_code=evidence.objcopy_exit_code,
            tool_observed=evidence.tool_observed,
            created_at=now,
        )
        state = FirmwareProductionState(
            production_id=firmware_production_id(detail.project_id, detail.conversation_id),
            project_id=detail.project_id,
            conversation_id=detail.conversation_id,
            firmware_spec_id=specification.firmware_spec_id,
            firmware_plan_fingerprint=specification.fingerprint,
            hw_fw_binding_fingerprint=expected_binding_fingerprint,
            design_truth_id=truth.truth_id,
            design_truth_fingerprint=truth.fingerprint,
            implementation_id=implementation.implementation_id,
            implementation_fingerprint=implementation.fingerprint,
            build_configuration_id=implementation.build_configuration_id,
            build_configuration_fingerprint=firmware_canonical_fingerprint(build_configuration),
            source_fingerprints=source_fingerprints,
            build_receipt=build_receipt,
            artifacts=tuple(projected_artifacts),
            status=(
                FirmwareProductionStatus.BUILD_PASS
                if checked_receipt.status is OperationStatus.SUCCEEDED and result_status == "BUILD_PASS"
                else FirmwareProductionStatus.BUILD_FAILED
            ),
            updated_at=now,
        )
        updated = detail.model_copy(
            update={
                "firmware_production": state,
                "updated_at": now,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def record_firmware_consistency(self, *, result: object) -> ConversationDetail:
        """Attach a separately computed HW/FW consistency result to the build state."""

        from embedded_copilot.engineering_consistency.contracts import (
            HardwareFirmwareConsistencyResult,
        )

        try:
            checked_result = HardwareFirmwareConsistencyResult.model_validate(
                result.model_dump(mode="python") if isinstance(result, HardwareFirmwareConsistencyResult) else result,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise ProjectCatalogRejected("firmware consistency result is invalid") from error
        if checked_result.conversation_id is None:
            raise ProjectCatalogRejected("firmware consistency result conversation binding is missing")
        stored_detail = self._repository.conversation(checked_result.conversation_id)
        if stored_detail is None:
            raise ProjectCatalogNotFound("conversation was not found")
        detail = self.conversation(
            checked_result.project_id,
            checked_result.conversation_id,
            tenant_id=stored_detail.tenant_id,
            requester=stored_detail.requester,
        )
        state = detail.firmware_production
        if state is None:
            raise ProjectCatalogRejected("firmware production build state is unavailable")
        receipt = state.build_receipt
        if (
            checked_result.firmware_specification_id != state.firmware_spec_id
            or checked_result.firmware_specification_fingerprint != state.firmware_plan_fingerprint
            or checked_result.design_truth_id != state.design_truth_id
            or checked_result.design_truth_fingerprint != state.design_truth_fingerprint
            or checked_result.implementation_specification_id != state.implementation_id
            or checked_result.implementation_fingerprint != state.implementation_fingerprint
            or checked_result.build_configuration_id != state.build_configuration_id
            or checked_result.build_configuration_fingerprint != state.build_configuration_fingerprint
            or checked_result.build_operation_id != receipt.operation_id
            or (checked_result.firmware_source_fingerprints and checked_result.firmware_source_fingerprints != state.source_fingerprints)
        ):
            raise ProjectCatalogRejected("firmware consistency result is stale or unbound")
        next_status = (
            FirmwareProductionStatus.STALE
            if checked_result.stale_inputs
            else FirmwareProductionStatus.FIRMWARE_GATE_PASS
            if (
                receipt.status == "BUILD_PASS"
                and checked_result.overall_status.value == "CONSISTENT"
                and checked_result.verification_level.value == "L3_ENGINEERING_VERIFIED"
            )
            else FirmwareProductionStatus.CONSISTENCY_FAILED
        )
        stale_inputs = tuple(dict.fromkeys((*state.stale_inputs, *checked_result.stale_inputs)))
        updated_state = state.model_copy(
            update={
                "consistency_result": checked_result,
                "status": next_status,
                "stale_inputs": stale_inputs,
                "updated_at": checked_result.computed_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        updated = detail.model_copy(
            update={
                "firmware_production": updated_state,
                "updated_at": checked_result.computed_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def mark_firmware_production_stale(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        reason: str,
        updated_at: datetime | None = None,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if detail.firmware_production is None:
            raise ProjectCatalogRejected("firmware production state is unavailable")
        now = updated_at or self._now()
        state = detail.firmware_production.mark_stale(reason=reason, updated_at=now)
        updated = detail.model_copy(
            update={"firmware_production": state, "updated_at": now, "fingerprint": "sha256:" + "0" * 64}
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"), strict=False)
        self._repository.save_conversation(checked)
        return checked

    def record_schematic_generation(
        self,
        *,
        operation: object,
        receipt: object,
        external_validation_status: str = "UNAVAILABLE",
    ) -> ConversationDetail:
        """Bind one successful worker receipt to the current project conversation.

        The worker remains the only file writer. This method only verifies the
        persisted receipt and the resulting file, then records a typed
        project-catalog projection for restart and stale-plan detection.
        """

        from embedded_copilot.execution_worker.contracts import (
            OperationReceipt,
            OperationStatus,
            OperationType,
            SchematicWriteOperation,
        )

        if external_validation_status not in {"PARSE_VERIFIED", "UNAVAILABLE"}:
            raise SchematicProductionRejected("schematic validation status is invalid")
        try:
            checked_operation = SchematicWriteOperation.model_validate(
                operation.model_dump(mode="python")
                if isinstance(operation, SchematicWriteOperation)
                else operation,
                strict=False,
            )
            checked_receipt = OperationReceipt.model_validate(
                receipt.model_dump(mode="python")
                if isinstance(receipt, OperationReceipt)
                else receipt,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic generation receipt is invalid") from error
        if (
            checked_operation.operation_type is not OperationType.KICAD_SCHEMATIC_WRITE
            or checked_receipt.operation_type is not OperationType.KICAD_SCHEMATIC_WRITE
            or checked_receipt.operation_id != checked_operation.operation_id
            or checked_receipt.tenant_id != checked_operation.tenant_id
            or checked_receipt.project_id != checked_operation.project_id
            or checked_receipt.status is not OperationStatus.SUCCEEDED
            or checked_receipt.workspace_apply_receipt_fingerprint is None
        ):
            raise SchematicProductionRejected("schematic generation receipt is not a successful write")
        mutations = tuple(
            item
            for item in checked_receipt.workspace_mutations
            if item.relative_path.casefold() == checked_operation.target_relative_path.casefold()
        )
        if (
            len(checked_receipt.workspace_mutations) != 1
            or len(mutations) != 1
            or mutations[0].content_fingerprint != checked_operation.content_fingerprint
        ):
            raise SchematicProductionRejected("schematic workspace receipt binding mismatch")

        detail = self.conversation(
            checked_operation.project_id,
            checked_operation.conversation_id,
            tenant_id=checked_operation.tenant_id,
            requester=checked_operation.requester_id,
        )
        production = detail.schematic_production
        if production is None:
            raise SchematicProductionRejected("schematic production state is unavailable")
        if production.status in {SchematicProductionStatus.GENERATED}:
            prior = production.generation_receipt
            if (
                prior is not None
                and prior.operation_receipt_fingerprint == checked_receipt.fingerprint
                and prior.artifact_fingerprint == checked_operation.content_fingerprint
            ):
                return detail
            raise SchematicProductionRejected("schematic generation receipt identity conflict")
        if production.status is not SchematicProductionStatus.QUEUED:
            raise SchematicProductionRejected("schematic production is not queued")
        operation_binding_matches = (
            production.operation_id == checked_operation.operation_id
            and production.operation_fingerprint == checked_operation.fingerprint
            and production.request.target_relative_path == checked_operation.target_relative_path
            and production.request.design_truth_fingerprint == checked_operation.design_truth_fingerprint
        )
        successor_operation = (
            # A retry may have produced a later, real worker receipt after the
            # queued catalog snapshot was persisted.  Permit that projection
            # only when every approved request/proposal/authorization binding
            # is unchanged; the receipt and the current artifact are still
            # validated below before the catalog operation binding is moved.
            not operation_binding_matches
            and production.operation_id is not None
            and checked_operation.operation_id != production.operation_id
            and checked_operation.project_id == production.project_id
            and checked_operation.conversation_id == production.conversation_id
            and checked_operation.requester_id == production.request.requester
            and checked_operation.proposal_id == production.proposal.proposal_id
            and checked_operation.proposal_fingerprint == production.proposal.fingerprint
            and checked_operation.schematic_request_id == production.request.request_id
            and checked_operation.schematic_request_fingerprint == production.request.fingerprint
            and checked_operation.design_truth_fingerprint == production.request.design_truth_fingerprint
            and checked_operation.proposal_decision_id == production.decision.decision_id
            and checked_operation.proposal_decision_fingerprint == production.decision.fingerprint
            and checked_operation.authorization_id == production.request.authorization_id
            and checked_operation.authorization_request_id == production.request.authorization_request_id
            and checked_operation.implementation_id == production.request.implementation_id
            and checked_operation.implementation_task_id == production.request.implementation_task_id
            and checked_operation.target_id == production.request.workspace_identity
            and checked_operation.target_relative_path == production.request.target_relative_path
        )
        if not operation_binding_matches and not successor_operation:
            raise SchematicProductionRejected("schematic production operation binding mismatch")
        planning = detail.schematic_planning
        approved_plan = (
            None
            if planning is None or planning.status is not SchematicPlanningStatus.APPROVED
            else planning.approved_plan
        )
        legacy_plan_binding = (
            approved_plan is None
            and production.request.schematic_plan_id is None
            and production.request.schematic_plan_fingerprint is None
        )
        if approved_plan is None and not legacy_plan_binding:
            raise SchematicProductionRejected("approved schematic plan is unavailable")
        if not legacy_plan_binding and (
            production.request.schematic_plan_id != approved_plan.plan_id
            or production.request.schematic_plan_fingerprint != approved_plan.fingerprint
        ):
            raise SchematicProductionRejected("schematic generation plan is stale")

        try:
            root = self.knowledge_snapshot_root(
                checked_operation.project_id,
                tenant_id=checked_operation.tenant_id,
            )
            normalized_path, target = self._relative(
                checked_operation.target_relative_path,
                root=root,
            )
            if normalized_path != "hardware/main.kicad_sch" or target.is_symlink() or not target.is_file():
                raise ValueError("schematic artifact target is invalid")
            content = target.read_bytes()
        except (OSError, RuntimeError, ValueError) as error:
            raise SchematicProductionRejected("generated schematic artifact is unavailable") from error
        if not content:
            raise SchematicProductionRejected("generated schematic artifact is empty")
        artifact_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
        if artifact_fingerprint != checked_operation.content_fingerprint:
            raise SchematicProductionRejected("generated schematic artifact fingerprint mismatch")

        receipt_id = "schematic-generation-receipt-" + hashlib.sha256(
            f"{checked_operation.operation_id}|{checked_receipt.fingerprint}|{artifact_fingerprint}".encode()
        ).hexdigest()[:32]
        generation_status = (
            SchematicGenerationStatus.SUCCESS
            if external_validation_status == "PARSE_VERIFIED"
            else SchematicGenerationStatus.ARTIFACT_GENERATED
        )
        generation_receipt = SchematicGenerationReceipt.create(
            receipt_id=receipt_id,
            project_id=checked_operation.project_id,
            conversation_id=checked_operation.conversation_id,
            operation_id=checked_operation.operation_id,
            operation_fingerprint=checked_operation.fingerprint,
            operation_receipt_fingerprint=checked_receipt.fingerprint,
            workspace_apply_receipt_fingerprint=checked_receipt.workspace_apply_receipt_fingerprint,
            artifact_path=normalized_path,
            schematic_plan_id=(None if legacy_plan_binding else approved_plan.plan_id),
            schematic_plan_fingerprint=(None if legacy_plan_binding else approved_plan.fingerprint),
            design_truth_fingerprint=checked_operation.design_truth_fingerprint,
            artifact_fingerprint=artifact_fingerprint,
            generator="embrix.kicad_schematic_serializer",
            generator_version="g3-v1",
            validation_status=external_validation_status,
            status=generation_status,
            source_refs=tuple(
                dict.fromkeys(
                    (
                        *production.request.source_refs,
                        checked_operation.operation_id,
                        checked_receipt.fingerprint,
                    )
                )
            ),
            created_at=checked_receipt.created_at,
        )
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=checked_operation.operation_id,
            operation_fingerprint=checked_operation.fingerprint,
            status=SchematicProductionStatus.GENERATED,
            generation_receipt=generation_receipt,
            repair_application_receipts=production.repair_application_receipts,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_schematic_parse_validation(
        self,
        *,
        evidence: object,
    ) -> ConversationDetail:
        """Promote one immutable external KiCad parse result into the G3 receipt."""

        try:
            checked_evidence = SchematicParseValidationEvidence.model_validate(
                evidence.model_dump(mode="python")
                if isinstance(evidence, SchematicParseValidationEvidence)
                else evidence,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic parse evidence is invalid") from error
        if checked_evidence.validation_status != "PARSE_VERIFIED":
            raise SchematicProductionRejected("schematic parse validation did not pass")

        detail = self._repository.conversation(checked_evidence.conversation_id)
        if detail is None or detail.project_id != checked_evidence.project_id:
            raise ProjectCatalogNotFound("conversation was not found")
        production = detail.schematic_production
        if production is None or production.generation_receipt is None:
            raise SchematicProductionRejected("schematic generation receipt is unavailable")
        receipt = production.generation_receipt
        if production.status is not SchematicProductionStatus.GENERATED:
            raise SchematicProductionRejected("schematic parse validation cannot update stale production")
        prior_parse = receipt.parse_validation
        if prior_parse is not None and prior_parse.fingerprint != checked_evidence.fingerprint:
            raise SchematicProductionRejected("schematic parse validation identity conflict")
        if (
            checked_evidence.operation_id != production.operation_id
            or checked_evidence.artifact_path != receipt.artifact_path
            or checked_evidence.artifact_fingerprint != receipt.artifact_fingerprint
        ):
            raise SchematicProductionRejected("schematic parse evidence binding mismatch")
        if prior_parse is None and (
            receipt.validation_status != "UNAVAILABLE"
            or receipt.status is not SchematicGenerationStatus.ARTIFACT_GENERATED
        ):
            raise SchematicProductionRejected("schematic parse evidence binding mismatch")
        if prior_parse is not None and (
            receipt.validation_status != "PARSE_VERIFIED"
            or receipt.status is not SchematicGenerationStatus.SUCCESS
        ):
            raise SchematicProductionRejected("schematic parse evidence binding mismatch")

        root = self.knowledge_snapshot_root(
            checked_evidence.project_id,
            tenant_id=detail.tenant_id,
        )
        normalized_path, target = self._relative(receipt.artifact_path, root=root)
        if normalized_path != receipt.artifact_path or target.is_symlink() or not target.is_file():
            raise SchematicProductionRejected("schematic parse artifact is unavailable")
        current_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if current_fingerprint != checked_evidence.artifact_fingerprint:
            raise SchematicProductionRejected("schematic parse artifact fingerprint mismatch")
        if prior_parse is not None:
            return detail

        verified_receipt = SchematicGenerationReceipt.create(
            **{
                **receipt.model_dump(mode="python", exclude={"fingerprint", "parse_validation"}),
                "validation_status": "PARSE_VERIFIED",
                "status": SchematicGenerationStatus.SUCCESS,
                "parse_validation": checked_evidence,
            }
        )
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=production.operation_id,
            operation_fingerprint=production.operation_fingerprint,
            status=SchematicProductionStatus.GENERATED,
            generation_receipt=verified_receipt,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def _current_schematic_bytes(
        self, project_id: str, artifact_path: str, artifact_fingerprint: str, *, tenant_id: str,
    ) -> bool:
        root = self.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
        normalized, target = self._relative(artifact_path, root=root)
        if normalized != artifact_path or target.is_symlink() or not target.is_file():
            return False
        try:
            return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest() == artifact_fingerprint
        except OSError:
            return False

    def validate_current_schematic_erc_source(
        self, *, operation: object, receipt: object, source_receipt: object,
        tenant_id: str, requester: str,
    ) -> None:
        """Read-only Owner check for the bytes and source of a reviewed ERC."""
        from embedded_copilot.execution_worker.contracts import (
            OperationReceipt,
            OperationStatus,
            OperationType,
            SchematicErcOperation,
        )

        if (not isinstance(operation, SchematicErcOperation)
                or not isinstance(receipt, OperationReceipt)
                or not isinstance(source_receipt, OperationReceipt)):
            raise SchematicProductionRejected("canonical ERC source is unavailable")
        self.get_project_for_requester(operation.project_id, tenant_id=tenant_id, requester=requester)
        if operation.tenant_id != tenant_id or operation.requester_id != requester:
            raise SchematicProductionRejected("ERC requester identity mismatch")
        detail = self.conversation(
            operation.project_id, operation.conversation_id,
            tenant_id=tenant_id, requester=requester,
        )
        production = detail.schematic_production
        generation = None if production is None else production.generation_receipt
        erc = None if production is None else production.erc_receipt
        if production is None or generation is None or (
            production.status is not SchematicProductionStatus.GENERATED
            or generation.status is not SchematicGenerationStatus.SUCCESS
            or generation.validation_status != "PARSE_VERIFIED"
            or generation.parse_validation is None
            or operation.source_operation_id != production.operation_id
            or operation.source_operation_receipt_fingerprint != generation.operation_receipt_fingerprint
            or operation.schematic_artifact_fingerprint != generation.artifact_fingerprint
            or operation.target_relative_path != generation.artifact_path
            or operation.schematic_artifact_ref != generation.artifact_path
            or erc is None
            or erc.operation_id != operation.operation_id
            or erc.operation_receipt_fingerprint != receipt.fingerprint
            or erc.artifact_path != generation.artifact_path
            or erc.artifact_fingerprint != generation.artifact_fingerprint
            or receipt.schematic_erc_result is None
            or erc.result.fingerprint != receipt.schematic_erc_result.fingerprint
            or source_receipt.operation_id != operation.source_operation_id
            or source_receipt.tenant_id != tenant_id
            or source_receipt.project_id != operation.project_id
            or source_receipt.operation_type is not OperationType.KICAD_SCHEMATIC_WRITE
            or source_receipt.status is not OperationStatus.SUCCEEDED
            or source_receipt.fingerprint != generation.operation_receipt_fingerprint
            or not any(
                item.relative_path == generation.artifact_path
                and item.content_fingerprint == generation.artifact_fingerprint
                for item in source_receipt.workspace_mutations
            )
        ):
            raise SchematicProductionRejected("current schematic source identity changed")
        root = self.knowledge_snapshot_root(operation.project_id, tenant_id=tenant_id)
        normalized, target = self._relative(generation.artifact_path, root=root)
        if normalized != generation.artifact_path or target.is_symlink() or not target.is_file():
            raise SchematicProductionRejected("current schematic source is unavailable")
        try:
            current_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as error:
            raise SchematicProductionRejected("current schematic source is unavailable") from error
        if current_fingerprint != generation.artifact_fingerprint:
            raise SchematicProductionRejected("current schematic source bytes changed")

    def record_schematic_erc_validation(
        self,
        *,
        operation: object,
        receipt: object,
    ) -> ConversationDetail:
        """Persist one real ERC receipt without granting ERC truth authority."""

        from embedded_copilot.execution_worker.contracts import (
            OperationReceipt,
            OperationStatus,
            OperationType,
            SchematicErcOperation,
        )

        try:
            checked_operation = SchematicErcOperation.model_validate(
                operation.model_dump(mode="python")
                if isinstance(operation, SchematicErcOperation)
                else operation,
                strict=False,
            )
            checked_receipt = OperationReceipt.model_validate(
                receipt.model_dump(mode="python")
                if isinstance(receipt, OperationReceipt)
                else receipt,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic ERC receipt is invalid") from error
        if (
            checked_operation.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or checked_receipt.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or checked_receipt.operation_id != checked_operation.operation_id
            or checked_receipt.tenant_id != checked_operation.tenant_id
            or checked_receipt.project_id != checked_operation.project_id
            or checked_receipt.status not in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}
            or checked_receipt.schematic_erc_result is None
        ):
            raise SchematicProductionRejected("schematic ERC receipt is not a typed worker receipt")
        if checked_operation.source_parse_operation_id is not None:
            execution = self._schematic_evidence_execution
            if (execution is None or execution.get_receipt(tenant_id=checked_operation.tenant_id,
                project_id=checked_operation.project_id, operation_id=checked_operation.operation_id) != checked_receipt
                or execution.get_operation(tenant_id=checked_operation.tenant_id, project_id=checked_operation.project_id,
                    operation_id=checked_operation.operation_id) != checked_operation):
                raise SchematicProductionRejected("ERC canonical receipt mismatch")
        result = checked_receipt.schematic_erc_result
        if (
            result.operation_id != checked_operation.operation_id
            or result.schematic_artifact_ref != checked_operation.target_relative_path
            or result.schematic_content_fingerprint != checked_operation.schematic_artifact_fingerprint
            or result.receipt_ref != checked_operation.source_operation_receipt_fingerprint
            or checked_receipt.schematic_erc_result_fingerprint != result.fingerprint
        ):
            raise SchematicProductionRejected("schematic ERC receipt binding mismatch")

        detail = self.conversation(
            checked_operation.project_id,
            checked_operation.conversation_id,
            tenant_id=checked_operation.tenant_id,
            requester=checked_operation.requester_id,
        )
        production = detail.schematic_production
        if production is None or production.generation_receipt is None:
            raise SchematicProductionRejected("schematic generation receipt is unavailable")
        generation = production.generation_receipt
        if (
            production.status is not SchematicProductionStatus.GENERATED
            or (checked_operation.source_parse_operation_id is None and (
                generation.status is not SchematicGenerationStatus.SUCCESS
                or generation.validation_status != "PARSE_VERIFIED" or generation.parse_validation is None))
            or (checked_operation.source_parse_operation_id is not None and not self.revalidate_schematic_evidence_operation(checked_operation))
            or checked_operation.source_operation_id != production.operation_id
            or checked_operation.source_operation_receipt_fingerprint != generation.operation_receipt_fingerprint
            or checked_operation.schematic_artifact_fingerprint != generation.artifact_fingerprint
            or checked_operation.target_relative_path != generation.artifact_path
        ):
            raise SchematicProductionRejected("schematic ERC requires the current parse-verified artifact")

        root = self.knowledge_snapshot_root(
            checked_operation.project_id,
            tenant_id=detail.tenant_id,
        )
        normalized_path, target = self._relative(generation.artifact_path, root=root)
        if normalized_path != generation.artifact_path or target.is_symlink() or not target.is_file():
            raise SchematicProductionRejected("schematic ERC artifact is unavailable")
        current_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if current_fingerprint != generation.artifact_fingerprint:
            raise SchematicProductionRejected("schematic ERC artifact fingerprint mismatch")
        if production.erc_receipt is not None:
            prior = production.erc_receipt
            if (
                prior.operation_id == checked_operation.operation_id
                and prior.operation_receipt_fingerprint == checked_receipt.fingerprint
                and prior.artifact_fingerprint == generation.artifact_fingerprint
            ):
                return detail
            raise SchematicProductionRejected("schematic ERC receipt identity conflict")

        proposal = production.proposal
        component_ids = {item.component_id for item in proposal.components}
        pin_ids = {item.pin_id for item in proposal.pins}
        net_ids = {item.net_id for item in proposal.nets}
        component_sources = {
            item.component_id: item.source_refs for item in proposal.components
        }
        pin_sources = {item.pin_id: item.source_refs for item in proposal.pins}
        net_sources = {item.net_id: item.source_refs for item in proposal.nets}
        correlations = []
        for issue in result.issues:
            component_id = issue.component_ref if issue.component_ref in component_ids else None
            pin_id = issue.pin_ref if issue.pin_ref in pin_ids else None
            net_id = issue.net_ref if issue.net_ref in net_ids else None
            matched = any((component_id, pin_id, net_id))
            truth_refs = tuple(
                dict.fromkeys(
                    (
                        *(component_sources.get(component_id, ()) if component_id else ()),
                        *(pin_sources.get(pin_id, ()) if pin_id else ()),
                        *(net_sources.get(net_id, ()) if net_id else ()),
                    )
                )
            )
            correlations.append(
                SchematicErcCorrelation.create(
                    issue_id=issue.issue_id,
                    project_id=detail.project_id,
                    artifact_fingerprint=generation.artifact_fingerprint,
                    component_id=component_id,
                    pin_id=pin_id,
                    net_id=net_id,
                    truth_evidence_refs=truth_refs,
                    plan_refs=() if not matched else (generation.schematic_plan_id,),
                    confidence=1.0 if matched else 0.0,
                    status=(
                        SchematicErcCorrelationStatus.MATCHED
                        if matched
                        else SchematicErcCorrelationStatus.UNMATCHED
                    ),
                )
            )
        projected_result = result
        if correlations:
            projected_result = SchematicErcResult.create(
                **{
                    **result.model_dump(mode="python", exclude={"fingerprint", "correlations"}),
                    "correlations": tuple(correlations),
                }
            )
        assessment = assess_schematic_repair(projected_result)
        gate_status = {
            SchematicErcEngineeringStatus.PASS: SchematicErcGateStatus.PASS,
            SchematicErcEngineeringStatus.WARNINGS_REMAIN: SchematicErcGateStatus.PARTIAL,
            SchematicErcEngineeringStatus.PARTIAL: SchematicErcGateStatus.PARTIAL,
            SchematicErcEngineeringStatus.BLOCKED: SchematicErcGateStatus.BLOCKED,
        }[projected_result.engineering_status]
        erc_receipt = SchematicErcReceipt.create(
            receipt_id=(
                "schematic-erc-receipt-"
                + hashlib.sha256(
                    f"{checked_operation.operation_id}|{checked_receipt.fingerprint}|{generation.artifact_fingerprint}".encode()
                ).hexdigest()[:32]
            ),
            project_id=detail.project_id,
            conversation_id=detail.conversation_id,
            operation_id=checked_operation.operation_id,
            operation_receipt_fingerprint=checked_receipt.fingerprint,
            artifact_path=generation.artifact_path,
            artifact_fingerprint=generation.artifact_fingerprint,
            schematic_plan_fingerprint=generation.schematic_plan_fingerprint,
            design_truth_fingerprint=generation.design_truth_fingerprint,
            kicad_version=projected_result.kicad_version,
            command_identity=projected_result.command_identity,
            tool_execution_status=checked_receipt.tool_execution_status,
            engineering_status=projected_result.engineering_status,
            result=projected_result,
            created_at=self._now(),
        )
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=production.operation_id,
            operation_fingerprint=production.operation_fingerprint,
            status=production.status,
            generation_receipt=production.generation_receipt,
            erc_receipt=erc_receipt,
            erc_assessment=assessment,
            repair_proposals=production.repair_proposals,
            repair_decisions=production.repair_decisions,
            repair_application_receipts=production.repair_application_receipts,
            erc_gate_status=gate_status,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_schematic_repair_proposal(
        self,
        *,
        project_id: str,
        conversation_id: str,
        tenant_id: str,
        requester: str,
        assessment: object,
        proposal: object,
    ) -> ConversationDetail:
        """Persist a reviewable repair proposal without applying it.

        This method is deliberately a ProjectCatalog projection boundary. It
        never writes the schematic, DesignTruth, or SchematicPlan. A proposal
        is accepted only when it is bound to the current parse-verified ERC
        result and artifact fingerprint.
        """

        try:
            checked_assessment = SchematicRepairAssessment.model_validate(
                assessment.model_dump(mode="python")
                if isinstance(assessment, SchematicRepairAssessment)
                else assessment,
                strict=False,
            )
            checked_proposal = SchematicRepairProposal.model_validate(
                proposal.model_dump(mode="python")
                if isinstance(proposal, SchematicRepairProposal)
                else proposal,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic repair proposal is invalid") from error
        if not checked_assessment.proposal_allowed:
            raise SchematicProductionRejected("ERC assessment does not allow a repair proposal")
        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        production = detail.schematic_production
        if (
            production is None
            or production.status is not SchematicProductionStatus.GENERATED
            or production.generation_receipt is None
            or production.generation_receipt.validation_status != "PARSE_VERIFIED"
            or production.erc_receipt is None
        ):
            raise SchematicProductionRejected("repair proposal requires the current parse-verified ERC receipt")
        erc = production.erc_receipt
        if (
            checked_assessment.erc_result_fingerprint != erc.result.fingerprint
            or checked_proposal.erc_result_fingerprint != erc.result.fingerprint
            or checked_proposal.base_proposal_id != production.proposal.proposal_id
            or checked_proposal.target_relative_path != production.generation_receipt.artifact_path
            or checked_proposal.source_fingerprint != erc.result.fingerprint
            or not checked_proposal.requires_user_approval
            or checked_proposal.status is not SchematicProductionStatus.AWAITING_APPROVAL
        ):
            raise SchematicProductionRejected("repair proposal is stale or not bound to the current ERC result")
        if checked_proposal.source_erc_receipt_fingerprint is None:
            checked_proposal = SchematicRepairProposal.create(
                **{
                    **checked_proposal.model_dump(mode="python", exclude={"fingerprint"}),
                    "source_erc_receipt_fingerprint": erc.fingerprint,
                }
            )
        elif checked_proposal.source_erc_receipt_fingerprint != erc.fingerprint:
            raise SchematicProductionRejected("repair proposal source ERC receipt is stale")
        if any(item.repair_id == checked_proposal.repair_id for item in production.repair_proposals):
            return detail
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=production.operation_id,
            operation_fingerprint=production.operation_fingerprint,
            status=production.status,
            generation_receipt=production.generation_receipt,
            erc_receipt=production.erc_receipt,
            erc_assessment=checked_assessment,
            repair_proposals=(*production.repair_proposals, checked_proposal),
            repair_decisions=production.repair_decisions,
            repair_application_receipts=production.repair_application_receipts,
            erc_gate_status=production.erc_gate_status,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def decide_schematic_repair(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        decision: object,
    ) -> ConversationDetail:
        """Record a typed repair decision; approval does not perform a write.

        A later approved truth/plan transition must invoke the existing G3
        generation authority. Keeping this method projection-only prevents an
        ERC result or a repair proposal from becoming a hidden write path.
        """

        try:
            checked_decision = SchematicRepairDecision.model_validate(
                decision.model_dump(mode="python")
                if isinstance(decision, SchematicRepairDecision)
                else decision,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic repair decision is invalid") from error
        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        production = detail.schematic_production
        if production is None or production.erc_receipt is None or production.generation_receipt is None:
            raise SchematicProductionRejected("repair decision requires an ERC receipt")
        if production.status is not SchematicProductionStatus.GENERATED:
            raise SchematicProductionRejected("repair decision cannot target stale production")
        repair = next(
            (item for item in production.repair_proposals if item.repair_id == checked_decision.repair_id),
            None,
        )
        if repair is None:
            raise SchematicProductionRejected("repair proposal was not found")
        if (
            checked_decision.proposal_fingerprint != repair.fingerprint
            or checked_decision.artifact_fingerprint != production.generation_receipt.artifact_fingerprint
            or repair.erc_result_fingerprint != production.erc_receipt.result.fingerprint
        ):
            raise SchematicProductionRejected("repair decision is stale or fingerprint-mismatched")
        if checked_decision.action is SchematicRepairDecisionAction.EDIT_FIX:
            if checked_decision.edited_proposal_fingerprint not in {
                item.fingerprint for item in production.repair_proposals
            }:
                raise SchematicProductionRejected("edited repair proposal is not persisted")
        if checked_decision.action is SchematicRepairDecisionAction.WAIVE_WITH_REASON:
            if production.erc_receipt.result.violations:
                raise SchematicProductionRejected("blocking ERC errors cannot be waived")
            if repair.classification is not SchematicErcIssueClassification.NON_BLOCKING_WARNING:
                raise SchematicProductionRejected("only non-blocking warnings may be waived")
        if any(item.decision_id == checked_decision.decision_id for item in production.repair_decisions):
            return detail
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=production.operation_id,
            operation_fingerprint=production.operation_fingerprint,
            status=production.status,
            generation_receipt=production.generation_receipt,
            erc_receipt=production.erc_receipt,
            erc_assessment=production.erc_assessment,
            repair_proposals=production.repair_proposals,
            repair_decisions=(*production.repair_decisions, checked_decision),
            repair_application_receipts=production.repair_application_receipts,
            erc_gate_status=production.erc_gate_status,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": checked_decision.decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_schematic_repair_application(
        self,
        *,
        project_id: str,
        conversation_id: str,
        tenant_id: str,
        requester: str,
        receipt: object,
    ) -> ConversationDetail:
        """Persist post-repair lineage after G3-V and a fresh real ERC.

        The method is projection-only. The write must already have passed the
        existing WorkspaceApply/Worker boundary; this method accepts only the
        resulting receipt lineage and fail-closes on stale identities.
        """

        try:
            checked_receipt = SchematicRepairApplicationReceipt.model_validate(
                receipt.model_dump(mode="python")
                if isinstance(receipt, SchematicRepairApplicationReceipt)
                else receipt,
                strict=False,
            )
        except (TypeError, ValueError) as error:
            raise SchematicProductionRejected("schematic repair application receipt is invalid") from error
        detail = self.conversation(
            project_id,
            conversation_id,
            tenant_id=tenant_id,
            requester=requester,
        )
        production = detail.schematic_production
        if (
            production is None
            or production.status is not SchematicProductionStatus.GENERATED
            or production.generation_receipt is None
            or production.generation_receipt.validation_status != "PARSE_VERIFIED"
            or production.generation_receipt.parse_validation is None
            or production.erc_receipt is None
            or checked_receipt.status is not SchematicRepairApplicationStatus.APPLIED
        ):
            raise SchematicProductionRejected("repair application requires the current parse-verified ERC receipt")
        if any(item.receipt_id == checked_receipt.receipt_id for item in production.repair_application_receipts):
            return detail
        repair = next(
            (item for item in production.repair_proposals if item.repair_id == checked_receipt.repair_id),
            None,
        )
        decision = next(
            (item for item in production.repair_decisions if item.decision_id == checked_receipt.repair_decision_id),
            None,
        )
        generation = production.generation_receipt
        erc = production.erc_receipt
        if repair is None or decision is None:
            raise SchematicProductionRejected("repair application lineage is not persisted")
        if (
            checked_receipt.project_id != production.project_id
            or checked_receipt.conversation_id != production.conversation_id
            or checked_receipt.repair_proposal_fingerprint != repair.fingerprint
            or checked_receipt.repair_decision_fingerprint != decision.fingerprint
            or decision.repair_id != repair.repair_id
            or decision.action is not checked_receipt.repair_action
            or decision.action not in {
                SchematicRepairDecisionAction.APPROVE_FIX,
                SchematicRepairDecisionAction.EDIT_FIX,
            }
            or checked_receipt.artifact_path != generation.artifact_path
            or checked_receipt.pre_artifact_fingerprint != decision.artifact_fingerprint
            or checked_receipt.pre_erc_result_fingerprint != repair.erc_result_fingerprint
            or checked_receipt.pre_erc_receipt_fingerprint != repair.source_erc_receipt_fingerprint
            or checked_receipt.post_design_truth_fingerprint != production.request.design_truth_fingerprint
            or checked_receipt.post_schematic_plan_fingerprint != generation.schematic_plan_fingerprint
            or checked_receipt.post_artifact_fingerprint != generation.artifact_fingerprint
            or checked_receipt.post_generation_receipt_fingerprint != generation.fingerprint
            or checked_receipt.post_parse_validation_fingerprint != generation.parse_validation.fingerprint
            or checked_receipt.post_erc_receipt_fingerprint != erc.fingerprint
            or checked_receipt.post_erc_result_fingerprint != erc.result.fingerprint
            or checked_receipt.workspace_apply_receipt_fingerprint != generation.workspace_apply_receipt_fingerprint
        ):
            raise SchematicProductionRejected("repair application receipt is stale or fingerprint-mismatched")
        next_state = SchematicProductionState.create(
            production_id=production.production_id,
            project_id=production.project_id,
            conversation_id=production.conversation_id,
            request=production.request,
            proposal=production.proposal,
            decision=production.decision,
            operation_id=production.operation_id,
            operation_fingerprint=production.operation_fingerprint,
            status=production.status,
            generation_receipt=generation,
            erc_receipt=erc,
            erc_assessment=production.erc_assessment,
            repair_proposals=production.repair_proposals,
            repair_decisions=production.repair_decisions,
            repair_application_receipts=(*production.repair_application_receipts, checked_receipt),
            erc_gate_status=production.erc_gate_status,
        )
        updated = detail.model_copy(
            update={
                "schematic_production": next_state,
                "updated_at": checked_receipt.applied_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def create_pcb_production_input(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        physical_design: object,
        schematic_artifact_id: str,
        schematic_artifact_fingerprint: str,
        schematic_content_fingerprint: str,
        workspace_identity: str,
        target_relative_path: str = "hardware/main.kicad_pcb",
        requested_at: datetime | None = None,
    ) -> ConversationDetail:
        """Persist the G4-derived PCB request before proposal generation."""
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        pcb_state = detail.pcb_engineering_state
        self.require_current_circuit_binding(
            project_id, None if pcb_state is None else pcb_state.intent_source_binding,
            tenant_id=tenant_id, requester=requester,
        )
        if detail.pcb_production_request is not None:
            return detail
        service = self._pcb_production_service
        if service is None:
            raise PCBProductionRejected("PCB production service is unavailable")
        latest_turn = detail.turns[-1] if detail.turns else None
        source_request_id = None if latest_turn is None else latest_turn.request_id
        source_trace_id = None if latest_turn is None else latest_turn.trace_id
        try:
            physical = PCBPhysicalDesignProposal.model_validate(
                physical_design.model_dump(mode="python") if isinstance(physical_design, PCBPhysicalDesignProposal) else physical_design,
                strict=False,
            )
            if physical.project_id != detail.project_id or physical.conversation_id != detail.conversation_id:
                raise ValueError("PCB physical design identity mismatch")
            request = service.build_request_from_g4(
                schematic_production=detail.schematic_production,
                schematic_plan=detail.schematic_planning,
                design_truth=detail.design_truth,
                hardware_selection=detail.hardware_selection,
                hardware_design=detail.hardware_design_spec,
                implementation=detail.implementation_spec,
                authorization=detail.execution_authorization,
                schematic_artifact_id=schematic_artifact_id,
                schematic_artifact_fingerprint=schematic_artifact_fingerprint,
                schematic_content_fingerprint=schematic_content_fingerprint,
                workspace_identity=workspace_identity,
                target_relative_path=target_relative_path,
                board=physical.board,
                placements=physical.placements,
                routes=physical.routes,
                routing=physical.routing,
                requester=requester,
                requested_at=requested_at or self._now(),
                source_request_id=source_request_id,
                trace_id=source_trace_id,
            )
            pcb_state = detail.pcb_engineering_state
            pcb_constraints = detail.pcb_constraint_set
            pcb_plan = detail.pcb_verification_plan
            if pcb_state is not None or pcb_constraints is not None or pcb_plan is not None:
                binding = None if pcb_state is None else pcb_state.intent_source_binding
                if (
                    pcb_state is None
                    or pcb_constraints is None
                    or pcb_plan is None
                    or binding is None
                    or binding.status.value != "BOUND"
                    or not pcb_state.element_bindings
                    or any(item.status.value != "BOUND" for item in pcb_state.element_bindings)
                    or pcb_state.status.value != "CURRENT"
                    or pcb_constraints.status.value != "CURRENT"
                    or pcb_plan.status.value != "CURRENT"
                    or pcb_constraints.state_fingerprint != pcb_state.fingerprint
                    or pcb_plan.pcb_state_fingerprint != pcb_state.fingerprint
                    or pcb_plan.constraint_set_fingerprint != pcb_constraints.fingerprint
                    or binding.project_id != detail.project_id
                    or binding.conversation_id != detail.conversation_id
                ):
                    raise PCBProductionRejected("current PCB source/constraint lineage is unavailable")
                element_fp = "sha256:" + hashlib.sha256(
                    "|".join(sorted(item.fingerprint for item in pcb_state.element_bindings)).encode()
                ).hexdigest()
                request = PCBProductionRequest.create(
                    **{
                        **request.model_dump(mode="python", exclude={"fingerprint"}),
                        "pcb_state_fingerprint": pcb_state.fingerprint,
                        "pcb_source_binding_fingerprint": binding.fingerprint,
                        "pcb_constraint_set_fingerprint": pcb_constraints.fingerprint,
                        "pcb_verification_plan_fingerprint": pcb_plan.fingerprint,
                        "pcb_element_binding_fingerprint": element_fp,
                    }
                )
        except (TypeError, ValueError, OSError, AttributeError) as error:
            raise PCBProductionRejected("PCB production input rejected") from error
        updated = detail.model_copy(update={"pcb_production_request": request, "updated_at": request.requested_at, "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_pcb_generation(
        self,
        *,
        operation: object,
        receipt: object,
    ) -> ConversationDetail:
        """Project a successful canonical PCB write receipt into ProjectCatalog."""
        from embedded_copilot.execution_worker.contracts import (
            OperationReceipt,
            OperationStatus,
            OperationType,
            PCBWriteOperation,
        )

        try:
            checked_operation = PCBWriteOperation.model_validate(operation.model_dump(mode="python") if isinstance(operation, PCBWriteOperation) else operation, strict=False)
            checked_receipt = OperationReceipt.model_validate(receipt.model_dump(mode="python") if isinstance(receipt, OperationReceipt) else receipt, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB generation receipt is invalid") from error
        if checked_operation.operation_type is not OperationType.KICAD_PCB_WRITE or checked_receipt.operation_type is not OperationType.KICAD_PCB_WRITE or checked_receipt.operation_id != checked_operation.operation_id or checked_receipt.project_id != checked_operation.project_id or checked_receipt.status is not OperationStatus.SUCCEEDED or checked_receipt.pcb_artifact is None or checked_receipt.workspace_apply_receipt_fingerprint is None:
            raise PCBProductionRejected("PCB generation receipt is not a successful write")
        artifact = checked_receipt.pcb_artifact
        if artifact.path != checked_operation.target_relative_path or artifact.content_hash != checked_operation.content_fingerprint or artifact.proposal_fingerprint != checked_operation.pcb_proposal_fingerprint:
            raise PCBProductionRejected("PCB artifact receipt binding mismatch")
        detail = self.conversation(checked_operation.project_id, checked_operation.conversation_id, tenant_id=checked_operation.tenant_id, requester=checked_operation.requester_id)
        production = detail.pcb_production
        if production is None or production.status is not PCBProductionStatus.QUEUED:
            raise PCBProductionRejected("PCB production is not queued")
        if production.operation_id != checked_operation.operation_id or production.operation_fingerprint != checked_operation.fingerprint or production.proposal.fingerprint != checked_operation.pcb_proposal_fingerprint:
            raise PCBProductionRejected("PCB generation operation binding mismatch")
        root = self.knowledge_snapshot_root(checked_operation.project_id, tenant_id=detail.tenant_id)
        normalized, target = self._relative(checked_operation.target_relative_path, root=root)
        if normalized != checked_operation.target_relative_path or target.is_symlink() or not target.is_file() or target.stat().st_size <= 0:
            raise PCBProductionRejected("generated PCB artifact is unavailable")
        current_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if current_fingerprint != checked_operation.content_fingerprint:
            raise PCBProductionRejected("generated PCB artifact fingerprint mismatch")
        if production.generation_receipt is not None:
            if production.generation_receipt.operation_receipt_fingerprint == checked_receipt.fingerprint and production.generation_receipt.artifact_fingerprint == current_fingerprint:
                return detail
            raise PCBProductionRejected("PCB generation receipt identity conflict")
        generation = PCBGenerationReceipt.create(
            receipt_id="pcb-generation-receipt-" + hashlib.sha256(f"{checked_operation.operation_id}|{checked_receipt.fingerprint}|{current_fingerprint}".encode()).hexdigest()[:32],
            project_id=checked_operation.project_id,
            conversation_id=checked_operation.conversation_id,
            operation_id=checked_operation.operation_id,
            operation_fingerprint=checked_operation.fingerprint,
            operation_receipt_fingerprint=checked_receipt.fingerprint,
            workspace_apply_receipt_fingerprint=checked_receipt.workspace_apply_receipt_fingerprint,
            artifact_path=normalized,
            artifact_fingerprint=current_fingerprint,
            proposal_fingerprint=production.proposal.fingerprint,
            schematic_fingerprint=production.request.schematic_artifact_fingerprint,
            schematic_erc_fingerprint=production.request.schematic_erc_fingerprint,
            design_truth_fingerprint=production.request.design_truth_fingerprint,
            hardware_design_fingerprint=production.request.hardware_design_fingerprint,
            generator="embrix.kicad_pcb_serializer",
            generator_version="g5-v1",
            validation_status=PCBParseValidationStatus.UNAVAILABLE,
            artifact=artifact,
            status="ARTIFACT_GENERATED",
            source_refs=tuple(dict.fromkeys((*production.request.source_refs, checked_operation.operation_id, checked_receipt.fingerprint, artifact.artifact_id))),
            created_at=checked_receipt.created_at,
        )
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=PCBProductionStatus.GENERATED, generation_receipt=generation)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_pcb_parse_validation(self, *, evidence: object) -> ConversationDetail:
        try:
            checked_evidence = PCBParseValidationEvidence.model_validate(evidence.model_dump(mode="python") if isinstance(evidence, PCBParseValidationEvidence) else evidence, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB parse evidence is invalid") from error
        detail = self._repository.conversation(checked_evidence.conversation_id)
        if detail is None or detail.project_id != checked_evidence.project_id:
            raise ProjectCatalogNotFound("conversation was not found")
        production = detail.pcb_production
        if production is None or production.generation_receipt is None or production.status is not PCBProductionStatus.GENERATED:
            raise PCBProductionRejected("PCB generation receipt is unavailable")
        generation = production.generation_receipt
        if generation.validation_status is PCBParseValidationStatus.PARSE_VERIFIED:
            if generation.parse_validation is not None and generation.parse_validation.fingerprint == checked_evidence.fingerprint:
                return detail
            raise PCBProductionRejected("PCB parse validation identity conflict")
        if checked_evidence.operation_id != generation.operation_id or checked_evidence.artifact_path != generation.artifact_path or checked_evidence.artifact_fingerprint != generation.artifact_fingerprint:
            raise PCBProductionRejected("PCB parse evidence binding mismatch")
        root = self.knowledge_snapshot_root(detail.project_id, tenant_id=detail.tenant_id)
        normalized, target = self._relative(generation.artifact_path, root=root)
        if normalized != generation.artifact_path or target.is_symlink() or not target.is_file() or "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest() != generation.artifact_fingerprint:
            raise PCBProductionRejected("PCB parse artifact fingerprint mismatch")
        next_validation_status = checked_evidence.validation_status
        next_generation_status = (
            "SUCCESS"
            if next_validation_status is PCBParseValidationStatus.PARSE_VERIFIED
            else "FAILED"
        )
        verified = PCBGenerationReceipt.create(
            **{
                **generation.model_dump(
                    mode="python",
                    exclude={"fingerprint", "parse_validation", "validation_status", "status"},
                ),
                "validation_status": next_validation_status,
                "parse_validation": checked_evidence,
                "status": next_generation_status,
            }
        )
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=PCBProductionStatus.GENERATED, generation_receipt=verified)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": checked_evidence.validated_at, "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_pcb_drc_validation(self, *, operation: object, receipt: object) -> ConversationDetail:
        from embedded_copilot.execution_worker.contracts import (
            OperationReceipt,
            OperationType,
            PcbDrcOperation,
        )

        try:
            checked_operation = PcbDrcOperation.model_validate(operation.model_dump(mode="python") if isinstance(operation, PcbDrcOperation) else operation, strict=False)
            checked_receipt = OperationReceipt.model_validate(receipt.model_dump(mode="python") if isinstance(receipt, OperationReceipt) else receipt, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB DRC receipt is invalid") from error
        if checked_operation.operation_type is not OperationType.KICAD_PCB_DRC or checked_receipt.operation_type is not OperationType.KICAD_PCB_DRC or checked_receipt.operation_id != checked_operation.operation_id or checked_receipt.pcb_drc_result is None:
            raise PCBProductionRejected("PCB DRC receipt is not typed")
        result = checked_receipt.pcb_drc_result
        legacy_result = result.fingerprint in result._legacy_fingerprint_candidates()
        if (
            (not legacy_result and (result.project_id != checked_operation.project_id or result.conversation_id != checked_operation.conversation_id))
            or result.operation_id != checked_operation.operation_id
            or result.pcb_content_hash != checked_operation.pcb_content_fingerprint
            or result.pcb_artifact_ref != checked_operation.target_relative_path
            or checked_operation.source_write_receipt_fingerprint is None
        ):
            raise PCBProductionRejected("PCB DRC result binding mismatch")
        detail = self.conversation(checked_operation.project_id, checked_operation.conversation_id, tenant_id=checked_operation.tenant_id, requester=checked_operation.requester_id)
        production = detail.pcb_production
        if production is None or production.generation_receipt is None or production.generation_receipt.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED:
            raise PCBProductionRejected("PCB DRC requires parse-verified PCB generation")
        generation = production.generation_receipt
        if checked_operation.source_write_operation_id != generation.operation_id or checked_operation.source_write_receipt_fingerprint != generation.operation_receipt_fingerprint or result.pcb_content_hash != generation.artifact_fingerprint:
            raise PCBProductionRejected("PCB DRC source artifact is stale")
        engineering_status = result.engineering_status or ({PCBDrcStatus.DRC_PASS: PCBEngineeringStatus.PASS, PCBDrcStatus.DRC_WARNINGS: PCBEngineeringStatus.WARNINGS_REMAIN, PCBDrcStatus.DRC_FAIL: PCBEngineeringStatus.BLOCKED, PCBDrcStatus.DRC_BLOCKED: PCBEngineeringStatus.BLOCKED, PCBDrcStatus.DRC_UNAVAILABLE: PCBEngineeringStatus.UNAVAILABLE}[result.status])
        command_identity = result.command_identity or "kicad-cli pcb drc"
        projected_result = result
        legacy_result = result.fingerprint in result._legacy_fingerprint_candidates()
        if not legacy_result and (result.engineering_status is not engineering_status or result.command_identity != command_identity):
            projected_result = type(result).create(**{**result.model_dump(mode="python", exclude={"fingerprint", "engineering_status", "command_identity"}), "engineering_status": engineering_status, "command_identity": command_identity})
        drc_receipt = PCBDrcReceipt.create(receipt_id="pcb-drc-receipt-" + hashlib.sha256(f"{checked_operation.operation_id}|{checked_receipt.fingerprint}|{projected_result.fingerprint}".encode()).hexdigest()[:32], project_id=detail.project_id, conversation_id=detail.conversation_id, operation_id=checked_operation.operation_id, operation_receipt_fingerprint=checked_receipt.fingerprint, artifact_path=generation.artifact_path, artifact_fingerprint=generation.artifact_fingerprint, kicad_version=projected_result.kicad_version, command_identity=command_identity, raw_report_fingerprint=projected_result.raw_report_fingerprint, tool_execution_status=projected_result.tool_execution_status, engineering_status=engineering_status, result=projected_result, created_at=checked_receipt.created_at)
        state_status = {PCBDrcStatus.DRC_PASS: PCBProductionStatus.DRC_PASS, PCBDrcStatus.DRC_WARNINGS: PCBProductionStatus.DRC_WARNINGS, PCBDrcStatus.DRC_FAIL: PCBProductionStatus.DRC_FAIL, PCBDrcStatus.DRC_BLOCKED: PCBProductionStatus.DRC_PENDING, PCBDrcStatus.DRC_UNAVAILABLE: PCBProductionStatus.DRC_PENDING}[result.status]
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=state_status, generation_receipt=generation, drc_result=projected_result, drc_receipt=drc_receipt, drc_receipt_fingerprint=drc_receipt.fingerprint, drc_gate_status=engineering_status, repair_proposals=production.repair_proposals, repair_decisions=production.repair_decisions, repair_application_receipts=production.repair_application_receipts)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_pcb_repair_proposal(
        self,
        *,
        project_id: str,
        conversation_id: str,
        tenant_id: str,
        requester: str,
        proposal: object,
    ) -> ConversationDetail:
        from embedded_copilot.pcb_production import PCBRepairProposal

        try:
            checked_proposal = PCBRepairProposal.model_validate(proposal.model_dump(mode="python") if isinstance(proposal, PCBRepairProposal) else proposal, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB repair proposal is invalid") from error
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        production = detail.pcb_production
        if production is None or production.drc_result is None or production.status not in {PCBProductionStatus.DRC_FAIL, PCBProductionStatus.DRC_WARNINGS}:
            raise PCBProductionRejected("PCB repair proposal requires a current DRC result")
        issue_ids = {item.issue_id for item in production.drc_result.issues}
        if checked_proposal.base_proposal_id != production.proposal.proposal_id or checked_proposal.base_proposal_fingerprint != production.proposal.fingerprint or checked_proposal.source_fingerprint != production.drc_result.fingerprint or not set(checked_proposal.issue_refs).issubset(issue_ids):
            raise PCBProductionRejected("PCB repair proposal is stale or unbound")
        if any(item.repair_id == checked_proposal.repair_id for item in production.repair_proposals):
            return detail
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=production.status, generation_receipt=production.generation_receipt, drc_result=production.drc_result, drc_receipt=production.drc_receipt, drc_receipt_fingerprint=production.drc_receipt_fingerprint, drc_gate_status=production.drc_gate_status, repair_proposals=(*production.repair_proposals, checked_proposal), repair_decisions=production.repair_decisions, repair_application_receipts=production.repair_application_receipts)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def decide_pcb_repair(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        decision: object,
    ) -> ConversationDetail:
        from embedded_copilot.pcb_production import (
            PCBRepairDecision,
            PCBRepairDecisionAction,
        )

        try:
            checked_decision = PCBRepairDecision.model_validate(decision.model_dump(mode="python") if isinstance(decision, PCBRepairDecision) else decision, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB repair decision is invalid") from error
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        production = detail.pcb_production
        if production is None or production.drc_result is None or production.generation_receipt is None:
            raise PCBProductionRejected("PCB repair decision requires a current DRC result")
        repair = next((item for item in production.repair_proposals if item.repair_id == checked_decision.repair_id), None)
        if repair is None or repair.fingerprint != checked_decision.proposal_fingerprint or checked_decision.artifact_fingerprint != production.generation_receipt.artifact_fingerprint:
            raise PCBProductionRejected("PCB repair decision is stale or fingerprint-mismatched")
        if checked_decision.action is PCBRepairDecisionAction.WAIVE_WITH_REASON and (
            any(item.severity.value == "ERROR" for item in production.drc_result.issues)
            or repair.classification.value != "NON_BLOCKING_WARNING"
        ):
            raise PCBProductionRejected("only non-blocking warning repairs may be waived")
        if any(item.decision_id == checked_decision.decision_id for item in production.repair_decisions):
            return detail
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=production.status, generation_receipt=production.generation_receipt, drc_result=production.drc_result, drc_receipt=production.drc_receipt, drc_receipt_fingerprint=production.drc_receipt_fingerprint, drc_gate_status=production.drc_gate_status, repair_proposals=production.repair_proposals, repair_decisions=(*production.repair_decisions, checked_decision), repair_application_receipts=production.repair_application_receipts)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": checked_decision.decided_at, "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def record_pcb_repair_application(
        self,
        *,
        project_id: str,
        conversation_id: str,
        tenant_id: str,
        requester: str,
        receipt: object,
        regenerated_proposal: object,
        generation_receipt: object,
        drc_receipt: object,
    ) -> ConversationDetail:
        from embedded_copilot.pcb_production import (
            PCBDrcReceipt,
            PCBProposal,
            PCBRepairApplicationReceipt,
        )

        try:
            checked_receipt = PCBRepairApplicationReceipt.model_validate(receipt.model_dump(mode="python") if isinstance(receipt, PCBRepairApplicationReceipt) else receipt, strict=False)
            checked_proposal = PCBProposal.model_validate(regenerated_proposal.model_dump(mode="python") if isinstance(regenerated_proposal, PCBProposal) else regenerated_proposal, strict=False)
            checked_generation = PCBGenerationReceipt.model_validate(generation_receipt.model_dump(mode="python") if isinstance(generation_receipt, PCBGenerationReceipt) else generation_receipt, strict=False)
            checked_drc = PCBDrcReceipt.model_validate(drc_receipt.model_dump(mode="python") if isinstance(drc_receipt, PCBDrcReceipt) else drc_receipt, strict=False)
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB repair application lineage is invalid") from error
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        production = detail.pcb_production
        if production is None or production.generation_receipt is None or production.drc_result is None:
            raise PCBProductionRejected("PCB repair application requires current generation and DRC receipts")
        repair = next((item for item in production.repair_proposals if item.repair_id == checked_receipt.repair_id), None)
        decision = next((item for item in production.repair_decisions if item.decision_id == checked_receipt.repair_decision_id), None)
        if repair is None or decision is None or decision.action.value not in {"APPROVE_FIX", "EDIT_FIX"} or checked_receipt.status.value != "APPLIED":
            raise PCBProductionRejected("PCB repair application is not approved")
        if checked_receipt.project_id != detail.project_id or checked_receipt.conversation_id != detail.conversation_id or checked_generation.project_id != detail.project_id or checked_generation.conversation_id != detail.conversation_id or checked_drc.project_id != detail.project_id or checked_drc.conversation_id != detail.conversation_id or checked_receipt.repair_proposal_fingerprint != repair.fingerprint or checked_receipt.repair_decision_fingerprint != decision.fingerprint or checked_receipt.pre_proposal_fingerprint != production.proposal.fingerprint or checked_receipt.pre_artifact_fingerprint != production.generation_receipt.artifact_fingerprint or checked_proposal.request_id != production.request.request_id or checked_proposal.project_id != production.project_id or checked_generation.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED or checked_drc.engineering_status not in {PCBEngineeringStatus.PASS, PCBEngineeringStatus.WARNINGS_REMAIN} or checked_drc.artifact_fingerprint != checked_generation.artifact_fingerprint or checked_receipt.post_proposal_fingerprint != checked_proposal.fingerprint or checked_receipt.post_artifact_fingerprint != checked_generation.artifact_fingerprint or checked_receipt.post_generation_receipt_fingerprint != checked_generation.fingerprint or checked_receipt.post_parse_validation_fingerprint != checked_generation.parse_validation.fingerprint or checked_receipt.post_drc_receipt_fingerprint != checked_drc.fingerprint or checked_receipt.post_drc_result_fingerprint != checked_drc.result.fingerprint or checked_receipt.artifact_path != checked_generation.artifact_path:
            raise PCBProductionRejected("PCB repair application is stale or fingerprint-mismatched")
        if any(item.receipt_id == checked_receipt.receipt_id for item in production.repair_application_receipts):
            return detail
        approved = decide_pcb_proposal(checked_proposal, decision=PCBProposalDecisionStatus.APPROVED, reviewer=decision.reviewer, decided_at=decision.decided_at)
        state_status = PCBProductionStatus.DRC_PASS if checked_drc.engineering_status is PCBEngineeringStatus.PASS else PCBProductionStatus.DRC_WARNINGS
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=checked_proposal, decision=approved, operation_id=checked_generation.operation_id, operation_fingerprint=checked_generation.operation_fingerprint, status=state_status, generation_receipt=checked_generation, drc_result=checked_drc.result, drc_receipt=checked_drc, drc_receipt_fingerprint=checked_drc.fingerprint, drc_gate_status=checked_drc.engineering_status, repair_proposals=production.repair_proposals, repair_decisions=production.repair_decisions, repair_application_receipts=(*production.repair_application_receipts, checked_receipt))
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": checked_receipt.applied_at, "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def mark_pcb_production_stale(self, *, project_id: str, conversation_id: str, tenant_id: str, requester: str, reason: str) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        production = detail.pcb_production
        if production is None or production.generation_receipt is None:
            return detail
        stale_generation = PCBGenerationReceipt.create(**{**production.generation_receipt.model_dump(mode="python", exclude={"fingerprint", "status", "validation_status", "parse_validation"}), "status": "STALE", "validation_status": PCBParseValidationStatus.FAILED})
        next_state = PCBProductionState.create(production_id=production.production_id, project_id=production.project_id, conversation_id=production.conversation_id, request=production.request, proposal=production.proposal, decision=production.decision, operation_id=production.operation_id, operation_fingerprint=production.operation_fingerprint, status=PCBProductionStatus.STALE, stale_reason=reason, generation_receipt=stale_generation)
        updated = detail.model_copy(update={"pcb_production": next_state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def create_pcb_production_proposal(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        pcb_state = detail.pcb_engineering_state
        self.require_current_circuit_binding(
            project_id, None if pcb_state is None else pcb_state.intent_source_binding,
            tenant_id=tenant_id, requester=requester,
        )
        if detail.pcb_production is not None:
            return detail
        request = detail.pcb_production_request
        service = self._pcb_production_service
        if request is None or service is None:
            raise PCBProductionRejected("PCB production precondition is unavailable")
        if request.requester != requester:
            raise PCBProductionRejected("PCB production requester binding mismatch")
        try:
            proposal = service.create_proposal(request)
            state = create_pcb_production_state(request, proposal)
        except (TypeError, ValueError, OSError) as error:
            raise PCBProductionRejected("PCB proposal creation rejected") from error
        updated = detail.model_copy(
            update={
                "pcb_production": state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def selection_revision_view(self, project_id, conversation_id, *, tenant_id, requester, view="CURRENT", revision_id=None, schema_version=2, parent_choices=None):
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        return SelectionRevisionLifecycle(self).view(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester, view=view, revision_id=revision_id, schema_version=schema_version, parent_choices=parent_choices)

    def create_hardware_selection_proposal_v2(self, project_id, conversation_id, *, tenant_id, requester, request_id, expected_head, preview_fingerprint, selections, schema_version=2, parent_choices=None):
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        return SelectionRevisionLifecycle(self).create(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester, request_id=request_id,
            expected_head=expected_head, preview_fingerprint=preview_fingerprint, selections=selections, schema_version=schema_version, parent_choices=parent_choices)

    def decide_hardware_selection_v2(self, project_id, conversation_id, *, tenant_id, requester, revision_id, selection_id, selection_fingerprint, expected_decision_fingerprint, decision):
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        return SelectionRevisionLifecycle(self).decide(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester, revision_id=revision_id,
            selection_id=selection_id, selection_fingerprint=selection_fingerprint,
            expected_decision_fingerprint=expected_decision_fingerprint, decision=decision)

    def create_hardware_selection_proposal(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        if SelectionRevisionLifecycle(self).head(detail) is not None:
            raise ProjectCatalogConflict("SELECTION_REVISION_SELECTOR_REQUIRED")
        existing = detail.hardware_selection
        if existing is not None:
            return detail
        source = self.hardware_component_source_for(detail)
        architecture = detail.architecture_design
        requirements = detail.requirement_context
        workflow = detail.engineering_workflow
        if source is None or architecture is None or requirements is None or workflow is None:
            raise HardwareSelectionRejected(
                "hardware selection precondition is unavailable"
            )
        if detail.architecture_review is None:
            raise HardwareSelectionRejected(
                "approved architecture review is unavailable"
            )
        try:
            proposal = HardwareSelectionService(source).create_proposal(
                architecture,
                requirements,
                workflow=workflow,
                conversation_id=detail.conversation_id,
            )
            state = HardwareSelectionState(
                proposal=proposal,
                status=HardwareSelectionStatus.REVIEW,
            )
        except (TypeError, ValueError, OSError) as error:
            raise HardwareSelectionRejected(
                "hardware selection proposal rejected"
            ) from error
        updated = detail.model_copy(
            update={
                "hardware_selection": state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def _project_supply_basis(self, detail: ConversationDetail, *, circuit_id: str,
                              circuit_fingerprint: str):
        """Re-read the existing Owner, reviewed plan, selected sources and Circuit."""
        plan, review, requirements = detail.engineering_plan, detail.engineering_plan_review, detail.requirement_context
        architecture, architecture_review = detail.architecture_design, detail.architecture_review
        selection = detail.hardware_selection
        if (plan is None or review is None or requirements is None or architecture is None
            or architecture_review is None or selection is None
            or plan.model_operation_schema_version != 2 or plan.status is not PlanStatus.APPROVED
            or review.status is not PlanReviewStatus.APPROVED
            or review.plan_id != plan.plan_id or review.project_id != plan.project_id
            or review.proposal_fingerprint != plan.content_fingerprint
            or plan.context_fingerprint != requirements.fingerprint
            or architecture.status is not ArchitectureDesignStatus.APPROVED
            or architecture_review.status.value != "APPROVED"
            or architecture.plan_id != plan.plan_id or architecture.plan_fingerprint != plan.fingerprint
            or architecture.context_fingerprint != requirements.fingerprint
            or architecture_review.architecture_id != architecture.architecture_id
            or architecture_review.proposal_fingerprint != architecture.content_fingerprint
            or selection.status is not HardwareSelectionStatus.APPROVED
            or selection.approval is None or selection.hardware_truth is None or selection.bom is None
            or selection.proposal.architecture_id != architecture.architecture_id
            or selection.proposal.architecture_fingerprint != architecture.fingerprint
            or selection.proposal.requirements_fingerprint != requirements.fingerprint):
            raise ProjectCatalogConflict("project supply approved source binding is stale")
        self._require_current_model_selection(selection, detail=detail)
        source = self.hardware_component_source_for(detail)
        if source is None:
            raise ProjectCatalogUnavailable("project supply source port is unavailable")
        truth = HardwareSelectionService(source).project_design_truth(
            DesignTruthBuilder().build_from_approved_architecture(
                architecture, tenant_id=detail.tenant_id, conversation_id=detail.conversation_id,
                architecture_review=architecture_review,
            ), selection.hardware_truth, architecture_review=architecture_review,
        )
        if not circuit_id or not circuit_fingerprint:
            raise ProjectCatalogConflict("project supply Circuit selector is missing")
        intents = self.current_circuit_intents(
            detail.project_id, tenant_id=detail.tenant_id, requester=detail.requester,
        )
        matches = tuple(item for item in intents if item.object_id == circuit_id
                        and item.fingerprint == circuit_fingerprint)
        if len(matches) != 1:
            raise ProjectCatalogConflict("project supply Circuit selector is stale")
        circuit = matches[0]
        self.require_current_circuit_binding(
            detail.project_id, circuit, tenant_id=detail.tenant_id, requester=detail.requester,
        )
        return plan, requirements, selection, truth, circuit

    @staticmethod
    def _project_supply_range(detail: ConversationDetail) -> tuple[Decimal, Decimal]:
        requirements = detail.requirement_context
        if requirements is None:
            raise ProjectCatalogConflict("project supply reviewed range unavailable")
        values: list[Decimal] = []
        turn_ids = {item.turn_id for item in detail.turns}
        for category in ("S02_VOLTAGE_MIN_V", "S02_VOLTAGE_MAX_V"):
            fields = tuple(item for item in requirements.standard_fields if item.category == category)
            if (len(fields) != 1 or fields[0].status.value != "CONFIRMED"
                or fields[0].source_turn_id is None or fields[0].source_turn_id not in turn_ids
                or fields[0].provenance.value not in {"USER_UTTERANCE", "PROJECT_METADATA"}):
                raise ProjectCatalogConflict("project supply reviewed range provenance unavailable")
            values.append(Decimal(decimal_text(fields[0].value)))
        if values[0] <= 0 or values[0] > values[1]:
            raise ProjectCatalogConflict("project supply reviewed voltage range is invalid")
        return values[0], values[1]

    @staticmethod
    def _project_supply_evidence(detail: ConversationDetail, truth: DesignTruth,
                                 relation: ProjectSupplyRelationV2) -> None:
        architecture = detail.architecture_design
        selection = detail.hardware_selection
        if architecture is None or selection is None:
            raise ProjectCatalogConflict("project supply evidence source unavailable")
        components = {item.component_id for item in truth.components}
        referenced = set(relation.source_component_refs + relation.consumer_component_refs)
        if not referenced <= components:
            raise ProjectCatalogConflict("project supply component reference is stale")
        bindings = {item.binding_id: item for item in truth.evidence_bindings}
        selected_sources = {(item.source.source_id, item.source.fingerprint): item.source
                            for item in selection.proposal.selected_components}
        architecture_sources = {item.component_id: item.fingerprint for item in architecture.system_components}
        architecture_sources[architecture.architecture_id] = architecture.fingerprint
        covered: set[str] = set()
        for ref in relation.evidence_refs:
            binding = bindings.get(ref)
            if binding is None or binding.project_id != detail.project_id or binding.entity_id not in referenced:
                raise ProjectCatalogConflict("project supply evidence entity is stale")
            if binding.source is DesignSource.DATASHEET:
                current = (binding.source_id, binding.source_fingerprint) in selected_sources
                current = current and binding.artifact_ref == binding.source_id
            elif binding.source is DesignSource.ARCHITECTURE:
                current = (architecture_sources.get(binding.source_id) == binding.source_fingerprint
                           and binding.artifact_ref == architecture.architecture_id)
            else:
                current = False
            if not current:
                raise ProjectCatalogConflict("project supply evidence source is stale")
            covered.add(binding.entity_id)
        if covered != referenced:
            raise ProjectCatalogConflict("project supply evidence component coverage is incomplete")

    @staticmethod
    def _approved_source_heads(detail: ConversationDetail) -> tuple[tuple[str, str], ...]:
        state = detail.project_supply
        if (type(state) is not ProjectSupplyStateV3 or state.review is None
                or state.review.decision != "APPROVED"):
            raise ProjectCatalogConflict("approved published source proof is unavailable")
        return tuple((item.source_id, item.publication_fingerprint)
                     for item in state.candidate.source_publications)

    def _source_publication_bindings(self, detail: ConversationDetail) -> tuple[SourcePublicationBindingV1, ...]:
        source = self.hardware_component_source_for(detail)
        selection = detail.hardware_selection
        if (type(source) is not PublishedComponentSourcePort
                or source.repository is not self._repository
                or source.tenant_id != detail.tenant_id
                or selection is None or selection.status is not HardwareSelectionStatus.APPROVED):
            raise ProjectCatalogConflict("trusted published component source is unavailable")
        requirements = {item.slot_id: item for item in selection.proposal.requirements}
        bindings: dict[str, SourcePublicationBindingV1] = {}
        for selected in selection.proposal.selected_components:
            record = selected.source
            publication = source.current_publication(detail.project_id, record.source_id)
            if (publication is None or publication.tenant_id != detail.tenant_id
                    or publication.project_id != detail.project_id
                    or publication.publisher_id != source.publisher_id
                    or publication.record.fingerprint != record.fingerprint
                    or publication.record != record):
                raise ProjectCatalogConflict("selected component publication is stale")
            requirement = requirements.get(selected.slot_id)
            if requirement is None:
                raise ProjectCatalogConflict("selected component slot is invalid")
            matches = tuple(item for item in source.search(
                detail.project_id, selection.proposal.architecture_id, requirement,
            ) if item.source_id == record.source_id)
            if len(matches) != 1 or matches[0] != publication.record:
                raise ProjectCatalogConflict("published source search changed")
            if source.current_publication(detail.project_id, record.source_id) != publication:
                raise ProjectCatalogConflict("selected source head changed during search")
            binding = SourcePublicationBindingV1(
                source_id=record.source_id, source_record_fingerprint=record.fingerprint,
                publisher_id=publication.publisher_id, publication_id=publication.publication_id,
                generation=publication.generation, publication_fingerprint=publication.fingerprint,
            )
            previous = bindings.setdefault(record.source_id, binding)
            if previous != binding:
                raise ProjectCatalogConflict("selected source is ambiguous")
        if not bindings:
            raise ProjectCatalogConflict("selected source publication is unavailable")
        for source_id, binding in bindings.items():
            publication = source.current_publication(detail.project_id, source_id)
            if publication is None or publication.fingerprint != binding.publication_fingerprint:
                raise ProjectCatalogConflict("selected source head changed")
        return tuple(bindings[source_id] for source_id in sorted(bindings))

    def _project_supply_checked(self, detail: ConversationDetail,
                                candidate: ProjectSupplyCandidateV2 | ProjectSupplyCandidateV3, *,
                                require_review: bool = False,
                                plan_id_override: str | None = None):
        if require_review and type(candidate) is not ProjectSupplyCandidateV3:
            raise ProjectCatalogConflict("legacy project supply lacks source publication proof")
        if (candidate.tenant_id != detail.tenant_id or candidate.project_id != detail.project_id
            or candidate.conversation_id != detail.conversation_id):
            raise ProjectCatalogConflict("project supply scope mismatch")
        evidence_fingerprints: tuple[str, ...] = ()
        if require_review:
            state = detail.project_supply
            review = None if state is None else state.review
            if (state is None or state.candidate.fingerprint != candidate.fingerprint
                or review is None or review.decision != "APPROVED"
                or (review.tenant_id, review.project_id, review.conversation_id) != (
                    detail.tenant_id, detail.project_id, detail.conversation_id)
                or review.candidate_id != candidate.candidate_id
                or review.candidate_fingerprint != candidate.fingerprint):
                raise ProjectCatalogConflict("project supply approved review binding is stale")
            evidence_fingerprints = (
                *(relation.fingerprint for relation in candidate.relations),
                candidate.fingerprint, review.fingerprint,
            )
        plan, requirements, selection, truth, circuit = self._project_supply_basis(
            detail, circuit_id=candidate.circuit_id, circuit_fingerprint=candidate.circuit_fingerprint,
        )
        if (candidate.plan_id != plan.plan_id or candidate.plan_fingerprint != plan.fingerprint
            or candidate.selection_id != selection.proposal.proposal_id
            or candidate.selection_fingerprint != selection.proposal.fingerprint
            or candidate.design_truth_id != truth.truth_id
            or candidate.design_truth_fingerprint != truth.fingerprint):
            raise ProjectCatalogConflict("project supply source selector is stale")
        minimum, maximum = self._project_supply_range(detail)
        for relation in candidate.relations:
            nominal = Decimal(relation.nominal_voltage_v)
            if not minimum <= nominal <= maximum:
                raise ProjectCatalogConflict("project supply nominal is outside reviewed range")
            self._project_supply_evidence(detail, truth, relation)
            prior_domain = next((item for item in truth.power_domains
                                 if item.power_domain_id == relation.power_domain_id), None)
            if prior_domain is not None and set(prior_domain.component_refs) != set(
                    relation.source_component_refs + relation.consumer_component_refs):
                raise ProjectCatalogConflict("project supply DesignTruth domain conflicts")
        planning = detail.schematic_planning
        if planning is None:
            anchor = self._repository.selection_schematic_plan_anchor(tenant_id=detail.tenant_id,
                project_id=detail.project_id,conversation_id=detail.conversation_id,requester=detail.requester)
            if anchor is not None:
                planning = anchor["planning"]
        target_path = ("hardware/main.kicad_sch" if planning is None
                       else planning.proposal.target.relative_path)
        service = SchematicPlanningService()
        head = self._repository.schematic_plan_revision_head(
            tenant_id=detail.tenant_id, project_id=detail.project_id,
            conversation_id=detail.conversation_id, requester=detail.requester,
        )
        revision_plan_id = plan_id_override if plan_id_override is not None else (
            None if head is None else head["plan_id"]
        )
        proposal = service.create_proposal(
            detail.architecture_design, requirements, selection.hardware_truth, selection.bom,
            design_truth=truth, conversation_id=detail.conversation_id,
            target_relative_path=target_path, circuit_intent=circuit, selection=selection,
            _project_supply_relations=candidate.relations,
            _project_supply_evidence_fingerprints=evidence_fingerprints,
            _plan_id=revision_plan_id,
        )
        if (planning is None or planning.status is not SchematicPlanningStatus.APPROVED) and head is None and plan_id_override is None:
            if detail.design_truth is not None and detail.design_truth.fingerprint != truth.fingerprint:
                raise ProjectCatalogConflict("project supply DesignTruth changed")
        net_ids = {item.net_id for item in proposal.nets}
        if any(relation.supply_net_ref not in net_ids or relation.ground_net_ref not in net_ids
               for relation in candidate.relations):
            raise ProjectCatalogConflict("project supply net reference is stale")
        for net in truth.nets:
            if net.binding_id in {ref for relation in candidate.relations
                                  for ref in (relation.supply_net_ref, relation.ground_net_ref)}:
                current = next((item for item in proposal.nets if item.net_id == net.binding_id), None)
                if (current is None or net.net_name != current.name
                    or net.signal != current.signal_id or net.nodes != current.nodes):
                    raise ProjectCatalogConflict("project supply DesignTruth net conflicts")
        self._require_current_model_selection(selection, detail=detail)
        if type(candidate) is ProjectSupplyCandidateV3 and candidate.source_publications != self._source_publication_bindings(detail):
            raise ProjectCatalogConflict("project supply source publication changed")
        self.require_current_circuit_binding(detail.project_id, circuit,
            tenant_id=detail.tenant_id, requester=detail.requester)
        latest = self.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester)
        if latest.fingerprint != detail.fingerprint:
            raise ProjectCatalogConflict("project supply conversation changed during validation")
        return proposal, truth, circuit

    @staticmethod
    def _project_supply_plan_currentness(detail: ConversationDetail, proposal: object,
                                         base_truth: DesignTruth) -> None:
        planning = detail.schematic_planning
        if planning is None or planning.proposal.fingerprint != proposal.fingerprint:
            raise ProjectCatalogConflict("project supply schematic proposal is stale")
        if planning.status is SchematicPlanningStatus.APPROVED:
            approval, approved_plan = planning.approval, planning.approved_plan
            if (approval is None or approved_plan is None
                or approval.decision is not SchematicPlanDecision.APPROVED):
                raise ProjectCatalogConflict("project supply schematic approval is stale")
            service = SchematicPlanningService()
            expected = service.decide(proposal, SchematicPlanDecision.APPROVED,
                                      reviewer=approval.reviewer, decided_at=approval.decided_at)
            if (expected.approval.fingerprint != approval.fingerprint
                or expected.approved_plan.fingerprint != approved_plan.fingerprint):
                raise ProjectCatalogConflict("project supply schematic approval changed")
            projected = service.project_design_truth(base_truth, approved_plan)
            if detail.design_truth is None or detail.design_truth.fingerprint != projected.fingerprint:
                raise ProjectCatalogConflict("project supply approved DesignTruth changed")

    def create_project_supply_candidate(self, project_id: str, conversation_id: str, *,
                                        tenant_id: str, requester: str, selectors: dict[str, object],
                                        relations: tuple[dict[str, object], ...]) -> ProjectSupplyStateV2:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        checked_relations = tuple(ProjectSupplyRelationV2.model_validate(item) for item in relations)
        bindings = self._source_publication_bindings(detail)
        candidate = ProjectSupplyCandidateV3(
            schema_version=3, candidate_id=f"project-supply-{uuid4()}",
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            relations=checked_relations, source_publications=bindings,
            created_at=self._now(), **selectors,
        )
        self._project_supply_checked(detail, candidate)
        state = ProjectSupplyStateV3(schema_version=3, candidate=candidate)
        updated = detail.model_copy(update={"project_supply": state, "updated_at": self._now(),
                                            "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=tuple((item.source_id, item.publication_fingerprint)
                                               for item in bindings),
        )
        return state

    def review_project_supply_candidate(self, project_id: str, conversation_id: str,
                                        candidate_id: str, candidate_fingerprint: str,
                                        decision: str, *, tenant_id: str, requester: str) -> ProjectSupplyStateV2:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        state = detail.project_supply
        if (state is None or state.candidate.candidate_id != candidate_id
            or state.candidate.fingerprint != candidate_fingerprint or state.review is not None):
            raise ProjectCatalogConflict("project supply candidate review binding is stale")
        self._project_supply_checked(detail, state.candidate)
        review = ProjectSupplyReviewV2(
            schema_version=2, review_id=f"project-supply-review-{uuid4()}",
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            candidate_id=candidate_id, candidate_fingerprint=candidate_fingerprint,
            decision=decision, reviewer_id=requester, decided_at=self._now(),
        )
        if type(state) is not ProjectSupplyStateV3:
            raise ProjectCatalogConflict("legacy project supply cannot receive a new review")
        reviewed = ProjectSupplyStateV3(schema_version=3, candidate=state.candidate, review=review)
        updated = detail.model_copy(update={"project_supply": reviewed, "updated_at": self._now(),
                                            "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=tuple((item.source_id, item.publication_fingerprint)
                                               for item in state.candidate.source_publications),
        )
        return self.conversation(project_id, conversation_id, tenant_id=tenant_id,
                                 requester=requester).project_supply

    def project_supply(self, project_id: str, conversation_id: str, *, tenant_id: str,
                       requester: str) -> dict[str, object]:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        options: tuple[dict[str, str], ...] = ()
        try:
            intents = tuple(item for item in self.current_circuit_intents(project_id, tenant_id=tenant_id, requester=requester)
                if self.circuit_source_current(project_id,item.object_id,item.revision,tenant_id=tenant_id,requester=requester))
            if len(intents) == 1:
                _plan, _requirements, selection, truth, _circuit = self._project_supply_basis(
                    detail, circuit_id=intents[0].object_id,
                    circuit_fingerprint=intents[0].fingerprint,
                )
                self._project_supply_range(detail)
                options = _project_supply_net_options(selection.hardware_truth, truth)
                if self.conversation(project_id, conversation_id,
                    tenant_id=tenant_id, requester=requester).fingerprint != detail.fingerprint:
                    options = ()
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError):
            options = ()
        state = detail.project_supply
        if state is None:
            return {"status": "UNAVAILABLE", "current_options": options}
        try:
            if type(state) is not ProjectSupplyStateV3:
                raise ProjectCatalogConflict("legacy source has no publication proof")
            self._project_supply_checked(detail, state.candidate)
            status = "REVIEW" if state.review is None else state.review.decision
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError):
            status = "STALE"
        return {"status": status, "candidate": state.candidate.model_dump(mode="json"),
                "review": None if state.review is None else state.review.model_dump(mode="json"),
                "current_options": options}

    @staticmethod
    def _standard_required_conditions(detail: ConversationDetail, package: str):
        from embedded_copilot.engineering_copilot.schematic_evidence import ParameterConditions, decimal_text

        requirements = detail.requirement_context
        if requirements is None:
            raise ProjectCatalogConflict("standard requirement unavailable")
        turns = {item.turn_id for item in detail.turns}
        fields = {}
        for category in (
            "S02_VOLTAGE_MIN_V", "S02_VOLTAGE_MAX_V",
            "S02_TEMPERATURE_MIN_C", "S02_TEMPERATURE_MAX_C",
            "S02_INTERFACE_MODE",
        ):
            found = tuple(item for item in requirements.standard_fields if item.category == category)
            if (len(found) != 1 or found[0].status.value != "CONFIRMED"
                or found[0].source_turn_id not in turns
                or found[0].provenance.value not in {"USER_UTTERANCE", "PROJECT_METADATA"}):
                raise ProjectCatalogConflict("standard reviewed conditions unavailable")
            fields[category] = found[0].value
        if fields["S02_INTERFACE_MODE"] != "I2C_STANDARD":
            raise ProjectCatalogConflict("standard reviewed mode is unsupported")
        required = ParameterConditions(
            voltage_min=decimal_text(fields["S02_VOLTAGE_MIN_V"]),
            voltage_max=decimal_text(fields["S02_VOLTAGE_MAX_V"]),
            temperature_min=decimal_text(fields["S02_TEMPERATURE_MIN_C"]),
            temperature_max=decimal_text(fields["S02_TEMPERATURE_MAX_C"]),
            mode="I2C_STANDARD", package=package,
        )
        if not all((required.voltage_min, required.voltage_max,
                    required.temperature_min, required.temperature_max)):
            raise ProjectCatalogConflict("standard reviewed conditions incomplete")
        return required

    def _standard_applicability_checked(
        self, detail: ConversationDetail, evidence: StandardApplicabilityEvidenceV2,
    ) -> None:
        if (evidence.tenant_id, evidence.project_id, evidence.conversation_id) != (
            detail.tenant_id, detail.project_id, detail.conversation_id
        ):
            raise ProjectCatalogConflict("standard evidence scope changed")
        supply = detail.project_supply
        if supply is None or supply.review is None or supply.review.decision != "APPROVED":
            raise ProjectCatalogConflict("standard supply relation unavailable")
        proposal, _truth, circuit = self._project_supply_checked(detail, supply.candidate, require_review=True)
        plan, requirements, selection = (
            detail.engineering_plan, detail.requirement_context, detail.hardware_selection
        )
        if (
            plan is None or requirements is None or selection is None
            or evidence.requirement_id != plan.requirement_id
            or evidence.requirement_fingerprint != requirements.fingerprint
            or evidence.plan_id != plan.plan_id or evidence.plan_fingerprint != plan.fingerprint
        ):
            raise ProjectCatalogConflict("standard plan or requirement changed")
        nets = {item.net_id: item for item in proposal.nets}
        instances = {item.instance_id: item for item in proposal.component_instances}
        selected = {item.slot_id: item.source for item in selection.proposal.selected_components}
        matching = []
        for bus in proposal.interfaces:
            if bus.protocol.upper() not in {"I2C", "I²C"}:
                continue
            bus_net_ids = {
                net.net_id for net in proposal.nets if net.signal_id in set(bus.signal_ids)
            }
            bus_instances = {
                item.instance_id for item in proposal.component_instances
                if item.design_truth_component_id in set(bus.participants)
            }
            if (len(bus_net_ids) == len(set(bus.signal_ids))
                and len(bus_instances) == len(set(bus.participants))
                and set(evidence.target_net_ids) == bus_net_ids
                and set(item.component_instance_id for item in evidence.target_components)
                == bus_instances):
                matching.append(bus)
        if len(matching) != 1 or any(net_id not in nets for net_id in evidence.target_net_ids):
            raise ProjectCatalogConflict("standard target bus coverage changed")
        port = self._schematic_parameter_port
        if port is None:
            raise ProjectCatalogUnavailable("standard original reader unavailable")
        for target in evidence.target_components:
            instance = instances.get(target.component_instance_id)
            source = None if instance is None else selected.get(instance.hardware_slot_id)
            if (source is None or target.source_record_id != source.source_id
                or target.source_record_fingerprint != source.fingerprint):
                raise ProjectCatalogConflict("standard target source changed")
            required = self._standard_required_conditions(detail, source.package)
            try:
                observed = port.read_device_conditions(
                    source, tenant_id=detail.tenant_id, project_id=detail.project_id,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
                raise ProjectCatalogUnavailable("standard target original unavailable") from error
            if observed != target.device_conditions or not observed.covers(required):
                raise ProjectCatalogConflict("standard target conditions unsupported")
        try:
            observed_fact = port.read_standard_fact(
                tenant_id=detail.tenant_id, project_id=detail.project_id,
                document_uri=evidence.document_uri,
                document_revision=evidence.document_revision,
                content_sha256=evidence.content_sha256,
                locator=evidence.locator, mode=evidence.mode,
                frequency_max_hz=evidence.frequency_max_hz,
                rise_time_max_s=evidence.rise_time_max_s,
                cbus_max_f=evidence.cbus_max_f,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("standard document original unavailable") from error
        if (
            observed_fact.document_uri != evidence.document_uri
            or observed_fact.document_revision != evidence.document_revision
            or observed_fact.content_sha256 != evidence.content_sha256
            or observed_fact.locator != evidence.locator
            or observed_fact.mode != evidence.mode
            or observed_fact.frequency_max_hz != evidence.frequency_max_hz
            or observed_fact.rise_time_max_s != evidence.rise_time_max_s
            or observed_fact.cbus_max_f != evidence.cbus_max_f
        ):
            raise ProjectCatalogConflict("standard fact changed")
        self._require_current_model_selection(selection, detail=detail)
        self.require_current_circuit_binding(detail.project_id, circuit,
            tenant_id=detail.tenant_id, requester=detail.requester)
        latest = self.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester)
        if latest.fingerprint != detail.fingerprint:
            raise ProjectCatalogConflict("standard Owner changed during validation")

    def create_standard_applicability(
        self, project_id: str, conversation_id: str, *, tenant_id: str,
        requester: str, values: dict[str, object],
    ) -> ReviewedStandardApplicabilityV2:
        detail = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        evidence = StandardApplicabilityEvidenceV2(
            schema_version=2, evidence_id=f"standard-applicability-{uuid4()}",
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            created_at=self._now(), **values,
        )
        self._standard_applicability_checked(detail, evidence)
        existing = detail.standard_applicability or ()
        updated = detail.model_copy(update={
            "standard_applicability": (*existing, ReviewedStandardApplicabilityV2(evidence=evidence)),
            "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64,
        })
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
        )
        return checked.standard_applicability[-1]

    def review_standard_applicability(
        self, project_id: str, evidence_id: str, *, tenant_id: str,
        requester: str, evidence_fingerprint: str, decision: str,
    ) -> ReviewedStandardApplicabilityV2:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        matches = [
            (detail, index, item)
            for detail in self._repository.conversations(project_id, tenant_id, requester)
            for index, item in enumerate(detail.standard_applicability or ())
            if item.evidence.evidence_id == evidence_id
        ]
        if len(matches) != 1:
            raise ProjectCatalogUnavailable("standard evidence unavailable or ambiguous")
        detail, index, state = matches[0]
        if state.evidence.fingerprint != evidence_fingerprint:
            raise ProjectCatalogConflict("standard evidence fingerprint changed")
        self._standard_applicability_checked(detail, state.evidence)
        if state.review is not None:
            if state.review.decision != decision:
                raise ProjectCatalogConflict("standard review is immutable")
            return state
        review = StandardApplicabilityReviewV2(
            schema_version=2, evidence_id=evidence_id,
            evidence_fingerprint=evidence_fingerprint,
            review_id=f"standard-review-{uuid4()}",
            tenant_id=tenant_id, project_id=project_id,
            conversation_id=detail.conversation_id, decision=decision,
            reviewer_id=requester, decided_at=self._now(),
        )
        reviewed = ReviewedStandardApplicabilityV2(evidence=state.evidence, review=review)
        records = list(detail.standard_applicability)
        records[index] = reviewed
        updated = detail.model_copy(update={
            "standard_applicability": tuple(records),
            "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64,
        })
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
        )
        return checked.standard_applicability[index]

    def standard_applicability(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
    ) -> tuple[tuple[ReviewedStandardApplicabilityV2, str], ...]:
        detail = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        output = []
        for state in detail.standard_applicability or ():
            try:
                self._standard_applicability_checked(detail, state.evidence)
                currentness = "REVIEW" if state.review is None else state.review.decision
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                currentness = "STALE"
            output.append((state, currentness))
        return tuple(output)

    def _validate_private_schematic_definitions(
        self, *, production_request, approved_schematic_plan, hardware_selection,
        tenant_id: str, symbol_definitions, expected_binding=None,
    ) -> tuple[tuple[str, str, str], ...]:
        """Join reviewed project originals to the exact serializer definitions."""
        from embedded_copilot.execution_worker.schematic_erc import SchematicSymbolLibraryValidator
        from embedded_copilot.engineering_copilot import SchematicProductionRequest

        request = SchematicProductionRequest.model_validate(production_request.model_dump(mode="python"))
        detail = self.conversation(request.project_id, request.conversation_id,
            tenant_id=tenant_id, requester=request.requester)
        planning, production = detail.schematic_planning, detail.schematic_production
        plan = None if planning is None else planning.approved_plan
        if (plan is None or planning.status is not SchematicPlanningStatus.APPROVED
            or plan != approved_schematic_plan or detail.hardware_selection != hardware_selection
            or production is None or production.request != request
            or request.schematic_plan_id != plan.plan_id
            or request.schematic_plan_fingerprint != plan.fingerprint
            or detail.design_truth is None
            or request.design_truth_fingerprint != detail.design_truth.fingerprint
            or self._schematic_plan_source_currentness(detail, plan, project_id=detail.project_id,
                tenant_id=tenant_id, requester=request.requester) != "CURRENT"
            or type(symbol_definitions) is not dict
            or any(type(key) is not str or type(value) is not str for key,value in symbol_definitions.items())):
            raise ProjectCatalogConflict("private schematic definition context changed")
        self._require_current_model_selection(hardware_selection, detail=detail)
        instances = sorted((item for item in plan.component_instances
            if item.symbol == "SHTC3_EP:SHTC3_EP"), key=lambda item: item.instance_id)
        if not instances or len(instances) > 128:
            raise ProjectCatalogConflict("private schematic definition context unavailable")
        token = []
        for instance in instances:
            matches = []
            for state in detail.library_terminal_maps or ():
                if (state.evidence.component_instance_id != instance.instance_id
                    or state.review is None or state.review.decision != "APPROVED"):
                    continue
                try:
                    originals = self._library_terminal_map_checked(detail, state.evidence)
                except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    continue
                matches.append((state, originals))
            if len(matches) != 1:
                raise ProjectCatalogConflict("private schematic definition review unavailable or ambiguous")
            state, originals = matches[0]
            source = originals.symbol_bytes.decode("utf-8", errors="strict")
            symbol = instance.symbol.split(":", 1)[1]
            definition = SchematicSymbolLibraryValidator._extract_symbol_definition(source, symbol)
            if definition is None:
                raise ProjectCatalogConflict("private schematic symbol unavailable")
            expected = SchematicSymbolLibraryValidator._expand_symbol_definition(
                source, definition, binding=instance.symbol, symbol=symbol)
            if symbol_definitions.get(instance.symbol) != expected:
                raise ProjectCatalogConflict("private schematic definition original mismatch")
            token.append((instance.instance_id, state.evidence.fingerprint, state.review.fingerprint))
        binding = tuple(token)
        if expected_binding is not None and binding != expected_binding:
            raise ProjectCatalogConflict("private schematic definition review generation changed")
        if self.conversation(detail.project_id, detail.conversation_id,
            tenant_id=tenant_id, requester=request.requester).fingerprint != detail.fingerprint:
            raise ProjectCatalogConflict("private schematic definition Owner changed during read")
        return binding

    def _library_terminal_map_checked(
        self, detail: ConversationDetail, evidence: LibraryTerminalMapEvidenceV2,
    ):
        if (evidence.tenant_id, evidence.project_id, evidence.conversation_id) != (
            detail.tenant_id, detail.project_id, detail.conversation_id
        ):
            raise ProjectCatalogConflict("library map scope changed")
        planning, selection = detail.schematic_planning, detail.hardware_selection
        plan = None if planning is None else planning.approved_plan
        if (plan is None or planning.status is not SchematicPlanningStatus.APPROVED
            or planning.approval is None or selection is None
            or selection.status is not HardwareSelectionStatus.APPROVED
            or evidence.plan_id != plan.plan_id or evidence.plan_fingerprint != plan.fingerprint
            or self._schematic_plan_source_currentness(detail, plan,
                project_id=detail.project_id, tenant_id=detail.tenant_id,
                requester=detail.requester) != "CURRENT"):
            raise ProjectCatalogConflict("library map approved plan changed")
        instance = next((item for item in plan.component_instances
            if item.instance_id == evidence.component_instance_id), None)
        source = None if instance is None else next((item.source
            for item in selection.proposal.selected_components
            if item.slot_id == instance.hardware_slot_id), None)
        if (source is None or source.source_id != evidence.source_record_id
            or source.fingerprint != evidence.source_record_fingerprint
            or source.symbol != evidence.symbol_library_id or source.footprint != evidence.footprint_library_id
            or instance.symbol != evidence.symbol_library_id or instance.footprint != evidence.footprint_library_id):
            raise ProjectCatalogConflict("library map selected source changed")
        self._require_current_model_selection(selection, detail=detail)
        port = self._schematic_parameter_port
        if port is None:
            raise ProjectCatalogUnavailable("library original reader unavailable")
        try:
            originals = port.read_library_originals(source, tenant_id=detail.tenant_id,
                project_id=detail.project_id, symbol_library_id=evidence.symbol_library_id,
                symbol_uri=evidence.symbol_uri, symbol_sha256=evidence.symbol_sha256,
                footprint_library_id=evidence.footprint_library_id,
                footprint_uri=evidence.footprint_uri, footprint_sha256=evidence.footprint_sha256)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise ProjectCatalogUnavailable("library originals unavailable") from error
        terminals = {item.number: item for item in originals.manufacturer_terminals}
        pins = dict(originals.library_symbol_pins)
        bindings = {item.manufacturer_terminal: item for item in evidence.terminal_bindings}
        if (set(bindings) != {"1", "2", "3", "4", "EP"}
            or set(terminals) != {"1", "2", "3", "4"}
            or set(pins) != {"1", "2", "3", "4", "5"}
            or set(originals.library_pad_ids) != set(pins)
            or originals.symbol_sha256 != evidence.symbol_sha256
            or originals.footprint_sha256 != evidence.footprint_sha256
            or originals.manufacturer_ep.internally_connected_to != "VSS/GND"
            or not originals.manufacturer_ep.may_connect_to_pcb_ground):
            raise ProjectCatalogConflict("library terminal coverage changed")
        source_pins = {item.number: item for item in source.pin_facts}
        for manufacturer, binding in bindings.items():
            logical = "5" if manufacturer == "EP" else manufacturer
            locator = originals.manufacturer_ep.locator if manufacturer == "EP" else terminals[manufacturer].locator
            name = "VSS" if manufacturer == "EP" else terminals[manufacturer].name
            actual_nets = tuple(net.net_id for net in plan.nets
                if any(node.component_id == instance.instance_id and node.pin_number == logical
                    for node in net.endpoints))
            plan_pins = tuple(item for item in plan.pin_bindings
                if item.component_id == instance.instance_id and item.pin_number == logical)
            source_pin = source_pins.get(logical)
            if (binding.manufacturer_locator != locator or binding.logical_symbol_pin != logical
                or binding.physical_pad_ids != (logical,) or pins.get(logical) != name
                or actual_nets != (binding.net_id,) or len(plan_pins) != 1
                or plan_pins[0].pin_name != name or plan_pins[0].pad_number != logical
                or source_pin is None or source_pin.name != name or source_pin.pad_number != logical):
                raise ProjectCatalogConflict("library terminal plan or original binding changed")
        self._require_current_model_selection(selection, detail=detail)
        if (self._schematic_plan_source_currentness(detail, plan,
            project_id=detail.project_id, tenant_id=detail.tenant_id,
            requester=detail.requester) != "CURRENT"
            or self.conversation(detail.project_id, detail.conversation_id,
                tenant_id=detail.tenant_id, requester=detail.requester).fingerprint != detail.fingerprint):
            raise ProjectCatalogConflict("library Owner changed during validation")
        return originals

    def _library_map_operation_state(self, operation, *, require_registered: bool = True):
        from embedded_copilot.execution_worker.contracts import SchematicReadBackOperation

        if (type(operation) is not SchematicReadBackOperation
            or operation.library_map_evidence_id is None):
            raise ProjectCatalogConflict("library readback selector unavailable")
        operation = SchematicReadBackOperation.model_validate(operation.model_dump(mode="python"))
        execution = self._schematic_evidence_execution
        canonical = None if execution is None else execution.get_operation(
            tenant_id=operation.tenant_id, project_id=operation.project_id, operation_id=operation.operation_id)
        if canonical != operation and (require_registered or canonical is not None):
            raise ProjectCatalogConflict("library readback operation identity changed")
        detail, plan, generation, source, receipt = self._canonical_schematic_sources(
            operation.project_id, operation.conversation_id,
            tenant_id=operation.tenant_id, requester=operation.requester_id)
        if (operation.schematic_plan_id != plan.plan_id or operation.schematic_plan_fingerprint != plan.fingerprint
            or operation.source_write_operation_id != source.operation_id
            or operation.source_write_receipt_fingerprint != receipt.fingerprint
            or operation.target_id != source.target_id or operation.target_fingerprint != source.target_fingerprint
            or operation.workspace_snapshot_id != source.workspace_snapshot_id
            or operation.workspace_fingerprint != source.workspace_fingerprint
            or operation.artifact_path != generation.artifact_path
            or operation.artifact_fingerprint != generation.artifact_fingerprint):
            raise ProjectCatalogConflict("library readback source changed")
        matches = tuple(item for item in (detail.library_terminal_maps or ())
            if item.evidence.evidence_id == operation.library_map_evidence_id
            and item.evidence.fingerprint == operation.library_map_evidence_fingerprint)
        if len(matches) != 1 or matches[0].review is None or matches[0].review.decision != "APPROVED":
            raise ProjectCatalogConflict("library readback Review unavailable")
        originals = self._library_terminal_map_checked(detail, matches[0].evidence)
        return detail, plan, matches[0], originals

    def _library_observation_material(self, operation, xml_bytes: bytes) -> dict[str, object]:
        from embedded_copilot.engineering_copilot.schematic_library_observation import parse_shtc3_ep_observation

        detail, plan, state, originals = self._library_map_operation_state(operation)
        evidence = state.evidence
        references = tuple(item.reference for item in detail.schematic_production.proposal.components
            if item.component_id == evidence.component_instance_id)
        if len(references) != 1:
            raise ProjectCatalogConflict("library readback reference unavailable")
        observed = parse_shtc3_ep_observation(symbol_bytes=originals.symbol_bytes,
            footprint_bytes=originals.footprint_bytes, xml_bytes=xml_bytes, component_ref=references[0],
            symbol_library_id=evidence.symbol_library_id, footprint_library_id=evidence.footprint_library_id,
            symbol_sha256=originals.symbol_sha256, footprint_sha256=originals.footprint_sha256)
        names = {}
        for net in plan.nets:
            if net.name in names:
                raise ProjectCatalogConflict("library readback net names ambiguous")
            names[net.name] = net.net_id
        manufacturer = {item.number: item for item in originals.manufacturer_terminals}
        bindings = []
        for connection in observed.observed_xml_nets:
            logical = connection.logical_symbol_pin
            terminal = "EP" if logical == "5" else logical
            locator = originals.manufacturer_ep.locator if terminal == "EP" else manufacturer[terminal].locator
            net_id = names.get(connection.xml_net_name)
            if net_id is None:
                raise ProjectCatalogConflict("library readback XML net unknown")
            bindings.append(LibraryTerminalBindingV2(manufacturer_terminal=terminal,
                manufacturer_locator=locator, logical_symbol_pin=logical,
                physical_pad_ids=(connection.physical_pad_id,), net_id=net_id).model_dump(mode="json"))
        self._library_map_operation_state(operation)
        return dict(schema_version=2, source_evidence_id=evidence.evidence_id,
            source_evidence_fingerprint=evidence.fingerprint,
            symbol_sha256=observed.symbol_sha256, footprint_sha256=observed.footprint_sha256,
            observed_symbol_pins=[pin for pin, _name in observed.observed_symbol_pins],
            observed_physical_pad_ids=list(observed.observed_physical_pad_ids), observed_net_bindings=bindings)

    def _library_verification_observation(self, operation, receipt, xml_bytes, evidence_ids, evidence_fingerprints):
        from embedded_copilot.execution_worker.contracts import parse_library_observation_payload

        if (tuple(evidence_ids) != (operation.library_map_evidence_id,)
            or tuple(evidence_fingerprints) != (operation.library_map_evidence_fingerprint,)):
            raise ProjectCatalogConflict("library map verification selector differs from operation")
        execution = self._schematic_evidence_execution
        canonical_receipt = execution.get_receipt(tenant_id=operation.tenant_id,
            project_id=operation.project_id, operation_id=operation.operation_id)
        if canonical_receipt != receipt or receipt.status.value != "SUCCEEDED" or len(receipt.evidence_references) != 1:
            raise ProjectCatalogConflict("library map canonical receipt member unavailable")
        identity = execution.get_artifact(tenant_id=operation.tenant_id, project_id=operation.project_id,
            artifact_id=receipt.evidence_references[0])
        primary = execution.get_artifact(tenant_id=operation.tenant_id, project_id=operation.project_id,
            artifact_id=receipt.schematic_readback_result.netlist_artifact.artifact_id)
        if identity is None or primary != receipt.schematic_readback_result.netlist_artifact:
            raise ProjectCatalogConflict("library map registered artifact unavailable")
        raw = execution.artifact_store.read(identity)
        registered_xml = execution.artifact_store.read(primary)
        if (registered_xml != xml_bytes or len(registered_xml) != primary.size_bytes
            or identity.toolchain_version != primary.toolchain_version
            or not self.revalidate_library_observation_payload(operation, identity, raw, registered_xml)):
            raise ProjectCatalogConflict("library map registered artifact binding invalid")
        observed = parse_library_observation_payload(raw, identity)
        detail, plan, state, originals = self._library_map_operation_state(operation)
        expected = state.evidence
        same = (observed.symbol_sha256 == expected.symbol_sha256
            and observed.footprint_sha256 == expected.footprint_sha256
            and observed.observed_symbol_pins == tuple(pin for pin, _name in originals.library_symbol_pins)
            and observed.observed_physical_pad_ids == originals.library_pad_ids
            and sorted(observed.observed_net_bindings, key=lambda item: item.manufacturer_terminal)
                == sorted(expected.terminal_bindings, key=lambda item: item.manufacturer_terminal))
        return {"evidence": expected.fingerprint, "review": state.review.fingerprint,
            "observation": observed.fingerprint, "artifact": identity.fingerprint,
            "bindings": [item.model_dump(mode="json") for item in observed.observed_net_bindings]}, same

    def collect_schematic_library_observation(self, operation, xml_bytes: bytes, *,
        created_at: datetime, tool_version: str):
        from embedded_copilot.execution_worker.contracts import ArtifactIdentity, parse_library_observation_payload

        payload = self._library_observation_material(operation, xml_bytes)
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        sha = "sha256:" + hashlib.sha256(content).hexdigest()
        identity = ArtifactIdentity(artifact_id="library-observation-" + hashlib.sha256(
            (operation.operation_id + sha).encode()).hexdigest()[:32], tenant_id=operation.tenant_id,
            project_id=operation.project_id, target_id=operation.target_id,
            workspace_fingerprint=operation.workspace_fingerprint, build_operation_id=operation.operation_id,
            toolchain_id="KICAD_XML_NETLIST", toolchain_version=tool_version,
            build_configuration="READ_ONLY_XML", artifact_type="MANIFEST", artifact_sha256=sha,
            size_bytes=len(content), created_at=created_at)
        parse_library_observation_payload(content, identity)
        return identity, content

    def revalidate_library_observation_payload(self, operation, identity, content: bytes, xml_bytes: bytes) -> bool:
        from embedded_copilot.execution_worker.contracts import parse_library_observation_payload

        try:
            observation = parse_library_observation_payload(content, identity)
            if (identity.tenant_id != operation.tenant_id or identity.project_id != operation.project_id
                or identity.target_id != operation.target_id or identity.workspace_fingerprint != operation.workspace_fingerprint
                or identity.build_operation_id != operation.operation_id or identity.toolchain_id != "KICAD_XML_NETLIST"
                or identity.build_configuration != "READ_ONLY_XML"):
                return False
            actual = self._library_observation_material(operation, xml_bytes)
            return observation.model_dump(mode="json", exclude={"artifact", "fingerprint"}) == actual
        except (ProjectCatalogError, AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def create_library_terminal_map(self, project_id: str, conversation_id: str, *,
        tenant_id: str, requester: str, values: dict[str, object]) -> ReviewedLibraryTerminalMapV2:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        evidence = LibraryTerminalMapEvidenceV2(schema_version=2,
            evidence_id=f"library-terminal-map-{uuid4()}", tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id, created_at=self._now(), **values)
        self._library_terminal_map_checked(detail, evidence)
        records = (*(detail.library_terminal_maps or ()), ReviewedLibraryTerminalMapV2(evidence=evidence))
        updated = detail.model_copy(update={"library_terminal_maps": records,
            "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(checked, expected_conversation_fingerprint=detail.fingerprint)
        return checked.library_terminal_maps[-1]

    def review_library_terminal_map(self, project_id: str, evidence_id: str, *, tenant_id: str,
        requester: str, evidence_fingerprint: str, decision: str) -> ReviewedLibraryTerminalMapV2:
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        matches = [(detail, index, item)
            for detail in self._repository.conversations(project_id, tenant_id, requester)
            for index, item in enumerate(detail.library_terminal_maps or ())
            if item.evidence.evidence_id == evidence_id]
        if len(matches) != 1:
            raise ProjectCatalogUnavailable("library evidence unavailable or ambiguous")
        detail, index, state = matches[0]
        if state.evidence.fingerprint != evidence_fingerprint:
            raise ProjectCatalogConflict("library evidence fingerprint changed")
        self._library_terminal_map_checked(detail, state.evidence)
        if state.review is not None:
            if state.review.decision != decision:
                raise ProjectCatalogConflict("library review is immutable")
            return state
        review = LibraryTerminalMapReviewV2(schema_version=2, evidence_id=evidence_id,
            evidence_fingerprint=evidence_fingerprint, review_id=f"library-map-review-{uuid4()}",
            tenant_id=tenant_id, project_id=project_id, conversation_id=detail.conversation_id,
            decision=decision, reviewer_id=requester, decided_at=self._now())
        reviewed = ReviewedLibraryTerminalMapV2(evidence=state.evidence, review=review)
        records = list(detail.library_terminal_maps); records[index] = reviewed
        updated = detail.model_copy(update={"library_terminal_maps": tuple(records),
            "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(checked, expected_conversation_fingerprint=detail.fingerprint)
        return checked.library_terminal_maps[index]

    def library_terminal_maps(self, project_id: str, conversation_id: str, *,
        tenant_id: str, requester: str) -> tuple[tuple[ReviewedLibraryTerminalMapV2, str], ...]:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        output = []
        for state in detail.library_terminal_maps or ():
            try:
                self._library_terminal_map_checked(detail, state.evidence)
                status = "REVIEW" if state.review is None else state.review.decision
            except (ProjectCatalogError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
                status = "STALE"
            output.append((state, status))
        return tuple(output)

    def model_operation_candidate_context(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
        operation_id: str, authority_source: object, authority_reference: object,
        selection_revision_input: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Return bounded current IDs only; this preview does not persist a proposal."""
        authority_source.revalidate(authority_reference)
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        architecture, requirements = detail.architecture_design, detail.requirement_context
        unresolved = ("bitrate", "EMC/standby", "terminal", "power", "component constraints")
        if operation_id == "HARDWARE_SELECTION_PROPOSAL" and selection_revision_input is not None:
            preview = self.selection_revision_view(project_id, conversation_id,
                tenant_id=tenant_id, requester=requester, view="PREVIEW",
                schema_version=selection_revision_input.get("schema_version",2),parent_choices=selection_revision_input.get("parent_choices"))
            if (tuple(preview["expected_head"]) != tuple(selection_revision_input["expected_head"])
                    or preview["preview_fingerprint"] != selection_revision_input["preview_fingerprint"]):
                raise ProjectCatalogConflict("SELECTION_PREVIEW_STALE")
            context = {"operation_id": operation_id, "selection_preview": preview,
                "choices": preview["choices"], "required_slots": preview["required_slots"], "unresolved": unresolved}
        elif operation_id == "HARDWARE_SELECTION_PROPOSAL":
            from ._selection_revision_lifecycle import SelectionRevisionLifecycle
            if SelectionRevisionLifecycle(self).head(detail) is not None:
                raise ProjectCatalogConflict("SELECTION_REVISION_SELECTOR_REQUIRED")
            if (self.hardware_component_source_for(detail) is None or architecture is None or requirements is None
                or detail.engineering_workflow is None or detail.architecture_review is None
                or detail.hardware_selection is not None):
                raise HardwareSelectionRejected("hardware model context unavailable")
            preview = HardwareSelectionService(self.hardware_component_source_for(detail)).create_proposal(
                architecture, requirements, workflow=detail.engineering_workflow,
                conversation_id=detail.conversation_id,
            )
            choices = tuple(
                {"slot_id": item.slot_id, "source_id": item.source.source_id, "source_fingerprint": item.source.fingerprint}
                for item in preview.evaluations if item.selectable
            )
            context = {"operation_id": operation_id, "choices": choices, "required_slots": tuple(item.slot_id for item in preview.requirements), "unresolved": unresolved}
        elif operation_id == "SCHEMATIC_DESIGN_PROPOSAL":
            selection = detail.hardware_selection
            if (selection is None or selection.status is not HardwareSelectionStatus.APPROVED
                or selection.approval is None or selection.hardware_truth is None or selection.bom is None
                or architecture is None or detail.architecture_review is None or requirements is None
                or detail.engineering_workflow is None or detail.schematic_planning is not None):
                raise SchematicPlanningRejected("schematic model context unavailable")
            self.require_current_circuit_binding(project_id, None, tenant_id=tenant_id, requester=requester)
            self._require_current_model_selection(selection, detail=detail)
            state = detail.project_supply
            if state is None or state.review is None or state.review.decision != "APPROVED":
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
            try:
                proposal, _truth, _circuit = self._project_supply_checked(detail, state.candidate, require_review=True)
            except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError) as error:
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
            context = {"operation_id": operation_id,
                       "nets": tuple({"net_id": net.net_id,
                                      "endpoint_binding_ids": tuple(item.binding_id for item in net.endpoints)}
                                     for net in proposal.nets),
                       "project_supply_candidate_fingerprint": state.candidate.fingerprint,
                       "project_supply_review_fingerprint": state.review.fingerprint,
                       "unresolved": unresolved}
        else:
            raise ValueError("model operation is unavailable")
        import json
        if len(json.dumps(context, ensure_ascii=False)) > 8192:
            raise ProjectCatalogUnavailable("model candidate context exceeds bound")
        authority_source.revalidate(authority_reference)
        return context

    def create_hardware_model_candidate_proposal(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
        candidate_selections: tuple[tuple[str, str, str], ...], authority_source: object,
        authority_reference: object,
    ) -> tuple[ConversationDetail, tuple[tuple[str, str, str], ...]]:
        authority_source.revalidate(authority_reference)
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        if SelectionRevisionLifecycle(self).head(detail) is not None:
            raise ProjectCatalogConflict("SELECTION_REVISION_SELECTOR_REQUIRED")
        if detail.hardware_selection is not None:
            raise HardwareSelectionRejected("hardware selection already exists")
        source = self.hardware_component_source_for(detail)
        if (source is None or detail.architecture_design is None or detail.requirement_context is None
            or detail.engineering_workflow is None or detail.architecture_review is None):
            raise HardwareSelectionRejected("hardware selection precondition is unavailable")
        try:
            proposal = HardwareSelectionService(source).create_proposal(
                detail.architecture_design, detail.requirement_context,
                workflow=detail.engineering_workflow, conversation_id=detail.conversation_id,
                candidate_selections=candidate_selections,
            )
            state = HardwareSelectionState(proposal=proposal, status=HardwareSelectionStatus.REVIEW)
        except (TypeError, ValueError, OSError) as error:
            raise HardwareSelectionRejected("model hardware candidate rejected") from error
        authority_source.revalidate(authority_reference)
        updated = detail.model_copy(update={"hardware_selection": state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        selected = tuple((item.slot_id, item.source.source_id, item.source.fingerprint) for item in proposal.selected_components)
        return checked, selected

    def decide_hardware_selection(
        self,
        project_id: str,
        conversation_id: str,
        decision: HardwareSelectionDecision,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        if SelectionRevisionLifecycle(self).head(detail) is not None:
            raise ProjectCatalogConflict("SELECTION_REVISION_SELECTOR_REQUIRED")
        selection = detail.hardware_selection
        if selection is None or selection.status is not HardwareSelectionStatus.REVIEW:
            raise HardwareSelectionRejected("hardware selection proposal is unavailable")
        source = self.hardware_component_source_for(detail)
        if source is None:
            raise HardwareSelectionRejected("hardware component source is unavailable")
        try:
            service = HardwareSelectionService(source)
            next_state = service.decide(
                selection.proposal,
                decision,
                reviewer=requester,
                decided_at=decided_at,
            )
            next_truth = detail.design_truth
            if next_state.status is HardwareSelectionStatus.APPROVED:
                architecture = detail.architecture_design
                architecture_review = detail.architecture_review
                if architecture is None or architecture_review is None:
                    raise ValueError("approved architecture review is unavailable")
                if next_truth is None:
                    next_truth = DesignTruthBuilder().build_from_approved_architecture(
                        architecture,
                        tenant_id=detail.tenant_id,
                        conversation_id=detail.conversation_id,
                        architecture_review=architecture_review,
                    )
                if next_state.hardware_truth is None:
                    raise ValueError("approved hardware truth is unavailable")
                next_truth = service.project_design_truth(
                    next_truth,
                    next_state.hardware_truth,
                    architecture_review=architecture_review,
                )
            else:
                next_truth = detail.design_truth
        except (TypeError, ValueError, OSError) as error:
            raise HardwareSelectionRejected(
                "hardware selection decision rejected"
            ) from error
        next_knowledge = None if next_truth is None else generate_knowledge_model(next_truth)
        updated = detail.model_copy(
            update={
                "hardware_selection": next_state,
                "design_truth": next_truth,
                "engineering_knowledge_model": next_knowledge,
                "teaching_context": None,
                "updated_at": decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    @staticmethod
    def _selection_has_typed_support(selection: HardwareSelectionState) -> bool:
        return any(
            item.supporting_for is not None
            or any(requirement.connection_expectations for requirement in item.source.supporting_requirements)
            for item in selection.proposal.selected_components
        )

    def _require_current_model_selection(self, selection: HardwareSelectionState, *, detail: ConversationDetail | None = None) -> None:
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        SelectionRevisionLifecycle(self).require_current(selection)
        if self._source_publication_host is not None:
            if detail is None or detail.hardware_selection != selection:
                raise SchematicPlanningRejected("current owned Selection scope is unavailable")
            source = self.hardware_component_source_for(detail)
        else:
            source = self._hardware_component_source
        if source is None:
            raise SchematicPlanningRejected("hardware component source is unavailable")
        try:
            current = HardwareSelectionService(source).selection_sources_current(selection)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise SchematicPlanningRejected("approved hardware selection source is unavailable") from error
        if not current:
            raise SchematicPlanningRejected("approved hardware selection source is stale")

    def _schematic_plan_source_currentness(
        self, detail: ConversationDetail, proposal: object, *,
        project_id: str, tenant_id: str, requester: str,
    ) -> str:
        selection = detail.hardware_selection
        if (
            selection is None or selection.status is not HardwareSelectionStatus.APPROVED
            or selection.approval is None or selection.bom is None
            or selection.hardware_truth is None or detail.architecture_design is None
            or detail.architecture_review is None or detail.requirement_context is None
        ):
            return "STALE"
        try:
            from ._selection_revision_lifecycle import SelectionRevisionLifecycle
            SelectionRevisionLifecycle(self).require_current(selection)
        except ProjectCatalogError:
            return "STALE"
        typed_support = self._selection_has_typed_support(selection)
        if typed_support:
            source = self.hardware_component_source_for(detail)
            if source is None:
                return "UNAVAILABLE"
            try:
                if not HardwareSelectionService(source).selection_sources_current(selection):
                    return "STALE"
            except (OSError, RuntimeError, TypeError, ValueError):
                return "UNAVAILABLE"
        state = detail.project_supply
        if state is None or state.review is None or state.review.decision != "APPROVED":
            return "STALE"
        if self.hardware_component_source_for(detail) is None:
            return "UNAVAILABLE"
        try:
            head = self._repository.schematic_plan_revision_head(
                tenant_id=tenant_id, project_id=project_id,
                conversation_id=detail.conversation_id, requester=requester,
            )
            if head is not None and (detail.schematic_planning != head["planning"]
                                     or head["plan_id"] != proposal.plan_id
                                     or head["plan_fingerprint"] != proposal.fingerprint):
                return "STALE"
            self.require_current_circuit_binding(
                project_id, proposal, tenant_id=tenant_id, requester=requester,
            )
            current, base_truth, _circuit_intent = self._project_supply_checked(
                detail, state.candidate, require_review=True,
            )
            if head is not None and base_truth != head["base_design_truth"]:
                return "STALE"
            self._project_supply_plan_currentness(detail, current, base_truth)
            if head is not None:
                latest_head = self._repository.schematic_plan_revision_head(
                    tenant_id=tenant_id, project_id=project_id,
                    conversation_id=detail.conversation_id, requester=requester,
                )
                if latest_head is None or (latest_head["revision_id"], latest_head["planning"]) != (
                        head["revision_id"], head["planning"]):
                    return "STALE"
        except (ProjectCatalogError, TypeError, ValueError, OSError, RuntimeError):
            return "STALE"
        planning = detail.schematic_planning
        if planning is None:
            return "STALE"
        expected = planning.approved_plan or planning.proposal
        return "CURRENT" if (current.fingerprint == planning.proposal.fingerprint
                             and expected.fingerprint == proposal.fingerprint) else "STALE"

    def schematic_plan_currentness(
        self, project_id: str, conversation_id: str,
        plan_id: str, plan_fingerprint: str, *, tenant_id: str, requester: str,
    ) -> tuple[str, str, str]:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester,
        )
        if self._repository.schematic_plan_revision_head(
            tenant_id=tenant_id, project_id=project_id,
            conversation_id=conversation_id, requester=requester,
        ) is not None:
            raise ProjectCatalogConflict("versioned schematic head requires a revision selector")
        planning = detail.schematic_planning
        proposal = None if planning is None else planning.approved_plan or planning.proposal
        if proposal is None:
            anchor = self._repository.selection_schematic_plan_anchor(tenant_id=tenant_id,project_id=project_id,conversation_id=conversation_id,requester=requester)
            if anchor is not None and (anchor["plan_id"],anchor["plan_fingerprint"]) == (plan_id,plan_fingerprint):
                return plan_id,plan_fingerprint,"STALE"
        if proposal is None or proposal.plan_id != plan_id or proposal.fingerprint != plan_fingerprint:
            raise ProjectCatalogConflict("schematic plan selector does not match persisted plan")
        return (
            plan_id, plan_fingerprint,
            self._schematic_plan_source_currentness(
                detail, proposal, project_id=project_id,
                tenant_id=tenant_id, requester=requester,
            ),
        )

    def create_schematic_plan(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        circuit_intent: object | None = None,
        circuit_intent_selector: tuple[str, str, str] | None = None,
    ) -> ConversationDetail:
        if circuit_intent_selector is not None:
            if circuit_intent is not None or len(circuit_intent_selector) != 3:
                raise ProjectCatalogConflict("Circuit selector is invalid")
            object_id, revision, fingerprint = circuit_intent_selector
            try:
                circuit_intent = self.circuit_intent_revision(
                    project_id, object_id, revision, fingerprint,
                    tenant_id=tenant_id, requester=requester,
                )
            except ProjectCatalogUnavailable as error:
                raise ProjectCatalogConflict("Circuit source binding is stale") from error
            if not self.circuit_source_current(
                project_id, object_id, revision, tenant_id=tenant_id, requester=requester,
            ):
                raise ProjectCatalogConflict("Circuit source binding is stale")
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        from ._selection_revision_lifecycle import SelectionRevisionLifecycle
        if SelectionRevisionLifecycle(self).head(detail) is not None:
            raise ProjectCatalogConflict("SCHEMATIC_REPLAN_SELECTOR_REQUIRED")
        if detail.schematic_planning is not None and circuit_intent_selector is not None:
            plan = detail.schematic_planning.approved_plan or detail.schematic_planning.proposal
            if (
                getattr(plan, "circuit_intent_id", None),
                getattr(plan, "circuit_intent_revision", None),
                getattr(plan, "circuit_intent_fingerprint", None),
            ) != circuit_intent_selector:
                raise ProjectCatalogConflict("Schematic plan is bound to another Circuit revision")
        self.require_current_circuit_binding(
            project_id,
            circuit_intent if detail.schematic_planning is None else (
                detail.schematic_planning.approved_plan or detail.schematic_planning.proposal
            ),
            tenant_id=tenant_id, requester=requester,
        )
        if detail.schematic_planning is not None:
            existing = detail.schematic_planning.approved_plan or detail.schematic_planning.proposal
            if self._schematic_plan_source_currentness(
                detail, existing, project_id=project_id,
                tenant_id=tenant_id, requester=requester,
            ) != "CURRENT":
                raise SchematicPlanningRejected("schematic planning source is not current")
            return detail
        selection = detail.hardware_selection
        architecture = detail.architecture_design
        architecture_review = detail.architecture_review
        requirements = detail.requirement_context
        workflow = detail.engineering_workflow
        if (
            selection is None
            or selection.status is not HardwareSelectionStatus.APPROVED
            or selection.hardware_truth is None
            or selection.bom is None
            or architecture is None
            or architecture_review is None
            or requirements is None
            or workflow is None
        ):
            raise SchematicPlanningRejected(
                "schematic planning precondition is unavailable"
            )
        state = detail.project_supply
        if state is None or state.review is None or state.review.decision != "APPROVED":
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        try:
            preview, base_truth, current_circuit = self._project_supply_checked(
                detail, state.candidate, require_review=True,
            )
            if circuit_intent is not None and (
                circuit_intent.object_id != current_circuit.object_id
                or circuit_intent.fingerprint != current_circuit.fingerprint
            ):
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
            proposal = SchematicPlanningService().create_proposal(
                architecture,
                requirements,
                selection.hardware_truth,
                selection.bom,
                design_truth=base_truth,
                conversation_id=detail.conversation_id,
                circuit_intent=current_circuit,
                selection=selection,
                _project_supply_relations=state.candidate.relations,
                _project_supply_evidence_fingerprints=preview.evidence_fingerprints,
            )
            state = SchematicPlanningState(
                proposal=proposal,
                status=proposal.status,
            )
        except SchematicPlanningBlocked:
            raise
        except (ProjectCatalogError, TypeError, ValueError, OSError, RuntimeError) as error:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
        updated = detail.model_copy(
            update={
                "schematic_planning": state,
                "updated_at": self._now(),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        latest = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if latest.fingerprint != detail.fingerprint:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        try:
            self._project_supply_checked(latest, latest.project_supply.candidate, require_review=True)
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=self._approved_source_heads(detail),
        )
        return checked

    @staticmethod
    def _schematic_revision_request_fingerprint(*, expected_head: tuple[str | None, str | None, str | None],
                                                 circuit_intent_selector: tuple[str, str, str] | None) -> str:
        body = json.dumps({"schema_version": 2, "expected_head_revision_id": expected_head[0],
                           "expected_head_plan_id": expected_head[1],
                           "expected_head_plan_fingerprint": expected_head[2],
                           "circuit_intent_selector": circuit_intent_selector},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    def _schematic_revision_view(self, revision: dict[str, object], *,
                                 tenant_id: str, project_id: str,
                                 conversation_id: str, requester: str) -> dict[str, object]:
        planning = revision["planning"]
        currentness = "STALE"
        if revision["is_current"]:
            detail = self.conversation(project_id, conversation_id,
                tenant_id=tenant_id, requester=requester)
            currentness = self._schematic_plan_source_currentness(
                detail, planning.approved_plan or planning.proposal,
                project_id=project_id, tenant_id=tenant_id, requester=requester,
            )
        return {"schema_version": 2, "revision_id": revision["revision_id"],
                "revision_number": revision["revision_number"],
                "predecessor_revision_id": revision["predecessor_revision_id"],
                "plan_id": revision["plan_id"],
                "plan_fingerprint": revision["plan_fingerprint"],
                "is_current": revision["is_current"], "currentness": currentness,
                "planning": planning.model_dump(mode="json"),
                "receipt_references": self._repository.schematic_plan_revision_receipts(
                    tenant_id=tenant_id, project_id=project_id,
                    conversation_id=conversation_id, requester=requester,
                    revision_id=revision["revision_id"], plan_id=revision["plan_id"],
                    plan_fingerprint=revision["plan_fingerprint"],
                )}

    def create_schematic_plan_v2(self, project_id: str, conversation_id: str, *,
                                 tenant_id: str, requester: str, request_id: str,
                                 expected_head: tuple[str | None, str | None, str | None],
                                 circuit_intent_selector: tuple[str, str, str] | None = None) -> dict[str, object]:
        request_fingerprint = self._schematic_revision_request_fingerprint(
            expected_head=expected_head, circuit_intent_selector=circuit_intent_selector,
        )
        previous = self._repository.schematic_plan_revision_by_request(
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            requester=requester, request_id=request_id,
            request_fingerprint=request_fingerprint,
        )
        if previous is not None:
            return self._schematic_revision_view(previous, tenant_id=tenant_id,
                project_id=project_id, conversation_id=conversation_id, requester=requester)
        detail = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        supply = detail.project_supply
        if (type(supply) is not ProjectSupplyStateV3 or supply.review is None
                or supply.review.decision != "APPROVED"):
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        plan_id = f"schematic-plan-{uuid4()}"
        try:
            proposal, base_truth, circuit = self._project_supply_checked(
                detail, supply.candidate, require_review=True, plan_id_override=plan_id,
            )
            if circuit_intent_selector is not None and (
                    circuit.object_id, circuit.revision, circuit.fingerprint) != circuit_intent_selector:
                raise ProjectCatalogConflict("Circuit source binding is stale")
            if proposal.status is not SchematicPlanningStatus.REVIEW:
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
            state = SchematicPlanningState(proposal=proposal, status=proposal.status)
        except SchematicPlanningBlocked:
            raise
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
        updated = detail.model_copy(update={
            "schematic_planning": state, "schematic_production": None,
            "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64,
        })
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        latest = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        if latest.fingerprint != detail.fingerprint:
            raise ProjectCatalogConflict("schematic plan conversation changed")
        self._project_supply_checked(latest, latest.project_supply.candidate,
            require_review=True, plan_id_override=plan_id)
        revision = self._repository.append_schematic_plan_revision(
            before=detail, after=checked, base_truth=base_truth,
            revision_id=f"schematic-revision-{uuid4()}", request_id=request_id,
            request_fingerprint=request_fingerprint, expected_head=expected_head,
            expected_source_publications=tuple((item.source_id, item.publication_fingerprint)
                                               for item in supply.candidate.source_publications),
        )
        return self._schematic_revision_view(revision, tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id, requester=requester)

    def decide_schematic_plan_v2(self, project_id: str, conversation_id: str,
                                 decision: SchematicPlanDecision, *, tenant_id: str,
                                 requester: str, revision_id: str, plan_id: str,
                                 plan_fingerprint: str, decided_at: datetime) -> dict[str, object]:
        revision = self._repository.schematic_plan_revision(
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            requester=requester, revision_id=revision_id, plan_id=plan_id,
            plan_fingerprint=plan_fingerprint,
        )
        old_state = revision["planning"]
        if old_state.status in (SchematicPlanningStatus.APPROVED, SchematicPlanningStatus.REJECTED):
            if old_state.approval is not None and old_state.approval.decision is decision:
                return self._schematic_revision_view(revision, tenant_id=tenant_id,
                    project_id=project_id, conversation_id=conversation_id, requester=requester)
            raise ProjectCatalogConflict("schematic plan revision was already decided")
        if not revision["is_current"] or old_state.status is not SchematicPlanningStatus.REVIEW:
            raise ProjectCatalogConflict("schematic plan decision is not current")
        detail = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        planning = detail.schematic_planning
        if (planning is None or planning.status is not SchematicPlanningStatus.REVIEW
                or planning.proposal.plan_id != plan_id
                or planning.proposal.fingerprint != plan_fingerprint):
            raise ProjectCatalogConflict("schematic plan decision binding changed")
        if self._schematic_plan_source_currentness(detail, planning.proposal,
                project_id=project_id, tenant_id=tenant_id, requester=requester) != "CURRENT":
            raise SchematicPlanningRejected("schematic planning source is not current")
        service = SchematicPlanningService()
        next_state = service.decide(planning.proposal, decision,
            reviewer=requester, decided_at=decided_at)
        next_truth = detail.design_truth
        if next_state.status is SchematicPlanningStatus.APPROVED:
            next_truth = service.project_design_truth(revision["base_design_truth"],
                next_state.approved_plan)
        next_knowledge = None if next_truth is None else generate_knowledge_model(next_truth)
        updated = detail.model_copy(update={
            "schematic_planning": next_state, "design_truth": next_truth,
            "engineering_knowledge_model": next_knowledge, "teaching_context": None,
            "schematic_production": None, "updated_at": decided_at,
            "fingerprint": "sha256:" + "0" * 64,
        })
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        latest = self.conversation(project_id, conversation_id,
            tenant_id=tenant_id, requester=requester)
        if latest.fingerprint != detail.fingerprint or self._schematic_plan_source_currentness(
                latest, planning.proposal, project_id=project_id,
                tenant_id=tenant_id, requester=requester) != "CURRENT":
            raise ProjectCatalogConflict("schematic plan source changed during decision")
        supply = latest.project_supply
        result = self._repository.decide_schematic_plan_revision(
            before=detail, after=checked, revision_id=revision_id,
            plan_id=plan_id, plan_fingerprint=plan_fingerprint,
            decision=decision.value,
            expected_source_publications=tuple((item.source_id, item.publication_fingerprint)
                                               for item in supply.candidate.source_publications),
        )
        return self._schematic_revision_view(result, tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id, requester=requester)

    def schematic_plan_revision(self, project_id: str, conversation_id: str, *,
                                tenant_id: str, requester: str, revision_id: str,
                                plan_id: str, plan_fingerprint: str) -> dict[str, object]:
        revision = self._repository.schematic_plan_revision(
            tenant_id=tenant_id, project_id=project_id, conversation_id=conversation_id,
            requester=requester, revision_id=revision_id, plan_id=plan_id,
            plan_fingerprint=plan_fingerprint,
        )
        return self._schematic_revision_view(revision, tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id, requester=requester)

    def schematic_plan_legacy_snapshot(self, project_id: str, conversation_id: str, *,
                                       tenant_id: str, requester: str,
                                       plan_id: str | None = None,
                                       plan_fingerprint: str | None = None) -> dict[str, object] | None:
        return self._repository.schematic_plan_legacy_snapshot(
            tenant_id=tenant_id, project_id=project_id,
            conversation_id=conversation_id, requester=requester,
            plan_id=plan_id, plan_fingerprint=plan_fingerprint,
        )

    def schematic_plan_revision_history(self, project_id: str, conversation_id: str, *,
                                        tenant_id: str, requester: str) -> tuple[dict[str, object], ...]:
        revisions = self._repository.schematic_plan_revision_history(
            tenant_id=tenant_id, project_id=project_id,
            conversation_id=conversation_id, requester=requester,
        )
        return tuple(self._schematic_revision_view(item, tenant_id=tenant_id,
            project_id=project_id, conversation_id=conversation_id,
            requester=requester) for item in revisions)

    def create_schematic_model_candidate_proposal(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str,
        review_items: tuple[tuple[str, tuple[str, ...]], ...], authority_source: object,
        authority_reference: object,
        expected_project_supply_fingerprints: tuple[str, str] | None = None,
    ) -> tuple[ConversationDetail, tuple[tuple[str, tuple[str, ...]], ...]]:
        authority_source.revalidate(authority_reference)
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if detail.schematic_planning is not None:
            raise SchematicPlanningRejected("schematic plan already exists")
        self.require_current_circuit_binding(project_id, None, tenant_id=tenant_id, requester=requester)
        selection = detail.hardware_selection
        architecture = detail.architecture_design
        architecture_review = detail.architecture_review
        requirements = detail.requirement_context
        if (selection is None or selection.status is not HardwareSelectionStatus.APPROVED
            or selection.approval is None or selection.hardware_truth is None or selection.bom is None
            or architecture is None or architecture_review is None or requirements is None
            or detail.engineering_workflow is None):
            raise SchematicPlanningRejected("schematic planning precondition is unavailable")
        self._require_current_model_selection(selection, detail=detail)
        state = detail.project_supply
        if state is None or state.review is None or state.review.decision != "APPROVED":
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        if (expected_project_supply_fingerprints is not None and expected_project_supply_fingerprints
            != (state.candidate.fingerprint, state.review.fingerprint)):
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        try:
            preview, base_truth, circuit = self._project_supply_checked(detail, state.candidate, require_review=True)
            proposal, validated = SchematicPlanningService().create_model_candidate_proposal(
                architecture, requirements, selection.hardware_truth, selection.bom,
                design_truth=base_truth, conversation_id=detail.conversation_id,
                review_items=review_items, circuit_intent=circuit, selection=selection,
                _project_supply_relations=state.candidate.relations,
                _project_supply_evidence_fingerprints=preview.evidence_fingerprints,
            )
            state = SchematicPlanningState(proposal=proposal, status=proposal.status)
        except SchematicPlanningBlocked:
            raise
        except (ProjectCatalogError, TypeError, ValueError, OSError, RuntimeError) as error:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
        authority_source.revalidate(authority_reference)
        latest = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if latest.fingerprint != detail.fingerprint:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        try:
            self._project_supply_checked(latest, latest.project_supply.candidate, require_review=True)
        except (ProjectCatalogError, OSError, RuntimeError, TypeError, ValueError) as error:
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED") from error
        updated = detail.model_copy(update={"schematic_planning": state, "updated_at": self._now(), "fingerprint": "sha256:" + "0" * 64})
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=self._approved_source_heads(detail),
        )
        return checked, validated

    def decide_schematic_plan(
        self,
        project_id: str,
        conversation_id: str,
        decision: SchematicPlanDecision,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        planning = detail.schematic_planning
        if self._repository.schematic_plan_revision_head(
            tenant_id=tenant_id, project_id=project_id,
            conversation_id=conversation_id, requester=requester,
        ) is not None:
            raise SchematicPlanningRejected("versioned schematic plan requires an exact revision decision")
        if planning is None or planning.status is not SchematicPlanningStatus.REVIEW:
            raise SchematicPlanningRejected("schematic plan proposal is unavailable")
        self.require_current_circuit_binding(
            project_id, planning.proposal, tenant_id=tenant_id, requester=requester
        )
        if self._schematic_plan_source_currentness(
            detail, planning.proposal, project_id=project_id,
            tenant_id=tenant_id, requester=requester,
        ) != "CURRENT":
            raise SchematicPlanningRejected("schematic planning source is not current")
        architecture = detail.architecture_design
        architecture_review = detail.architecture_review
        if architecture is None or architecture_review is None:
            raise SchematicPlanningRejected("approved architecture review is unavailable")
        try:
            service = SchematicPlanningService()
            next_state = service.decide(
                planning.proposal,
                decision,
                reviewer=requester,
                decided_at=decided_at,
            )
            next_truth = detail.design_truth
            if next_state.status is SchematicPlanningStatus.APPROVED:
                if next_state.approved_plan is None:
                    raise ValueError("approved schematic plan is unavailable")
                if next_truth is None:
                    next_truth = DesignTruthBuilder().build_from_approved_architecture(
                        architecture,
                        tenant_id=detail.tenant_id,
                        conversation_id=detail.conversation_id,
                        architecture_review=architecture_review,
                    )
                next_truth = service.project_design_truth(
                    next_truth,
                    next_state.approved_plan,
                )
        except (TypeError, ValueError, OSError) as error:
            raise SchematicPlanningRejected(
                "schematic planning decision rejected"
            ) from error
        next_schematic_production = detail.schematic_production
        if next_schematic_production is not None and next_schematic_production.generation_receipt is not None:
            next_plan_fingerprint = (
                None
                if next_state.approved_plan is None
                else next_state.approved_plan.fingerprint
            )
            if next_plan_fingerprint != next_schematic_production.generation_receipt.schematic_plan_fingerprint:
                try:
                    next_schematic_production = _mark_schematic_production_stale(
                        next_schematic_production
                    )
                except (TypeError, ValueError) as error:
                    raise SchematicPlanningRejected(
                        "existing schematic artifact cannot be marked stale"
                    ) from error
        next_knowledge = None if next_truth is None else generate_knowledge_model(next_truth)
        updated = detail.model_copy(
            update={
                "schematic_planning": next_state,
                "design_truth": next_truth,
                "engineering_knowledge_model": next_knowledge,
                "teaching_context": None,
                "schematic_production": next_schematic_production,
                "updated_at": decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        latest = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        if (latest.fingerprint != detail.fingerprint
            or self._schematic_plan_source_currentness(latest, planning.proposal,
                project_id=project_id, tenant_id=tenant_id, requester=requester) != "CURRENT"):
            raise SchematicPlanningRejected("schematic planning source is not current")
        self._repository.save_conversation_if_fingerprint(
            checked, expected_conversation_fingerprint=detail.fingerprint,
            expected_source_publications=self._approved_source_heads(detail),
        )
        return checked

    def decide_pcb_production(
        self,
        project_id: str,
        conversation_id: str,
        decision: PCBProposalDecisionStatus,
        *,
        tenant_id: str,
        requester: str,
        decided_at: datetime,
        bridge: object | None,
    ) -> ConversationDetail:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        pcb_state = detail.pcb_engineering_state
        self.require_current_circuit_binding(
            project_id, None if pcb_state is None else pcb_state.intent_source_binding,
            tenant_id=tenant_id, requester=requester,
        )
        production = detail.pcb_production
        if production is None or production.status is not PCBProductionStatus.AWAITING_APPROVAL:
            raise PCBProductionRejected("PCB proposal is unavailable")
        try:
            proposal_decision = decide_pcb_proposal(
                production.proposal,
                decision=decision,
                reviewer=requester,
                decided_at=decided_at,
            )
        except (TypeError, ValueError) as error:
            raise PCBProductionRejected("PCB proposal decision rejected") from error
        if proposal_decision.decision is PCBProposalDecisionStatus.REJECTED:
            next_state = PCBProductionState.create(
                production_id=production.production_id,
                project_id=production.project_id,
                conversation_id=production.conversation_id,
                request=production.request,
                proposal=production.proposal,
                decision=proposal_decision,
                status=PCBProductionStatus.REJECTED,
            )
        else:
            enqueue = getattr(bridge, "approve_and_enqueue", None)
            if not callable(enqueue):
                raise PCBProductionRejected("PCB execution bridge is unavailable")
            try:
                operation_request = enqueue(
                    production_request=production.request,
                    proposal=production.proposal,
                    decision=proposal_decision,
                    tenant_id=tenant_id,
                )
            except (TypeError, ValueError, OSError, RuntimeError) as error:
                raise PCBProductionRejected("PCB operation rejected") from error
            operation = operation_request.operation
            next_state = PCBProductionState.create(
                production_id=production.production_id,
                project_id=production.project_id,
                conversation_id=production.conversation_id,
                request=production.request,
                proposal=production.proposal,
                decision=proposal_decision,
                operation_id=operation.operation_id,
                operation_fingerprint=operation.fingerprint,
                status=PCBProductionStatus.QUEUED,
            )
        updated = detail.model_copy(
            update={
                "pcb_production": next_state,
                "updated_at": decided_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def attach_artifact_evidence_review(
        self,
        project_id: str,
        conversation_id: str,
        review_id: str,
        *,
        tenant_id: str,
        requester: str,
    ) -> ConversationDetail:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        bundle = detail.engineering_evidence_bundle
        if bundle is None:
            raise ArtifactIntelligenceRejected("artifact evidence bundle is unavailable")
        if bundle.status is EngineeringEvidenceStatus.CANDIDATE:
            bundle = transition_engineering_evidence(
                bundle, EngineeringEvidenceStatus.REVIEWING
            )
        elif bundle.status is not EngineeringEvidenceStatus.REVIEWING:
            raise ArtifactIntelligenceRejected("artifact evidence review state is invalid")
        snapshot = detail.artifact_intelligence_snapshot
        if snapshot is None:
            raise ArtifactIntelligenceRejected("artifact intelligence snapshot is unavailable")
        updated_snapshot = snapshot.model_copy(
            update={
                "engineering_evidence_bundle": bundle,
                "review_id": review_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        updated = detail.model_copy(
            update={
                "artifact_intelligence_snapshot": updated_snapshot,
                "engineering_evidence_bundle": bundle,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return checked

    def build_reverse_design_truth(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        review: object | None = None,
    ) -> DesignTruth:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        bundle = detail.engineering_evidence_bundle
        if bundle is None:
            raise EvidenceReviewRequired("engineering evidence review is required")
        if review is not None:
            request = getattr(review, "review_request", None)
            decision = getattr(review, "decision", None)
            reference = getattr(request, "target_reference", None)
            if (
                getattr(getattr(decision, "decision", None), "value", None) != "APPROVE"
                or request is None
                or reference is None
                or request.project_id != project_id
                or reference.target_id != bundle.bundle_id
                or reference.target_fingerprint != bundle.bundle_fingerprint
            ):
                raise EvidenceReviewRequired("engineering evidence review is required")
            if bundle.status is EngineeringEvidenceStatus.REVIEWING:
                bundle = transition_engineering_evidence(
                    bundle, EngineeringEvidenceStatus.APPROVED
                )
        if bundle.status is not EngineeringEvidenceStatus.APPROVED:
            raise EvidenceReviewRequired("engineering evidence review is required")
        truth = DesignTruthBuilder().build_from_approved_evidence(bundle)
        if truth.tenant_id != tenant_id or truth.project_id != project_id:
            raise ProjectCatalogForbidden("design truth identity mismatch")
        updated = detail.model_copy(
            update={
                "engineering_evidence_bundle": bundle,
                "design_truth": truth,
                "engineering_knowledge_model": generate_knowledge_model(truth),
                "teaching_context": None,
                "engineering_workflow": None,
                "hardware_design_spec": None,
                "firmware_spec": None,
                "simulation_spec": None,
                "verification_spec": None,
                "implementation_spec": None,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return truth

    def generate_teaching_context(
        self,
        project_id: str,
        conversation_id: str,
        *,
        tenant_id: str,
        requester: str,
        subject_id: str | None = None,
        mode: LearningMode = LearningMode.OVERVIEW,
    ) -> TeachingContext:
        detail = self.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        knowledge = detail.engineering_knowledge_model
        if knowledge is None:
            raise ProjectCatalogRejected("engineering knowledge model is unavailable")
        teaching = project_generate_teaching_context(
            knowledge,
            subject_id=subject_id,
            mode=mode,
        )
        updated = detail.model_copy(
            update={"teaching_context": teaching, "fingerprint": "sha256:" + "0" * 64}
        )
        checked = ConversationDetail.model_validate(updated.model_dump(mode="python"))
        self._repository.save_conversation(checked)
        return teaching

    def routing_context(self, session_id: str) -> ApplicationRoutingContext | None:
        detail = self._repository.conversation_for_session(session_id)
        if detail is None:
            return None
        return self._routing_context_for_detail(detail)

    def routing_context_for_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str) -> ApplicationRoutingContext:
        detail = self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        return self._routing_context_for_detail(detail)

    def _routing_context_for_detail(self, detail: ConversationDetail) -> ApplicationRoutingContext:
        project = self._repository.project(detail.project_id)
        previous = detail.turns[-1] if detail.turns else None
        artifact_count = 0
        if project is not None:
            try:
                context = self.context(
                    project.project_id,
                    tenant_id=project.tenant_id,
                )
                artifact_count = sum(len(group.references) for group in context.groups)
            except Exception:
                artifact_count = 0
        return ApplicationRoutingContext(
            project_creation_mode=(
                None if project is None else project.creation_mode.value
            ),
            project_type=None if project is None else project.project_type.value,
            project_status=(
                None if project is None else project.initialization_status.value
            ),
            conversation_id=detail.conversation_id,
            previous_route=None if previous is None else previous.route,
            previous_analysis_state=None if previous is None else previous.analysis_state.value,
            previous_intent=None if previous is None else previous.previous_intent,
            available_artifact_count=artifact_count,
            requirement_context=detail.requirement_context,
            research_snapshot=detail.research_snapshot,
            engineering_plan=detail.engineering_plan,
            engineering_plan_review=detail.engineering_plan_review,
            architecture_design=detail.architecture_design,
            architecture_review=detail.architecture_review,
            design_truth=detail.design_truth,
            engineering_session_mode=detail.engineering_session_mode,
        )

    def begin_turn(self, session_id: str, *, request_id: str, trace_id: str | None = None, question: str, created_at: datetime, context_references: tuple[dict[str, object], ...] = ()) -> None:
        detail = self._repository.conversation_for_session(session_id)
        if detail is None:
            return
        if any(turn.request_id == request_id for turn in detail.turns):
            return
        title = detail.title
        if not detail.turns:
            title = question.splitlines()[0].strip()[:64] or "新对话"
        previous = detail.turns[-1] if detail.turns else None
        continuation = previous is not None
        turn = ConversationTurn(
            turn_id=f"turn-{uuid4()}", request_id=request_id, trace_id=trace_id, status=ConversationTurnStatus.PENDING,
            continuation_of_turn_id=previous.turn_id if continuation else None,
            previous_analysis_state=previous.analysis_state.value if continuation else None,
            previous_route=previous.route if continuation else None,
            previous_intent=("UNKNOWN" if previous.route == "CLARIFICATION_REQUIRED" else previous.route) if continuation else None,
            question=question, response_type=resolve_response_type(question),
            context_references=context_references, created_at=created_at,
        )
        updated = detail.model_copy(update={"title": title, "turns": detail.turns + (turn,), "updated_at": created_at, "fingerprint": "sha256:" + "0" * 64})
        self._repository.save_conversation(ConversationDetail.model_validate(updated.model_dump(mode="python")))
        project = self._repository.project(detail.project_id)
        if project is not None and created_at > project.last_activity_at:
            active = project.model_copy(update={"last_activity_at": created_at, "updated_at": created_at, "fingerprint": "sha256:" + "0" * 64})
            self._repository.update_project(ProjectView.model_validate(active.model_dump(mode="python")))

    def complete_turn(self, session_id: str, request_id: str, answer: object, completed_at: datetime, *, route: str | None = None, trace_id: str | None = None, terminal_event: str | None = "ANSWER_COMPLETED", analysis_state: str | None = None, analysis_reason_code: str | None = None, requirement_context: RequirementContext | None = None, research_snapshot: EngineeringResearchSnapshot | None = None, requirement_suggestion_decisions: tuple[RequirementSuggestionDecision, ...] | None = None, reference_project_decisions: tuple[ReferenceProjectDecision, ...] | None = None, feature_decisions: tuple[FeatureDecision, ...] | None = None, requirement_change_proposal: RequirementChangeProposal | None = None, requirement_final_confirmation: RequirementFinalConfirmation | None = None, research_intent_refinement: ResearchIntentRefinement | None = None, engineering_plan: EngineeringPlan | None = None, engineering_plan_review: PlanReviewDecision | None = None, architecture_design: object | None = None, architecture_review: object | None = None, design_truth: DesignTruth | None = None) -> None:
        detail = self._repository.conversation_for_session(session_id)
        if detail is None:
            return
        payload = answer.model_dump(mode="json")
        turns = tuple(
            turn if turn.request_id != request_id else ConversationTurn(
                turn_id=turn.turn_id, request_id=turn.request_id, trace_id=trace_id or turn.trace_id, route=route or turn.route, status=ConversationTurnStatus.COMPLETED,
                continuation_of_turn_id=turn.continuation_of_turn_id, previous_analysis_state=turn.previous_analysis_state, previous_route=turn.previous_route,
                previous_intent=turn.previous_intent,
                question=turn.question, answer_text=payload["answer_text"],
                response_type=payload.get("response_type", turn.response_type),
                artifact_refs=payload.get("artifact_refs", turn.artifact_refs),
                evidence=tuple(payload.get("evidence_details", ())),
                recommendations=tuple(payload.get("recommendations", ())),
                context_references=turn.context_references, created_at=turn.created_at, completed_at=completed_at,
                analysis_state=analysis_state or "COMPLETED",
                analysis_reason_code=analysis_reason_code,
                terminal_event=terminal_event,
            ) for turn in detail.turns
        )
        next_requirement = requirement_context or detail.requirement_context
        next_research = research_snapshot if research_snapshot is not None else detail.research_snapshot
        next_suggestion_decisions = (
            requirement_suggestion_decisions
            if requirement_suggestion_decisions is not None
            else detail.requirement_suggestion_decisions
        )
        next_reference_decisions = (
            reference_project_decisions
            if reference_project_decisions is not None
            else detail.reference_project_decisions
        )
        next_feature_decisions = feature_decisions if feature_decisions is not None else detail.feature_decisions
        next_change_proposal = requirement_change_proposal if requirement_change_proposal is not None else detail.requirement_change_proposal
        next_confirmation = requirement_final_confirmation if requirement_final_confirmation is not None else detail.requirement_final_confirmation
        next_refinement = research_intent_refinement if research_intent_refinement is not None else detail.research_intent_refinement
        next_plan = engineering_plan or detail.engineering_plan
        next_review = engineering_plan_review or detail.engineering_plan_review
        next_architecture = architecture_design or detail.architecture_design
        next_architecture_review = architecture_review or detail.architecture_review
        next_design_truth = design_truth or detail.design_truth
        if (
            next_design_truth is not None
            and next_design_truth.origin is DesignTruthOrigin.CREATE
            and next_design_truth.tenant_id != detail.tenant_id
            and next_design_truth.tenant_id == "default-tenant"
            and next_architecture is not None
        ):
            # Older callers created architecture projections before tenant
            # binding was added. Rebind that legacy default only through the
            # approved architecture builder; never accept another tenant.
            legacy_fingerprint = next_design_truth.fingerprint
            next_design_truth = DesignTruthBuilder().build_from_approved_architecture(
                next_architecture,
                tenant_id=detail.tenant_id,
                conversation_id=detail.conversation_id,
                architecture_review=next_architecture_review,
            ).model_copy(update={"fingerprint": legacy_fingerprint})
        if next_plan is not None and next_requirement is not None and next_plan.context_fingerprint != next_requirement.fingerprint and next_plan.status.value != "SUPERSEDED":
            next_plan = None
            next_review = None
        if next_architecture is not None and next_plan is not None and next_architecture.plan_fingerprint != next_plan.fingerprint and next_architecture.status.value not in {"SUPERSEDED", "INVALIDATED"}:
            next_architecture = None
            next_architecture_review = None
        next_hardware_selection = detail.hardware_selection
        if next_hardware_selection is not None:
            proposal = next_hardware_selection.proposal
            if (
                next_requirement is None
                or next_architecture is None
                or next_architecture.status is not ArchitectureDesignStatus.APPROVED
                or proposal.architecture_id != next_architecture.architecture_id
                or proposal.architecture_fingerprint != next_architecture.fingerprint
                or proposal.requirements_fingerprint != next_requirement.fingerprint
            ):
                next_hardware_selection = None
        if next_design_truth is not None and next_design_truth.origin is DesignTruthOrigin.CREATE:
            if next_architecture is None or next_architecture.status is not ArchitectureDesignStatus.APPROVED:
                next_design_truth = None
            elif (
                next_design_truth.project_id != detail.project_id
                or next_design_truth.architecture_id != next_architecture.architecture_id
            ):
                next_design_truth = None
        elif next_design_truth is not None and (
            next_design_truth.project_id != detail.project_id
            or next_design_truth.architecture_id is not None
        ):
            next_design_truth = None
        next_knowledge_model = None if next_design_truth is None else generate_knowledge_model(next_design_truth)
        next_teaching_context = detail.teaching_context
        if (
            next_knowledge_model is None
            or detail.engineering_knowledge_model is None
            or next_knowledge_model.fingerprint != detail.engineering_knowledge_model.fingerprint
        ):
            next_teaching_context = None
        next_workflow = None
        if (
            next_plan is not None
            and next_architecture is not None
            and next_plan.status is PlanStatus.APPROVED
            and next_architecture.status is ArchitectureDesignStatus.APPROVED
        ):
            if detail.engineering_workflow is not None and _workflow_sources_match(
                detail.engineering_workflow, next_plan, next_architecture
            ):
                next_workflow = detail.engineering_workflow
            else:
                next_workflow = generate_engineering_workflow(next_architecture, next_plan)
        next_hardware_design_spec = None
        if next_workflow is not None and next_architecture is not None:
            try:
                next_hardware_design_spec = generate_hardware_design_spec(
                    next_architecture,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_hardware_design_spec = None
        next_firmware_spec = None
        if next_hardware_design_spec is not None and next_workflow is not None:
            try:
                next_firmware_spec = generate_firmware_spec(
                    next_hardware_design_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_firmware_spec = None
        next_simulation_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_workflow is not None:
            try:
                next_simulation_spec = generate_simulation_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_simulation_spec = None
        next_verification_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_simulation_spec is not None and next_workflow is not None:
            try:
                next_verification_spec = generate_verification_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_simulation_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_verification_spec = None
        next_implementation_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_simulation_spec is not None and next_verification_spec is not None and next_workflow is not None:
            try:
                next_implementation_spec = generate_implementation_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_simulation_spec,
                    next_verification_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_implementation_spec = None
        (
            next_capability_evaluation,
            next_execution_intents,
            next_execution_authorization,
        ) = _capability_state(
            next_implementation_spec,
            conversation_id=detail.conversation_id,
            requested_by=detail.requester,
            current_authorization=detail.execution_authorization,
        )
        command = next((turn.question for turn in turns if turn.request_id == request_id), None)
        application_trace_id = trace_id or next(
            (turn.trace_id for turn in detail.turns if turn.request_id == request_id),
            None,
        )
        if (
            command
            and is_requirement_change_feedback(command)
            and next_requirement is not None
            and (next_change_proposal is None or next_change_proposal.status is not RequirementChangeStatus.PENDING)
        ):
            try:
                next_change_proposal = self._research_service.interpret_requirement_feedback(
                    next_requirement,
                    command,
                    research_snapshot=next_research,
                    decided_by=detail.requester,
                    created_at=completed_at,
                )
                next_confirmation = None
            except (TypeError, ValueError):
                # A malformed refinement is kept as a normal chat result; it never mutates truth.
                next_change_proposal = detail.requirement_change_proposal
        next_firmware_source_generation = _firmware_source_state(
            tenant_id=detail.tenant_id,
            firmware_spec=next_firmware_spec,
            implementation_spec=next_implementation_spec,
            execution_intents=next_execution_intents,
            execution_authorization=next_execution_authorization,
            existing=detail.firmware_source_generation,
            command=command,
            source_request_id=request_id,
            trace_id=application_trace_id,
        )
        next_schematic_production = _schematic_production_state(
            design_truth=next_design_truth,
            hardware_design=next_hardware_design_spec,
            implementation_spec=next_implementation_spec,
            execution_intents=next_execution_intents,
            execution_authorization=next_execution_authorization,
            existing=detail.schematic_production,
            command=command,
            conversation_id=detail.conversation_id,
            requester=detail.requester,
            schematic_planning=detail.schematic_planning,
            source_request_id=request_id,
            trace_id=application_trace_id,
        )
        updated = detail.model_copy(update={"turns": turns, "updated_at": completed_at, "requirement_context": next_requirement, "research_snapshot": next_research, "requirement_suggestion_decisions": next_suggestion_decisions, "reference_project_decisions": next_reference_decisions, "feature_decisions": next_feature_decisions, "requirement_change_proposal": next_change_proposal, "requirement_final_confirmation": next_confirmation, "research_intent_refinement": next_refinement, "engineering_plan": next_plan, "engineering_plan_review": next_review, "architecture_design": next_architecture, "architecture_review": next_architecture_review, "design_truth": next_design_truth, "engineering_knowledge_model": next_knowledge_model, "teaching_context": next_teaching_context, "engineering_workflow": next_workflow, "hardware_design_spec": next_hardware_design_spec, "hardware_selection": next_hardware_selection, "firmware_spec": next_firmware_spec, "simulation_spec": next_simulation_spec, "verification_spec": next_verification_spec, "implementation_spec": next_implementation_spec, "capability_evaluation": next_capability_evaluation, "execution_intents": next_execution_intents, "execution_authorization": next_execution_authorization, "firmware_source_generation": next_firmware_source_generation, "schematic_production": next_schematic_production, "fingerprint": "sha256:" + "0" * 64})
        self._repository.save_conversation(ConversationDetail.model_validate(updated.model_dump(mode="python")))

    def fail_turn(
        self,
        session_id: str,
        request_id: str,
        error_code: str | None,
        completed_at: datetime,
        *,
        context_references: tuple[dict[str, object], ...] = (),
        analysis_state: str = "FAILED",
        analysis_reason_code: str | None = None,
        route: str | None = None,
        trace_id: str | None = None,
        terminal_event: str | None = "ANSWER_FAILED",
        answer_text: str | None = None,
        requirement_context: RequirementContext | None = None,
        research_snapshot: EngineeringResearchSnapshot | None = None,
        requirement_suggestion_decisions: tuple[RequirementSuggestionDecision, ...] | None = None,
        engineering_plan: EngineeringPlan | None = None,
        engineering_plan_review: PlanReviewDecision | None = None,
        architecture_design: object | None = None,
        architecture_review: object | None = None,
        design_truth: DesignTruth | None = None,
    ) -> None:
        detail = self._repository.conversation_for_session(session_id)
        if detail is None:
            return
        turns = tuple(
            turn if turn.request_id != request_id else ConversationTurn(
                turn_id=turn.turn_id, request_id=turn.request_id, trace_id=trace_id or turn.trace_id, route=route or turn.route, status=(ConversationTurnStatus.WAITING_FOR_CLARIFICATION if analysis_state == "WAITING_FOR_CLARIFICATION" else ConversationTurnStatus.FAILED),
                continuation_of_turn_id=turn.continuation_of_turn_id, previous_analysis_state=turn.previous_analysis_state, previous_route=turn.previous_route,
                previous_intent=turn.previous_intent,
                question=turn.question, response_type=turn.response_type,
                artifact_refs=turn.artifact_refs,
                answer_text=answer_text,
                context_references=context_references or turn.context_references,
                error_code=error_code,
                analysis_state=analysis_state,
                analysis_reason_code=analysis_reason_code,
                created_at=turn.created_at, completed_at=completed_at,
                terminal_event=terminal_event,
            ) for turn in detail.turns
        )
        next_requirement = requirement_context or detail.requirement_context
        next_research = research_snapshot if research_snapshot is not None else detail.research_snapshot
        next_suggestion_decisions = (
            requirement_suggestion_decisions
            if requirement_suggestion_decisions is not None
            else detail.requirement_suggestion_decisions
        )
        next_plan = engineering_plan or detail.engineering_plan
        next_review = engineering_plan_review or detail.engineering_plan_review
        next_architecture = architecture_design or detail.architecture_design
        next_architecture_review = architecture_review or detail.architecture_review
        next_design_truth = design_truth or detail.design_truth
        if (
            next_design_truth is not None
            and next_design_truth.origin is DesignTruthOrigin.CREATE
            and next_design_truth.tenant_id != detail.tenant_id
            and next_design_truth.tenant_id == "default-tenant"
            and next_architecture is not None
        ):
            legacy_fingerprint = next_design_truth.fingerprint
            next_design_truth = DesignTruthBuilder().build_from_approved_architecture(
                next_architecture,
                tenant_id=detail.tenant_id,
                conversation_id=detail.conversation_id,
                architecture_review=next_architecture_review,
            ).model_copy(update={"fingerprint": legacy_fingerprint})
        if next_plan is not None and next_requirement is not None and next_plan.context_fingerprint != next_requirement.fingerprint and next_plan.status.value != "SUPERSEDED":
            next_plan = None
            next_review = None
        if next_architecture is not None and next_plan is not None and next_architecture.plan_fingerprint != next_plan.fingerprint and next_architecture.status.value not in {"SUPERSEDED", "INVALIDATED"}:
            next_architecture = None
            next_architecture_review = None
        next_hardware_selection = detail.hardware_selection
        if next_hardware_selection is not None:
            proposal = next_hardware_selection.proposal
            if (
                next_requirement is None
                or next_architecture is None
                or next_architecture.status is not ArchitectureDesignStatus.APPROVED
                or proposal.architecture_id != next_architecture.architecture_id
                or proposal.architecture_fingerprint != next_architecture.fingerprint
                or proposal.requirements_fingerprint != next_requirement.fingerprint
            ):
                next_hardware_selection = None
        if next_design_truth is not None and next_design_truth.origin is DesignTruthOrigin.CREATE:
            if next_architecture is None or next_architecture.status is not ArchitectureDesignStatus.APPROVED:
                next_design_truth = None
            elif (
                next_design_truth.project_id != detail.project_id
                or next_design_truth.architecture_id != next_architecture.architecture_id
            ):
                next_design_truth = None
        elif next_design_truth is not None and (
            next_design_truth.project_id != detail.project_id
            or next_design_truth.architecture_id is not None
        ):
            next_design_truth = None
        next_knowledge_model = None if next_design_truth is None else generate_knowledge_model(next_design_truth)
        next_teaching_context = detail.teaching_context
        if (
            next_knowledge_model is None
            or detail.engineering_knowledge_model is None
            or next_knowledge_model.fingerprint != detail.engineering_knowledge_model.fingerprint
        ):
            next_teaching_context = None
        next_workflow = None
        if (
            next_plan is not None
            and next_architecture is not None
            and next_plan.status is PlanStatus.APPROVED
            and next_architecture.status is ArchitectureDesignStatus.APPROVED
        ):
            if detail.engineering_workflow is not None and _workflow_sources_match(
                detail.engineering_workflow, next_plan, next_architecture
            ):
                next_workflow = detail.engineering_workflow
            else:
                next_workflow = generate_engineering_workflow(next_architecture, next_plan)
        next_hardware_design_spec = None
        if next_workflow is not None and next_architecture is not None:
            try:
                next_hardware_design_spec = generate_hardware_design_spec(
                    next_architecture,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_hardware_design_spec = None
        next_firmware_spec = None
        if next_hardware_design_spec is not None and next_workflow is not None:
            try:
                next_firmware_spec = generate_firmware_spec(
                    next_hardware_design_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_firmware_spec = None
        next_simulation_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_workflow is not None:
            try:
                next_simulation_spec = generate_simulation_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_simulation_spec = None
        next_verification_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_simulation_spec is not None and next_workflow is not None:
            try:
                next_verification_spec = generate_verification_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_simulation_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_verification_spec = None
        next_implementation_spec = None
        if next_hardware_design_spec is not None and next_firmware_spec is not None and next_simulation_spec is not None and next_verification_spec is not None and next_workflow is not None:
            try:
                next_implementation_spec = generate_implementation_spec(
                    next_hardware_design_spec,
                    next_firmware_spec,
                    next_simulation_spec,
                    next_verification_spec,
                    next_workflow,
                )
            except (TypeError, ValueError):
                next_implementation_spec = None
        (
            next_capability_evaluation,
            next_execution_intents,
            next_execution_authorization,
        ) = _capability_state(
            next_implementation_spec,
            conversation_id=detail.conversation_id,
            requested_by=detail.requester,
            current_authorization=detail.execution_authorization,
        )
        next_firmware_source_generation = _firmware_source_state(
            tenant_id=detail.tenant_id,
            firmware_spec=next_firmware_spec,
            implementation_spec=next_implementation_spec,
            execution_intents=next_execution_intents,
            execution_authorization=next_execution_authorization,
            existing=detail.firmware_source_generation,
            command=None,
        )
        next_schematic_production = _schematic_production_state(design_truth=next_design_truth, hardware_design=next_hardware_design_spec, implementation_spec=next_implementation_spec, execution_intents=next_execution_intents, execution_authorization=next_execution_authorization, existing=detail.schematic_production, command=None, conversation_id=detail.conversation_id, requester=detail.requester, schematic_planning=detail.schematic_planning)
        updated = detail.model_copy(update={"turns": turns, "updated_at": completed_at, "requirement_context": next_requirement, "research_snapshot": next_research, "requirement_suggestion_decisions": next_suggestion_decisions, "engineering_plan": next_plan, "engineering_plan_review": next_review, "architecture_design": next_architecture, "architecture_review": next_architecture_review, "design_truth": next_design_truth, "engineering_knowledge_model": next_knowledge_model, "teaching_context": next_teaching_context, "engineering_workflow": next_workflow, "hardware_design_spec": next_hardware_design_spec, "hardware_selection": next_hardware_selection, "firmware_spec": next_firmware_spec, "simulation_spec": next_simulation_spec, "verification_spec": next_verification_spec, "implementation_spec": next_implementation_spec, "capability_evaluation": next_capability_evaluation, "execution_intents": next_execution_intents, "execution_authorization": next_execution_authorization, "firmware_source_generation": next_firmware_source_generation, "schematic_production": next_schematic_production, "fingerprint": "sha256:" + "0" * 64})
        self._repository.save_conversation(ConversationDetail.model_validate(updated.model_dump(mode="python")))


__all__ = ("ProjectCatalogRuntime", "ProjectClockPort", "SystemProjectClock")
