from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api import v70_release_routes
from embedded_copilot.api.desktop_identity import (
    ProductionLocalSessionIdentityAdapter,
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.production_release import webview_session_diagnostic as diagnostic
from embedded_copilot.production_release.session import LocalSessionManager
from scripts import v70_desktop_launcher


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _protected_app(manager: LocalSessionManager) -> FastAPI:
    application = FastAPI()
    application.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(manager)
    application.add_middleware(diagnostic.WebViewSessionDiagnosticMiddleware)

    @application.get("/api/v1/copilot/desktop/system/provider")
    def provider_projection(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> dict[str, str]:
        del identity
        return {"status": "safe-projection"}

    return application


def test_webview_diagnostic_is_disabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    diagnostic.emit(
        "WEBVIEW_PROTECTED_REQUEST_RECEIVED",
        result="RECEIVED",
        route="PROVIDER_PROJECTION",
        cookie_present_value=True,
        origin="WEBVIEW",
    )

    assert not output.exists()


def test_webview_diagnostic_uses_only_fixed_safe_fields_and_never_cookie_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    cookie_canary = "WEBVIEW_COOKIE_CANARY_DO_NOT_LEAK"
    token_canary = "WEBVIEW_SESSION_TOKEN_CANARY_DO_NOT_LEAK"
    nonce_canary = "WEBVIEW_BOOTSTRAP_NONCE_CANARY_DO_NOT_LEAK"
    with diagnostic.request_scope("WEBVIEW_REQUEST_ID_CANARY_DO_NOT_LEAK"):
        diagnostic.emit(
            "WEBVIEW_PROTECTED_REQUEST_RECEIVED",
            result="RECEIVED",
            route="PROVIDER_PROJECTION",
            cookie_present_value=True,
            origin="WEBVIEW",
        )
        diagnostic.emit(
            "UNTRUSTED_STAGE",
            result="UNTRUSTED_RESULT",
            route="UNTRUSTED_ROUTE",
            origin="UNATTRIBUTED",
        )

    rows = _events(output)
    assert len(rows) == 1
    assert set(rows[0]) <= diagnostic.ALLOWED_EVENT_FIELDS
    assert rows[0]["cookie_present"] is True
    assert rows[0]["pid"] > 0
    assert rows[0]["build_id"] == diagnostic.DIAGNOSTIC_BUILD_ID
    content = output.read_text(encoding="utf-8")
    assert all(canary not in content for canary in (cookie_canary, token_canary, nonce_canary))
    assert "WEBVIEW_REQUEST_ID_CANARY_DO_NOT_LEAK" not in content


def test_webview_diagnostic_writer_failure_is_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingPath:
        parent = None

        def __init__(self) -> None:
            self.parent = self

        def mkdir(self, **kwargs: object) -> None:
            del kwargs
            raise OSError("WEBVIEW_DIAGNOSTIC_SINK_CANARY")

    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: FailingPath())

    diagnostic.emit(
        "WEBVIEW_SESSION_CREATED",
        result="SUCCESS",
        route="SESSION_EXCHANGE",
        origin="UNATTRIBUTED",
    )


def test_webview_diagnostic_sink_is_append_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    history = '{"existing":"history"}\n'
    output.write_text(history, encoding="utf-8")
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    diagnostic.emit(
        "WEBVIEW_PROTECTED_RESPONSE",
        result="SUCCESS",
        route="PROVIDER_PROJECTION",
        http_status=200,
        origin="WEBVIEW",
    )

    assert output.read_text(encoding="utf-8").startswith(history)


def test_webview_diagnostic_concurrent_writes_remain_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    def write() -> None:
        diagnostic.emit(
            "WEBVIEW_PROTECTED_RESPONSE",
            result="SUCCESS",
            route="PROVIDER_PROJECTION",
            http_status=200,
            origin="WEBVIEW",
        )

    threads = [threading.Thread(target=write) for _ in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = _events(output)
    assert rows
    assert all(set(row) <= diagnostic.ALLOWED_EVENT_FIELDS for row in rows)


def test_protected_auth_semantics_are_identical_when_diagnostic_is_toggled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path
    manager = LocalSessionManager()
    application = _protected_app(manager)
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    with TestClient(application) as client:
        disabled = client.get(
            "/api/v1/copilot/desktop/system/provider",
            headers={diagnostic.DIAGNOSTIC_ORIGIN_HEADER: diagnostic.DIAGNOSTIC_ORIGIN_VALUE},
        )
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    with TestClient(application) as client:
        enabled = client.get(
            "/api/v1/copilot/desktop/system/provider",
            headers={diagnostic.DIAGNOSTIC_ORIGIN_HEADER: diagnostic.DIAGNOSTIC_ORIGIN_VALUE},
        )

    assert (disabled.status_code, disabled.json()) == (enabled.status_code, enabled.json())
    assert enabled.status_code == 401


def test_protected_request_records_cookie_presence_not_cookie_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    application = _protected_app(LocalSessionManager())

    with TestClient(application) as client:
        client.cookies.set(diagnostic.DESKTOP_SESSION_COOKIE, "WEBVIEW_COOKIE_CANARY_DO_NOT_LEAK")
        response = client.get(
            "/api/v1/copilot/desktop/system/provider",
            headers={diagnostic.DIAGNOSTIC_ORIGIN_HEADER: diagnostic.DIAGNOSTIC_ORIGIN_VALUE},
        )

    assert response.status_code == 401
    rows = _events(output)
    request = next(row for row in rows if row["stage"] == "WEBVIEW_PROTECTED_REQUEST_RECEIVED")
    identity = next(row for row in rows if row["stage"] == "WEBVIEW_DESKTOP_IDENTITY_UNKNOWN")
    assert request["cookie_present"] is True
    assert identity["identity_result"] == "UNKNOWN"
    content = output.read_text(encoding="utf-8")
    assert "WEBVIEW_COOKIE_CANARY_DO_NOT_LEAK" not in content


def test_valid_identity_semantics_are_identical_when_diagnostic_is_toggled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    manager = LocalSessionManager()
    token = manager.exchange(manager.issue_bootstrap(requester="requester", tenant_id="tenant"))
    application = _protected_app(manager)

    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    with TestClient(application) as client:
        client.cookies.set(diagnostic.DESKTOP_SESSION_COOKIE, token)
        disabled = client.get("/api/v1/copilot/desktop/system/provider")
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    with TestClient(application) as client:
        client.cookies.set(diagnostic.DESKTOP_SESSION_COOKIE, token)
        enabled = client.get("/api/v1/copilot/desktop/system/provider")

    assert (disabled.status_code, disabled.json()) == (enabled.status_code, enabled.json())
    assert enabled.status_code == 200
    assert any(row["stage"] == "WEBVIEW_DESKTOP_IDENTITY_PASS" for row in _events(output))
    assert token not in output.read_text(encoding="utf-8")


def test_session_exchange_records_safe_backend_sequence_without_nonce_or_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    manager = LocalSessionManager()
    runtime = SimpleNamespace(
        sessions=manager,
        settings=SimpleNamespace(desktop_session_ttl_seconds=1800),
    )
    monkeypatch.setattr(v70_release_routes, "_runtime", lambda request: runtime)
    application = FastAPI()
    application.add_middleware(diagnostic.WebViewSessionDiagnosticMiddleware)
    application.include_router(v70_release_routes.router)

    nonce = manager.issue_bootstrap(requester="requester", tenant_id="tenant")
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            headers={diagnostic.DIAGNOSTIC_ORIGIN_HEADER: diagnostic.DIAGNOSTIC_ORIGIN_VALUE},
            json={"nonce": nonce},
        )

    assert response.status_code == 200
    assert "EmbeddedCopilotSession=" in response.headers["set-cookie"]
    assert [row["stage"] for row in _events(output)] == [
        "WEBVIEW_SESSION_EXCHANGE_REQUEST_RECEIVED",
        "WEBVIEW_SESSION_EXCHANGE_ROUTE_ENTERED",
        "WEBVIEW_SESSION_CREATED",
        "WEBVIEW_SESSION_COOKIE_SET",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_BEGIN",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_COMPLETE",
    ]
    content = output.read_text(encoding="utf-8")
    assert nonce not in content
    assert "WEBVIEW_SESSION_TOKEN_CANARY_DO_NOT_LEAK" not in content
    assert "Set-Cookie" not in content


def test_diagnostic_bootstrap_marker_is_native_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")

    assert v70_desktop_launcher._diagnostic_bootstrap_query(browser=False) == "?webview_session_diagnostic=1"
    assert v70_desktop_launcher._diagnostic_bootstrap_query(browser=True) == ""


def test_exchange_response_event_waits_for_downstream_asgi_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    send_time_stages: list[list[object]] = []

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start":
            send_time_stages.append([row["stage"] for row in _events(output)])

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/copilot/desktop/system/bootstrap/exchange",
        "headers": [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")],
        "state": {},
    }
    asyncio.run(diagnostic.WebViewSessionDiagnosticMiddleware(application)(scope, receive, send))

    assert send_time_stages
    assert "WEBVIEW_SESSION_EXCHANGE_RESPONSE" not in send_time_stages[0]
    assert any(row["stage"] == "WEBVIEW_SESSION_EXCHANGE_RESPONSE" for row in _events(output))


def test_provider_response_send_complete_requires_final_body_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/copilot/desktop/system/provider",
        "headers": [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")],
        "state": {},
    }
    asyncio.run(diagnostic.WebViewSessionDiagnosticMiddleware(application)(scope, receive, send))

    rows = _events(output)
    stages = [row["stage"] for row in rows]
    assert "WEBVIEW_PROVIDER_RESPONSE_SEND_BEGIN" in stages
    assert "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE" in stages
    assert stages.index("WEBVIEW_PROVIDER_RESPONSE_SEND_BEGIN") < stages.index("WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE")
    complete = next(row for row in _events(output) if row["stage"] == "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE")
    assert complete["route"] == "PROVIDER"
    assert complete["backend_response_status"] == 200
    assert complete["asgi_final_body_send_completed"] is True
    assert complete["asgi_send_failure_safe_class"] == "NONE"
    assert complete["more_body_final"] is False
    assert len(sent) == 2


def test_provider_response_send_failure_never_reports_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("RESPONSE_SEND_CANARY")

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/copilot/desktop/system/provider",
        "headers": [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")],
        "state": {},
    }
    with pytest.raises(OSError):
        asyncio.run(diagnostic.WebViewSessionDiagnosticMiddleware(application)(scope, receive, send))

    stages = [row["stage"] for row in _events(output)]
    assert "WEBVIEW_PROVIDER_RESPONSE_SEND_BEGIN" in stages
    assert "WEBVIEW_PROVIDER_RESPONSE_SEND_FAILURE" in stages
    assert "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE" not in stages
    failure = next(row for row in _events(output) if row["stage"] == "WEBVIEW_PROVIDER_RESPONSE_SEND_FAILURE")
    assert failure["route"] == "PROVIDER"
    assert failure["backend_response_status"] == 200
    assert failure["asgi_final_body_send_completed"] is False
    assert failure["asgi_send_failure_safe_class"] == "HTTP_ERROR"
    assert failure["more_body_final"] is False


def test_project_locations_response_send_complete_requires_final_body_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/copilot/projects/locations",
        "headers": [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")],
        "state": {},
    }
    asyncio.run(diagnostic.WebViewSessionDiagnosticMiddleware(application)(scope, receive, send))

    complete = next(
        row
        for row in _events(output)
        if row["stage"] == "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_COMPLETE"
    )
    assert complete == {
        **{key: complete[key] for key in ("timestamp", "pid", "build_id", "request_id")},
        "stage": "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_COMPLETE",
        "result": "SUCCESS",
        "route": "PROJECT_LOCATIONS",
        "origin_marker": "WEBVIEW",
        "backend_response_status": 200,
        "asgi_final_body_send_completed": True,
        "asgi_send_failure_safe_class": "NONE",
        "more_body_final": False,
        "method_class": "GET",
        "content_type_class": "ABSENT",
        "content_length_present": False,
        "transfer_encoding_present": False,
        "content_encoding_present": False,
        "connection_close_present": False,
        "response_body_mode": "NORMAL",
        "middleware_chain_class": "PROTECTED_DESKTOP_IDENTITY",
        "cors_header_presence": False,
        "corp_policy_category": "ABSENT",
        "cache_control_presence": False,
    }
    assert len(sent) == 2


@pytest.mark.parametrize(
    ("method", "path", "expected_stage", "route", "middleware_chain_class"),
    (
        (
            "POST",
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_COMPLETE",
            "SESSION_EXCHANGE",
            "PUBLIC_SESSION_EXCHANGE",
        ),
        (
            "GET",
            "/api/v1/copilot/projects/locations",
            "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_COMPLETE",
            "PROJECT_LOCATIONS",
            "PROTECTED_DESKTOP_IDENTITY",
        ),
        (
            "GET",
            "/api/v1/copilot/desktop/system/provider",
            "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE",
            "PROVIDER",
            "PROTECTED_DESKTOP_IDENTITY",
        ),
    ),
)
def test_three_control_final_send_metadata_is_self_contained_and_secret_safe(
    method: str,
    path: str,
    expected_stage: str,
    route: str,
    middleware_chain_class: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    async def application(scope: dict[str, object], receive: object, send: object) -> None:
        del scope, receive
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", b"2"),
                (b"cache-control", b"no-store"),
                (b"cross-origin-resource-policy", b"same-origin"),
                (b"authorization", b"Bearer SECRET_METADATA_CANARY"),
                (b"set-cookie", b"SECRET_METADATA_CANARY"),
            ],
        })
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        del message

    scope: dict[str, object] = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")],
        "state": {},
    }
    asyncio.run(diagnostic.WebViewSessionDiagnosticMiddleware(application)(scope, receive, send))

    row = next(item for item in _events(output) if item["stage"] == expected_stage)
    assert row["route"] == route
    assert row["backend_response_status"] == 200
    assert row["asgi_final_body_send_completed"] is True
    assert row["more_body_final"] is False
    assert row["method_class"] == method
    assert row["content_type_class"] == "JSON"
    assert row["content_length_present"] is True
    assert row["transfer_encoding_present"] is False
    assert row["content_encoding_present"] is False
    assert row["connection_close_present"] is False
    assert row["response_body_mode"] == "NORMAL"
    assert row["middleware_chain_class"] == middleware_chain_class
    assert row["cors_header_presence"] is False
    assert row["corp_policy_category"] == "SAME_ORIGIN"
    assert row["cache_control_presence"] is True
    assert "SECRET_METADATA_CANARY" not in output.read_text(encoding="utf-8")
