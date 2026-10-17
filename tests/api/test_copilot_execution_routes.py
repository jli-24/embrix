from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_execution_routes import router
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    BuildEvidence,
    BuildOperation,
    OperationEvent,
    OperationEventType,
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
    ToolchainIdentity,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)


HEADERS = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}
FP = "sha256:" + "4" * 64


class _Port:
    def __init__(self) -> None:
        self.operations = {}
        self.events = ()
        self.event_reads = []
        self.approval_calls = []
        self.enqueue_calls = []
        self.receipt = None

    def create_operation(self, request):
        operation = request.operation
        self.operations[operation.operation_id] = operation

    def get_status(self, *, tenant_id, project_id, operation_id):
        operation = self.operations[operation_id]
        return OperationStatusView(operation_id=operation_id, tenant_id=tenant_id, project_id=project_id, operation_type=operation.operation_type, status=OperationStatus.WAIT_APPROVAL, approval_id=None, worker_id=None, recovery_state=RecoveryState.NONE, cancellation_requested=False, updated_at=datetime.now(UTC), safe_detail="awaiting approval")

    def read_operation_events_after(
        self, *, tenant_id, project_id, last_event_id, limit
    ):
        self.event_reads.append((tenant_id, project_id, last_event_id, limit))
        if last_event_id == "operation-event-foreign":
            return None
        return self.events

    def issue_approval_for_id(self, **kwargs):
        from types import SimpleNamespace

        self.approval_calls.append(kwargs)
        return SimpleNamespace(
            grant_id="approval-1", tenant_id=kwargs["tenant_id"], project_id=kwargs["project_id"],
            approval_purpose=SimpleNamespace(value="BUILD"), operation_id=kwargs["operation_id"],
            operation_type=SimpleNamespace(value="BUILD"), operation_fingerprint=FP,
            artifact_id=None, artifact_sha256=None, reviewer_id=kwargs["reviewer_id"],
            status=SimpleNamespace(value="ISSUED"), expires_at=kwargs["expires_at"], fingerprint=FP,
        )

    def enqueue_by_id(self, **kwargs):
        self.enqueue_calls.append(kwargs)

    def get_receipt(self, *, tenant_id, project_id, operation_id):
        return self.receipt


def _app(port=None):
    app = FastAPI()
    app.state.execution_operation_port = port
    class _Catalog:
        def get_project_for_requester(self, project_id, *, tenant_id, requester):
            return object()
    app.state.project_catalog_runtime = _Catalog()

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
    assert response.json()["correlation"] is None
    assert rejected.status_code == 422


def test_execution_route_is_unavailable_without_composed_port() -> None:
    with TestClient(_app()) as client:
        response = client.post("/api/v1/copilot/operations/build", headers=HEADERS, json=_build_body())
    assert response.status_code == 503
    assert response.json()["error"] == "CONTROLLED_BUILD_UNAVAILABLE"


