from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from embedded_copilot.native_shell import host
from embedded_copilot.production_release import webview_session_diagnostic as diagnostic


class _FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Callable[[object], None]] = []

    def __iadd__(self, handler: Callable[[object], None]) -> _FakeEvent:
        self.handlers.append(handler)
        return self

    def __isub__(self, handler: Callable[[object], None]) -> _FakeEvent:
        self.handlers.remove(handler)
        return self

    def fire(self, value: object) -> None:
        for handler in tuple(self.handlers):
            handler(value)


class _FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(
            request_sent=_FakeEvent(),
            response_received=_FakeEvent(),
        )


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    ("url", "method", "expected"),
    (
        (
            "http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?token=SECRET#fragment",
            "GET",
            "PROVIDER",
        ),
        (
            "http://127.0.0.1:6487/api/v1/copilot/projects/locations?session=SECRET#fragment",
            "GET",
            "PROJECT_LOCATIONS",
        ),
        (
            "http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider",
            "POST",
            None,
        ),
        (
            "http://127.0.0.1:6487/assets/app.js?token=SECRET#fragment",
            "GET",
            None,
        ),
    ),
)
def test_network_route_classifier_uses_only_the_two_real_get_paths(
    url: str,
    method: str,
    expected: str | None,
) -> None:
    assert diagnostic.classify_webview_network_route(url, method=method) == expected


def test_network_response_route_classifier_does_not_require_request_method() -> None:
    assert (
        diagnostic.classify_webview_network_route(
            "http://127.0.0.1:6487/api/v1/copilot/projects/locations?query=SECRET#fragment"
        )
        == "PROJECT_LOCATIONS"
    )


def test_network_diagnostic_payload_is_fixed_and_content_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    raw_url = "http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?api_key=SECRET"
    diagnostic.emit(
        "WEBVIEW_NETWORK_REQUESTED",
        result="RECEIVED",
        route="PROVIDER",
        method="GET",
        request_sequence=1,
        origin="WEBVIEW",
    )
    diagnostic.emit(
        "WEBVIEW_NETWORK_RESPONSE_RECEIVED",
        result="SUCCESS",
        route="PROVIDER",
        http_status=200,
        request_sequence=1,
        origin="WEBVIEW",
    )

    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "WEBVIEW_NETWORK_REQUESTED",
        "WEBVIEW_NETWORK_RESPONSE_RECEIVED",
    ]
    assert rows[0]["route"] == "PROVIDER"
    assert rows[0]["method"] == "GET"
    assert rows[1]["http_status"] == 200
    assert rows[1]["request_sequence"] == rows[0]["request_sequence"]
    assert set(rows[0]) <= diagnostic.ALLOWED_EVENT_FIELDS
    assert set(rows[1]) <= diagnostic.ALLOWED_EVENT_FIELDS
    content = output.read_text(encoding="utf-8")
    assert raw_url not in content
    assert all(
        field not in content
        for field in ("headers", "Cookie", "Authorization", "Origin", "body", "Set-Cookie")
    )


def test_passive_observer_is_idempotent_and_unbinds_on_window_close() -> None:
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()

    assert observer.bind(window) is True
    assert observer.bind(window) is True
    assert len(window.events.request_sent.handlers) == 1
    assert len(window.events.response_received.handlers) == 1

    observer.unbind(window)
    assert window.events.request_sent.handlers == []
    assert window.events.response_received.handlers == []


def test_network_sequence_remains_process_local_across_window_recreation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    first_window = _FakeWindow()
    first_observer = host._PassiveWebViewNetworkObserver()
    assert first_observer.bind(first_window) is True
    first_window.events.request_sent.fire(
        SimpleNamespace(
            url="http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider",
            method="GET",
            headers={},
        )
    )
    first_observer.unbind(first_window)

    second_window = _FakeWindow()
    second_observer = host._PassiveWebViewNetworkObserver()
    assert second_observer.bind(second_window) is True
    second_window.events.request_sent.fire(
        SimpleNamespace(
            url="http://127.0.0.1:6487/api/v1/copilot/projects/locations",
            method="GET",
            headers={},
        )
    )

    rows = _events(output)
    assert rows[1]["request_sequence"] == rows[0]["request_sequence"] + 1
    second_observer.unbind(second_window)


