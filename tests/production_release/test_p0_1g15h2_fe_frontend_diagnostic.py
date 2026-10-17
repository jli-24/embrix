from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Request
from pydantic import ValidationError

from embedded_copilot.api import v70_release_routes
from embedded_copilot.api.v70_release_models import FrontendProviderDiagnosticInput
from embedded_copilot.production_release import webview_session_diagnostic as diagnostic


REQUEST_ID = "12345678-1234-1234-1234-1234567890ab"
ORIGIN_HEADERS = [(diagnostic.DIAGNOSTIC_ORIGIN_HEADER.lower().encode(), b"webview")]


def _request(*, host: str = "127.0.0.1", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/copilot/desktop/system/diagnostics/frontend-provider",
        "headers": ORIGIN_HEADERS if headers is None else headers,
        "client": (host, 43123),
        "state": {},
    }
    return Request(scope)


def _payload(**overrides: object) -> FrontendProviderDiagnosticInput:
    values: dict[str, object] = {
        "build_id": diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
        "stage": "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
        "result": "SUCCESS",
        "route": "PROVIDER_PROJECTION",
        "fetch_attempt_id": "provider-fetch-1",
        "event_sequence": 6,
        "projection_state": "CONFIGURED",
        "credential_state": "CONFIGURED",
        "request_id": REQUEST_ID,
    }
    values.update(overrides)
    return FrontendProviderDiagnosticInput.model_validate(values)


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_frontend_diagnostic_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    response = v70_release_routes.frontend_provider_diagnostic(_payload(), _request())

    assert response.status_code == 204
    assert not output.exists()


def test_frontend_diagnostic_accepts_only_local_fixed_safe_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    response = v70_release_routes.frontend_provider_diagnostic(_payload(), _request())

    assert response.status_code == 204
    rows = _events(output)
    assert rows == [
        {
            "timestamp": rows[0]["timestamp"],
            "pid": rows[0]["pid"],
            "build_id": "P0-1G15H2-FE",
            "stage": "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
            "result": "SUCCESS",
            "request_id": REQUEST_ID,
            "origin_marker": "WEBVIEW",
            "backend_origin_header_class": "NO_ORIGIN",
            "route": "PROVIDER_PROJECTION",
            "fetch_attempt_id": "provider-fetch-1",
            "event_sequence": 6,
            "projection_state": "CONFIGURED",
            "credential_state": "CONFIGURED",
        }
    ]


def test_frontend_diagnostic_rejects_non_loopback_and_unmarked_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    remote = v70_release_routes.frontend_provider_diagnostic(_payload(), _request(host="192.0.2.1"))
    unmarked = v70_release_routes.frontend_provider_diagnostic(_payload(), _request(headers=[]))

    assert remote.status_code == 204
    assert unmarked.status_code == 204
    assert not output.exists()


def test_frontend_diagnostic_schema_forbids_extra_fields_without_echoing_canaries() -> None:
    canary = "FRONTEND_PROVIDER_API_KEY_CANARY_DO_NOT_LEAK"

    with pytest.raises(ValidationError) as raised:
        FrontendProviderDiagnosticInput.model_validate(
            {
                "build_id": diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
                "stage": "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
                "result": "SUCCESS",
                "route": "PROVIDER_PROJECTION",
                "provider_projection": canary,
            }
        )

    assert canary not in str(raised.value)


