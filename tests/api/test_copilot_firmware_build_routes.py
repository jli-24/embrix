from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api import copilot_firmware_build_routes as routes
from embedded_copilot.api import main as api_main
from embedded_copilot.api.desktop_identity import LocalDevelopmentIdentityAdapter
from embedded_copilot.application.firmware_build_context_source import (
    BuildContextInspection,
    FirmwareBuildContextSource,
)
from embedded_copilot.core.config import Settings
from embedded_copilot.engineering_copilot import PlanReviewStatus
from embedded_copilot.execution_worker.contracts import OperationStatus, OperationType
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.targets import SQLiteTargetRegistry
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from tests.project_catalog.test_brownfield_build import attached, propose


def _client(source: FirmwareBuildContextSource | None) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
        requester="engineer", tenant_id="tenant"
    )
    app.state.firmware_build_context_source = source
    return TestClient(app)


def _source(inspection: BuildContextInspection):
    source = FirmwareBuildContextSource.__new__(FirmwareBuildContextSource)
    calls: list[str] = []

    def inspect(**kwargs):
        assert kwargs == {
            "tenant_id": "tenant", "requester": "engineer",
            "project_id": "project", "conversation_id": "conversation",
        }
        calls.append("inspect")
        return inspection

    def prepare(**kwargs):
        assert kwargs["expected_context_version"] == "v1"
        calls.append("prepare")
        return inspection

    source.inspect = inspect
    source.prepare = prepare
    return source, calls


