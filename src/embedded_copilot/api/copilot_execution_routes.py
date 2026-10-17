from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.execution_worker.contracts import (
    BuildOperation, FlashOperation, HilOperation, OperationEvent, OperationReceipt, OperationRequest, TestOperation, UartObservationOperation,
    ToolchainIdentity, CancellationRequest, OperationType, OperationStatus, OperationStatusView, RecoveryState, SchematicErcOperation,
)
from embedded_copilot.execution_worker.service import ExecutionUnavailable
from embedded_copilot.project_catalog import (
    ProjectCatalogError, ProjectCatalogForbidden, ProjectCatalogNotFound,
    ProjectCatalogUnavailable,
)

from .copilot_execution_models import (
    BuildOperationCreateRequest, FlashOperationCreateRequest, HilOperationCreateRequest,
    OperationApprovalRequest, OperationCancelRequest, TestOperationCreateRequest, UartOperationCreateRequest,
    approval_response, event_response, receipt_response, status_response, SchematicApprovalContext,
)


class ExecutionOperationPort(Protocol):
    def create_operation(self, request: OperationRequest) -> Any: ...
    def issue_approval_for_id(self, **kwargs: Any) -> Any: ...
    def enqueue_by_id(self, **kwargs: Any) -> Any: ...
    def request_cancellation(self, request: CancellationRequest) -> Any: ...
    def get_status(self, **kwargs: Any) -> Any: ...
    def get_operation(self, **kwargs: Any) -> Any: ...
    def get_receipt(self, **kwargs: Any) -> Any: ...
    def get_artifact(self, **kwargs: Any) -> Any: ...
    def read_operation_events_after(self, **kwargs: Any) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot/operations", tags=["controlled-execution"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "trace_id": getattr(request.state, "trace_id", "")})


def _port(request: Request) -> ExecutionOperationPort | None:
    value = getattr(request.app.state, "execution_operation_port", None)
    return value if value is not None else None


def _project_access(request: Request, identity: TrustedDesktopIdentity, project_id: str) -> JSONResponse | None:
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    if catalog is None or not callable(getattr(catalog, "get_project_for_requester", None)):
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    try:
        catalog.get_project_for_requester(
            project_id, tenant_id=identity.tenant_id, requester=identity.requester
        )
    except ProjectCatalogForbidden:
        return _error(request, 403, "PROJECT_CATALOG_FORBIDDEN")
    except ProjectCatalogNotFound:
        return _error(request, 404, "PROJECT_NOT_FOUND")
    except ProjectCatalogUnavailable:
        return _error(request, 503, "PROJECT_CATALOG_UNAVAILABLE")
    except ProjectCatalogError:
        return _error(request, 422, "PROJECT_CATALOG_REJECTED")
    return None


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
    if denied := _project_access(request, identity, body.project_id):
        return denied
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
    if denied := _project_access(request, identity, body.project_id):
        return denied
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
    if denied := _project_access(request, identity, body.project_id):
        return denied
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
    if denied := _project_access(request, identity, body.project_id):
        return denied
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
    if denied := _project_access(request, identity, body.project_id):
        return denied
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
    if denied := _project_access(request, identity, project_id):
        return denied
    if body.reviewer_id != identity.requester:
        return _error(request, 422, "EXECUTION_APPROVAL_REJECTED")
    try:
        read_operation = getattr(port, "get_operation", None)
        if callable(read_operation):
            operation = read_operation(
                tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id,
            )
            if getattr(operation, "operation_type", None) is OperationType.KICAD_NATIVE_FOOTPRINT_MOVE and (
                not getattr(request.app.state, "native_move_production_available", False)
                or getattr(operation, "production_context", None) is None
            ):
                return _error(request, 503, "NATIVE_MOVE_UNAVAILABLE")
            if getattr(operation, "operation_type", None) is OperationType.KICAD_PCB_DRC_BASELINE:
                revalidate = getattr(request.app.state, "drc_baseline_revalidator", None)
                if not getattr(request.app.state, "drc_baseline_available", False) or not callable(revalidate) or revalidate(operation) is not True:
                    return _error(request, 409, "DRC_BASELINE_UNAVAILABLE")
        grant = port.issue_approval_for_id(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id, reviewer_id=identity.requester, nonce=body.nonce, expires_at=body.expires_at)
        port.enqueue_by_id(tenant_id=identity.tenant_id, project_id=grant.project_id, operation_id=operation_id, approval_id=grant.grant_id)
        return approval_response(grant)
    except Exception:
        return _error(request, 422, "EXECUTION_APPROVAL_REJECTED")


