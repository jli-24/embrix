from __future__ import annotations

# ruff: noqa: B008, BLE001
import json
from collections.abc import Iterator
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from embedded_copilot.api.desktop_product_models import (
    DesktopChatInput,
    DesktopPreferenceInput,
    DesktopSessionOpenInput,
)
from embedded_copilot.desktop_product_runtime import (
    DesktopChatRequest,
    DesktopProductError,
    ProductConflict,
    ProductSessionRejected,
    ProductUnavailable,
)


class DesktopProductService(Protocol):
    def open_session(self, request, *, requester: str): ...
    def close_session(self, product_session_id: str, *, requester: str): ...
    def explorer(self, product_session_id: str, *, requester: str): ...
    def timeline(self, product_session_id: str, *, requester: str): ...
    def chat(self, request, *, requester: str): ...
    def preferences_for(self, *, requester: str): ...
    def update_preferences(self, preferences, *, requester: str): ...
    def notifications(self, product_session_id: str, *, requester: str): ...
    def events_after(
        self, product_session_id: str, *, requester: str, last_event_id: str | None
    ): ...


router = APIRouter(prefix="/api/v1/desktop", tags=["desktop"])


def _service(request: Request) -> DesktopProductService | None:
    return getattr(request.app.state, "desktop_product_runtime", None)


def _requester(value: str = Header(..., alias="X-Desktop-Requester")) -> str:
    from embedded_copilot.engineering_desktop.contracts import identifier

    return identifier(value, field="desktop requester")


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "trace_id": request.state.trace_id}
    )


def _map(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ProductSessionRejected):
        return _error(request, 403, "DESKTOP_SESSION_REJECTED")
    if isinstance(error, ProductConflict):
        return _error(request, 409, "DESKTOP_PRODUCT_CONFLICT")
    if isinstance(error, ProductUnavailable):
        return _error(request, 503, "DESKTOP_PRODUCT_UNAVAILABLE")
    if isinstance(error, DesktopProductError):
        return _error(request, 400, "DESKTOP_PRODUCT_REJECTED")
    raise error


def _unavailable(request: Request) -> JSONResponse:
    return _error(request, 503, "DESKTOP_PRODUCT_UNAVAILABLE")


@router.post("/sessions")
def open_session(
    payload: DesktopSessionOpenInput,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.open_session(payload.to_request(), requester=requester)
    except Exception as error:
        return _map(request, error)


@router.delete("/sessions/{product_session_id}")
def close_session(
    product_session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.close_session(product_session_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{product_session_id}/explorer")
def explorer(
    product_session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.explorer(product_session_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{product_session_id}/timeline")
def timeline(
    product_session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.timeline(product_session_id, requester=requester)
    except Exception as error:
        return _map(request, error)


@router.post("/sessions/{product_session_id}/chat")
def chat(
    product_session_id: str,
    payload: DesktopChatInput,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.chat(
            DesktopChatRequest(
                product_session_id=product_session_id,
                request_id=payload.request_id,
                trace_id=payload.trace_id,
                question=payload.question,
                constraints=payload.constraints,
                requested_at=payload.requested_at,
            ),
            requester=requester,
        )
    except Exception as error:
        return _map(request, error)


@router.get("/preferences")
def preferences(
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.preferences_for(requester=requester)
    except Exception as error:
        return _map(request, error)


@router.put("/preferences")
def update_preferences(
    payload: DesktopPreferenceInput,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.update_preferences(
            payload.to_preferences(requester=requester), requester=requester
        )
    except Exception as error:
        return _map(request, error)


@router.get("/sessions/{product_session_id}/notifications")
def notifications(
    product_session_id: str,
    request: Request,
    requester: str = Depends(_requester),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        return service.notifications(product_session_id, requester=requester)
    except Exception as error:
        return _map(request, error)


def _sse(events) -> Iterator[str]:
    for event in events:
        payload = json.dumps(event.model_dump(mode="json"), separators=(",", ":"))
        yield f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {payload}\n\n"


@router.get("/sessions/{product_session_id}/events")
def events(
    product_session_id: str,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    requester: str = Query(..., min_length=1, alias="requester"),
    service: DesktopProductService | None = Depends(_service),
):
    if service is None:
        return _unavailable(request)
    try:
        requester = _requester(requester)
        values = service.events_after(
            product_session_id, requester=requester, last_event_id=last_event_id
        )
        if values is None:
            return StreamingResponse(
                iter(["event: RESYNC_REQUIRED\ndata: {}\n\n"]),
                media_type="text/event-stream",
            )
        return StreamingResponse(_sse(values), media_type="text/event-stream")
    except Exception as error:
        return _map(request, error)