def test_preview_blocked_context_is_read_only() -> None:
    source, calls = _source(BuildContextInspection("BLOCKED", "BOUND_TARGET_MISSING", context_version="v1"))
    response = _client(source).get(
        "/api/v1/copilot/firmware-build/context",
        params={"project_id": "project", "conversation_id": "conversation"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "BLOCKED", "reason_code": "BOUND_TARGET_MISSING",
        "context_version": "v1", "project": None, "target": None,
        "source_revision": None, "workspace_fingerprint": None,
        "configuration": None,
        "toolchain": None, "plan": None, "proposal": None,
    }
    assert calls == ["inspect"]


def test_preview_projects_workspace_content_identity_for_history_comparison() -> None:
    snapshot = SimpleNamespace(
        project=SimpleNamespace(name="Firmware"),
        workspace=SimpleNamespace(snapshot=SimpleNamespace(project_fingerprint="sha256:" + "a" * 64)),
        build_request=SimpleNamespace(expected_source_revision="sha256:" + "b" * 64),
        current_configuration=SimpleNamespace(profile="Release", toolchain_id="ARM_NONE_EABI_GCC", toolchain_version="15.3.1"),
        conversation=SimpleNamespace(engineering_plan=None),
        target=SimpleNamespace(board_family="STM32F1"),
        proposal=SimpleNamespace(proposal_id="proposal-1"),
    )
    projected = routes._projection(BuildContextInspection("READY", "CURRENT_BINDING", snapshot, "version-1"))
    assert projected["workspace_fingerprint"] == snapshot.workspace.snapshot.project_fingerprint
    assert projected["source_revision"] == snapshot.build_request.expected_source_revision


def test_proposal_review_requires_explicit_review_and_context_version() -> None:
    source, calls = _source(BuildContextInspection("BLOCKED", "APPROVED_WORKSPACE_RECEIPT_MISSING", context_version="v1"))
    client = _client(source)
    rejected = client.post(
        "/api/v1/copilot/firmware-build/proposals",
        json={"project_id": "project", "conversation_id": "conversation",
              "context_version": "v1", "reviewed": False},
    )
    assert rejected.status_code == 422
    assert calls == []
    accepted = client.post(
        "/api/v1/copilot/firmware-build/proposals",
        json={"project_id": "project", "conversation_id": "conversation",
              "context_version": "v1", "reviewed": True},
    )
    assert accepted.json()["reason_code"] == "APPROVED_WORKSPACE_RECEIPT_MISSING"
    assert calls == ["prepare"]


def test_confirmation_blocks_changed_context_without_task_or_approval() -> None:
    source, calls = _source(BuildContextInspection("READY", "CURRENT_BINDING", context_version="v2"))
    client = _client(source)
    client.app.state.execution_operation_port = object()
    client.app.state.project_catalog_runtime = object()
    client.app.state.desktop_copilot_chat_runtime = SimpleNamespace(application_runtime=object())
    response = client.post(
        "/api/v1/copilot/firmware-build/confirm",
        json={"project_id": "project", "conversation_id": "conversation",
              "context_version": "v1", "confirmed": True},
    )
    assert response.json() == {
        "status": "BLOCKED", "reason_code": "CONTEXT_STALE",
        "message": "构建上下文已变化，需要重新预览。",
        "task_id": None, "operation_id": None,
    }
    assert calls == ["inspect"]


def test_confirmation_requires_explicit_true() -> None:
    source, calls = _source(BuildContextInspection("READY", "CURRENT_BINDING", context_version="v1"))
    response = _client(source).post(
        "/api/v1/copilot/firmware-build/confirm",
        json={"project_id": "project", "conversation_id": "conversation",
              "context_version": "v1", "confirmed": False},
    )
    assert response.status_code == 422
    assert calls == []


def test_brownfield_routes_do_not_require_conversation_and_keep_confirmation_explicit() -> None:
    source = FirmwareBuildContextSource.__new__(FirmwareBuildContextSource)
    calls: list[tuple[str, object]] = []

    def inspect(**kwargs):
        assert kwargs["conversation_id"] is None
        calls.append(("inspect", kwargs["project_id"]))
        return BuildContextInspection("BLOCKED", "BOUND_TARGET_MISSING", context_version="v1")

    def prepare(**kwargs):
        assert kwargs["conversation_id"] is None
        assert kwargs["expected_context_version"] == "v1"
        calls.append(("prepare", kwargs["project_id"]))
        return BuildContextInspection("BLOCKED", "TOOLCHAIN_UNAVAILABLE", context_version="v1")

    source.inspect = inspect
    source.prepare = prepare
    client = _client(source)
    assert client.get(
        "/api/v1/copilot/firmware-build/brownfield/context",
        params={"project_id": "project"},
    ).json()["reason_code"] == "BOUND_TARGET_MISSING"
    assert client.post(
        "/api/v1/copilot/firmware-build/brownfield/proposals",
        json={"project_id": "project", "context_version": "v1", "reviewed": False},
    ).status_code == 422
    assert client.post(
        "/api/v1/copilot/firmware-build/brownfield/proposals",
        json={"project_id": "project", "context_version": "v1", "reviewed": True},
    ).json()["reason_code"] == "TOOLCHAIN_UNAVAILABLE"
    assert client.post(
        "/api/v1/copilot/firmware-build/brownfield/confirm",
        json={"project_id": "project", "context_version": "v1", "confirmed": False},
    ).status_code == 422
    assert calls == [("inspect", "project"), ("prepare", "project")]


def test_brownfield_routes_reach_wait_approval_without_conversation(tmp_path: Path) -> None:
    catalog, workspace, project, _, configuration = attached(tmp_path)
    observed = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    proposed = propose(catalog, project, configuration, observed.project_fingerprint)
    catalog.decide_brownfield_build(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
        proposal_fingerprint=proposed.plan.content_fingerprint,
        decision=PlanReviewStatus.APPROVED,
    )
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    execution = ExecutionOperationService(store=store)
    tool = SimpleNamespace(resolve=lambda tool_id, **kwargs: SimpleNamespace(
        status="AVAILABLE", version_verified=True,
        executable=tmp_path / "observed-tool",
        definition=SimpleNamespace(tool_id=tool_id, tool_version=configuration.toolchain_version),
    ))
    source = FirmwareBuildContextSource(
        catalog=catalog, workspace_apply=workspace, execution=execution,
        targets=SQLiteTargetRegistry(store), tools=tool,
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
        requester="engineer-1", tenant_id="tenant-1",
    )
    app.state.firmware_build_context_source = source
    app.state.execution_operation_port = execution
    app.state.project_catalog_runtime = catalog
    client = TestClient(app)
    path = "/api/v1/copilot/firmware-build/brownfield"
    preview = client.get(f"{path}/context", params={"project_id": project.project_id}).json()
    assert preview["reason_code"] == "BOUND_TARGET_MISSING"
    prepared = client.post(f"{path}/proposals", json={
        "project_id": project.project_id,
        "context_version": preview["context_version"], "reviewed": True,
    }).json()
    assert prepared["status"] == "READY"
    confirmed = client.post(f"{path}/confirm", json={
        "project_id": project.project_id,
        "context_version": prepared["context_version"], "confirmed": True,
    }).json()
    assert confirmed["status"] == "CONFIRMED"
    status = execution.get_status(
        tenant_id="tenant-1", project_id=project.project_id,
        operation_id=confirmed["operation_id"],
    )
    assert status.status is OperationStatus.WAIT_APPROVAL
    assert status.approval_id is None


def test_confirmation_creates_wait_approval_task_without_approving(monkeypatch) -> None:
    turn = SimpleNamespace(
        question="帮我检查当前固件是否能构建", request_id="request",
        turn_id="turn", trace_id="trace", created_at=datetime.now(UTC),
    )
    snapshot = SimpleNamespace(
        conversation=SimpleNamespace(turns=(turn,)), build_request=object(), bindings=object()
    )
    source, calls = _source(BuildContextInspection("READY", "CURRENT_BINDING", snapshot, "v1"))
    client = _client(source)
    execution = SimpleNamespace(
        issue_approval_for_id=lambda **_: (_ for _ in ()).throw(AssertionError("approval called")),
        enqueue_by_id=lambda **_: (_ for _ in ()).throw(AssertionError("enqueue called")),
        get_status=lambda **_: SimpleNamespace(
            tenant_id="tenant", project_id="project", operation_id="operation",
            status=OperationStatus.WAIT_APPROVAL,
            operation_type=OperationType.BUILD, receipt_fingerprint=None,
            correlation=SimpleNamespace(task_id="task"),
        ),
    )
    client.app.state.execution_operation_port = execution
    client.app.state.project_catalog_runtime = object()
    client.app.state.desktop_copilot_chat_runtime = SimpleNamespace(application_runtime=object())
    monkeypatch.setattr(routes, "EngineeringContextResolver", lambda _: SimpleNamespace(
        resolve=lambda *_: SimpleNamespace(
            status=routes.ResolutionStatus.RESOLVED,
            context=SimpleNamespace(stage_reference="build-stage"),
        )
    ))

    class Entry:
        def __init__(self, **_):
            pass

        def prepare(self, *_args, **kwargs):
            assert kwargs["stage_id"] == "build-stage"
            assert kwargs["action"] == "FIRMWARE_BUILD_CHECK"
            return SimpleNamespace(status="READY_CONFIRMATION", prepared=object())

        def submit(self, _prepared, *, user_confirmed):
            assert user_confirmed is True
            return SimpleNamespace(
                status="BLOCKED", message="等待 Operation Approval。",
                task_id="task", operation_id="operation",
            )

    monkeypatch.setattr(routes, "EngineeringTaskEntry", Entry)
    monkeypatch.setattr(routes, "FirmwareBuildTaskService", lambda _: object())
    response = client.post(
        "/api/v1/copilot/firmware-build/confirm",
        json={"project_id": "project", "conversation_id": "conversation",
              "context_version": "v1", "confirmed": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "CONFIRMED", "reason_code": "WAIT_APPROVAL",
        "message": "等待 Operation Approval。", "task_id": "task",
        "operation_id": "operation",
    }
    assert calls == ["inspect", "inspect"]


def test_production_composition_shares_execution_owners_and_preview_writes_nothing(
    tmp_path: Path, monkeypatch,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    projects = tmp_path / "projects"
    projects.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production", production_state_root=tmp_path / "state",
            project_root=projects, desktop_static_assets=assets, _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )
    real_worker = api_main.ExecutionWorker
    worker_ports: dict[str, object] = {}

    def capture_worker(**ports):
        worker_ports.update(ports)
        return real_worker(**ports)

    monkeypatch.setattr(api_main, "ExecutionWorker", capture_worker)
    app = api_main.create_app(
        settings=runtime.settings, production_release_runtime=runtime,
    )
    assert "/api/v1/copilot/firmware-build/context" in app.openapi()["paths"]
    assert "/api/v1/copilot/operations/firmware-build" not in app.openapi()["paths"]
    with TestClient(app) as client:
        source = app.state.firmware_build_context_source
        assert isinstance(source, FirmwareBuildContextSource)
        assert source._execution is app.state.execution_operation_port
        assert source._execution.store is worker_ports["store"]
        assert source._targets is worker_ports["targets"]
        assert source._tools is worker_ports["registry"]
        assert worker_ports["build_context_revalidator"] == source.revalidate_operation
        assert worker_ports["build_sandbox_root"] != projects
        assert app.state.project_catalog_runtime._brownfield_workspace is source._workspace
        nonce = runtime.sessions.issue_bootstrap(
            requester="engineer", tenant_id="tenant",
        )
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        with sqlite3.connect(source._execution.store._database) as database:
            before = database.execute("SELECT COUNT(*) FROM v66_objects").fetchone()[0]
        response = client.get(
            "/api/v1/copilot/firmware-build/context",
            params={"project_id": "missing", "conversation_id": "missing"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "BLOCKED"
        with sqlite3.connect(source._execution.store._database) as database:
            after = database.execute("SELECT COUNT(*) FROM v66_objects").fetchone()[0]
        assert after == before