def test_frontend_failure_fields_are_safe_and_body_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    v70_release_routes.frontend_provider_diagnostic(
        _payload(
            stage="WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE",
            result="FAILURE",
            projection_state=None,
            credential_state=None,
            safe_error_class="JSON_PARSE_ERROR",
            failure_stage="JSON_PARSE",
        ),
        _request(),
    )

    row = _events(output)[0]
    assert row["safe_error_class"] == "JSON_PARSE_ERROR"
    assert row["failure_stage"] == "JSON_PARSE"
    assert row["fetch_attempt_id"] == "provider-fetch-1"
    assert row["event_sequence"] == 6
    assert "provider" not in row
    assert "response" not in row
    assert set(row) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_frontend_render_state_provider_presence_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    v70_release_routes.frontend_provider_diagnostic(
        _payload(
            stage="WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE",
            result="SUCCESS",
            projection_state="CONFIGURED",
            credential_state="CONFIGURED",
            provider_present=True,
        ),
        _request(),
    )

    row = _events(output)[0]
    assert row["provider_present"] is True
    assert set(row) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_frontend_fetcher_identity_is_a_bounded_enum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    v70_release_routes.frontend_provider_diagnostic(
        _payload(
            stage="WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
            result="ENTERED",
            fetcher_identity="OTHER",
        ),
        _request(),
    )

    row = _events(output)[0]
    assert row["fetcher_identity"] == "OTHER"
    assert set(row) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_frontend_failure_carries_safe_fetcher_origin_and_rejection_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    v70_release_routes.frontend_provider_diagnostic(
        _payload(
            stage="WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
            result="FAILURE",
            projection_state=None,
            credential_state=None,
            safe_error_class="FETCH_REJECTED",
            failure_stage="FETCH",
            production_fetcher_identity="GLOBAL_BROWSER_FETCH",
            frontend_origin_class="LOOPBACK_HTTP",
            provider_target_origin_class="SAME_ORIGIN",
            frontend_provider_same_origin=True,
            provider_request_url_strategy="RELATIVE_SAME_ORIGIN",
            request_credentials_mode="SAME_ORIGIN",
            document_ready_state_at_fetch="COMPLETE",
            navigation_state_at_fetch="STABLE",
            document_generation_at_fetch=1,
            document_generation_at_rejection=1,
            navigation_started_during_fetch=False,
            document_context_changed_during_fetch=False,
            abort_signal_attached_to_provider_fetch="NONE",
            abort_trigger_observed=False,
            abort_trigger_source="NONE",
            fetch_rejection_error_class="TYPE_ERROR",
            fetch_rejection_error_name="TypeError",
            fetch_rejection_message_category="FAILED_TO_FETCH",
            fetch_failure_kind="PROMISE_REJECTION",
            fetch_failure_stage="FETCH_CALL",
        ),
        _request(),
    )

    row = _events(output)[0]
    assert row["production_fetcher_identity"] == "GLOBAL_BROWSER_FETCH"
    assert row["frontend_provider_same_origin"] is True
    assert row["fetch_rejection_error_class"] == "TYPE_ERROR"
    assert row["fetch_rejection_error_name"] == "TypeError"
    assert row["fetch_rejection_message_category"] == "FAILED_TO_FETCH"
    assert row["fetch_failure_kind"] == "PROMISE_REJECTION"
    assert row["fetch_failure_stage"] == "FETCH_CALL"
    assert row["document_generation_at_fetch"] == 1
    assert set(row) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_project_locations_frontend_resolution_uses_the_same_safe_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    payload = _payload(
        build_id=diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
        stage="WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
        result="SUCCESS",
        route="PROJECT_LOCATIONS",
        fetch_attempt_id=None,
        event_sequence=None,
        projection_state=None,
        credential_state=None,
        http_status=200,
    )
    response = v70_release_routes.frontend_provider_diagnostic(payload, _request())

    assert response.status_code == 204
    row = _events(output)[0]
    assert row["route"] == "PROJECT_LOCATIONS"
    assert row["http_status"] == 200
    assert set(row) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_frontend_readiness_event_promotes_h10_after_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    handoffs: list[tuple[str, str, str, int]] = []
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    monkeypatch.setattr(
        v70_release_routes,
        "record_native_handoff",
        lambda stage, status, *, reason_code, http_status: handoffs.append(
            (stage, status, reason_code, http_status)
        ),
    )
    payload = _payload(
        stage="WEBVIEW_FRONTEND_READINESS_PASS",
        result="SUCCESS",
        fetch_attempt_id=None,
        event_sequence=None,
    )

    response = v70_release_routes.frontend_provider_diagnostic(payload, _request())

    assert response.status_code == 204
    assert _events(output)[0]["stage"] == "WEBVIEW_FRONTEND_READINESS_PASS"
    assert handoffs == [
        ("H10_CHAT_READY", "PASS", "FRONTEND_REQUIRED_DEPENDENCIES_READY", 204)
    ]


def test_frontend_readiness_does_not_promote_h10_when_disabled_untrusted_or_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoffs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        v70_release_routes,
        "record_native_handoff",
        lambda stage, status, **_kwargs: handoffs.append((stage, status)),
    )
    readiness = _payload(
        stage="WEBVIEW_FRONTEND_READINESS_PASS",
        result="SUCCESS",
        fetch_attempt_id=None,
        event_sequence=None,
    )

    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    v70_release_routes.frontend_provider_diagnostic(readiness, _request())
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    v70_release_routes.frontend_provider_diagnostic(readiness, _request(host="192.0.2.1"))
    v70_release_routes.frontend_provider_diagnostic(readiness, _request(headers=[]))
    v70_release_routes.frontend_provider_diagnostic(
        _payload(
            stage="WEBVIEW_FRONTEND_READINESS_PASS",
            result="FAILURE",
            fetch_attempt_id=None,
            event_sequence=None,
        ),
        _request(),
    )

    assert handoffs == []


def test_frontend_rejection_fields_are_strict_safe_enums() -> None:
    with pytest.raises(ValidationError):
        FrontendProviderDiagnosticInput.model_validate(
            {
                "build_id": diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
                "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
                "result": "FAILURE",
                "route": "PROVIDER_PROJECTION",
                "fetch_rejection_error_name": "TypeError https://secret",
                "fetch_rejection_message_category": "RAW_MESSAGE",
                "fetch_failure_kind": "RAW_KIND",
                "fetch_failure_stage": "RAW_STAGE",
            }
        )


def test_frontend_origin_fields_are_strict_enums() -> None:
    with pytest.raises(ValidationError):
        FrontendProviderDiagnosticInput.model_validate(
            {
                "build_id": diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
                "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
                "result": "FAILURE",
                "route": "PROVIDER_PROJECTION",
                "frontend_origin_class": "SAME_MACHINE",
            }
        )


def test_frontend_diagnostic_rejects_invalid_attempt_sequence_and_failure_stage() -> None:
    with pytest.raises(ValidationError):
        FrontendProviderDiagnosticInput.model_validate(
            {
                "build_id": diagnostic.FRONTEND_DIAGNOSTIC_BUILD_ID,
                "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
                "result": "FAILURE",
                "route": "PROVIDER_PROJECTION",
                "fetch_attempt_id": "webview-session-not-an-attempt",
                "event_sequence": 0,
                "failure_stage": "NOT_A_FAILURE_STAGE",
            }
        )
