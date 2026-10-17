from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_copilot_chat_models import CopilotChatInput
from embedded_copilot.api.desktop_copilot_chat_routes import router
from embedded_copilot.application import ApplicationErrorCode, ApplicationRunStatus
from embedded_copilot.desktop_copilot_chat import DesktopCopilotChatRuntime
from embedded_copilot.desktop_copilot_chat.adapters import (
    FakeChatApplicationAdapter,
    FakeChatAuditSink,
    FakeChatEnvironmentProjection,
    FakeChatEventStore,
    FakeChatProjectProjection,
    FakeChatSessionStore,
    FixedChatClock,
)
from embedded_copilot.multimodal.context import (
    ProcessLocalAttachmentBindingRepository,
)
from tests.desktop_copilot_chat.factories import (
    NOW,
    PROJECT,
    REQUESTER,
    TENANT,
    application_result,
    panel,
    project,
    question,
    reference,
)


class _TraceStore:
    def __init__(self) -> None:
        self.events = []

    def append(self, event):
        self.events.append(event)
        return event

    def read(self, trace_id):
        return tuple(event for event in self.events if event.trace_id == trace_id)


def _runtime(*, traces=None) -> DesktopCopilotChatRuntime:
    application_request = question()
    return DesktopCopilotChatRuntime(
        application=FakeChatApplicationAdapter(application_result(application_request)),
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=FakeChatAuditSink(),
        events=FakeChatEventStore(),
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
        traces=traces,
    )


def _app(runtime: object | None):
    app = FastAPI()
    app.state.desktop_copilot_chat_runtime = runtime

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "test-trace"
        return await call_next(request)

    app.include_router(router)
    return app


def _headers() -> dict[str, str]:
    return {
        "X-Copilot-Requester": REQUESTER,
        "X-Environment-Tenant": TENANT,
    }


def _open(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/copilot/chat/sessions",
        headers=_headers(),
        json={
            "session_id": "session-1",
            "project_id": PROJECT,
            "expires_at": "2026-08-10T13:00:00Z",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_chat_api_requires_headers_and_injected_runtime() -> None:
    with TestClient(_app(None)) as client:
        missing = client.post("/api/v1/copilot/chat/sessions", json={})
        unavailable = client.post(
            "/api/v1/copilot/chat/sessions",
            headers=_headers(),
            json={
                "session_id": "session-1",
                "project_id": PROJECT,
                "expires_at": "2026-08-10T13:00:00Z",
            },
        )
    assert missing.status_code == 401
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "COPILOT_CHAT_UNAVAILABLE"


def test_chat_api_accepts_json_arrays_and_replays_sanitized_sse() -> None:
    with TestClient(_app(_runtime())) as client:
        session = _open(client)
        project_response = client.get(
            "/api/v1/copilot/chat/sessions/session-1/project", headers=_headers()
        )
        references_response = client.get(
            "/api/v1/copilot/chat/sessions/session-1/references", headers=_headers()
        )
        submitted = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project_response.json(),
                "application_question": {
                    "request_id": "request-1",
                    "trace_id": "trace-1",
                    "project_id": PROJECT,
                    "requester": REQUESTER,
                    "question": "Why is measured power 120mA?",
                    "constraints": [],
                    "requested_at": "2026-08-10T12:00:00Z",
                },
                "context_references": references_response.json(),
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )
        stream = client.get(
            "/api/v1/copilot/chat/events?session_id=session-1&requester=engineer-1&tenant=tenant-1",
            headers=_headers(),
        )
        routes = {
            path: set(methods)
            for path, methods in client.app.openapi()["paths"].items()
            if path.startswith("/api/v1/copilot/chat")
        }
    assert session["project_id"] == PROJECT
    assert submitted.status_code == 200
    assert submitted.json()["evidence_details"][0]["source_type"] == "HIL"
    assert submitted.json()["evidence_details"][0]["status"] == "OBSERVED"
    assert submitted.json()["recommendations"] == []
    assert "event: CHAT_STARTED" in stream.text
    assert "event: ANSWER_CHUNK" in stream.text
    assert "event: ANSWER_COMPLETED" in stream.text
    assert "prompt" not in stream.text
    assert "provider" not in stream.text
    assert "answer_text" not in stream.text
    assert all(methods <= {"get", "post", "delete"} for methods in routes.values())


def test_chat_api_recomputes_omitted_question_fingerprint_after_json_normalization() -> None:
    """Public JSON may use arrays; the boundary must derive the identity once."""

    question_payload = question().model_dump(mode="json", exclude={"fingerprint"})

    with TestClient(_app(_runtime())) as client:
        _open(client)
        submitted = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "application_question": question_payload,
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert submitted.status_code == 200


def test_chat_api_projects_persisted_trace_without_private_diagnostics() -> None:
    trace_store = _TraceStore()
    with TestClient(_app(_runtime(traces=trace_store))) as client:
        _open(client)
        project_response = client.get(
            "/api/v1/copilot/chat/sessions/session-1/project", headers=_headers()
        )
        references_response = client.get(
            "/api/v1/copilot/chat/sessions/session-1/references", headers=_headers()
        )
        submitted = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project_response.json(),
                "application_question": {
                    "request_id": "request-1",
                    "trace_id": "trace-1",
                    "project_id": PROJECT,
                    "requester": REQUESTER,
                    "question": "Why is measured power 120mA?",
                    "constraints": [],
                    "requested_at": "2026-08-10T12:00:00Z",
                },
                "context_references": references_response.json(),
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )
        projected = client.get(
            "/api/v1/copilot/chat/sessions/session-1/trace?trace_id=trace-1",
            headers=_headers(),
        )

    assert submitted.status_code == 200
    assert projected.status_code == 200
    body = projected.json()
    assert body
    assert body[-1]["stage"] == "UI_PROJECTION"
    assert body[-1]["service_identity"] == "DesktopProjection"
    assert body[-1]["terminal_event"] == "ANSWER_COMPLETED"
    assert all("original_exception_type" not in item for item in body)
    assert "prompt" not in projected.text.lower()