def test_passive_observer_correlates_each_route_by_process_local_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    provider_url = "http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?query=SECRET"
    locations_url = "http://127.0.0.1:6487/api/v1/copilot/projects/locations#fragment"
    window.events.request_sent.fire(
        SimpleNamespace(
            url=provider_url,
            method="GET",
            headers={"Authorization": "AUTH_CANARY", "Cookie": "COOKIE_CANARY"},
        )
    )
    window.events.response_received.fire(
        SimpleNamespace(
            url=provider_url,
            status_code=200,
            headers={"Set-Cookie": "SESSION_CANARY"},
        )
    )
    window.events.request_sent.fire(SimpleNamespace(url=locations_url, method="GET", headers={}))
    window.events.response_received.fire(SimpleNamespace(url=locations_url, status_code=200, headers={}))
    window.events.request_sent.fire(
        SimpleNamespace(
            url="http://127.0.0.1:6487/assets/app.js?token=ASSET_CANARY",
            method="GET",
            headers={},
        )
    )

    rows = _events(output)
    network_rows = [
        (row["stage"], row["route"])
        for row in rows
        if row["stage"] == "WEBVIEW_NETWORK_REQUESTED"
    ]
    assert network_rows == [
        ("WEBVIEW_NETWORK_REQUESTED", "PROVIDER"),
        ("WEBVIEW_NETWORK_REQUESTED", "PROJECT_LOCATIONS"),
    ]
    requests = [row for row in rows if row["stage"] == "WEBVIEW_NETWORK_REQUESTED"]
    assert all("request_sequence" in row for row in requests)
    observations = [row for row in rows if row["stage"] == "WEBVIEW_RESPONSE_OBSERVATION"]
    assert [(row["route"], row["classification_result"]) for row in observations] == [
        ("PROVIDER", "MATCHED"),
        ("PROJECT_LOCATIONS", "MATCHED"),
    ]
    assert all(row["route_match"] is True and row["response_callback_invoked"] is True for row in observations)
    assert diagnostic.WEBVIEW_NETWORK_CORRELATION_STRATEGY == "ROUTE_LEVEL_ONLY"
    content = output.read_text(encoding="utf-8")
    assert all(canary not in content for canary in ("SECRET", "AUTH_CANARY", "COOKIE_CANARY", "SESSION_CANARY"))


def test_response_without_a_matching_request_remains_route_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    window.events.response_received.fire(
        SimpleNamespace(
            url="http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?token=SECRET",
            status_code=502,
            headers={"Authorization": "AUTH_CANARY"},
        )
    )

    row = next(row for row in _events(output) if row["stage"] == "WEBVIEW_RESPONSE_OBSERVATION")
    assert row["route"] == "PROVIDER"
    assert row["http_status"] == 502
    assert row["route_match"] is True
    assert row["classification_result"] == "MATCHED"
    assert "request_sequence" not in row


def test_response_callback_malformed_event_isolated_with_safe_failure_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    window.events.response_received.fire(SimpleNamespace())

    rows = _events(output)
    assert len(rows) == 1
    assert rows[0]["stage"] == "WEBVIEW_RESPONSE_OBSERVATION"
    assert rows[0]["response_callback_invoked"] is True
    assert rows[0]["result"] == "FAILURE"
    assert rows[0]["classification_result"] == "MALFORMED"
    assert rows[0]["response_callback_stage"] == "RESPONSE_EVENT_PARSE"
    assert rows[0]["response_callback_safe_exception_class"] == "ATTRIBUTE_ERROR"
    assert all("url" not in row and "headers" not in row for row in rows)
    observer.unbind(window)


def test_unrelated_response_has_positive_callback_evidence_but_no_network_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    window.events.response_received.fire(SimpleNamespace(
        url="http://127.0.0.1:6487/assets/app.js?token=RESPONSE_SECRET",
        status_code=200,
        headers={"Authorization": "AUTH_SECRET"},
    ))

    rows = _events(output)
    assert len(rows) == 1
    assert rows[0]["stage"] == "WEBVIEW_RESPONSE_OBSERVATION"
    assert rows[0]["route"] == "OTHER"
    assert rows[0]["route_match"] is False
    assert rows[0]["classification_result"] == "IGNORED"
    content = output.read_text(encoding="utf-8")
    assert "RESPONSE_SECRET" not in content
    assert "AUTH_SECRET" not in content
    observer.unbind(window)


