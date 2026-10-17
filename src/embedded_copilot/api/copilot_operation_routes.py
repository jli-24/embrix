from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity

from .copilot_operation_models import (
    CopilotOperationCapability,
    CopilotOperationCreateRequest,
    CopilotOperationStatus,
    CopilotOperationView,
)


class TenantBoundOperationPort(Protocol):
    def create_operation(
        self, *, tenant_id: str, requester: str, request: CopilotOperationCreateRequest
    ) -> Any: ...

    def get_operation(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot/operations", tags=["copilot-operations"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _view(value: object, *, project_id: str | None = None) -> CopilotOperationView:
    if isinstance(value, CopilotOperationView):
        result = value
    else:
        result = CopilotOperationView.model_validate(value, from_attributes=True)
    if project_id is not None and result.project_id != project_id:
        raise ValueError("operation project binding mismatch")
    return result


@router.post("", response_model=CopilotOperationView)
def create_operation(
    request: Request,
    body: CopilotOperationCreateRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotOperationView | JSONResponse:
    """Create only a typed proposal envelope; no current Desktop executor is bound."""

    port = getattr(request.app.state, "copilot_operation_port", None)
    method = getattr(port, "create_operation", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "OPERATION_UNAVAILABLE")
    try:
        value = method(
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            request=body,
        )
        return _view(value, project_id=body.project_id)
    except Exception:
        return _error(request, 422, "OPERATION_REJECTED")


@router.get("/{operation_id}", response_model=CopilotOperationView)
def get_operation(
    operation_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotOperationView | JSONResponse:
    port = getattr(request.app.state, "copilot_operation_port", None)
    method = getattr(port, "get_operation", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "OPERATION_UNAVAILABLE")
    try:
        value = method(
            tenant_id=identity.tenant_id,
            project_id=project_id,
            operation_id=operation_id,
        )
        if value is None:
            return _error(request, 404, "OPERATION_NOT_FOUND")
        return _view(value, project_id=project_id)
    except Exception:
        return _error(request, 422, "OPERATION_REJECTED")


__all__ = ("TenantBoundOperationPort", "router")