def test_chat_api_rejects_stale_question_fingerprint_after_field_change() -> None:
    question_payload = question().model_dump(mode="json")
    question_payload["constraints"] = ["answer_detail:standard"]

    with TestClient(_app(_runtime())) as client:
        _open(client)
        rejected = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "application_question": question_payload,
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert rejected.status_code == 422


def test_chat_api_rejects_invalid_request_payload_with_422() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        invalid = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert invalid.status_code == 422
    assert any(
        detail["loc"][-1] == "application_question"
        for detail in invalid.json()["detail"]
    )


def test_chat_api_accepts_default_off_external_search_flag() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        submitted = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "application_question": question().model_dump(mode="json"),
                "external_search": False,
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert submitted.status_code == 200


def test_chat_api_rejects_non_boolean_external_search_flag() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        invalid = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "application_question": question().model_dump(mode="json"),
                "external_search": "true",
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert invalid.status_code == 422


def test_chat_api_binds_enabled_external_search_into_application_request() -> None:
    payload = CopilotChatInput.model_validate(
        {
            "project_reference": project().model_dump(mode="json"),
            "application_question": question().model_dump(mode="json"),
            "external_search": True,
            "context_references": [],
            "requested_at": "2026-08-10T12:00:00Z",
        }
    )

    request = payload.to_request(session_id="session-1")

    assert request.external_search is True
    assert request.application_question.external_search is True


def test_chat_api_returns_stable_external_evidence_unavailable_code() -> None:
    runtime = _runtime()
    runtime._application.result = application_result(
        question(), status=ApplicationRunStatus.FAILED
    )
    failed = runtime._application.result.model_dump(mode="python")
    failed["error_code"] = ApplicationErrorCode.EXTERNAL_EVIDENCE_UNAVAILABLE
    failed["fingerprint"] = "sha256:" + "0" * 64
    runtime._application.result = type(runtime._application.result).model_validate(failed)
    with TestClient(_app(runtime)) as client:
        _open(client)
        response = client.post(
            "/api/v1/copilot/chat/sessions/session-1/requests",
            headers=_headers(),
            json={
                "project_reference": project().model_dump(mode="json"),
                "application_question": question().model_dump(mode="json"),
                "external_search": True,
                "context_references": [],
                "requested_at": "2026-08-10T12:00:00Z",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"] == "COPILOT_CHAT_EXTERNAL_EVIDENCE_UNAVAILABLE"


def test_chat_api_rejects_cross_tenant_and_returns_resync_for_invalid_cursor() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        denied = client.get(
            "/api/v1/copilot/chat/sessions/session-1/project",
            headers={
                "X-Copilot-Requester": REQUESTER,
                "X-Environment-Tenant": "tenant-2",
            },
        )
        resync = client.get(
            "/api/v1/copilot/chat/events?session_id=session-1&requester=engineer-1&tenant=tenant-1",
            headers={**_headers(), "Last-Event-ID": "unknown-event"},
        )
    assert denied.status_code == 403
    assert denied.json()["error"] == "COPILOT_CHAT_REJECTED"
    assert resync.status_code == 200
    assert "event: RESYNC_REQUIRED" in resync.text


def _attachment_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "reference_id": "attachment-1",
        "type": "FILE",
        "basename": "sensor_config.h",
        "summary": "User-selected read-only attachment reference",
        "size_bytes": 1024,
        "created_at": "2026-08-10T12:00:00Z",
    }
    payload.update(updates)
    return payload


def test_chat_api_binds_safe_attachment_reference_without_content() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        created = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(),
        )
        references = client.get(
            "/api/v1/copilot/chat/sessions/session-1/references",
            headers=_headers(),
        )

    assert created.status_code == 201
    assert created.json()["reference_kind"] == "WORKSPACE"
    assert created.json()["display_label"] == "sensor_config.h"
    assert "content" not in created.text
    assert created.json() in references.json()


def test_chat_api_rejects_unsafe_or_duplicate_attachment_metadata() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        first = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(),
        )
        duplicate = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(),
        )
        unsafe_path = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(reference_id="attachment-2", basename="C:\\secret\\token.txt"),
        )
        text_type = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(reference_id="attachment-3", type="TEXT"),
        )
        oversized = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(reference_id="attachment-4", size_bytes=25 * 1024 * 1024 + 1),
        )
        arbitrary_summary = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers=_headers(),
            json=_attachment_payload(reference_id="attachment-5", summary="raw file content"),
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert unsafe_path.status_code == 422
    assert text_type.status_code == 422
    assert oversized.status_code == 422
    assert arbitrary_summary.status_code == 422


def test_chat_api_rejects_attachment_for_cross_tenant_session() -> None:
    with TestClient(_app(_runtime())) as client:
        _open(client)
        denied = client.post(
            "/api/v1/copilot/chat/sessions/session-1/attachments",
            headers={
                "X-Copilot-Requester": REQUESTER,
                "X-Environment-Tenant": "tenant-2",
            },
            json=_attachment_payload(),
        )

    assert denied.status_code == 403
    assert denied.json()["error"] == "COPILOT_CHAT_REJECTED"