def test_real_app_same_tenant_non_owner_cannot_read_execution_status_receipt_and_artifact(
    tmp_path, monkeypatch,
) -> None:
    """The real Desktop route must bind every project read to the Catalog owner."""
    from embedded_copilot.api.main import create_app
    from embedded_copilot.core.config import Settings
    from embedded_copilot.project_catalog import ProjectCatalogRuntime

    state_db = tmp_path / "execution-owner.sqlite3"
    project_root = tmp_path / "projects"
    project_root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=tmp_path / "catalog.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
    )
    app = create_app(
        settings=Settings(
            environment="test",
            desktop_state_database=state_db,
            project_root=project_root,
            _env_file=None,
        ),
        project_catalog_runtime=catalog,
    )
    owner_headers = {**HEADERS, "X-Copilot-Requester": "engineer-1"}
    other_headers = {**HEADERS, "X-Copilot-Requester": "engineer-2"}

    with TestClient(app) as client:
        execution = app.state.execution_operation_port
        location = client.get(
            "/api/v1/copilot/projects/locations", headers=owner_headers
        ).json()[0]
        preview = client.post(
            "/api/v1/copilot/projects/previews",
            headers=owner_headers,
            json={
                "name": "Owner Bound Execution Project",
                "parent_location": {
                    "location_id": location["location_id"],
                    "fingerprint": location["fingerprint"],
                },
                "project_type": "STM32",
                "creation_mode": "CREATE_NEW",
                "existing_directory": None,
            },
        )
        assert preview.status_code == 200, preview.text
        project = client.post(
            "/api/v1/copilot/projects",
            headers=owner_headers,
            json={
                "preview_id": preview.json()["preview_id"],
                "preview_fingerprint": preview.json()["fingerprint"],
                "confirmed_at": datetime.now(UTC).isoformat(),
            },
        )
        assert project.status_code in {200, 202}, project.text
        project_id = project.json()["project_id"]
        created = client.post(
            "/api/v1/copilot/operations/test",
            headers=owner_headers,
            json={**_test_body(), "project_id": project_id},
        )
        assert created.status_code == 200, created.text
        operation_id = created.json()["operation_id"]
        content = b"verified artifact"
        artifact = ArtifactIdentity(**_artifact_identity(
            build_operation_id=operation_id, project_id=project_id,
            artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        ))
        execution._artifact_store.put(artifact, content)
        execution._store.save_artifact(artifact)
        execution._store.save_receipt(
            OperationReceipt(
                operation_id=operation_id,
                tenant_id="tenant-1",
                project_id=project_id,
                operation_type=OperationType.TEST,
                status=OperationStatus.FAILED,
                tool_execution_status="FAILED",
                flash_verification_status="NOT_APPLICABLE",
                target_runtime_verification_status="NOT_VERIFIED",
                artifact=artifact,
                created_at=datetime.now(UTC),
            )
        )

        paths = (
            f"/api/v1/copilot/operations/{operation_id}/status?project_id={project_id}",
            f"/api/v1/copilot/operations/{operation_id}/receipt?project_id={project_id}",
            f"/api/v1/copilot/operations/artifacts/artifact-test-1?project_id={project_id}",
        )
        owner_results = [client.get(path, headers=owner_headers) for path in paths]
        non_owner_results = [client.get(path, headers=other_headers) for path in paths]
        non_owner_events = client.get(
            f"/api/v1/copilot/operations/events/stream?project_id={project_id}",
            headers=other_headers,
        )
        non_owner_approval = client.post(
            f"/api/v1/copilot/operations/{operation_id}/approval?project_id={project_id}",
            headers=other_headers,
            json={"reviewer_id": "engineer-2", "nonce": "review-2", "expires_at": "2026-10-01T00:00:00+00:00"},
        )
        non_owner_cancel = client.post(
            f"/api/v1/copilot/operations/{operation_id}/cancel?project_id={project_id}",
            headers=other_headers,
            json={"reason": "cancel request"},
        )
        non_owner_create = client.post(
            "/api/v1/copilot/operations/test", headers=other_headers,
            json={**_test_body(), "project_id": project_id},
        )
        artifact_path = app.state.execution_operation_port._artifact_store._root / artifact.artifact_id
        artifact_path.write_bytes(b"corrupt")
        corrupt_results = [client.get(path, headers=owner_headers) for path in paths[1:]]
        artifact_path.unlink()
        missing_results = [client.get(path, headers=owner_headers) for path in paths[1:]]
        execution.artifact_store.put(artifact, content)

    assert [response.status_code for response in owner_results] == [200, 200, 200]
    assert [response.status_code for response in non_owner_results] == [403, 403, 403]
    assert owner_results[0].json()["operation_id"] == operation_id
    assert all(response.json()["error"] == "PROJECT_CATALOG_FORBIDDEN" for response in non_owner_results)
    assert [response.status_code for response in (non_owner_events, non_owner_approval, non_owner_cancel, non_owner_create)] == [403, 403, 403, 403]
    assert [response.status_code for response in corrupt_results] == [422, 422]
    assert [response.status_code for response in missing_results] == [503, 503]
    assert all("artifact_sha256" not in response.text for response in (*corrupt_results, *missing_results))

    from embedded_copilot.api import main as api_main

    def unavailable_tools(*args, **kwargs):
        raise RuntimeError("current toolchain unavailable")

    monkeypatch.setattr(api_main, "ExecutionToolRegistry", unavailable_tools)
    reopened_catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=tmp_path / "catalog.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
    )
    reopened_app = create_app(
        settings=Settings(
            environment="test", desktop_state_database=state_db,
            project_root=project_root, _env_file=None,
        ),
        project_catalog_runtime=reopened_catalog,
    )
    with TestClient(reopened_app) as client:
        assert reopened_app.state.controlled_build_status == "UNAVAILABLE"
        reopened_results = [client.get(path, headers=owner_headers) for path in paths[1:]]
    assert [response.status_code for response in reopened_results] == [200, 200]


