from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity

from .copilot_workspace_models import (
    ApplyApprovalRequest,
    ApplyOperationRequest,
    ChangeSetCreateRequest,
    PreviewRequest,
    ReceiptView,
    approval_view,
    changeset_view,
    preview_view,
    receipt_view,
)


class WorkspaceApplyApiPort(Protocol):
    def create_changeset(self, *, tenant_id: str, requester_id: str, request: ChangeSetCreateRequest) -> Any: ...
    def get_changeset(self, *, tenant_id: str, project_id: str, changeset_id: str) -> Any: ...
    def create_preview(self, *, tenant_id: str, requester_id: str, request: PreviewRequest) -> Any: ...
    def issue_apply_approval(self, *, tenant_id: str, requester_id: str, request: ApplyApprovalRequest) -> Any: ...
    def apply(self, *, tenant_id: str, requester_id: str, request: ApplyOperationRequest) -> Any: ...
    def get_receipt(self, *, tenant_id: str, project_id: str, operation_id: str) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot/workspace", tags=["workspace-controlled-apply"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "trace_id": getattr(request.state, "trace_id", "")})


def _port(request: Request) -> WorkspaceApplyApiPort | None:
    value = getattr(request.app.state, "workspace_apply_port", None)
    return value if value is not None else None


@router.post("/changesets")
def create_changeset(body: ChangeSetCreateRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_CHANGESET_UNAVAILABLE")
    try:
        return changeset_view(port.create_changeset(tenant_id=identity.tenant_id, requester_id=identity.requester, request=body))
    except Exception:
        return _error(request, 422, "WORKSPACE_CHANGESET_REJECTED")


@router.get("/changesets/{changeset_id}")
def get_changeset(changeset_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_CHANGESET_UNAVAILABLE")
    try:
        value = port.get_changeset(tenant_id=identity.tenant_id, project_id=project_id, changeset_id=changeset_id)
        return _error(request, 404, "WORKSPACE_CHANGESET_NOT_FOUND") if value is None else changeset_view(value)
    except Exception:
        return _error(request, 422, "WORKSPACE_CHANGESET_REJECTED")


@router.post("/previews")
def create_preview(body: PreviewRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_PREVIEW_UNAVAILABLE")
    try:
        return preview_view(port.create_preview(tenant_id=identity.tenant_id, requester_id=identity.requester, request=body))
    except Exception:
        return _error(request, 422, "WORKSPACE_PREVIEW_REJECTED")


@router.post("/apply-approvals")
def issue_apply_approval(body: ApplyApprovalRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_APPLY_UNAVAILABLE")
    try:
        return approval_view(port.issue_apply_approval(tenant_id=identity.tenant_id, requester_id=identity.requester, request=body))
    except Exception:
        return _error(request, 422, "WORKSPACE_APPLY_APPROVAL_REJECTED")


@router.post("/operations")
def apply(body: ApplyOperationRequest, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_APPLY_UNAVAILABLE")
    try:
        return receipt_view(port.apply(tenant_id=identity.tenant_id, requester_id=identity.requester, request=body))
    except Exception:
        return _error(request, 422, "WORKSPACE_APPLY_REJECTED")


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    port = _port(request)
    if port is None:
        return _error(request, 503, "WORKSPACE_APPLY_UNAVAILABLE")
    try:
        value = port.get_receipt(tenant_id=identity.tenant_id, project_id=project_id, operation_id=operation_id)
        return _error(request, 404, "WORKSPACE_OPERATION_NOT_FOUND") if value is None else receipt_view(value)
    except Exception:
        return _error(request, 422, "WORKSPACE_OPERATION_REJECTED")


@router.get("/receipts/{operation_id}")
def get_receipt(operation_id: str, project_id: str, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    return get_operation(operation_id, project_id, request, identity)


__all__ = ("WorkspaceApplyApiPort", "router")
