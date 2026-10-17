from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import (
    DesktopSessionCookieMiddleware,
    ProductionLocalSessionIdentityAdapter,
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.production_release.session import LocalSessionManager, SessionRejected


def _clock() -> tuple[list[datetime], callable]:
    current = [datetime(2026, 8, 28, 12, 0, tzinfo=UTC)]
    return current, lambda: current[0]


def test_authenticated_activity_slides_session_expiry_without_resurrecting_idle_session() -> None:
    current, now = _clock()
    manager = LocalSessionManager(now=now, session_ttl_seconds=1800)
    token = manager.exchange(manager.issue_bootstrap(requester="requester", tenant_id="tenant"))

    current[0] += timedelta(seconds=1200)
    assert manager.authenticate(token).requester == "requester"
    current[0] += timedelta(seconds=1799)
    assert manager.authenticate(token).tenant_id == "tenant"
    current[0] += timedelta(seconds=1800)
    with pytest.raises(SessionRejected) as error:
        manager.authenticate(token)
    assert error.value.code == "DESKTOP_SESSION_EXPIRED"


def test_missing_invalid_and_expired_sessions_have_distinct_safe_codes() -> None:
    current, now = _clock()
    manager = LocalSessionManager(now=now, session_ttl_seconds=1800)

    with pytest.raises(SessionRejected) as missing:
        manager.authenticate(None)
    assert missing.value.code == "DESKTOP_IDENTITY_REQUIRED"

    with pytest.raises(SessionRejected) as invalid:
        manager.authenticate("invalid-session")
    assert invalid.value.code == "DESKTOP_IDENTITY_INVALID"

    token = manager.exchange(manager.issue_bootstrap(requester="requester", tenant_id="tenant"))
    current[0] += timedelta(seconds=1800)
    with pytest.raises(SessionRejected) as expired:
        manager.authenticate(token)
    assert expired.value.code == "DESKTOP_SESSION_EXPIRED"


def test_authenticated_dependency_refreshes_cookie_and_expired_route_fails_closed() -> None:
    current, now = _clock()
    manager = LocalSessionManager(now=now, session_ttl_seconds=1800)
    app = FastAPI()
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(manager)
    app.add_middleware(DesktopSessionCookieMiddleware)
    reached: list[str] = []

    @app.get("/api/protected")
    def protected(identity: TrustedDesktopIdentity = Depends(desktop_identity)):
        reached.append(identity.requester)
        return {"status": "ok"}

    @app.get("/api/protected-json")
    def protected_json(identity: TrustedDesktopIdentity = Depends(desktop_identity)):
        del identity
        return JSONResponse(content={"status": "ok"})

    @app.get("/api/protected-stream")
    def protected_stream(identity: TrustedDesktopIdentity = Depends(desktop_identity)):
        del identity
        return StreamingResponse(iter(["ok"]), media_type="text/plain")

    token = manager.exchange(manager.issue_bootstrap(requester="requester", tenant_id="tenant"))
    with TestClient(app) as client:
        client.cookies.set("EmbeddedCopilotSession", token)
        current[0] += timedelta(seconds=1200)
        renewed = client.get("/api/protected")
        assert renewed.status_code == 200
        set_cookie = renewed.headers["set-cookie"]
        assert "EmbeddedCopilotSession=" in set_cookie
        assert "Max-Age=1800" in set_cookie
        assert "Path=/api" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "Secure" not in set_cookie
        assert renewed.headers.get_list("content-length") == [str(len(renewed.content))]

        for path in ("/api/protected-json", "/api/protected-stream"):
            response = client.get(path)
            assert response.status_code == 200
            assert "EmbeddedCopilotSession=" in response.headers["set-cookie"]
            assert "Max-Age=1800" in response.headers["set-cookie"]
            expected_content_lengths = (
                [str(len(response.content))]
                if path == "/api/protected-json"
                else []
            )
            assert response.headers.get_list("content-length") == expected_content_lengths

        current[0] += timedelta(seconds=1801)
        rejected = client.get("/api/protected")

    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "DESKTOP_SESSION_EXPIRED"
    assert reached == ["requester"]


def test_bootstrap_nonce_is_single_use_and_restart_invalidates_old_session() -> None:
    current, now = _clock()
    manager = LocalSessionManager(now=now)
    nonce = manager.issue_bootstrap(requester="requester", tenant_id="tenant")
    token = manager.exchange(nonce)
    with pytest.raises(SessionRejected):
        manager.exchange(nonce)
    with pytest.raises(SessionRejected):
        LocalSessionManager(now=now).authenticate(token)
