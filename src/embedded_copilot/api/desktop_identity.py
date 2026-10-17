from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, Response

from embedded_copilot.desktop_copilot_chat import identifier
from embedded_copilot.production_release.session import (
    LocalSessionManager,
    SessionRejected,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    cookie_present as diagnostic_cookie_present,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    emit as emit_webview_diagnostic,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    identity_result_for_code,
    origin_marker,
)

DESKTOP_SESSION_COOKIE = "EmbeddedCopilotSession"
DESKTOP_SESSION_COOKIE_PATH = "/api"


def set_desktop_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    response.set_cookie(
        key=DESKTOP_SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=False,
        samesite="strict",
        path=DESKTOP_SESSION_COOKIE_PATH,
    )


@dataclass(frozen=True, slots=True)
class TrustedDesktopIdentity:
    """Identity consumed by Desktop routes after authentication resolution."""

    requester: str
    tenant_id: str
    authentication: str


class LocalDevelopmentIdentityAdapter:
    """Explicit single-user identity adapter for local development only."""

    def __init__(self, *, requester: str = "desktop-user", tenant_id: str = "desktop-tenant") -> None:
        self._identity = TrustedDesktopIdentity(
            requester=identifier(requester, field="desktop requester"),
            tenant_id=identifier(tenant_id, field="environment tenant"),
            authentication="LOCAL_DEVELOPMENT",
        )

    def resolve(self, request: Request) -> TrustedDesktopIdentity:
        del request
        return self._identity


class ProductionLocalSessionIdentityAdapter:
    """Resolve only the HttpOnly, in-memory v7.0 loopback session cookie."""

    def __init__(self, sessions: LocalSessionManager) -> None:
        self._sessions = sessions

    @property
    def session_ttl_seconds(self) -> int:
        return self._sessions.session_ttl_seconds

    def resolve(self, request: Request) -> TrustedDesktopIdentity:
        has_cookie = diagnostic_cookie_present(request)
        marker = origin_marker(request)
        emit_webview_diagnostic(
            "WEBVIEW_DESKTOP_IDENTITY_BEGIN",
            result="ENTERED",
            cookie_present_value=has_cookie,
            origin=marker,
        )
        try:
            value = self._sessions.authenticate(request.cookies.get(DESKTOP_SESSION_COOKIE))
        except SessionRejected as error:
            identity_result = identity_result_for_code(error.code)
            emit_webview_diagnostic(
                f"WEBVIEW_DESKTOP_IDENTITY_{identity_result}",
                result="AUTH_REJECTED",
                cookie_present_value=has_cookie,
                identity_result=identity_result,
                error_code=error.code,
                origin=marker,
            )
            raise HTTPException(status_code=401, detail=error.code) from error
        request.state.desktop_session_renewed = True
        emit_webview_diagnostic(
            "WEBVIEW_DESKTOP_IDENTITY_PASS",
            result="SUCCESS",
            cookie_present_value=has_cookie,
            identity_result="PASS",
            origin=marker,
        )
        return TrustedDesktopIdentity(
            requester=identifier(value.requester, field="desktop requester"),
            tenant_id=identifier(value.tenant_id, field="environment tenant"),
            authentication="LOCAL_BOOTSTRAP_SESSION",
        )


class DesktopSessionCookieMiddleware:
    """Refresh the session cookie on the final response, including streamed responses."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_cookie(message: Mapping[str, Any]) -> None:
            state = scope.get("state")
            if message.get("type") != "http.response.start" or not isinstance(state, dict) or not state.get("desktop_session_renewed"):
                await send(message)
                return
            application = scope.get("app")
            provider = getattr(getattr(application, "state", None), "desktop_identity_provider", None)
            token = Request(scope).cookies.get(DESKTOP_SESSION_COOKIE)
            if not isinstance(provider, ProductionLocalSessionIdentityAdapter) or token is None:
                await send(message)
                return
            refreshed = Response()
            set_desktop_session_cookie(refreshed, token, max_age=provider.session_ttl_seconds)
            cookie_headers = [
                header
                for header in refreshed.raw_headers
                if header[0].lower() == b"set-cookie"
            ]
            updated = dict(message)
            updated["headers"] = [*message.get("headers", ()), *cookie_headers]
            await send(updated)

        await self._app(scope, receive, send_with_cookie)


def desktop_identity(
    request: Request,
    response: Response,
    requester_header: str | None = Header(default=None, alias="X-Copilot-Requester"),
    tenant_header: str | None = Header(default=None, alias="X-Environment-Tenant"),
) -> TrustedDesktopIdentity:
    """Resolve a trusted identity and keep legacy headers out of production trust.

    Production deployments must install ``app.state.desktop_identity_provider``.
    The header fallback is deliberately restricted to development mode and exists
    only to keep the offline single-user desktop usable.
    """

    provider = getattr(request.app.state, "desktop_identity_provider", None)
    if provider is not None:
        value = provider.resolve(request)
        if not isinstance(value, TrustedDesktopIdentity):
            raise HTTPException(status_code=401, detail="DESKTOP_IDENTITY_UNAVAILABLE")
        if isinstance(provider, ProductionLocalSessionIdentityAdapter):
            del response
        return value

    settings = getattr(request.app.state, "settings", None)
    environment = str(getattr(settings, "environment", "development")).lower()
    local_enabled = bool(getattr(settings, "desktop_local_identity_enabled", True))
    if environment not in {"development", "test", "local"} or not local_enabled:
        raise HTTPException(status_code=401, detail="DESKTOP_IDENTITY_REQUIRED")
    if requester_header is None or tenant_header is None:
        raise HTTPException(status_code=401, detail="DESKTOP_IDENTITY_REQUIRED")
    try:
        return TrustedDesktopIdentity(
            requester=identifier(requester_header, field="desktop requester"),
            tenant_id=identifier(tenant_header, field="environment tenant"),
            authentication="LOCAL_DEVELOPMENT_HEADER",
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=401, detail="DESKTOP_IDENTITY_INVALID") from error


def desktop_requester(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.requester


def desktop_tenant(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> str:
    return identity.tenant_id


__all__ = (
    "LocalDevelopmentIdentityAdapter",
    "DesktopSessionCookieMiddleware",
    "ProductionLocalSessionIdentityAdapter",
    "TrustedDesktopIdentity",
    "DESKTOP_SESSION_COOKIE",
    "DESKTOP_SESSION_COOKIE_PATH",
    "desktop_identity",
    "desktop_requester",
    "desktop_tenant",
    "set_desktop_session_cookie",
)
