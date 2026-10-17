# ruff: noqa: B008, BLE001

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from embedded_copilot.copilot_environment import (
    CopilotEnvironmentError,
    EnvironmentConflict,
    EnvironmentRejected,
    EnvironmentUnavailable,
    identifier,
)
from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity


class CopilotEnvironmentService(Protocol):
    def list_projects(self, tenant_id: str): ...
    def project(self, tenant_id: str, project_id: str): ...
    def team(self, tenant_id: str): ...
    def runtime_health(self, tenant_id: str): ...
    def audit_views(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        object_reference: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ): ...
    def memory_views(self, tenant_id: str, *, project_id: str | None = None): ...
    def events_after(self, tenant_id: str, *, last_event_fingerprint: str | None): ...


router = APIRouter(prefix="/api/v1/environment", tags=["environment"])


def _service(request: Request) -> CopilotEnvironmentService | None:
    return getattr(request.app.state, "copilot_environment_runtime", None)


def _tenant(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.tenant_id


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": request.state.trace_id},
    )


def _map(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, EnvironmentRejected):
        return _error(request, 403, "ENVIRONMENT_TENANT_REJECTED")
    if isinstance(error, EnvironmentConflict):
        return _error(request, 409, "ENVIRONMENT_CONFLICT")
    if isinstance(error, EnvironmentUnavailable):
        return _error(request, 503, "ENVIRONMENT_UNAVAILABLE")
    if isinstance(error, CopilotEnvironmentError):
        return _error(request, 400, "ENVIRONMENT_REJECTED")
    raise error


def _unavailable(request: Request) -> JSONResponse:
    return _error(request, 503, "ENVIRONMENT_UNAVAILABLE")


@router.get("/projects")
def projects(
    request: Request,
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.list_projects(tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get("/projects/{project_id}")
def project(
    project_id: str,
    request: Request,
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.project(tenant_id, project_id)
    except Exception as error:
        return _map(request, error)


@router.get("/team")
def team(
    request: Request,
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.team(tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get("/runtime-health")
def runtime_health(
    request: Request,
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.runtime_health(tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get("/audit")
def audit(
    request: Request,
    project_id: str | None = Query(None),
    object_reference: str | None = Query(None),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.audit_views(
            tenant_id,
            project_id=project_id,
            object_reference=object_reference,
            start=start,
            end=end,
        )
    except Exception as error:
        return _map(request, error)


@router.get("/memory-view")
def memory_view(
    request: Request,
    project_id: str | None = Query(None),
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.memory_views(tenant_id, project_id=project_id)
    except Exception as error:
        return _map(request, error)


def _sse(events) -> Iterator[str]:
    for event in events:
        payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
        yield f"id: {event.fingerprint}\nevent: {event.event_type.value}\ndata: {payload}\n\n"


@router.get("/events")
def events(
    request: Request,
    last_event_fingerprint: str | None = Header(None, alias="Last-Event-ID"),
    tenant_id: str = Depends(_tenant),
    service: CopilotEnvironmentService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        values = service.events_after(
            tenant_id, last_event_fingerprint=last_event_fingerprint
        )
        if values is None:
            return StreamingResponse(
                iter(["event: RESYNC_REQUIRED\ndata: {}\n\n"]),
                media_type="text/event-stream",
            )
        return StreamingResponse(_sse(values), media_type="text/event-stream")
    except Exception as error:
        return _map(request, error)
