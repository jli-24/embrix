from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.execution_worker.contracts import (
    BuildOperation, FlashOperation, HilOperation, OperationEvent, OperationRequest, TestOperation, UartObservationOperation,
    ToolchainIdentity, CancellationRequest, OperationType,
)

from .copilot_execution_models import (
    BuildOperationCreateRequest, FlashOperationCreateRequest, HilOperationCreateRequest,
    OperationApprovalRequest, OperationCancelRequest, TestOperationCreateRequest, UartOperationCreateRequest,
    approval_response, event_response, status_response,
)


class ExecutionOperationPort(Protocol):
    def create_operation(self, request: OperationRequest) -> Any: ...
    def issue_approval_for_id(self, **kwargs: Any) -> Any: ...
    def enqueue_by_id(self, **kwargs: Any) -> Any: ...
    def request_cancellation(self, request: CancellationRequest) -> Any: ...
    def get_status(self, **kwargs: Any) -> Any: ...
    def get_artifact(self, **kwargs: Any) -> Any: ...
    def read_operation_events_after(self, **kwargs: Any) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot/operations", tags=["controlled-execution"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "trace_id": getattr(request.state, "trace_id", "")})


def _port(request: Request) -> ExecutionOperationPort | None:
    value = getattr(request.app.state, "execution_operation_port", None)
    return value if value is not None else None


def _base(body: Any, identity: TrustedDesktopIdentity) -> dict[str, Any]:
    return {key: value for key, value in {**body.model_dump(mode="python"), "tenant_id": identity.tenant_id, "requester_id": identity.requester}.items() if key != "target_kind"}


