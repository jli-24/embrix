"""Desktop firmware build entry backed by the existing engineering owners."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.application.context_resolution import (
    EngineeringContextResolver,
    ResolutionStatus,
    TrustedApplicationContext,
)
from embedded_copilot.application.firmware_build_context_source import (
    BuildContextInspection,
    FirmwareBuildContextSource,
)
from embedded_copilot.application.models import ApplicationQuestionRequest
from embedded_copilot.application.task_entry import (
    EngineeringIntent,
    EngineeringTaskEntry,
    submit_brownfield_build,
)
from embedded_copilot.application.task_firmware_build import FirmwareBuildTaskService
from embedded_copilot.execution_worker.contracts import OperationStatus, OperationType
from embedded_copilot.firmware_engineering.request_intent import (
    is_firmware_build_request,
)
from embedded_copilot.project_catalog.exceptions import ProjectCatalogConflict

router = APIRouter(prefix="/api/v1/copilot/firmware-build", tags=["firmware-build"])


class _StrictRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ProposalReviewRequest(_StrictRequest):
    project_id: str
    conversation_id: str
    context_version: str
    reviewed: Literal[True]


class TaskConfirmationRequest(_StrictRequest):
    project_id: str
    conversation_id: str
    context_version: str
    confirmed: Literal[True]


class BrownfieldProposalReviewRequest(_StrictRequest):
    project_id: str
    context_version: str
    reviewed: Literal[True]


class BrownfieldTaskConfirmationRequest(_StrictRequest):
    project_id: str
    context_version: str
    confirmed: Literal[True]


def _source(request: Request) -> FirmwareBuildContextSource | None:
    value = getattr(request.app.state, "firmware_build_context_source", None)
    return value if isinstance(value, FirmwareBuildContextSource) else None


def _projection(value: BuildContextInspection) -> dict[str, str | None]:
    snapshot = value.snapshot
    if snapshot is None:
        return {
            "status": value.status,
            "reason_code": value.reason_code,
            "context_version": value.context_version,
            "project": None,
            "target": None,
            "source_revision": None,
            "workspace_fingerprint": None,
            "configuration": None,
            "toolchain": None,
            "plan": None,
            "proposal": None,
        }
    build = snapshot.build_request
    config = snapshot.current_configuration
    plan = getattr(snapshot, "authoritative_plan", None) or (
        None if snapshot.conversation is None else snapshot.conversation.engineering_plan
    )
    stages = () if plan is None else tuple(
        phase for phase in plan.phases
        if any(output.output_type == "BUILD_ARTIFACT" for output in phase.outputs)
    )
    return {
        "status": value.status,
        "reason_code": value.reason_code,
        "context_version": value.context_version,
        "project": snapshot.project.name,
        "target": None if snapshot.target is None else snapshot.target.board_family,
        "source_revision": None if build is None else build.expected_source_revision,
        "workspace_fingerprint": (
            None if snapshot.workspace is None
            else snapshot.workspace.snapshot.project_fingerprint
        ),
        "configuration": None if config is None else config.profile,
        "toolchain": None if config is None else f"{config.toolchain_id} {config.toolchain_version}",
        "plan": stages[0].name if len(stages) == 1 else None,
        "proposal": None if snapshot.proposal is None else snapshot.proposal.proposal_id,
    }


def _blocked(reason_code: str, message: str = "当前固件构建上下文不可用。") -> dict[str, str | None]:
    return {"status": "BLOCKED", "reason_code": reason_code, "message": message,
            "task_id": None, "operation_id": None}


@router.get("/context")
def preview_context(
    project_id: str,
    conversation_id: str,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    if source is None:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_SOURCE_UNAVAILABLE"))
    return _projection(source.inspect(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=project_id, conversation_id=conversation_id,
    ))


@router.get("/brownfield/context")
def preview_brownfield_context(
    project_id: str,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    if source is None:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_SOURCE_UNAVAILABLE"))
    return _projection(source.inspect(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=project_id, conversation_id=None,
    ))


@router.post("/brownfield/proposals")
def review_brownfield_proposal(
    body: BrownfieldProposalReviewRequest,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    if source is None:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_SOURCE_UNAVAILABLE"))
    if not body.context_version:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_STALE"))
    return _projection(source.prepare(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=body.project_id, conversation_id=None,
        expected_context_version=body.context_version,
    ))


@router.post("/brownfield/confirm")
def confirm_brownfield_task(
    body: BrownfieldTaskConfirmationRequest,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    execution = getattr(request.app.state, "execution_operation_port", None)
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    if source is None or execution is None or catalog is None:
        return _blocked("TASK_ENTRY_UNAVAILABLE")
    inspected = source.inspect(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=body.project_id, conversation_id=None,
    )
    if inspected.context_version != body.context_version:
        return _blocked("CONTEXT_STALE", "构建上下文已变化，需要重新预览。")
    if inspected.status != "READY" or inspected.snapshot is None:
        return _blocked(inspected.reason_code)
    snapshot = inspected.snapshot
    if snapshot.authoritative_plan is None or snapshot.build_request is None or snapshot.bindings is None:
        return _blocked("APPROVED_PLAN_MISSING")
    try:
        context = catalog.brownfield_build_context(
            body.project_id, tenant_id=identity.tenant_id,
            requester=identity.requester,
        )
        if (
            context.plan != snapshot.authoritative_plan
            or context.review != snapshot.authoritative_review
            or context.configuration != snapshot.current_configuration
        ):
            return _blocked("CONTEXT_STALE")
        state = submit_brownfield_build(
            catalog=catalog, build_service=FirmwareBuildTaskService(execution),
            context=context, build_request=snapshot.build_request,
            bindings=snapshot.bindings, user_confirmed=True,
        )
        operation_id = state.operation_reference.operation_id
        operation = execution.get_status(
            tenant_id=identity.tenant_id, project_id=body.project_id,
            operation_id=operation_id,
        )
        if (
            operation is None
            or operation.tenant_id != identity.tenant_id
            or operation.project_id != body.project_id
            or operation.operation_id != operation_id
            or operation.status is not OperationStatus.WAIT_APPROVAL
            or operation.operation_type is not OperationType.BUILD
            or operation.receipt_fingerprint is not None
            or operation.correlation is None
            or operation.correlation.task_id != state.task_id
        ):
            return _blocked("OPERATION_NOT_WAITING_APPROVAL")
        return {
            "status": "CONFIRMED", "reason_code": "WAIT_APPROVAL",
            "message": "构建操作等待独立审批。",
            "task_id": state.task_id, "operation_id": operation_id,
        }
    except ProjectCatalogConflict:
        return _blocked("CONTEXT_STALE", "构建上下文已变化，需要重新预览。")
    except Exception:  # noqa: BLE001 - task entry fails closed without exposing internal state
        return _blocked("TASK_ENTRY_UNAVAILABLE")


@router.post("/proposals")
def review_proposal(
    body: ProposalReviewRequest,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    if source is None:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_SOURCE_UNAVAILABLE"))
    if not body.context_version:
        return _projection(BuildContextInspection("BLOCKED", "CONTEXT_STALE"))
    return _projection(source.prepare(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=body.project_id, conversation_id=body.conversation_id,
        expected_context_version=body.context_version,
    ))


@router.post("/confirm")
def confirm_task(
    body: TaskConfirmationRequest,
    request: Request,
    identity: Annotated[TrustedDesktopIdentity, Depends(desktop_identity)],
) -> dict[str, str | None]:
    source = _source(request)
    execution = getattr(request.app.state, "execution_operation_port", None)
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    chat = getattr(request.app.state, "desktop_copilot_chat_runtime", None)
    classifier = getattr(chat, "application_runtime", None)
    if source is None or execution is None or catalog is None or classifier is None:
        return _blocked("TASK_ENTRY_UNAVAILABLE")
    inspected = source.inspect(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=body.project_id, conversation_id=body.conversation_id,
    )
    if inspected.context_version != body.context_version:
        return _blocked("CONTEXT_STALE", "构建上下文已变化，需要重新预览。")
    if inspected.status != "READY" or inspected.snapshot is None:
        return _blocked(inspected.reason_code)
    snapshot = inspected.snapshot
    detail = snapshot.conversation
    candidates = tuple(
        turn for turn in detail.turns
        if is_firmware_build_request(turn.question)
        or turn.question == "帮我检查当前固件是否能构建"
    )
    if not candidates:
        return _blocked("BUILD_REQUEST_TURN_MISSING")
    turn = max(candidates, key=lambda value: (value.created_at, value.turn_id))
    intent = EngineeringIntent(
        objective=turn.question,
        project_reference=body.project_id,
        engineering_domain="FIRMWARE",
        expected_outcome="检查固件构建结果",
        constraints=(),
        source_context=(turn.turn_id, turn.request_id, turn.question,
                        turn.created_at.isoformat(), body.conversation_id),
        correlation_id=turn.request_id,
    )
    resolved = EngineeringContextResolver(source).resolve(
        intent,
        TrustedApplicationContext(
            tenant_id=identity.tenant_id, requester=identity.requester,
            conversation_id=body.conversation_id, current_project_id=body.project_id,
        ),
    )
    if resolved.status is not ResolutionStatus.RESOLVED or resolved.context is None:
        return _blocked(f"CONTEXT_{resolved.status.value}")
    current = source.inspect(
        tenant_id=identity.tenant_id, requester=identity.requester,
        project_id=body.project_id, conversation_id=body.conversation_id,
    )
    if current.status != "READY" or current.context_version != body.context_version:
        return _blocked("CONTEXT_STALE", "构建上下文已变化，需要重新预览。")
    question = ApplicationQuestionRequest(
        request_id=turn.request_id, trace_id=turn.trace_id or turn.request_id,
        project_id=body.project_id, tenant_id=identity.tenant_id,
        requester=identity.requester, question=turn.question,
        requested_at=turn.created_at,
    )
    entry = EngineeringTaskEntry(
        classifier=classifier, catalog=catalog,
        build_service=FirmwareBuildTaskService(execution),
        trusted_tenant_id=identity.tenant_id, trusted_requester=identity.requester,
        status_reader=execution,
    )
    prepared = entry.prepare(
        question, conversation_id=body.conversation_id,
        action="FIRMWARE_BUILD_CHECK",
        stage_id=resolved.context.stage_reference,
        build_request=snapshot.build_request, bindings=snapshot.bindings,
    )
    if prepared.status != "READY_CONFIRMATION" or prepared.prepared is None:
        return _blocked(prepared.status, prepared.message)
    # Task confirmation only creates WAIT_APPROVAL. Approval and Worker remain
    # separate operations through the existing Execution boundary.
    submitted = entry.submit(prepared.prepared, user_confirmed=True)
    if submitted.operation_id is None or submitted.task_id is None:
        return _blocked(submitted.status, submitted.message)
    try:
        operation = execution.get_status(
            tenant_id=identity.tenant_id, project_id=body.project_id,
            operation_id=submitted.operation_id,
        )
        if (
            operation is None
            or operation.tenant_id != identity.tenant_id
            or operation.project_id != body.project_id
            or operation.operation_id != submitted.operation_id
            or operation.status is not OperationStatus.WAIT_APPROVAL
            or operation.operation_type is not OperationType.BUILD
            or operation.receipt_fingerprint is not None
            or operation.correlation is None
            or operation.correlation.task_id != submitted.task_id
        ):
            return _blocked("OPERATION_NOT_WAITING_APPROVAL")
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return _blocked("OPERATION_STATUS_UNAVAILABLE")
    return {"status": "CONFIRMED", "reason_code": "WAIT_APPROVAL",
            "message": submitted.message, "task_id": submitted.task_id,
            "operation_id": submitted.operation_id}