def test_observer_swallows_classification_exception_without_url_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    class _ExplodingUrl:
        def __str__(self) -> str:
            raise RuntimeError("https://127.0.0.1/provider?token=CLASSIFICATION_SECRET")

    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True
    window.events.request_sent.fire(SimpleNamespace(url=_ExplodingUrl(), method="GET"))

    assert not output.exists()
    observer.unbind(window)


def test_observer_isolated_from_sink_exceptions_and_does_not_mutate_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True
    request = SimpleNamespace(
        url="http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?token=SECRET",
        method="GET",
        headers={"Authorization": "AUTH_CANARY"},
    )
    response = SimpleNamespace(
        url=request.url,
        status_code=200,
        headers={"Set-Cookie": "SESSION_CANARY"},
    )
    request_before = vars(request).copy()
    response_before = vars(response).copy()

    def failing_sink(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("raw-url-SECRET")

    monkeypatch.setattr(host, "emit_webview_network_diagnostic", failing_sink)
    window.events.request_sent.fire(request)
    window.events.response_received.fire(response)

    assert vars(request) == request_before
    assert vars(response) == response_before


def test_response_callback_emits_one_self_contained_positive_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    window.events.response_received.fire(SimpleNamespace(
        url="http://127.0.0.1:6487/api/v1/copilot/desktop/system/provider?token=RESPONSE_SECRET",
        status_code=200,
        headers={"Authorization": "AUTH_CANARY", "Set-Cookie": "COOKIE_CANARY"},
    ))

    rows = _events(output)
    assert [row["stage"] for row in rows] == ["WEBVIEW_RESPONSE_OBSERVATION"]
    assert rows[0] == {
        "build_id": rows[0]["build_id"],
        "stage": "WEBVIEW_RESPONSE_OBSERVATION",
        "result": "RECEIVED",
        "route": "PROVIDER",
        "route_match": True,
        "http_status": 200,
        "classification_result": "MATCHED",
        "response_callback_invoked": True,
        "response_callback_stage": "RESPONSE_EVENT_PARSE",
        "response_callback_safe_exception_class": "NONE",
        "origin_marker": "WEBVIEW",
        "pid": rows[0]["pid"],
        "request_id": rows[0]["request_id"],
        "timestamp": rows[0]["timestamp"],
    }
    content = output.read_text(encoding="utf-8")
    assert all(canary not in content for canary in ("RESPONSE_SECRET", "AUTH_CANARY", "COOKIE_CANARY"))
    observer.unbind(window)


def test_response_observation_marks_unrelated_and_malformed_events_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    window = _FakeWindow()
    observer = host._PassiveWebViewNetworkObserver()
    assert observer.bind(window) is True

    window.events.response_received.fire(SimpleNamespace(url="http://127.0.0.1:6487/assets/app.js?token=SECRET", status_code=200))
    window.events.response_received.fire(SimpleNamespace())

    rows = _events(output)
    assert rows[0]["stage"] == "WEBVIEW_RESPONSE_OBSERVATION"
    assert rows[0]["route"] == "OTHER"
    assert rows[0]["route_match"] is False
    assert rows[0]["classification_result"] == "IGNORED"
    assert rows[1]["classification_result"] == "MALFORMED"
    assert rows[1]["response_callback_safe_exception_class"] == "ATTRIBUTE_ERROR"
    assert "SECRET" not in output.read_text(encoding="utf-8")
    observer.unbind(window)


def test_provider_diagnostic_window_is_bounded_by_attempt_begin_and_terminal_event() -> None:
    events = [
        {"fetch_attempt_id": "provider-fetch-1", "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN", "timestamp": "2026-08-30T00:00:01+00:00"},
        {"stage": "WEBVIEW_NETWORK_RESPONSE_RECEIVED", "timestamp": "2026-08-30T00:00:02+00:00"},
        {"fetch_attempt_id": "provider-fetch-1", "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE", "timestamp": "2026-08-30T00:00:03+00:00"},
        {"fetch_attempt_id": "provider-fetch-1", "stage": "WEBVIEW_NETWORK_RESPONSE_RECEIVED", "timestamp": "2026-08-30T00:00:04+00:00"},
    ]

    assert diagnostic.provider_diagnostic_window(events, "provider-fetch-1") == (
        "2026-08-30T00:00:01+00:00",
        "2026-08-30T00:00:03+00:00",
    )
    assert diagnostic.provider_diagnostic_window_evidence(events, "provider-fetch-1") == {
        "diagnostic_window_start": "2026-08-30T00:00:01+00:00",
        "diagnostic_window_end": "2026-08-30T00:00:03+00:00",
        "provider_fetch_attempt_count_in_window": 1,
        "provider_matching_request_count_in_window": 0,
        "provider_matching_response_count_in_window": 0,
        "provider_backend_response_count_in_window": 0,
        "provider_response_count_in_window": 0,
        "provider_concurrent_same_route_requests": None,
    }


def test_provider_terminal_event_bounds_window_without_begin_delivery() -> None:
    events = [
        {
            "fetch_attempt_id": "provider-fetch-3",
            "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
            "timestamp": "2026-08-30T00:00:03+00:00",
            "fetch_attempt_started_at": "2026-08-30T00:00:01.000Z",
            "fetch_attempt_finished_at": "2026-08-30T00:00:03.000Z",
            "fetch_attempt_duration_ms": 2000,
        },
        {"stage": "WEBVIEW_NETWORK_REQUESTED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:02+00:00"},
        {"stage": "WEBVIEW_RESPONSE_OBSERVATION", "route": "PROVIDER", "route_match": True, "classification_result": "MATCHED", "timestamp": "2026-08-30T00:00:02+00:00"},
        {"stage": "WEBVIEW_PROTECTED_RESPONSE", "route": "PROVIDER_PROJECTION", "http_status": 200, "timestamp": "2026-08-30T00:00:02+00:00"},
    ]

    assert diagnostic.provider_diagnostic_window(events, "provider-fetch-3") == (
        "2026-08-30T00:00:01.000Z",
        "2026-08-30T00:00:03.000Z",
    )
    evidence = diagnostic.provider_diagnostic_window_evidence(events, "provider-fetch-3")
    assert evidence["provider_fetch_attempt_count_in_window"] == 1
    assert evidence["provider_matching_request_count_in_window"] == 1
    assert evidence["provider_matching_response_count_in_window"] == 1
    assert evidence["provider_backend_response_count_in_window"] == 1


def test_provider_window_evidence_counts_only_events_inside_inclusive_window() -> None:
    events = [
        {"stage": "WEBVIEW_NETWORK_REQUESTED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:00+00:00"},
        {"fetch_attempt_id": "provider-fetch-2", "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN", "timestamp": "2026-08-30T00:00:01+00:00"},
        {"stage": "WEBVIEW_NETWORK_REQUESTED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:02+00:00"},
        {"stage": "WEBVIEW_NETWORK_REQUESTED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:03+00:00"},
        {"stage": "WEBVIEW_NETWORK_RESPONSE_RECEIVED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:04+00:00"},
        {"fetch_attempt_id": "provider-fetch-2", "stage": "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS", "timestamp": "2026-08-30T00:00:04+00:00"},
        {"stage": "WEBVIEW_NETWORK_RESPONSE_RECEIVED", "route": "PROVIDER", "timestamp": "2026-08-30T00:00:05+00:00"},
    ]

    assert diagnostic.provider_diagnostic_window_evidence(events, "provider-fetch-2") == {
        "diagnostic_window_start": "2026-08-30T00:00:01+00:00",
        "diagnostic_window_end": "2026-08-30T00:00:04+00:00",
        "provider_fetch_attempt_count_in_window": 1,
        "provider_matching_request_count_in_window": 2,
        "provider_matching_response_count_in_window": 1,
        "provider_backend_response_count_in_window": 0,
        "provider_response_count_in_window": 1,
        "provider_concurrent_same_route_requests": 1,
    }


def test_project_locations_response_control_requires_frontend_webview_and_backend_success() -> None:
    assert diagnostic.project_locations_response_control_valid(
        webview_request_observed=True,
        webview_response_observed=True,
        webview_response_status=200,
        backend_request_observed=True,
        backend_http_status=200,
        frontend_fetch_resolved=True,
        result="SUCCESS",
    ) is True
    assert diagnostic.project_locations_response_control_valid(
        webview_request_observed=True,
        webview_response_observed=True,
        webview_response_status=200,
        backend_request_observed=True,
        backend_http_status=200,
        frontend_fetch_resolved=False,
        result="SUCCESS",
    ) is False


def test_provider_gate_uses_backend_dispatch_when_request_event_was_lost() -> None:
    result = diagnostic.evaluate_provider_response_resolution_gate(
        control_group_valid=True,
        provider_webview_request_observed=False,
        provider_backend_request_observed=True,
        provider_backend_http_status=200,
        provider_backend_identity_result="PASS",
        provider_webview_response_observed=True,
        provider_webview_response_status=200,
        provider_fetch_attempt_count_in_window=1,
        provider_response_count_in_window=1,
        provider_concurrent_same_route_requests=0,
        fetch_failure_kind="PROMISE_REJECTION",
        fetch_failure_stage="FETCH_CALL",
        fetch_rejection_error_name="TypeError",
        fetch_rejection_message_category="FAILED_TO_FETCH",
    )

    assert result == {
        "provider_network_dispatch_confirmed": True,
        "provider_response_correlation_mode": "ROUTE_LEVEL_ONLY",
        "mechanism_classification": "BACKEND_RESPONSE_TO_JAVASCRIPT_FETCH_RESOLUTION_BOUNDARY",
    }


def test_provider_gate_refuses_route_level_correlation_when_window_is_ambiguous() -> None:
    result = diagnostic.evaluate_provider_response_resolution_gate(
        control_group_valid=True,
        provider_webview_request_observed=False,
        provider_backend_request_observed=True,
        provider_backend_http_status=200,
        provider_backend_identity_result="PASS",
        provider_webview_response_observed=True,
        provider_webview_response_status=200,
        provider_fetch_attempt_count_in_window=2,
        provider_response_count_in_window=1,
        provider_concurrent_same_route_requests=1,
        fetch_failure_kind="PROMISE_REJECTION",
        fetch_failure_stage="FETCH_CALL",
        fetch_rejection_error_name="TypeError",
        fetch_rejection_message_category="FAILED_TO_FETCH",
    )

    assert result["mechanism_classification"] == "RESPONSE_CORRELATION_AMBIGUOUS"


def test_provider_gate_does_not_treat_classifier_category_as_policy_root_cause() -> None:
    result = diagnostic.evaluate_provider_response_resolution_gate(
        control_group_valid=True,
        provider_webview_request_observed=False,
        provider_backend_request_observed=True,
        provider_backend_http_status=200,
        provider_backend_identity_result="PASS",
        provider_webview_response_observed=True,
        provider_webview_response_status=200,
        provider_fetch_attempt_count_in_window=1,
        provider_response_count_in_window=1,
        provider_concurrent_same_route_requests=0,
        fetch_failure_kind="HTTP_STATUS",
        fetch_failure_stage="HTTP_STATUS",
        fetch_rejection_error_name="Unknown",
        fetch_rejection_message_category="EMPTY",
    )

    assert result["mechanism_classification"] == "MECHANISM_UNCONFIRMED"


def test_provider_gate_requires_backend_identity_and_known_rejection_evidence() -> None:
    base = {
        "control_group_valid": True,
        "provider_webview_request_observed": False,
        "provider_backend_request_observed": True,
        "provider_backend_http_status": 200,
        "provider_webview_response_observed": True,
        "provider_webview_response_status": 200,
        "provider_fetch_attempt_count_in_window": 1,
        "provider_response_count_in_window": 1,
        "provider_concurrent_same_route_requests": 0,
        "fetch_failure_kind": "PROMISE_REJECTION",
        "fetch_failure_stage": "FETCH_CALL",
        "fetch_rejection_error_name": "TypeError",
        "fetch_rejection_message_category": "FAILED_TO_FETCH",
    }
    assert diagnostic.evaluate_provider_response_resolution_gate(
        **base,
        provider_backend_identity_result="MISSING",
    )["mechanism_classification"] == "MECHANISM_UNCONFIRMED"
    ambiguous_classifier = {**base, "fetch_rejection_error_name": "Other"}
    assert diagnostic.evaluate_provider_response_resolution_gate(
        **ambiguous_classifier,
        provider_backend_identity_result="PASS",
    )["mechanism_classification"] == "MECHANISM_UNCONFIRMED"