def _event_stream(projections) -> StreamingResponse:
    def frames():
        for projection in projections:
            data = json.dumps(
                projection.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            yield f"id: {projection.event_id}\nevent: {projection.event_type}\ndata: {data}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resync_stream() -> StreamingResponse:
    return StreamingResponse(
        iter(('event: RESYNC_REQUIRED\ndata: {"reason_code":"RESYNC_REQUIRED"}\n\n',)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/build")
def create_build(body: BuildOperationCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "CONTROLLED_BUILD_UNAVAILABLE")
    values = _base(body, identity)
    now = datetime.now(UTC)
    operation = BuildOperation(operation_id=f"build-{uuid4().hex}", created_at=now, expires_at=now + timedelta(minutes=10), toolchain=ToolchainIdentity(toolchain_id=body.toolchain_id, tool_version=body.toolchain_version, tool_family=body.tool_family, supported_operation=OperationType.BUILD, supported_target=body.target_kind), **{key: value for key, value in values.items() if key not in {"toolchain_id", "toolchain_version", "tool_family"}})
    try:
        port.create_operation(OperationRequest(operation=operation))
        return status_response(port.get_status(tenant_id=identity.tenant_id, project_id=body.project_id, operation_id=operation.operation_id), target_id=body.target_id, workspace_id=body.workspace_snapshot_id)
    except Exception:
        return _error(request, 422, "BUILD_OPERATION_REJECTED")


@router.post("/test")
def create_test(body: TestOperationCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "CONTROLLED_TEST_UNAVAILABLE")
    try:
        if body.artifact_identity is not None and body.artifact_identity.tenant_id != identity.tenant_id:
            return _error(request, 422, "TEST_OPERATION_REJECTED")
        now = datetime.now(UTC)
        operation = TestOperation(
            operation_id=f"test-{uuid4().hex}",
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            requester_id=identity.requester,
            target_id=body.target_id,
            target_fingerprint=body.target_fingerprint,
            workspace_snapshot_id=body.workspace_id,
            workspace_fingerprint=body.workspace_fingerprint,
            proposal_id=body.proposal_id,
            proposal_fingerprint=body.proposal_fingerprint,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
            nonce=body.nonce,
            test_suite_id=body.test_suite_id,
            test_profile_id=body.test_profile_id,
            artifact=body.artifact_identity,
        )
        port.create_operation(OperationRequest(operation=operation))
        status = port.get_status(
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            operation_id=operation.operation_id,
        )
        return status_response(
            status,
            target_id=body.target_id,
            workspace_id=body.workspace_id,
        )
    except Exception:
        return _error(request, 422, "TEST_OPERATION_REJECTED")


@router.post("/flash")
def create_flash(body: FlashOperationCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "CONTROLLED_FLASH_UNAVAILABLE")
    values = _base(body, identity)
    now = datetime.now(UTC)
    operation = FlashOperation(operation_id=f"flash-{uuid4().hex}", operation_type=OperationType.FLASH, created_at=now, expires_at=now + timedelta(minutes=5), **values)
    try:
        port.create_operation(OperationRequest(operation=operation))
        return status_response(port.get_status(tenant_id=identity.tenant_id, project_id=body.project_id, operation_id=operation.operation_id), target_id=body.target_id, workspace_id=body.workspace_snapshot_id)
    except Exception:
        return _error(request, 422, "FLASH_OPERATION_REJECTED")


@router.post("/uart")
def create_uart(body: UartOperationCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "UART_OBSERVATION_UNAVAILABLE")
    now = datetime.now(UTC)
    operation = UartObservationOperation(operation_id=f"uart-{uuid4().hex}", operation_type=OperationType.UART_OBSERVATION, created_at=now, expires_at=now + timedelta(minutes=5), **_base(body, identity))
    try:
        port.create_operation(OperationRequest(operation=operation))
        return status_response(port.get_status(tenant_id=identity.tenant_id, project_id=body.project_id, operation_id=operation.operation_id), target_id=body.target_id, workspace_id=body.workspace_snapshot_id)
    except Exception:
        return _error(request, 422, "UART_OPERATION_REJECTED")


@router.post("/hil")
def create_hil(body: HilOperationCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "HIL_UNAVAILABLE")
    now = datetime.now(UTC)
    operation = HilOperation(operation_id=f"hil-{uuid4().hex}", operation_type=OperationType.HIL, created_at=now, expires_at=now + timedelta(minutes=15), **_base(body, identity))
    try:
        port.create_operation(OperationRequest(operation=operation))
        return status_response(port.get_status(tenant_id=identity.tenant_id, project_id=body.project_id, operation_id=operation.operation_id), target_id=body.target_id, workspace_id=body.workspace_snapshot_id)
    except Exception:
        return _error(request, 422, "HIL_OPERATION_REJECTED")


@router.post("/{operation_id}/approval")
def approve_operation(operation_id: str, project_id: str, body: OperationApprovalRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    try:
        grant = port.issue_approval_for_id(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id, reviewer_id=body.reviewer_id, nonce=body.nonce, expires_at=body.expires_at)
        port.enqueue_by_id(tenant_id=identity.tenant_id, project_id=grant.project_id, operation_id=operation_id, approval_id=grant.grant_id)
        return approval_response(grant)
    except Exception:
        return _error(request, 422, "EXECUTION_APPROVAL_REJECTED")


@router.post("/{operation_id}/cancel")
def cancel_operation(operation_id: str, project_id: str, body: OperationCancelRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    try:
        status = port.request_cancellation(CancellationRequest(request_id=f"cancel-{uuid4().hex}", tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id, requester_id=identity.requester, reason=body.reason, requested_at=datetime.now(UTC)))
        return status_response(status)
    except Exception:
        return _error(request, 422, "CANCELLATION_REJECTED")


@router.get("/events/stream")
def stream_operation_events(
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    try:
        events = port.read_operation_events_after(
            tenant_id=identity.tenant_id,
            project_id=project_id,
            last_event_id=last_event_id,
            limit=100,
        )
        if events is None:
            return _resync_stream()
        projections = []
        for value in events:
            event = OperationEvent.model_validate(value.model_dump(mode="python"))
            if event.tenant_id != identity.tenant_id or event.project_id != project_id:
                raise ValueError("operation event scope mismatch")
            projections.append(event_response(event))
        return _event_stream(tuple(projections))
    except Exception:
        return _error(request, 422, "EXECUTION_EVENT_STREAM_REJECTED")


@router.get("/{operation_id}/status")
def get_operation_status(operation_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    try:
        status = port.get_status(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id)
        return _error(request, 404, "OPERATION_NOT_FOUND") if status is None else status_response(status)
    except Exception:
        return _error(request, 422, "OPERATION_STATUS_REJECTED")


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "ARTIFACT_STORE_UNAVAILABLE")
    try:
        value = port.get_artifact(tenant_id=identity.tenant_id, project_id=project_id, artifact_id=artifact_id)
        return _error(request, 404, "ARTIFACT_NOT_FOUND") if value is None else value
    except Exception:
        return _error(request, 422, "ARTIFACT_REJECTED")


__all__ = ("ExecutionOperationPort", "router")
