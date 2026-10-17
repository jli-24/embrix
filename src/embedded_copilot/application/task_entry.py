"""Opt-in application entry for a reviewed firmware build task.

The caller injects authenticated tenant and requester identity independently of
ApplicationQuestionRequest; both identities must match before catalog access.
Catalog remains the authority for the conversation, approved plan and review.
This module never grants execution approval or drives an execution worker.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from embedded_copilot.engineering_copilot.common import fp, ident, validate_copy
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.engineering_task_runtime.build_state import (
    FirmwareBuildCoordinationState,
)
from embedded_copilot.engineering_task_runtime.contracts import (
    CoordinationMetadata,
    EngineeringTaskCoordination,
    PlanReference,
    ProjectReference,
    RequestReference,
    StageReference,
    StageSourceReference,
    TaskIdentity,
    validate_coordination,
)
from embedded_copilot.engineering_task_runtime.models import EngineeringTaskRequest
from embedded_copilot.execution_worker.contracts import (
    OperationStatus,
    OperationStatusView,
    OperationType,
)
from embedded_copilot.firmware_engineering import FirmwareBuildRequest
from embedded_copilot.firmware_engineering.readiness import is_firmware_build_request
from embedded_copilot.project_catalog.brownfield_build import BrownfieldBuildContext
from embedded_copilot.project_catalog.models import ConversationDetail, ProjectView

from .models import (
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
)
from .ports import ApplicationAgentRuntimePort
from .task_firmware_build import FirmwareBuildTaskService


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode()
    ).hexdigest()


def _build_command(value: str) -> bool:
    normalized = re.sub(
        r"[\s，。！？、,.!?]+", "", unicodedata.normalize("NFKC", value)
    ).casefold()
    return (
        is_firmware_build_request(value) or normalized == "帮我检查当前固件是否能构建"
    )


class TaskClassifierPort(Protocol):
    def classify(
        self, request: ApplicationQuestionRequest
    ) -> ApplicationRouteDecision: ...


class TaskCatalogPort(Protocol):
    def get_project(self, project_id: str, *, tenant_id: str) -> ProjectView: ...

    def conversation(
        self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str
    ) -> ConversationDetail: ...


class BuildStatusPort(Protocol):
    def get_status(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationStatusView | None: ...


@dataclass(frozen=True)
class EngineeringIntent:
    objective: str
    project_reference: str
    engineering_domain: Literal["FIRMWARE"]
    expected_outcome: str
    constraints: tuple[str, ...]
    source_context: tuple[str, str, str, str, str]
    correlation_id: str


@dataclass(frozen=True)
class BuildBindings:
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    expires_at: datetime
    approval_nonce: str = "task-build-submission"
    resolved_toolchain_version: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "target_id",
            "workspace_snapshot_id",
            "proposal_id",
            "approval_nonce",
        ):
            ident(getattr(self, name), name)
        for name in (
            "target_fingerprint",
            "workspace_fingerprint",
            "proposal_fingerprint",
        ):
            fp(getattr(self, name), name)
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise ValueError("expires_at is invalid")
        if self.resolved_toolchain_version is not None:
            ident(self.resolved_toolchain_version, "resolved_toolchain_version")


@dataclass(frozen=True)
class PreparedTaskEntry:
    question: ApplicationQuestionRequest
    conversation_id: str
    intent: EngineeringIntent
    plan_reference: PlanReference
    stage_reference: StageReference
    task_created_at: datetime
    build_request: FirmwareBuildRequest
    bindings: BuildBindings
    confirmation_binding: str


@dataclass(frozen=True)
class TaskEntryResult:
    status: str
    message: str
    prepared: PreparedTaskEntry | None = None
    task_id: str | None = None
    operation_id: str | None = None
    receipt_available: bool = False
    evidence_available: bool = False

    def user_projection(self) -> UserTaskProjection:
        """Return only fixed Embrix copy and safe references, never review internals."""
        return UserTaskProjection(
            status=self.status,
            message=self.message,
            task_id=self.task_id,
            operation_id=self.operation_id,
            receipt_available=self.receipt_available,
            evidence_available=self.evidence_available,
        )


@dataclass(frozen=True)
class UserTaskProjection:
    status: str
    message: str
    task_id: str | None
    operation_id: str | None
    receipt_available: bool
    evidence_available: bool


class _CatalogSource:
    """Read-only source bound to a trusted application identity and one catalog turn."""

    def __init__(
        self,
        catalog: TaskCatalogPort,
        prepared: PreparedTaskEntry,
        task: EngineeringTaskRequest,
    ) -> None:
        self._catalog = catalog
        self._prepared = prepared
        self._task = task
        self.bound_tenant_id = task.tenant_id
        self.bound_project_id = task.project_id
        self.bound_principal_id = prepared.question.requester

    def _detail(self) -> ConversationDetail | None:
        request = self._prepared.question
        detail = validate_copy(
            self._catalog.conversation(
                request.project_id,
                self._prepared.conversation_id,
                tenant_id=request.tenant_id,
                requester=request.requester,
            ),
            ConversationDetail,
        )
        if (
            detail.project_id,
            detail.tenant_id,
            detail.requester,
            detail.conversation_id,
        ) != (
            request.project_id,
            request.tenant_id,
            request.requester,
            self._prepared.conversation_id,
        ):
            return None
        turn_id, request_id, question, created_at, _ = (
            self._prepared.intent.source_context
        )
        matches = [turn for turn in detail.turns if turn.turn_id == turn_id]
        if len(matches) != 1:
            return None
        turn = matches[0]
        if (turn.request_id, turn.question, turn.created_at.isoformat()) != (
            request_id,
            question,
            created_at,
        ):
            return None
        return detail

    def resolve_task_request(self, task_id: str) -> EngineeringTaskRequest | None:
        if task_id != self._task.task_id or self._detail() is None:
            return None
        return self._task

    def resolve_plan(
        self, task_id: str, plan_id: str
    ) -> tuple[EngineeringPlan, PlanReviewDecision] | None:
        detail = self._detail()
        if detail is None or task_id != self._task.task_id:
            return None
        plan, review = detail.engineering_plan, detail.engineering_plan_review
        if plan is None or review is None or plan.plan_id != plan_id:
            return None
        return plan, review


class _BrownfieldCatalogSource:
    """Re-read the approved Catalog record during Task Service validation."""

    def __init__(
        self, catalog: TaskCatalogPort, context: BrownfieldBuildContext,
        task: EngineeringTaskRequest,
    ) -> None:
        self._catalog = catalog
        self._fingerprint = context.fingerprint
        self._task = task
        self.bound_tenant_id = context.tenant_id
        self.bound_project_id = context.project_id
        self.bound_principal_id = context.requester

    def _current(self) -> BrownfieldBuildContext | None:
        current = self._catalog.brownfield_build_context(
            self.bound_project_id, tenant_id=self.bound_tenant_id,
            requester=self.bound_principal_id,
        )
        checked = validate_copy(current, BrownfieldBuildContext)
        return checked if checked.fingerprint == self._fingerprint else None

    def resolve_task_request(self, task_id: str) -> EngineeringTaskRequest | None:
        return self._task if task_id == self._task.task_id and self._current() else None

    def resolve_plan(
        self, task_id: str, plan_id: str
    ) -> tuple[EngineeringPlan, PlanReviewDecision] | None:
        context = self._current()
        if (
            task_id != self._task.task_id or context is None
            or context.review is None or context.plan.plan_id != plan_id
        ):
            return None
        return context.plan, context.review


def submit_brownfield_build(
    *, catalog: TaskCatalogPort, build_service: FirmwareBuildTaskService,
    context: BrownfieldBuildContext, build_request: FirmwareBuildRequest,
    bindings: BuildBindings, user_confirmed: bool,
) -> FirmwareBuildCoordinationState:
    """Explicit Brownfield Task Entry using the existing Plan and Task Service."""
    if user_confirmed is not True:
        raise ValueError("build task confirmation is required")
    checked = validate_copy(context, BrownfieldBuildContext)
    request = validate_copy(build_request, FirmwareBuildRequest)
    if (
        checked.plan.status is not PlanStatus.APPROVED
        or checked.review is None
        or checked.review.status is not PlanReviewStatus.APPROVED
        or request.project_id != checked.project_id
        or request.conversation_id is not None
        or request.firmware_spec_fingerprint != checked.firmware_spec.fingerprint
        or request.implementation_fingerprint != checked.implementation_spec.fingerprint
        or request.configuration != checked.configuration
        or request.expected_source_revision != checked.firmware_spec.source_revision
        or type(bindings) is not BuildBindings
        or bindings.target_id != checked.workspace_target_id
        or bindings.workspace_fingerprint != checked.workspace_fingerprint
    ):
        raise ValueError("brownfield build context binding rejected")
    phase = checked.plan.phases[0]
    plan_ref = PlanReference(
        plan_id=checked.plan.plan_id,
        content_fingerprint=checked.plan.content_fingerprint,
        review_fingerprint=checked.review.fingerprint,
    )
    stage_ref = StageReference(
        stage_id=phase.phase_id, plan_reference=plan_ref,
        source_reference=StageSourceReference(
            source_type="APPROVED_PLAN_PHASE", phase_id=phase.phase_id,
            phase_fingerprint=phase.fingerprint,
        ),
    )
    task = EngineeringTaskRequest(
        task_id="brownfield-build-task-" + _digest((
            checked.tenant_id, checked.project_id, checked.fingerprint,
            request.fingerprint,
        ))[:40],
        tenant_id=checked.tenant_id, project_id=checked.project_id,
        objective="构建已确认的当前工程源码快照", constraints=(),
        created_at=checked.created_at,
    )
    coordination = EngineeringTaskCoordination(
        task_identity=TaskIdentity(
            task_id=task.task_id, tenant_id=task.tenant_id,
            project_id=task.project_id,
        ),
        request_reference=RequestReference(
            task_id=task.task_id, request_fingerprint=task.fingerprint,
        ),
        project_reference=ProjectReference(
            tenant_id=task.tenant_id, project_id=task.project_id,
        ),
        plan_reference=plan_ref, current_stage_reference=stage_ref,
        coordination_metadata=CoordinationMetadata(
            reference_id="brownfield-build-" + checked.plan.plan_id,
        ),
    )
    return build_service.submit(
        coordination, _BrownfieldCatalogSource(catalog, checked, task), request,
        requester_id=checked.requester,
        target_id=bindings.target_id,
        target_fingerprint=bindings.target_fingerprint,
        workspace_snapshot_id=bindings.workspace_snapshot_id,
        workspace_fingerprint=bindings.workspace_fingerprint,
        proposal_id=bindings.proposal_id,
        proposal_fingerprint=bindings.proposal_fingerprint,
        expires_at=bindings.expires_at,
        approval_nonce=bindings.approval_nonce,
        resolved_toolchain_version=bindings.resolved_toolchain_version,
    )


def _project(
    state: FirmwareBuildCoordinationState, status_reader: BuildStatusPort | None
) -> TaskEntryResult:
    status = state.coordination_status
    messages = {
        "BUILD_PASS": "当前固件在本次构建配置下构建通过。此结果不代表硬件运行验证完成。",
        "BUILD_FAILED": "本次固件构建失败。已保留构建回执和失败证据。",
        "UNVERIFIED": "构建结果尚未完成验证。",
        "BLOCKED": "构建任务当前受阻，尚无可确认的构建结果。",
    }
    message = messages[status]
    if (
        status == "BLOCKED"
        and state.receipt_reference is None
        and status_reader is not None
    ):
        try:
            operation_status = validate_copy(
                status_reader.get_status(
                    tenant_id=state.tenant_id,
                    project_id=state.project_id,
                    operation_id=state.operation_reference.operation_id,
                ),
                OperationStatusView,
            )
            if (
                operation_status.status is OperationStatus.WAIT_APPROVAL
                and operation_status.operation_type is OperationType.BUILD
                and operation_status.receipt_fingerprint is None
                and operation_status.tenant_id == state.tenant_id
                and operation_status.project_id == state.project_id
                and operation_status.operation_id
                == state.operation_reference.operation_id
                and operation_status.correlation is not None
                and operation_status.correlation.task_id == state.task_id
            ):
                message = "已建立固件构建检查任务。构建操作等待审批，尚未执行。"
        except Exception:  # noqa: BLE001 - optional read never elevates a blocked projection
            message = messages[status]
    return TaskEntryResult(
        status=status,
        message=message,
        task_id=state.task_id,
        operation_id=state.operation_reference.operation_id,
        receipt_available=state.receipt_reference is not None,
        evidence_available=state.receipt_reference is not None
        and state.receipt_reference.evidence_fingerprint is not None,
    )


def _checked_project(
    raw_state: FirmwareBuildCoordinationState,
    coordination: EngineeringTaskCoordination,
    build_request: FirmwareBuildRequest,
    status_reader: BuildStatusPort | None,
    *,
    operation_id: str | None = None,
) -> TaskEntryResult:
    state = validate_copy(raw_state, FirmwareBuildCoordinationState)
    task = coordination.task_identity
    if (
        (state.task_id, state.tenant_id, state.project_id)
        != (task.task_id, task.tenant_id, task.project_id)
        or state.plan_reference != coordination.plan_reference
        or state.stage_reference != coordination.current_stage_reference
        or state.operation_reference.build_request_fingerprint
        != build_request.fingerprint
        or (
            operation_id is not None
            and state.operation_reference.operation_id != operation_id
        )
    ):
        raise ValueError("build state binding rejected")
    return _project(state, status_reader)


class EngineeringTaskEntry:
    """Two-step task entry: prepare reviewed intent, then explicit confirmation."""

    def __init__(
        self,
        *,
        classifier: TaskClassifierPort | ApplicationAgentRuntimePort,
        catalog: TaskCatalogPort,
        build_service: FirmwareBuildTaskService,
        trusted_tenant_id: str,
        trusted_requester: str,
        status_reader: BuildStatusPort | None = None,
    ) -> None:
        self._classifier = classifier
        self._catalog = catalog
        self._build_service = build_service
        self._trusted_tenant_id = trusted_tenant_id
        self._trusted_requester = trusted_requester
        self._status_reader = status_reader
        self._pending: dict[str, PreparedTaskEntry] = {}

    def prepare(
        self,
        question: ApplicationQuestionRequest,
        *,
        conversation_id: str,
        action: Literal["FIRMWARE_BUILD_CHECK"] | None,
        stage_id: str | None = None,
        build_request: FirmwareBuildRequest | None = None,
        bindings: BuildBindings | None = None,
    ) -> TaskEntryResult:
        try:
            checked = validate_copy(question, ApplicationQuestionRequest)
            classify = getattr(self._classifier, "classify", None)
            decision = validate_copy(
                classify(checked)
                if callable(classify)
                else self._classifier.route(checked),
                ApplicationRouteDecision,
            )
            if (
                decision.request_id != checked.request_id
                or decision.request_fingerprint != checked.fingerprint
            ):
                return TaskEntryResult("CLASSIFICATION_REJECTED", "无法确认请求类型。")
            if action is None:
                return TaskEntryResult("INFORMATIONAL", "已按普通问题处理。")
            if action != "FIRMWARE_BUILD_CHECK":
                return TaskEntryResult("CLASSIFICATION_REJECTED", "无法确认请求类型。")
            if decision.route not in {
                ApplicationRoute.FIRMWARE,
                ApplicationRoute.CLARIFICATION_REQUIRED,
            } or not _build_command(checked.question):
                return TaskEntryResult(
                    "CLASSIFICATION_REJECTED", "无法确认固件构建检查请求。"
                )
            if (checked.tenant_id, checked.requester) != (
                self._trusted_tenant_id,
                self._trusted_requester,
            ) or not conversation_id:
                return TaskEntryResult("IDENTITY_REJECTED", "需要确认当前项目和身份。")
            project = validate_copy(
                self._catalog.get_project(
                    checked.project_id, tenant_id=checked.tenant_id
                ),
                ProjectView,
            )
            if (project.project_id, project.tenant_id) != (
                checked.project_id,
                checked.tenant_id,
            ):
                return TaskEntryResult("IDENTITY_REJECTED", "需要确认当前项目和身份。")
            detail = validate_copy(
                self._catalog.conversation(
                    checked.project_id,
                    conversation_id,
                    tenant_id=checked.tenant_id,
                    requester=checked.requester,
                ),
                ConversationDetail,
            )
            if (
                detail.project_id,
                detail.tenant_id,
                detail.requester,
                detail.conversation_id,
            ) != (
                checked.project_id,
                checked.tenant_id,
                checked.requester,
                conversation_id,
            ):
                return TaskEntryResult("SOURCE_UNAVAILABLE", "无法确认当前请求来源。")
            turns = [
                turn
                for turn in detail.turns
                if turn.request_id == checked.request_id
                and turn.question == checked.question
            ]
            if len(turns) != 1:
                return TaskEntryResult("SOURCE_UNAVAILABLE", "无法确认当前请求来源。")
            turn = turns[0]
            if (
                not checked.question.strip()
                or build_request is None
                or type(bindings) is not BuildBindings
                or (
                    type(build_request) is FirmwareBuildRequest
                    and build_request.configuration is None
                )
            ):
                return TaskEntryResult(
                    "MISSING_INFORMATION", "需要确认当前项目、固件版本或构建配置。"
                )
            build = validate_copy(build_request, FirmwareBuildRequest)
            if build.project_id != checked.project_id or build.configuration is None:
                return TaskEntryResult(
                    "MISSING_INFORMATION", "需要确认当前项目、固件版本或构建配置。"
                )
            plan = detail.engineering_plan
            review = detail.engineering_plan_review
            if plan is None or review is None:
                return TaskEntryResult(
                    "WAITING_PLAN", "当前任务缺少已批准的构建计划，需要先完成计划确认。"
                )
            plan = validate_copy(plan, EngineeringPlan)
            review = validate_copy(review, PlanReviewDecision)
            if (
                plan.project_id != checked.project_id
                or plan.status is not PlanStatus.APPROVED
                or review.project_id != checked.project_id
                or review.plan_id != plan.plan_id
                or review.status is not PlanReviewStatus.APPROVED
                or review.proposal_fingerprint != plan.content_fingerprint
            ):
                return TaskEntryResult(
                    "WAITING_PLAN", "当前任务缺少已批准的构建计划，需要先完成计划确认。"
                )
            phase = next(
                (item for item in plan.phases if item.phase_id == stage_id), None
            )
            if phase is None or not any(
                item.output_type == "BUILD_ARTIFACT" for item in phase.outputs
            ):
                return TaskEntryResult(
                    "STAGE_REJECTED", "当前计划没有对应的固件构建阶段。"
                )
            source_context = (
                turn.turn_id,
                turn.request_id,
                turn.question,
                turn.created_at.isoformat(),
                conversation_id,
            )
            intent = EngineeringIntent(
                objective=checked.question,
                project_reference=checked.project_id,
                engineering_domain="FIRMWARE",
                expected_outcome="检查固件构建结果",
                constraints=checked.constraints,
                source_context=source_context,
                correlation_id=checked.request_id,
            )
            plan_ref = PlanReference(
                plan_id=plan.plan_id,
                content_fingerprint=plan.content_fingerprint,
                review_fingerprint=review.fingerprint,
            )
            stage_ref = StageReference(
                stage_id=phase.phase_id,
                plan_reference=plan_ref,
                source_reference=StageSourceReference(
                    source_type="APPROVED_PLAN_PHASE",
                    phase_id=phase.phase_id,
                    phase_fingerprint=phase.fingerprint,
                ),
            )
            binding = _digest(
                (
                    intent,
                    plan_ref.fingerprint,
                    stage_ref.fingerprint,
                    turn.created_at,
                    build.fingerprint,
                    bindings,
                )
            )
            prepared = PreparedTaskEntry(
                checked,
                conversation_id,
                intent,
                plan_ref,
                stage_ref,
                turn.created_at,
                build,
                bindings,
                binding,
            )
            self._pending[binding] = prepared
            return TaskEntryResult(
                "READY_CONFIRMATION",
                "请确认当前构建计划、阶段和构建配置。",
                prepared=prepared,
            )
        except Exception:  # noqa: BLE001 - catalog and classification ports fail closed
            return TaskEntryResult("SOURCE_UNAVAILABLE", "无法确认当前请求来源。")

    def submit(
        self, prepared: PreparedTaskEntry, *, user_confirmed: bool
    ) -> TaskEntryResult:
        if user_confirmed is not True:
            return TaskEntryResult(
                "CONFIRMATION_REQUIRED", "需要确认当前构建计划、阶段和构建配置。"
            )
        if not isinstance(prepared, PreparedTaskEntry):
            return TaskEntryResult("SOURCE_UNAVAILABLE", "无法确认当前请求来源。")
        try:
            if self._pending.get(prepared.confirmation_binding) is not prepared:
                return TaskEntryResult(
                    "CONFIRMATION_REQUIRED", "构建信息已变化，需要重新确认。"
                )
            _, coordination, source = self._resolve(prepared)
            b = prepared.bindings
            state = self._build_service.submit(
                coordination,
                source,
                prepared.build_request,
                requester_id=self._trusted_requester,
                target_id=b.target_id,
                target_fingerprint=b.target_fingerprint,
                workspace_snapshot_id=b.workspace_snapshot_id,
                workspace_fingerprint=b.workspace_fingerprint,
                proposal_id=b.proposal_id,
                proposal_fingerprint=b.proposal_fingerprint,
                expires_at=b.expires_at,
                approval_nonce=b.approval_nonce,
                resolved_toolchain_version=b.resolved_toolchain_version,
            )
            return _checked_project(
                state, coordination, prepared.build_request, self._status_reader
            )
        except Exception:  # noqa: BLE001 - no exception details cross the user projection
            return TaskEntryResult(
                "SOURCE_UNAVAILABLE", "任务所依据的计划或构建信息已变化，需要重新确认。"
            )

    def refresh(
        self, prepared: PreparedTaskEntry, *, operation_id: str
    ) -> TaskEntryResult:
        try:
            if self._pending.get(prepared.confirmation_binding) is not prepared:
                raise ValueError("entry changed")
            _, coordination, source = self._resolve(prepared)
            return _checked_project(
                self._build_service.refresh(
                    coordination,
                    source,
                    prepared.build_request,
                    operation_id=operation_id,
                ),
                coordination,
                prepared.build_request,
                self._status_reader,
                operation_id=operation_id,
            )
        except Exception:  # noqa: BLE001 - fail closed at authoritative source boundary
            return TaskEntryResult("SOURCE_UNAVAILABLE", "无法确认当前构建结果。")

    def _resolve(
        self, prepared: PreparedTaskEntry
    ) -> tuple[EngineeringTaskRequest, EngineeringTaskCoordination, _CatalogSource]:
        question = validate_copy(prepared.question, ApplicationQuestionRequest)
        if (question.tenant_id, question.requester) != (
            self._trusted_tenant_id,
            self._trusted_requester,
        ):
            raise ValueError("identity changed")
        if (
            _digest(
                (
                    prepared.intent,
                    prepared.plan_reference.fingerprint,
                    prepared.stage_reference.fingerprint,
                    prepared.task_created_at,
                    prepared.build_request.fingerprint,
                    prepared.bindings,
                )
            )
            != prepared.confirmation_binding
        ):
            raise ValueError("confirmation changed")
        task = EngineeringTaskRequest(
            task_id="entry-"
            + _digest(
                (
                    question.tenant_id,
                    question.project_id,
                    prepared.conversation_id,
                    prepared.intent.source_context[0],
                    question.request_id,
                )
            )[:40],
            tenant_id=self._trusted_tenant_id,
            project_id=question.project_id,
            objective=prepared.intent.objective,
            constraints=prepared.intent.constraints,
            created_at=prepared.task_created_at,
        )
        coordination = EngineeringTaskCoordination(
            task_identity=TaskIdentity(
                task_id=task.task_id,
                tenant_id=task.tenant_id,
                project_id=task.project_id,
            ),
            request_reference=RequestReference(
                task_id=task.task_id, request_fingerprint=task.fingerprint
            ),
            project_reference=ProjectReference(
                tenant_id=task.tenant_id, project_id=task.project_id
            ),
            plan_reference=prepared.plan_reference,
            current_stage_reference=prepared.stage_reference,
            coordination_metadata=CoordinationMetadata(
                reference_id="entry-" + question.request_id
            ),
        )
        project = validate_copy(
            self._catalog.get_project(
                question.project_id, tenant_id=self._trusted_tenant_id
            ),
            ProjectView,
        )
        if (project.project_id, project.tenant_id) != (
            question.project_id,
            question.tenant_id,
        ):
            raise ValueError("project changed")
        source = _CatalogSource(self._catalog, prepared, task)
        validate_coordination(coordination, source)
        return task, coordination, source