def test_execution_route_fails_closed_without_project_catalog() -> None:
    app = _app(_Port())
    app.state.project_catalog_runtime = None
    with TestClient(app) as client:
        response = client.post("/api/v1/copilot/operations/test", headers=HEADERS, json=_test_body())
    assert response.status_code == 503
    assert response.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"


def test_operation_approval_binds_reviewer_to_trusted_desktop_identity() -> None:
    port = _Port()
    body = {"reviewer_id": "engineer-1", "nonce": "review-1", "expires_at": "2026-10-01T00:00:00+00:00"}
    with TestClient(_app(port)) as client:
        spoofed = client.post("/api/v1/copilot/operations/build-1/approval?project_id=project-1", headers=HEADERS, json={**body, "reviewer_id": "other-user"})
        anonymous = client.post("/api/v1/copilot/operations/build-1/approval?project_id=project-1", json=body)
        accepted = client.post("/api/v1/copilot/operations/build-1/approval?project_id=project-1", headers=HEADERS, json=body)
    assert spoofed.status_code == 422
    assert anonymous.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["reviewer_id"] == "engineer-1"
    assert len(port.approval_calls) == len(port.enqueue_calls) == 1
    assert port.approval_calls[0]["reviewer_id"] == "engineer-1"


def test_receipt_route_projects_scoped_build_evidence_without_raw_diagnostics() -> None:
    port = _Port()
    artifact = ArtifactIdentity(**_artifact_identity(build_operation_id="build-1"))
    evidence = BuildEvidence(
        operation_id="build-1", request_fingerprint=FP, result_status="BUILD_PASS",
        toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3", source_fingerprints=(FP,),
        output_artifact_ids=(artifact.artifact_id,), diagnostics=("private/path/raw stdout",),
        tool_observed=True, created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    port.receipt = OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED", artifact=artifact,
        build_evidence=evidence, safe_diagnostics_summary="private/path/raw stdout",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with TestClient(_app(port)) as client:
        accepted = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
        cross_project = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-other", headers=HEADERS)
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["operation_id"] == "build-1"
    assert payload["artifact"]["build_operation_id"] == "build-1"
    assert payload["build_evidence"]["result_status"] == "BUILD_PASS"
    assert payload["build_evidence"]["source_fingerprints"] == [FP]
    assert payload["safe_diagnostics_summary"] == "BUILD_PASS"
    assert payload["target_runtime_verification_status"] == "NOT_VERIFIED"
    assert "private/path/raw stdout" not in accepted.text
    assert "diagnostics" not in payload["build_evidence"]
    assert cross_project.status_code == 422


def test_failed_build_receipt_exposes_evidence_reference_without_raw_summary() -> None:
    port = _Port()
    port.receipt = OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.FAILED,
        tool_execution_status="FAILED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED",
        evidence_references=("build-evidence-build-1", "C:/private/source"),
        safe_diagnostics_summary="C:/private/source raw stdout",
        build_evidence=BuildEvidence(
            operation_id="build-1", request_fingerprint=FP, result_status="BUILD_FAIL",
            toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3",
            output_artifact_ids=("artifact-build-1-log",), log_artifact_id="artifact-build-1-log",
            diagnostics=("C:/private/source raw stdout",), tool_observed=True,
            compiler_exit_code=1, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with TestClient(_app(port)) as client:
        response = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["safe_diagnostics_summary"] == "BUILD_FAIL"
    assert payload["evidence_references"] == ["build-evidence-build-1"]
    assert payload["build_evidence"]["log_artifact_id"] == "artifact-build-1-log"
    assert payload["build_evidence"]["compiler_exit_code"] == 1
    assert "C:/private/source" not in response.text


def test_receipt_route_rejects_path_disguised_as_log_artifact_reference() -> None:
    port = _Port()
    port.receipt = OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.FAILED,
        tool_execution_status="FAILED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED",
        build_evidence=BuildEvidence(
            operation_id="build-1", request_fingerprint=FP, result_status="BUILD_FAIL",
            toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3",
            output_artifact_ids=("C:/private/log",), log_artifact_id="C:/private/log",
            tool_observed=True, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with TestClient(_app(port)) as client:
        response = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
    assert response.status_code == 422
    assert "C:/private/log" not in response.text


def test_receipt_route_rejects_artifact_from_another_operation() -> None:
    port = _Port()
    port.receipt = OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED",
        artifact=ArtifactIdentity(**_artifact_identity(build_operation_id="build-other")),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with TestClient(_app(port)) as client:
        response = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"] == "OPERATION_RECEIPT_REJECTED"


def test_receipt_route_reports_missing_and_unavailable() -> None:
    with TestClient(_app(_Port())) as client:
        missing = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
    with TestClient(_app()) as client:
        unavailable = client.get("/api/v1/copilot/operations/build-1/receipt?project_id=project-1", headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()["error"] == "OPERATION_RECEIPT_NOT_FOUND"
    assert unavailable.status_code == 503


def test_receipt_source_revision_comes_from_same_persisted_build_operation() -> None:
    port = _Port()
    now = datetime.now(UTC)
    config = FirmwareBuildConfiguration(
        target_kind="STM32F446RE", build_system="CMAKE",
        toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3",
        profile="DEBUG", build_directory=".", expected_artifact_types=("ELF",),
    )
    firmware = FirmwareBuildRequest.create(
        project_id="project-1", firmware_reference="firmware-1",
        build_profile="DEBUG", firmware_spec_id="spec-1",
        firmware_spec_fingerprint=FP, implementation_id="implementation-1",
        implementation_fingerprint=FP, target_kind="STM32F446RE",
        build_system="CMAKE", toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3", configuration=config,
        source_artifacts=(FirmwareSourceArtifact(
            relative_path="main.c", content_fingerprint=FP,
            source_revision=FP, size_bytes=10,
        ),), expected_source_revision=FP, actual_source_revision=FP,
    )
    port.operations["build-1"] = BuildOperation(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        requester_id="engineer-1", target_id="target-1", target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id="proposal-1", proposal_fingerprint=FP,
        created_at=now, expires_at=now + timedelta(minutes=10), nonce="nonce-1",
        toolchain=ToolchainIdentity(
            toolchain_id="CMAKE_NINJA_BUILD", tool_version="3",
            tool_family="CMAKE", supported_operation=OperationType.BUILD,
            supported_target="STM32F446RE",
        ), configuration="DEBUG", expected_artifact_type="ELF",
        firmware_build_request_id=firmware.firmware_reference,
        firmware_build_request_fingerprint=firmware.fingerprint,
        firmware_spec_id=firmware.firmware_spec_id,
        firmware_spec_fingerprint=firmware.firmware_spec_fingerprint,
        implementation_id=firmware.implementation_id,
        implementation_fingerprint=firmware.implementation_fingerprint,
        source_artifacts=firmware.source_artifacts, build_system=firmware.build_system,
        firmware_build_request=firmware,
    )
    port.get_operation = lambda **kwargs: port.operations.get(kwargs["operation_id"])
    port.receipt = OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.FAILED,
        tool_execution_status="FAILED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED", created_at=now,
    )

    with TestClient(_app(port)) as client:
        response = client.get(
            "/api/v1/copilot/operations/build-1/receipt?project_id=project-1",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["source_revision"] == FP

    legacy_payload = port.operations["build-1"].model_dump(mode="python")
    for field in (
        "firmware_build_request_id", "firmware_build_request_fingerprint",
        "firmware_spec_id", "firmware_spec_fingerprint",
        "implementation_id", "implementation_fingerprint",
    ):
        legacy_payload[field] = None
    legacy_payload["firmware_build_request"] = None
    legacy_payload["fingerprint"] = "sha256:" + "0" * 64
    port.operations["build-1"] = BuildOperation.model_validate(legacy_payload)
    with TestClient(_app(port)) as client:
        unbound = client.get(
            "/api/v1/copilot/operations/build-1/receipt?project_id=project-1",
            headers=HEADERS,
        )
    assert unbound.status_code == 200
    assert unbound.json()["source_revision"] is None


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


def _operation_event(*, event_id: str, sequence: int, event_type, status, tenant_id: str = "tenant-1"):
    return OperationEvent(
        event_id=event_id,
        sequence=sequence,
        tenant_id=tenant_id,
        project_id="project-1",
        operation_id="operation-test-1",
        operation_fingerprint=FP,
        operation_type=OperationType.TEST,
        event_type=event_type,
        status=status,
        recovery_state=RecoveryState.NONE,
        approval_id="approval-test-1",
        receipt_fingerprint=None,
        reason_code=event_type.value,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence),
    )


def test_execution_event_stream_replays_typed_sse_and_passes_cursor_scope() -> None:
    port = _Port()
    port.events = (
        _operation_event(
            event_id="operation-event-2",
            sequence=2,
            event_type=OperationEventType.OPERATION_QUEUED,
            status=OperationStatus.QUEUED,
        ),
        _operation_event(
            event_id="operation-event-3",
            sequence=3,
            event_type=OperationEventType.OPERATION_STARTED,
            status=OperationStatus.RUNNING,
        ),
    )

    with TestClient(_app(port)) as client:
        response = client.get(
            "/api/v1/copilot/operations/events/stream?project_id=project-1",
            headers={**HEADERS, "Last-Event-ID": "operation-event-1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "id: operation-event-2" in response.text
    assert "event: OPERATION_QUEUED" in response.text
    assert "event: OPERATION_STARTED" in response.text
    assert '"operation_type":"TEST"' in response.text
    assert '"reason_code":"OPERATION_STARTED"' in response.text
    assert port.event_reads == [
        ("tenant-1", "project-1", "operation-event-1", 100)
    ]
    for forbidden in ("command", "executable", "cwd", "environment", "shell", "path"):
        assert forbidden not in response.text.casefold()


def test_execution_event_stream_returns_resync_for_unknown_or_cross_scope_cursor() -> None:
    port = _Port()

    with TestClient(_app(port)) as client:
        response = client.get(
            "/api/v1/copilot/operations/events/stream?project_id=project-1",
            headers={**HEADERS, "Last-Event-ID": "operation-event-foreign"},
        )

    assert response.status_code == 200
    assert "event: RESYNC_REQUIRED" in response.text
    assert '"reason_code":"RESYNC_REQUIRED"' in response.text
    assert "operation-event-foreign" not in response.text


def test_execution_event_stream_is_unavailable_without_composed_port() -> None:
    with TestClient(_app()) as client:
        response = client.get(
            "/api/v1/copilot/operations/events/stream?project_id=project-1",
            headers=HEADERS,
        )

    assert response.status_code == 503
    assert response.json()["error"] == "EXECUTION_UNAVAILABLE"


def test_execution_event_stream_rejects_port_output_from_another_scope() -> None:
    port = _Port()
    port.events = (
        _operation_event(
            event_id="operation-event-foreign-scope",
            sequence=1,
            event_type=OperationEventType.OPERATION_STARTED,
            status=OperationStatus.RUNNING,
            tenant_id="tenant-other",
        ),
    )

    with TestClient(_app(port)) as client:
        response = client.get(
            "/api/v1/copilot/operations/events/stream?project_id=project-1",
            headers=HEADERS,
        )

    assert response.status_code == 422
    assert response.json()["error"] == "EXECUTION_EVENT_STREAM_REJECTED"
    assert "operation-event-foreign-scope" not in response.text