@router.post("/{operation_id}/cancel")
def cancel_operation(operation_id: str, project_id: str, body: OperationCancelRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    if denied := _project_access(request, identity, project_id):
        return denied
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
    if denied := _project_access(request, identity, project_id):
        return denied
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
    if denied := _project_access(request, identity, project_id):
        return denied
    try:
        status = port.get_status(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id)
        if status is None:
            return _error(request, 404, "OPERATION_NOT_FOUND")
        checked_status = OperationStatusView.model_validate(status.model_dump(mode="python"))
        if (checked_status.tenant_id, checked_status.project_id, checked_status.operation_id) != (identity.tenant_id, project_id, operation_id):
            raise ValueError("operation status scope mismatch")
        context = None
        read_operation = getattr(port, "get_operation", None)
        if checked_status.operation_type is OperationType.KICAD_SCHEMATIC_ERC and callable(read_operation):
            operation = read_operation(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id)
            if operation is not None:
                if type(operation) is not SchematicErcOperation:
                    raise ValueError("canonical ERC operation type mismatch")
                operation = SchematicErcOperation.model_validate(operation.model_dump(mode="python"))
                if (operation.tenant_id, operation.project_id, operation.operation_id) != (identity.tenant_id, project_id, operation_id):
                    raise ValueError("canonical ERC operation scope mismatch")
                revalidate = getattr(request.app.state.project_catalog_runtime, "revalidate_schematic_evidence_operation", None)
                if (checked_status.status is OperationStatus.WAIT_APPROVAL
                        and checked_status.recovery_state is RecoveryState.NONE
                        and not checked_status.cancellation_requested
                        and operation.requester_id == identity.requester
                        and datetime.now(UTC) < operation.expires_at
                        and operation.source_parse_operation_id is not None
                        and callable(revalidate) and revalidate(operation) is True):
                    names = SchematicApprovalContext.model_fields
                    context = SchematicApprovalContext.model_validate({key: getattr(operation, key) for key in names})
        return status_response(checked_status, schematic_approval_context=context)
    except Exception:
        return _error(request, 422, "OPERATION_STATUS_REJECTED")


@router.get("/{operation_id}/receipt")
def get_operation_receipt(operation_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "EXECUTION_UNAVAILABLE")
    if denied := _project_access(request, identity, project_id):
        return denied
    try:
        value = port.get_receipt(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id)
        if value is None:
            return _error(request, 404, "OPERATION_RECEIPT_NOT_FOUND")
        receipt = OperationReceipt.model_validate(value.model_dump(mode="python"))
        if (receipt.tenant_id, receipt.project_id, receipt.operation_id) != (identity.tenant_id, project_id, operation_id):
            raise ValueError("operation receipt scope mismatch")
        artifact = receipt.artifact
        if artifact is not None and (
            artifact.tenant_id != identity.tenant_id
            or artifact.project_id != project_id
            or (receipt.operation_type is OperationType.BUILD and artifact.build_operation_id != operation_id)
        ):
            raise ValueError("operation artifact scope mismatch")
        if artifact is not None and receipt.build_evidence is not None and artifact.artifact_id not in receipt.build_evidence.output_artifact_ids:
            raise ValueError("operation build evidence artifact mismatch")
        source_revision = None
        read_operation = getattr(port, "get_operation", None)
        if receipt.operation_type is OperationType.BUILD and callable(read_operation):
            operation = read_operation(
                tenant_id=identity.tenant_id, project_id=project_id,
                operation_id=operation_id,
            )
            if not isinstance(operation, BuildOperation) or (
                operation.tenant_id, operation.project_id, operation.operation_id
            ) != (identity.tenant_id, project_id, operation_id):
                raise ValueError("build operation receipt binding mismatch")
            if (
                artifact is not None
                and (
                    artifact.workspace_fingerprint != operation.workspace_fingerprint
                    or artifact.target_id != operation.target_id
                    or artifact.build_configuration != operation.configuration
                    or artifact.toolchain_id != operation.toolchain.toolchain_id
                    or artifact.toolchain_version != operation.toolchain.tool_version
                )
            ) or (
                receipt.build_evidence is not None
                and (
                    receipt.build_evidence.request_fingerprint
                    != operation.firmware_build_request_fingerprint
                    or receipt.build_evidence.toolchain_id != operation.toolchain.toolchain_id
                    or receipt.build_evidence.toolchain_version != operation.toolchain.tool_version
                    or receipt.build_evidence.source_fingerprints
                    != tuple(item.content_fingerprint for item in operation.source_artifacts)
                )
            ):
                raise ValueError("build receipt evidence binding mismatch")
            revisions = {item.source_revision for item in operation.source_artifacts}
            bound_request = operation.firmware_build_request
            if (
                bound_request is not None
                and operation.firmware_build_request_fingerprint == bound_request.fingerprint
                and operation.source_artifacts == bound_request.source_artifacts
                and len(revisions) == 1
            ):
                revision = next(iter(revisions))
                if revision.startswith("sha256:") and (
                    bound_request.expected_source_revision == revision
                    and bound_request.actual_source_revision == revision
                ):
                    source_revision = revision
        return receipt_response(receipt, source_revision=source_revision)
    except ExecutionUnavailable:
        return _error(request, 503, "OPERATION_RECEIPT_UNAVAILABLE")
    except Exception:
        return _error(request, 422, "OPERATION_RECEIPT_REJECTED")


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "ARTIFACT_STORE_UNAVAILABLE")
    if denied := _project_access(request, identity, project_id):
        return denied
    try:
        value = port.get_artifact(tenant_id=identity.tenant_id, project_id=project_id, artifact_id=artifact_id)
        return _error(request, 404, "ARTIFACT_NOT_FOUND") if value is None else value
    except ExecutionUnavailable:
        return _error(request, 503, "ARTIFACT_STORE_UNAVAILABLE")
    except Exception:
        return _error(request, 422, "ARTIFACT_REJECTED")


__all__ = ("ExecutionOperationPort", "router")
