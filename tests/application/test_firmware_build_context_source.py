"""Focused checks for the read-only preview and explicit producer boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

from embedded_copilot.application.firmware_build_context_source import (
    FirmwareBuildContextSource,
)
from embedded_copilot.application.task_entry import submit_brownfield_build
from embedded_copilot.application.task_firmware_build import FirmwareBuildTaskService
from embedded_copilot.engineering_copilot import PlanReviewStatus
from embedded_copilot.engineering_workspace_apply.models import (
    OperationStatus as ApplyStatus,
)
from embedded_copilot.engineering_workspace_apply.models import (
    RecoveryStatus,
    ValidationStatus,
    WorkspaceFile,
    WorkspaceSnapshot,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.targets import SQLiteTargetRegistry
from embedded_copilot.firmware_engineering import FirmwareBuildRequest
from embedded_copilot.project_catalog.exceptions import ProjectCatalogForbidden
from tests.application.test_task_entry import (
    NOW,
    Catalog,
    firmware_request,
    source_detail,
)
from tests.project_catalog.test_brownfield_build import attached, propose

FP = "sha256:" + "a" * 64


class _Catalog(Catalog):
    def projects(self, tenant_id):
        return (self.get_project("project-1", tenant_id=tenant_id),)

    def get_project_for_requester(self, project_id, *, tenant_id, requester):
        if project_id != "project-1":
            raise ProjectCatalogForbidden("project requester mismatch")
        project = self.get_project(project_id, tenant_id=tenant_id)
        if requester != "user-1":
            raise ProjectCatalogForbidden("project requester mismatch")
        return project


@dataclass
class _Workspace:
    value: WorkspaceSnapshot
    receipt: object | None = None

    def snapshot(self, *, tenant_id, project_id):
        assert (tenant_id, project_id) == ("tenant-1", "project-1")
        return self.value

    def list_receipts(self, project_id, *, limit):
        assert project_id == "project-1"
        return () if self.receipt is None else (self.receipt,)

    def get_receipt(self, project_id, operation_id):
        if self.receipt is not None and self.receipt.operation_id == operation_id:
            return self.receipt
        return None

    def get_changeset(self, tenant_id, project_id, changeset_id):
        assert (tenant_id, project_id, changeset_id) == (
            "tenant-1", "project-1", "changeset-1"
        )
        return SimpleNamespace(
            tenant_id=tenant_id, project_id=project_id,
            target=SimpleNamespace(target_id="project-root"), fingerprint=FP,
        )


def _source(monkeypatch, tmp_path):
    from embedded_copilot.application import firmware_build_context_source as module

    catalog = _Catalog()
    detail = source_detail()
    original = firmware_request()
    config = original.configuration
    spec = SimpleNamespace(
        target_platform="Nucleo-F446RE", mcu_family="STM32F4",
        mcu_exact_part=config.target_kind, source_layout=("main.c",),
        firmware_spec_id="firmware-1",
    )
    implementation = SimpleNamespace(implementation_id="implementation-1")
    catalog.detail = SimpleNamespace(
        **{
            **{name: getattr(detail, name) for name in (
                "tenant_id", "project_id", "requester", "conversation_id", "turns",
                "engineering_plan", "engineering_plan_review",
            )},
            "firmware_spec": spec,
            "implementation_spec": implementation,
            "build_configuration": config,
        }
    )

    def request(_spec, _implementation, *, configuration, source_artifacts,
                expected_source_revision, actual_source_revision):
        return FirmwareBuildRequest.create(
            project_id="project-1", conversation_id="conversation-1",
            firmware_reference="firmware-1", build_profile=configuration.profile,
            firmware_spec_id="firmware-1", firmware_spec_fingerprint=FP,
            implementation_id="implementation-1", implementation_fingerprint=FP,
            target_kind=configuration.target_kind, build_system=configuration.build_system,
            toolchain_id=configuration.toolchain_id,
            toolchain_version=configuration.toolchain_version,
            configuration=configuration, source_artifacts=source_artifacts,
            expected_source_revision=expected_source_revision,
            actual_source_revision=actual_source_revision,
        )

    monkeypatch.setattr(module, "create_firmware_build_request", request)
    workspace = _Workspace(WorkspaceSnapshot(
        tenant_id="tenant-1", project_id="project-1", target_id="project-root",
        project_fingerprint=FP, target_fingerprint="sha256:" + "b" * 64,
        files=(WorkspaceFile(
            relative_file_reference="main.c", exists=True,
            content_fingerprint="sha256:" + "c" * 64, size=10,
        ),), captured_at=NOW,
    ))
    tools = SimpleNamespace(resolve=lambda tool_id, **kwargs: SimpleNamespace(
        status="AVAILABLE", version_verified=True,
        executable=tmp_path / "observed-tool",
        definition=SimpleNamespace(tool_id=tool_id, tool_version=config.toolchain_version),
    ))
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    targets = SQLiteTargetRegistry(store)
    execution = ExecutionOperationService(store=store)
    source = FirmwareBuildContextSource(
        catalog=catalog, workspace_apply=workspace,
        execution=execution, targets=targets, tools=tools,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    args = {
        "tenant_id": "tenant-1", "requester": "user-1",
        "project_id": "project-1", "conversation_id": "conversation-1",
    }
    return source, workspace, store, catalog, args


def test_brownfield_snapshot_requires_no_apply_receipt_and_confirms_wait_approval(tmp_path):
    catalog, workspace, project, source_dir, configuration = attached(tmp_path)
    observed = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    proposed = propose(catalog, project, configuration, observed.project_fingerprint)
    approved = catalog.decide_brownfield_build(
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
    context_source = FirmwareBuildContextSource(
        catalog=catalog, workspace_apply=workspace, execution=execution,
        targets=SQLiteTargetRegistry(store), tools=tool,
    )
    args = dict(tenant_id="tenant-1", requester="engineer-1",
                project_id=project.project_id, conversation_id=None)
    preview = context_source.inspect(**args)
    assert preview.reason_code == "BOUND_TARGET_MISSING"
    prepared = context_source.prepare(**args, expected_context_version=preview.context_version)
    assert prepared.status == "READY"
    snapshot = prepared.snapshot
    assert snapshot is not None and snapshot.proposal is not None
    assert snapshot.proposal.provenance.value == "EXISTING_SNAPSHOT"
    assert snapshot.proposal.approved_workspace_operation_id is None
    assert workspace.list_receipts(project.project_id) == ()
    state = submit_brownfield_build(
        catalog=catalog, build_service=FirmwareBuildTaskService(execution),
        context=approved, build_request=snapshot.build_request,
        bindings=snapshot.bindings, user_confirmed=True,
    )
    status = execution.get_status(
        tenant_id="tenant-1", project_id=project.project_id,
        operation_id=state.operation_reference.operation_id,
    )
    assert status.status.value == "WAIT_APPROVAL"
    operation = execution.get_operation(
        tenant_id="tenant-1", project_id=project.project_id,
        operation_id=state.operation_reference.operation_id,
    )
    assert context_source.revalidate_operation(operation)
    (source_dir / "src/main.c").write_text("changed\n", encoding="utf-8")
    assert context_source.inspect(**args).reason_code == "CATALOG_CONTEXT_STALE"
    assert not context_source.revalidate_operation(operation)
    changed = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    revised_configuration = configuration.model_copy(update={"profile": "RELEASE"})
    revised = propose(catalog, project, revised_configuration, changed.project_fingerprint)
    catalog.decide_brownfield_build(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
        proposal_fingerprint=revised.plan.content_fingerprint,
        decision=PlanReviewStatus.APPROVED,
    )
    next_preview = context_source.inspect(**args)
    assert next_preview.reason_code == "BOUND_TARGET_CONFLICT"
    next_prepared = context_source.prepare(
        **args, expected_context_version=next_preview.context_version,
    )
    assert next_prepared.status == "READY"
    assert next_prepared.snapshot.proposal.fingerprint != operation.proposal_fingerprint
    assert not context_source.revalidate_operation(operation)


def test_preview_and_missing_receipt_never_write(monkeypatch, tmp_path):
    source, _, store, _, args = _source(monkeypatch, tmp_path)
    preview = source.inspect(**args)
    assert preview.status == "BLOCKED"
    assert preview.reason_code == "BOUND_TARGET_MISSING"
    assert preview.snapshot is not None and preview.context_version is not None
    assert source.snapshot(**args) is None
    blocked = source.prepare(**args, expected_context_version=preview.context_version)
    assert blocked.reason_code == "APPROVED_WORKSPACE_RECEIPT_MISSING"
    assert store.get_candidate("tenant-1", "project-1", "build-candidate:" + "0" * 48) is None
    assert store.get_target("tenant-1", "project-1", "project-root") is None
    assert store.list_operations("tenant-1", "project-1") == ()


def test_explicit_prepare_is_idempotent_and_source_drift_blocks(monkeypatch, tmp_path):
    source, workspace, store, catalog, args = _source(monkeypatch, tmp_path)
    workspace.receipt = SimpleNamespace(
        operation_id="workspace-operation-1", project_id="project-1",
        changeset_id="changeset-1", changeset_fingerprint=FP,
        status=ApplyStatus.APPLIED, recovery_status=RecoveryStatus.AVAILABLE,
        validation_status=ValidationStatus.PASSED,
        after_project_fingerprint=workspace.value.project_fingerprint,
        fingerprint="sha256:" + "d" * 64,
    )
    preview = source.inspect(**args)
    stale = source.prepare(**args, expected_context_version="sha256:" + "0" * 64)
    assert stale.reason_code == "CONTEXT_STALE"
    assert store.get_target("tenant-1", "project-1", "project-root") is None
    first = source.prepare(**args, expected_context_version=preview.context_version)
    assert first.status == "READY"
    assert first.snapshot is not None and first.snapshot.proposal is not None
    second = source.prepare(**args, expected_context_version=first.context_version)
    assert second.status == "READY"
    assert second.snapshot.proposal == first.snapshot.proposal
    assert store.list_operations("tenant-1", "project-1") == ()

    catalog.detail.firmware_spec.target_platform = "different-board"
    conflict = source.inspect(**args)
    assert conflict.reason_code == "BOUND_TARGET_CONFLICT"
    assert conflict.context_version != first.context_version
    assert source.prepare(**args, expected_context_version=conflict.context_version).reason_code == "BOUND_TARGET_CONFLICT"
    catalog.detail.firmware_spec.target_platform = "Nucleo-F446RE"

    workspace.value = workspace.value.model_copy(update={
        "project_fingerprint": "sha256:" + "e" * 64,
        "target_fingerprint": "sha256:" + "f" * 64,
        "files": (WorkspaceFile(
            relative_file_reference="main.c", exists=True,
            content_fingerprint="sha256:" + "9" * 64, size=10,
        ),),
        "fingerprint": "sha256:" + "0" * 64,
    })
    changed = source.inspect(**args)
    assert changed.context_version != first.context_version
    assert source.prepare(**args, expected_context_version=first.context_version).reason_code == "CONTEXT_STALE"
    assert source.prepare(**args, expected_context_version=changed.context_version).reason_code == "APPROVED_WORKSPACE_RECEIPT_MISSING"
    assert store.list_operations("tenant-1", "project-1") == ()


def test_real_apply_recovery_state_available_is_accepted_but_recovery_required_is_not(monkeypatch, tmp_path):
    source, workspace, _, _, args = _source(monkeypatch, tmp_path)
    workspace.receipt = SimpleNamespace(
        operation_id="workspace-operation-1", project_id="project-1",
        changeset_id="changeset-1", changeset_fingerprint=FP,
        status=ApplyStatus.APPLIED, recovery_status=RecoveryStatus.AVAILABLE,
        validation_status=ValidationStatus.PASSED,
        after_project_fingerprint=workspace.value.project_fingerprint,
        fingerprint="sha256:" + "d" * 64,
    )
    preview = source.inspect(**args)
    accepted = source.prepare(**args, expected_context_version=preview.context_version)
    assert accepted.status == "READY"
    workspace.receipt.recovery_status = RecoveryStatus.RECOVERY_REQUIRED
    assert source.inspect(**args).reason_code == "WORKSPACE_RECEIPT_STALE"


def test_missing_build_request_turn_blocks_before_workspace_access(monkeypatch, tmp_path):
    source, _workspace, store, catalog, args = _source(monkeypatch, tmp_path)
    catalog.detail.turns = ()
    inspected = source.inspect(**args)
    assert inspected.reason_code == "BUILD_REQUEST_TURN_MISSING"
    assert source.prepare(**args).reason_code == "BUILD_REQUEST_TURN_MISSING"
    assert store.get_target("tenant-1", "project-1", "project-root") is None


def test_same_tenant_non_owner_conversation_cannot_read_build_context(monkeypatch, tmp_path):
    source, workspace, _store, catalog, args = _source(monkeypatch, tmp_path)
    args["requester"] = "user-2"
    catalog.detail.requester = "user-2"
    catalog.conversation = lambda *a, **kw: catalog.detail
    workspace.snapshot = lambda **kw: (_ for _ in ()).throw(
        AssertionError("workspace must not be read before owner authorization")
    )

    inspected = source.inspect(**args)
    assert inspected.status == "BLOCKED"
    assert inspected.reason_code == "PROJECT_ACCESS_DENIED"
    assert inspected.snapshot is None
    assert source.prepare(**args).reason_code == "PROJECT_ACCESS_DENIED"


def test_missing_owner_lookup_fails_closed(monkeypatch, tmp_path):
    source, workspace, _store, catalog, args = _source(monkeypatch, tmp_path)
    catalog.get_project_for_requester = None
    workspace.snapshot = lambda **kw: (_ for _ in ()).throw(
        AssertionError("workspace must not be read without owner lookup")
    )

    inspected = source.inspect(**args)
    assert inspected.status == "BLOCKED"
    assert inspected.reason_code == "CONTEXT_SOURCE_UNAVAILABLE"


def test_project_discovery_only_returns_requester_owned_projects(monkeypatch, tmp_path):
    source, _workspace, _store, catalog, _args = _source(monkeypatch, tmp_path)
    owned = catalog.get_project("project-1", tenant_id="tenant-1")
    other = owned.model_copy(update={"project_id": "project-2"})
    catalog.projects = lambda tenant_id: (owned, other)

    assert source.projects(tenant_id="tenant-1", requester="user-1") == (owned,)
    assert source.projects(tenant_id="tenant-1", requester="user-2") == ()

    catalog.get_project_for_requester = None
    assert source.projects(tenant_id="tenant-1", requester="user-1") == ()
