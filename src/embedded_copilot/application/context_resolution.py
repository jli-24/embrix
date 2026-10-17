"""Read-only, opt-in context resolution for a firmware build check.

The injected source owns discovery and current authority. Resolution produces
references for a later Task Entry confirmation; it never prepares or submits a task.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from embedded_copilot.engineering_copilot.common import validate_copy
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.engineering_workspace_apply.models import WorkspaceSnapshot
from embedded_copilot.execution_worker.contracts import (
    ExecutionProposal,
    ExecutionTarget,
    OperationType,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
)
from embedded_copilot.firmware_engineering.models import canonical_fingerprint
from embedded_copilot.project_catalog.models import ConversationDetail, ProjectView

from .task_entry import BuildBindings, EngineeringIntent


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    AMBIGUOUS_CONTEXT = "AMBIGUOUS_CONTEXT"
    WAITING_FOR_PLAN = "WAITING_FOR_PLAN"
    CONTEXT_STALE = "CONTEXT_STALE"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class TrustedApplicationContext:
    tenant_id: str
    requester: str
    conversation_id: str
    current_project_id: str | None = None


@dataclass(frozen=True)
class ContextSnapshot:
    """Current typed records returned by a trusted read-only source."""

    project: ProjectView
    conversation: ConversationDetail | None
    build_request: FirmwareBuildRequest | None
    bindings: BuildBindings | None
    target: ExecutionTarget | None
    proposal: ExecutionProposal | None
    workspace: WorkspaceAuthoritySnapshot | None
    current_configuration: FirmwareBuildConfiguration | None
    authoritative_plan: EngineeringPlan | None = None
    authoritative_review: PlanReviewDecision | None = None


@dataclass(frozen=True)
class WorkspaceAuthoritySnapshot:
    snapshot_id: str
    snapshot: WorkspaceSnapshot


class EngineeringContextSourcePort(Protocol):
    def projects(
        self, *, tenant_id: str, requester: str
    ) -> tuple[ProjectView, ...]: ...

    def snapshot(
        self, *, tenant_id: str, requester: str, project_id: str, conversation_id: str
    ) -> ContextSnapshot | None: ...


@dataclass(frozen=True)
class ConfirmationPayload:
    project: str
    source_version: str
    build_configuration: str
    target: str
    plan_stage: str


@dataclass(frozen=True)
class ResolvedEngineeringContext:
    project_reference: str
    workspace_reference: str
    source_reference: str
    plan_reference: str
    stage_reference: str
    build_configuration: str
    target_reference: str
    proposal_reference: str
    context_version: str
    fingerprints: tuple[tuple[str, str], ...]
    confirmation: ConfirmationPayload


@dataclass(frozen=True)
class ContextResolutionResult:
    status: ResolutionStatus
    context: ResolvedEngineeringContext | None = None


def _version(values: tuple[tuple[str, str], ...]) -> str:
    serialized = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class EngineeringContextResolver:
    def __init__(self, source: EngineeringContextSourcePort) -> None:
        self._source = source

    def resolve(
        self,
        intent: EngineeringIntent,
        application: TrustedApplicationContext,
        *,
        expected_context_version: str | None = None,
    ) -> ContextResolutionResult:
        if (
            type(intent) is not EngineeringIntent
            or type(application) is not TrustedApplicationContext
            or intent.engineering_domain != "FIRMWARE"
            or not intent.correlation_id
            or len(intent.source_context) != 5
            or intent.source_context[1] != intent.correlation_id
            or intent.source_context[2] != intent.objective
            or intent.source_context[4] != application.conversation_id
            or not application.tenant_id
            or not application.requester
        ):
            return ContextResolutionResult(ResolutionStatus.REJECTED)
        try:
            projects = tuple(
                validate_copy(item, ProjectView)
                for item in self._source.projects(
                    tenant_id=application.tenant_id, requester=application.requester
                )
            )
            if any(item.tenant_id != application.tenant_id for item in projects):
                return ContextResolutionResult(ResolutionStatus.REJECTED)
            if len({item.project_id for item in projects}) != len(projects):
                return ContextResolutionResult(ResolutionStatus.BLOCKED)
            if application.current_project_id is not None:
                matches = [
                    item
                    for item in projects
                    if item.project_id == application.current_project_id
                ]
                if len(matches) != 1:
                    return ContextResolutionResult(ResolutionStatus.REJECTED)
            else:
                matches = projects
            if not matches:
                return ContextResolutionResult(ResolutionStatus.CLARIFICATION_REQUIRED)
            if len(matches) != 1:
                return ContextResolutionResult(ResolutionStatus.AMBIGUOUS_CONTEXT)
            project = matches[0]
            # The intent's project text is a correlation check, never a selection source.
            if intent.project_reference != project.project_id:
                return ContextResolutionResult(ResolutionStatus.REJECTED)
            raw = self._source.snapshot(
                tenant_id=application.tenant_id,
                requester=application.requester,
                project_id=project.project_id,
                conversation_id=application.conversation_id,
            )
            if type(raw) is not ContextSnapshot:
                return ContextResolutionResult(ResolutionStatus.BLOCKED)
            current_project = validate_copy(raw.project, ProjectView)
            if raw.conversation is None:
                return ContextResolutionResult(ResolutionStatus.BLOCKED)
            detail = validate_copy(raw.conversation, ConversationDetail)
            if current_project != project or (
                detail.tenant_id,
                detail.project_id,
                detail.requester,
                detail.conversation_id,
            ) != (
                application.tenant_id,
                project.project_id,
                application.requester,
                application.conversation_id,
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            turn_id, request_id, question, created_at, _ = intent.source_context
            turns = [item for item in detail.turns if item.turn_id == turn_id]
            if len(turns) != 1 or (
                turns[0].request_id,
                turns[0].question,
                turns[0].created_at.isoformat(),
            ) != (request_id, question, created_at):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            if (raw.authoritative_plan is None) != (raw.authoritative_review is None):
                return ContextResolutionResult(ResolutionStatus.BLOCKED)
            if raw.authoritative_plan is not None:
                plan = validate_copy(raw.authoritative_plan, EngineeringPlan)
                review = validate_copy(raw.authoritative_review, PlanReviewDecision)
            else:
                plan, review = detail.engineering_plan, detail.engineering_plan_review
            if plan is None or review is None:
                return ContextResolutionResult(ResolutionStatus.WAITING_FOR_PLAN)
            if (
                plan.project_id != project.project_id
                or plan.status is not PlanStatus.APPROVED
                or review.project_id != project.project_id
                or review.plan_id != plan.plan_id
                or review.status is not PlanReviewStatus.APPROVED
                or review.proposal_fingerprint != plan.content_fingerprint
            ):
                return ContextResolutionResult(ResolutionStatus.REJECTED)
            stages = [
                phase
                for phase in plan.phases
                if any(
                    output.output_type == "BUILD_ARTIFACT" for output in phase.outputs
                )
            ]
            if not stages:
                return ContextResolutionResult(ResolutionStatus.REJECTED)
            if len(stages) > 1:
                return ContextResolutionResult(ResolutionStatus.AMBIGUOUS_CONTEXT)
            stage = stages[0]
            if (
                stage.project_id != project.project_id
                or stage.plan_id != plan.plan_id
                or any(
                    (output.project_id, output.plan_id, output.phase_id)
                    != (project.project_id, plan.plan_id, stage.phase_id)
                    for output in stage.outputs
                )
            ):
                return ContextResolutionResult(ResolutionStatus.REJECTED)
            if (
                raw.build_request is None
                or raw.build_request.configuration is None
                or raw.current_configuration is None
            ):
                return ContextResolutionResult(ResolutionStatus.CLARIFICATION_REQUIRED)
            build = validate_copy(raw.build_request, FirmwareBuildRequest)
            current_configuration = validate_copy(
                raw.current_configuration, FirmwareBuildConfiguration
            )
            if (
                detail.build_configuration is not None
                and detail.build_configuration != current_configuration
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            if build.configuration != current_configuration:
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            if (
                build.project_id != project.project_id
                or (
                    build.conversation_id is not None
                    and build.conversation_id != detail.conversation_id
                )
                or not build.source_artifacts
                or any(
                    not source.source_revision
                    or source.source_revision.casefold() in {"latest", "unknown"}
                    for source in build.source_artifacts
                )
            ):
                return ContextResolutionResult(ResolutionStatus.CLARIFICATION_REQUIRED)
            if (
                build.build_profile != build.configuration.profile
                or build.target_kind != build.configuration.target_kind
                or build.build_system != build.configuration.build_system
                or build.toolchain_id != build.configuration.toolchain_id
                or build.toolchain_version != build.configuration.toolchain_version
                or (
                    build.expected_source_revision is not None
                    and any(
                        source.source_revision != build.expected_source_revision
                        for source in build.source_artifacts
                    )
                )
                or (
                    build.actual_source_revision is not None
                    and any(
                        source.source_revision != build.actual_source_revision
                        for source in build.source_artifacts
                    )
                )
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            if (
                raw.bindings is None
                or raw.target is None
                or raw.proposal is None
                or type(raw.workspace) is not WorkspaceAuthoritySnapshot
            ):
                return ContextResolutionResult(ResolutionStatus.BLOCKED)
            bindings = raw.bindings
            if bindings.expires_at <= datetime.now(UTC):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            target = validate_copy(raw.target, ExecutionTarget)
            proposal = validate_copy(raw.proposal, ExecutionProposal)
            workspace = validate_copy(raw.workspace.snapshot, WorkspaceSnapshot)
            if (
                raw.workspace.snapshot_id != bindings.workspace_snapshot_id
                or workspace.project_fingerprint != bindings.workspace_fingerprint
                or (workspace.tenant_id, workspace.project_id, workspace.target_id)
                != (application.tenant_id, project.project_id, target.target_id)
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            files = {item.relative_file_reference: item for item in workspace.files}
            if any(
                source.relative_path not in files
                or not files[source.relative_path].exists
                or files[source.relative_path].content_fingerprint
                != source.content_fingerprint
                or files[source.relative_path].size != source.size_bytes
                for source in build.source_artifacts
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            if (
                (target.tenant_id, target.project_id, target.target_id)
                != (application.tenant_id, project.project_id, bindings.target_id)
                or target.fingerprint != bindings.target_fingerprint
                or (proposal.tenant_id, proposal.project_id, proposal.requester_id)
                != (application.tenant_id, project.project_id, application.requester)
                or proposal.operation_type is not OperationType.BUILD
                or (proposal.target_id, proposal.target_fingerprint)
                != (target.target_id, target.fingerprint)
                or (proposal.workspace_snapshot_id, proposal.workspace_fingerprint)
                != (bindings.workspace_snapshot_id, bindings.workspace_fingerprint)
                or (proposal.proposal_id, proposal.fingerprint)
                != (bindings.proposal_id, bindings.proposal_fingerprint)
                or proposal.build_configuration != build.configuration.profile
                or proposal.toolchain_id != build.configuration.toolchain_id
                or proposal.toolchain_version != build.configuration.toolchain_version
                or proposal.source_revision is None
                or proposal.firmware_build_request_fingerprint != build.fingerprint
                or any(
                    source.source_revision != proposal.source_revision
                    for source in build.source_artifacts
                )
                or target.target_kind != build.configuration.target_kind
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            config_fp = canonical_fingerprint(build.configuration)
            source_fp = _version(
                tuple(
                    sorted(
                        (
                            source.relative_path,
                            source.content_fingerprint + ":" + source.source_revision,
                        )
                        for source in build.source_artifacts
                    )
                )
            )
            fingerprints = (
                ("project", project.fingerprint),
                ("workspace", bindings.workspace_fingerprint),
                ("source", source_fp),
                ("plan", plan.content_fingerprint),
                ("review", review.fingerprint),
                ("stage", stage.fingerprint),
                ("configuration", config_fp),
                ("target", target.fingerprint),
                ("proposal", proposal.fingerprint),
            )
            version = _version(fingerprints)
            if (
                expected_context_version is not None
                and expected_context_version != version
            ):
                return ContextResolutionResult(ResolutionStatus.CONTEXT_STALE)
            revisions = {source.source_revision for source in build.source_artifacts}
            return ContextResolutionResult(
                ResolutionStatus.RESOLVED,
                ResolvedEngineeringContext(
                    project_reference=project.project_id,
                    workspace_reference=bindings.workspace_snapshot_id,
                    source_reference=build.firmware_reference,
                    plan_reference=plan.plan_id,
                    stage_reference=stage.phase_id,
                    build_configuration=config_fp,
                    target_reference=target.target_id,
                    proposal_reference=proposal.proposal_id,
                    context_version=version,
                    fingerprints=fingerprints,
                    confirmation=ConfirmationPayload(
                        project=project.name,
                        source_version=", ".join(sorted(revisions)),
                        build_configuration=build.configuration.profile,
                        target=target.board_family,
                        plan_stage=stage.name,
                    ),
                ),
            )
        except (TypeError, ValueError):
            return ContextResolutionResult(ResolutionStatus.REJECTED)
        except (OSError, RuntimeError):
            return ContextResolutionResult(ResolutionStatus.BLOCKED)
