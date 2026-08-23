from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_execution_routes import router
from embedded_copilot.execution_worker.contracts import OperationStatus, OperationStatusView, RecoveryState


HEADERS = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}
FP = "sha256:" + "4" * 64


class _Port:
    def __init__(self) -> None:
        self.operations = {}

    def create_operation(self, request):
        operation = request.operation
        self.operations[operation.operation_id] = operation

    def get_status(self, *, tenant_id, project_id, operation_id):
        operation = self.operations[operation_id]
        return OperationStatusView(operation_id=operation_id, tenant_id=tenant_id, project_id=project_id, operation_type=operation.operation_type, status=OperationStatus.WAIT_APPROVAL, approval_id=None, worker_id=None, recovery_state=RecoveryState.NONE, cancellation_requested=False, updated_at=datetime.now(UTC), safe_detail="awaiting approval")


def _app(port=None):
    app = FastAPI()
    app.state.execution_operation_port = port

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-v66"
        return await call_next(request)

    app.include_router(router)
    return app


def _build_body():
    return {
        "project_id": "project-1", "target_id": "target-1", "target_kind": "STM32F446RE", "target_fingerprint": FP,
        "workspace_snapshot_id": "snapshot-1", "workspace_fingerprint": FP, "proposal_id": "proposal-1", "proposal_fingerprint": FP,
        "nonce": "nonce-1", "toolchain_id": "CMAKE_NINJA_BUILD", "toolchain_version": "3", "tool_family": "CMAKE_NINJA",
        "configuration": "DEBUG", "expected_artifact_type": "ELF",
    }


def _test_body():
    return {
        "project_id": "project-1",
        "target_id": "target-1",
        "target_fingerprint": FP,
        "workspace_id": "snapshot-1",
        "workspace_fingerprint": FP,
        "proposal_id": "proposal-test-1",
        "proposal_fingerprint": FP,
        "nonce": "nonce-test-1",
        "test_suite_id": "firmware-smoke",
        "test_profile_id": "default-validation",
    }


def _artifact_identity(**overrides):
    values = {
        "artifact_id": "artifact-test-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "target_id": "target-1",
        "workspace_fingerprint": FP,
        "build_operation_id": "operation-build-1",
        "toolchain_id": "CMAKE_NINJA_BUILD",
        "toolchain_version": "3",
        "build_configuration": "DEBUG",
        "artifact_type": "ELF",
        "artifact_sha256": "sha256:" + "5" * 64,
        "size_bytes": 128,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    values.update(overrides)
    return values


def test_build_route_creates_waiting_typed_operation_without_command_fields() -> None:
    port = _Port()
    with TestClient(_app(port)) as client:
        response = client.post("/api/v1/copilot/operations/build", headers=HEADERS, json=_build_body())
        rejected = client.post("/api/v1/copilot/operations/build", headers=HEADERS, json={**_build_body(), "command": "cmake --build ."})
    assert response.status_code == 200
    assert response.json()["status"] == "WAIT_APPROVAL"
    assert rejected.status_code == 422


def test_execution_route_is_unavailable_without_composed_port() -> None:
    with TestClient(_app()) as client:
        response = client.post("/api/v1/copilot/operations/build", headers=HEADERS, json=_build_body())
    assert response.status_code == 503
    assert response.json()["error"] == "CONTROLLED_BUILD_UNAVAILABLE"


def test_test_route_creates_waiting_typed_operation() -> None:
    port = _Port()

    with TestClient(_app(port)) as client:
        response = client.post(
            "/api/v1/copilot/operations/test",
            headers=HEADERS,
            json=_test_body(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation_type"] == "TEST"
    assert payload["status"] == "WAIT_APPROVAL"
    assert payload["project_id"] == "project-1"
    assert payload["target_id"] == "target-1"
    assert payload["workspace_id"] == "snapshot-1"
    operation = port.operations[payload["operation_id"]]
    assert operation.operation_type.value == "TEST"
    assert operation.workspace_snapshot_id == "snapshot-1"
    assert operation.artifact is None


@pytest.mark.parametrize(
    "missing",
    ("project_id", "target_id", "workspace_id", "test_suite_id", "test_profile_id"),
)
def test_test_route_rejects_missing_required_identity(missing: str) -> None:
    body = _test_body()
    body.pop(missing)

    with TestClient(_app(_Port())) as client:
        response = client.post(
            "/api/v1/copilot/operations/test",
            headers=HEADERS,
            json=body,
        )

    assert response.status_code == 422


def test_test_route_rejects_cross_bound_artifact_identity() -> None:
    body = {
        **_test_body(),
        "artifact_identity": _artifact_identity(target_id="target-other"),
    }

    with TestClient(_app(_Port())) as client:
        response = client.post(
            "/api/v1/copilot/operations/test",
            headers=HEADERS,
            json=body,
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "forbidden",
    ("command", "executable", "cwd", "environment", "script", "shell", "path"),
)
def test_test_route_rejects_execution_fields(forbidden: str) -> None:
    with TestClient(_app(_Port())) as client:
        response = client.post(
            "/api/v1/copilot/operations/test",
            headers=HEADERS,
            json={**_test_body(), forbidden: "forbidden"},
        )

    assert response.status_code == 422


def test_test_request_openapi_schema_contains_no_execution_fields() -> None:
    schema = _app(_Port()).openapi()
    request_schema = schema["components"]["schemas"]["TestOperationCreateRequest"]
    serialized = str(request_schema).casefold()

    for forbidden in ("command", "executable", "cwd", "environment", "script", "shell", "path"):
        assert forbidden not in serialized


def test_existing_execution_routes_remain_registered() -> None:
    paths = _app(_Port()).openapi()["paths"]

    for operation in ("build", "flash", "uart", "hil"):
        assert f"/api/v1/copilot/operations/{operation}" in paths
