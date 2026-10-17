# ruff: noqa: B008, BLE001

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from embedded_copilot.api.desktop_copilot_chat_models import (
    CopilotChatAttachmentInput,
    CopilotChatInput,
    CopilotChatSessionOpenInput,
    CopilotChatTraceView,
)
from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.desktop_copilot_chat import (
    ChatApplicationFailed,
    ChatConflict,
    ChatContextReference,
    ChatRejected,
    ChatUnavailable,
    DesktopCopilotChatError,
    identifier,
)


class DesktopCopilotChatService(Protocol):
    def open_session(self, request): ...
    def close_session(self, session_id: str, *, requester: str, tenant_id: str): ...
    def submit(self, request, *, requester: str, tenant_id: str): ...
    def classify(self, request, *, requester: str, tenant_id: str): ...
    def project(self, session_id: str, *, requester: str, tenant_id: str): ...
    def references(self, session_id: str, *, requester: str, tenant_id: str): ...
    def runtime_panel(self, session_id: str, *, requester: str, tenant_id: str): ...
    def bind_attachment(self, binding, *, requester: str, tenant_id: str): ...
    def events_after(
        self,
        session_id: str,
        *,
        requester: str,
        tenant_id: str,
        last_event_id: str | None,
    ): ...
    def trace(
        self,
        session_id: str,
        trace_id: str,
        *,
        requester: str,
        tenant_id: str,
    ): ...


router = APIRouter(prefix="/api/v1/copilot/chat", tags=["copilot-chat"])


def _service(request: Request) -> DesktopCopilotChatService | None:
    return getattr(request.app.state, "desktop_copilot_chat_runtime", None)


def _requester(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.requester


def _tenant(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.tenant_id


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": request.state.trace_id},
    )


def _map(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ChatRejected):
        return _error(request, 403, "COPILOT_CHAT_REJECTED")
    if isinstance(error, ChatConflict):
        return _error(request, 409, "COPILOT_CHAT_CONFLICT")
    if isinstance(error, ChatApplicationFailed):
        return _error(request, 422, error.code)
    if isinstance(error, ChatUnavailable):
        return _error(request, 503, "COPILOT_CHAT_UNAVAILABLE")
    if isinstance(error, DesktopCopilotChatError):
        return _error(request, 400, "COPILOT_CHAT_REJECTED")
    raise error


def _unavailable(request: Request) -> JSONResponse:
    return _error(request, 503, "COPILOT_CHAT_UNAVAILABLE")


@router.post("/sessions")
def open_session(
    payload: CopilotChatSessionOpenInput,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.open_session(
            payload.to_request(requester=requester, tenant_id=tenant_id)
        )
    except Exception as error:
        return _map(request, error)


@router.delete("/sessions/{session_id}")
def close_session(
    session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.close_session(
            session_id, requester=requester, tenant_id=tenant_id
        )
    except Exception as error:
        return _map(request, error)


@router.post("/sessions/{session_id}/requests")
def submit(
    session_id: str,
    payload: CopilotChatInput,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.submit(
            payload.to_request(session_id=session_id),
            requester=requester,
            tenant_id=tenant_id,
        )
    except Exception as error:
        return _map(request, error)


@router.post("/sessions/{session_id}/classify")
def classify(
    session_id: str,
    payload: CopilotChatInput,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.classify(
            payload.to_request(session_id=session_id),
            requester=requester,
            tenant_id=tenant_id,
        )
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{session_id}/project")
def project(
    session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.project(session_id, requester=requester, tenant_id=tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{session_id}/runtime-panel")
def runtime_panel(
    session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.runtime_panel(
            session_id, requester=requester, tenant_id=tenant_id
        )
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{session_id}/references")
def references(
    session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.references(session_id, requester=requester, tenant_id=tenant_id)
    except Exception as error:
        return _map(request, error)


@router.get(
    "/sessions/{session_id}/trace",
    response_model=list[CopilotChatTraceView],
)
def trace(
    session_id: str,
    request: Request,
    trace_id: str = Query(..., min_length=1),
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return [
            CopilotChatTraceView.from_event(event)
            for event in service.trace(
                session_id,
                trace_id,
                requester=requester,
                tenant_id=tenant_id,
            )
        ]
    except Exception as error:
        return _map(request, error)


@router.post(
    "/sessions/{session_id}/attachments",
    response_model=ChatContextReference,
    status_code=201,
)
def bind_attachment(
    session_id: str,
    payload: CopilotChatAttachmentInput,
    request: Request,
    requester: str = Depends(_requester),
    tenant_id: str = Depends(_tenant),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.bind_attachment(
            payload.to_binding(session_id=session_id),
            requester=requester,
            tenant_id=tenant_id,
        )
    except Exception as error:
        return _map(request, error)


def _sse(events) -> Iterator[str]:
    for event in events:
        payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
        yield f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"


@router.get("/events")
def events(
    request: Request,
    session_id: str = Query(..., min_length=1),
    requester: str = Query(..., min_length=1),
    tenant_id: str = Query(..., min_length=1, alias="tenant"),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    service: DesktopCopilotChatService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        requested_requester = identifier(requester, field="copilot requester")
        requested_tenant = identifier(tenant_id, field="environment tenant")
        if requested_requester != identity.requester or requested_tenant != identity.tenant_id:
            return _error(request, 403, "COPILOT_CHAT_REJECTED")
        values = service.events_after(
            session_id,
            requester=identity.requester,
            tenant_id=identity.tenant_id,
            last_event_id=last_event_id,
        )
        if values is None:
            return StreamingResponse(
                iter(["event: RESYNC_REQUIRED\ndata: {}\n\n"]),
                media_type="text/event-stream",
            )
        return StreamingResponse(_sse(values), media_type="text/event-stream")
    except Exception as error:
        return _map(request, error)
