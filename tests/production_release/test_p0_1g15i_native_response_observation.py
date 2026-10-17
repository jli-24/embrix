from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.native_shell import host
from embedded_copilot.native_shell import native_response_observation as native


LISTENER_PORT = 6487
LOOPBACK_ORIGIN = f"http://127.0.0.1:{LISTENER_PORT}"


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[native.NativeWebViewResponseObservationSink, Path]:
    output = tmp_path / "native-response.jsonl"
    monkeypatch.setenv(native.NATIVE_RESPONSE_DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    return (
        native.NativeWebViewResponseObservationSink(
            listener_port=LISTENER_PORT,
            output_path=output,
            runtime_pid=4321,
        ),
        output,
    )


@pytest.mark.parametrize(
    ("path", "route"),
    (
        ("/api/v1/copilot/desktop/system/bootstrap/exchange", "SESSION_EXCHANGE"),
        ("/api/v1/copilot/projects/locations", "PROJECT_LOCATIONS"),
        ("/api/v1/copilot/desktop/system/provider", "PROVIDER"),
    ),
)
def test_native_sink_classifies_three_real_response_routes(
    path: str,
    route: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)

    sink.observe(SimpleNamespace(url=f"{LOOPBACK_ORIGIN}{path}?token=SECRET", status_code=200))

    assert _rows(output) == [
        {
            "timestamp": _rows(output)[0]["timestamp"],
            "runtime_pid": 4321,
            "native_response_sequence": 1,
            "route": route,
            "route_match": True,
            "http_status": 200,
            "host_class": "LOOPBACK",
            "listener_port_match": True,
            "classification_result": "MATCHED",
            "safe_exception_class": "NONE",
        }
    ]


def test_native_sink_classifies_unrelated_response_as_safe_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)

    sink.observe(SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/assets/app.js?token=SECRET", status_code=200))

    assert _rows(output)[0] | {"timestamp": "ignored"} == {
        "timestamp": "ignored",
        "runtime_pid": 4321,
        "native_response_sequence": 1,
        "route": "OTHER",
        "route_match": False,
        "http_status": 200,
        "host_class": "LOOPBACK",
        "listener_port_match": True,
        "classification_result": "IGNORED",
        "safe_exception_class": "NONE",
    }


def test_native_sink_records_malformed_event_without_raw_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)

    sink.observe(SimpleNamespace())

    row = _rows(output)[0]
    assert row["classification_result"] == "MALFORMED"
    assert row["safe_exception_class"] == "NONE"
    assert row["route"] == "OTHER"
    assert "http_status" not in row


def test_native_sink_extracts_status_and_classifies_host_and_listener_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)

    sink.observe(SimpleNamespace(
        url=f"http://localhost:{LISTENER_PORT}/api/v1/copilot/desktop/system/provider",
        status_code=204,
    ))
    sink.observe(SimpleNamespace(
        url=f"https://example.invalid:{LISTENER_PORT}/api/v1/copilot/desktop/system/provider",
        status_code=200,
    ))
    sink.observe(SimpleNamespace(
        url=f"http://127.0.0.1:{LISTENER_PORT + 1}/api/v1/copilot/desktop/system/provider",
        status_code=200,
    ))

    rows = _rows(output)
    assert (rows[0]["http_status"], rows[0]["host_class"], rows[0]["listener_port_match"]) == (204, "LOOPBACK", True)
    assert (rows[1]["route"], rows[1]["host_class"], rows[1]["listener_port_match"]) == ("OTHER", "NON_LOOPBACK", False)
    assert (rows[2]["route"], rows[2]["host_class"], rows[2]["listener_port_match"]) == ("OTHER", "LOOPBACK", False)


def test_native_sink_is_append_only_and_sequence_is_monotonic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)
    first = SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/bootstrap/exchange", status_code=200)
    second = SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/projects/locations", status_code=200)
    third = SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/provider", status_code=200)

    sink.observe(first)
    sink.observe(second)
    sink.observe(third)

    rows = _rows(output)
    assert [row["native_response_sequence"] for row in rows] == [1, 2, 3]
    assert [row["route"] for row in rows] == ["SESSION_EXCHANGE", "PROJECT_LOCATIONS", "PROVIDER"]
    assert sink.drop_count == 0


def test_native_sink_permission_error_is_noop_for_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, _ = _sink(tmp_path, monkeypatch)

    def denied(_path: Path, _payload: dict[str, object]) -> None:
        raise PermissionError("SECRET_NATIVE_CANARY")

    monkeypatch.setattr(native, "_append_json_line", denied)
    response = SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/provider", status_code=200)

    assert sink.observe(response) is None
    assert sink.drop_count == 1


def test_native_sink_never_serializes_raw_url_headers_body_or_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)
    canary = "SECRET_NATIVE_CANARY"
    response = SimpleNamespace(
        url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/provider?token={canary}",
        status_code=200,
        headers={
            "Authorization": f"Bearer {canary}",
            "Cookie": canary,
            "Set-Cookie": canary,
        },
        body=canary,
    )

    sink.observe(response)

    raw = output.read_text(encoding="utf-8")
    row = _rows(output)[0]
    assert canary not in raw
    assert set(row) == native.NATIVE_RESPONSE_ALLOWED_FIELDS
    assert not set(row) & {"url", "path", "query", "host", "origin", "headers", "body", "exception", "repr"}


def test_native_classifier_exception_is_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)

    def explode(_url: object, *, listener_port: int | None) -> native.NativeResponseClassification:
        del listener_port
        raise ValueError("SECRET_NATIVE_CANARY")

    monkeypatch.setattr(native, "classify_native_response_url", explode)
    sink.observe(SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/ignored", status_code=200))

    row = _rows(output)[0]
    assert row["classification_result"] == "ERROR"
    assert row["safe_exception_class"] == "VALUE_ERROR"
    assert "SECRET_NATIVE_CANARY" not in output.read_text(encoding="utf-8")


def test_native_sink_is_default_off_and_uses_per_process_temp_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(native.NATIVE_RESPONSE_DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(native.tempfile, "gettempdir", lambda: str(tmp_path))
    output = native.native_response_output_path(runtime_pid=7654)
    sink = native.NativeWebViewResponseObservationSink(listener_port=LISTENER_PORT, runtime_pid=7654)

    sink.observe(SimpleNamespace(url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/provider", status_code=200))

    assert output == tmp_path / "embrix-p0-1g" / "native-webview-response-7654.jsonl"
    assert not output.exists()


def test_native_observation_does_not_mutate_response_and_is_bound_to_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink, output = _sink(tmp_path, monkeypatch)
    observer = host._PassiveWebViewNetworkObserver(native_response_sink=sink)
    response = SimpleNamespace(
        url=f"{LOOPBACK_ORIGIN}/api/v1/copilot/desktop/system/provider",
        status_code=200,
        headers={"Cookie": "SECRET_NATIVE_CANARY"},
    )
    before = dict(vars(response))

    observer._handle_response(response)

    assert vars(response) == before
    assert _rows(output)[0]["route"] == "PROVIDER"


def test_native_observation_failure_never_escapes_pywebview_callback() -> None:
    class FailingSink:
        def observe(self, _response: object) -> None:
            raise OSError("SECRET_NATIVE_CANARY")

    observer = host._PassiveWebViewNetworkObserver(native_response_sink=FailingSink())

    assert observer._handle_response(SimpleNamespace()) is None
