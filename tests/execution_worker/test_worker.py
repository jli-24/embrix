from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    ArtifactIdentity,
    BuildOperation,
    ExecutionProposal,
    ExecutionTarget,
    FlashOperation,
    HilOperation,
    HilScenario,
    HilStep,
    HilStepKind,
    OperationApprovalGrant,
    OperationEvent,
    OperationEventType,
    OperationRequest,
    OperationType,
    OperationStatus,
    OperationStatusView,
    RecoveryState,
    TargetBindingStatus,
    TestOperation as ExecutionTestOperation,
    ToolchainIdentity,
    UartObservationOperation,
)
from embedded_copilot.execution_worker.ports import AdapterResult
from embedded_copilot.execution_worker.registry import ExecutionToolRegistry, ToolResolution, TrustedToolDiscovery
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration, FirmwareBuildRequest, FirmwareSourceArtifact,
)
from tests.execution_worker.test_service import _build_operation_for_proposal, _build_proposal


FP = "sha256:" + "3" * 64


def _operation() -> BuildOperation:
    now = datetime.now(UTC)
    return BuildOperation(
        operation_id="operation-worker-1", tenant_id="tenant-1", project_id="project-1", requester_id="requester-1",
        target_id="target-1", target_fingerprint=FP, workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id="proposal-1", proposal_fingerprint=FP, created_at=now, expires_at=now + timedelta(minutes=10), nonce="nonce-1",
        toolchain=ToolchainIdentity(toolchain_id="CMAKE_NINJA_BUILD", tool_version="3", tool_family="CMAKE_NINJA", supported_operation=OperationType.BUILD, supported_target="STM32F446RE"),
        configuration="DEBUG", expected_artifact_type="ELF",
    )


def _test_operation() -> ExecutionTestOperation:
    now = datetime.now(UTC)
    return ExecutionTestOperation(
        operation_id="operation-test-worker-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-test-1",
        proposal_fingerprint=FP,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        nonce="nonce-test-worker-1",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
        correlation=CorrelationContext(
            request_id="request-worker-1",
            trace_id="trace-worker-1",
            project_id="project-1",
            operation_id="operation-test-worker-1",
        ),
    )


def _flash_operation() -> FlashOperation:
    now = datetime.now(UTC)
    artifact = ArtifactIdentity(
        artifact_id="artifact-flash-worker-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        workspace_fingerprint=FP,
        build_operation_id="operation-build-worker-1",
        toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3",
        build_configuration="DEBUG",
        artifact_type="ELF",
        artifact_sha256="sha256:" + "5" * 64,
        size_bytes=128,
        created_at=now,
    )
    return FlashOperation(
        operation_id="operation-flash-worker-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-flash-1",
        proposal_fingerprint=FP,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        nonce="nonce-flash-worker-1",
        artifact=artifact,
        tool_id="OPENOCD_FLASH",
        tool_version="0.12",
    )


def _uart_operation() -> UartObservationOperation:
    now = datetime.now(UTC)
    return UartObservationOperation(
        operation_id="operation-uart-worker-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-uart-1",
        proposal_fingerprint=FP,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        nonce="nonce-uart-worker-1",
        port_binding_id="uart-binding-1",
        baud_rate=115200,
        duration_seconds=10,
        max_bytes=4096,
    )


def _hil_operation() -> HilOperation:
    now = datetime.now(UTC)
    return HilOperation(
        operation_id="operation-hil-worker-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-hil-1",
        proposal_fingerprint=FP,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        nonce="nonce-hil-worker-1",
        scenario=HilScenario(
            scenario_id="scenario-worker-1",
            project_id="project-1",
            target_id="target-1",
            artifact_id="artifact-hil-worker-1",
            steps=(HilStep(kind=HilStepKind.ASSERT_NO_PANIC),),
            timeout_seconds=30,
        ),
        flash_operation_id="operation-flash-worker-1",
    )


def _target() -> ExecutionTarget:
    return ExecutionTarget(
        target_id="target-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE",
        mcu_family="STM32F446RE",
        stable_identity_hash="probe-1",
        candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="operator-1",
        bound_at=datetime.now(UTC),
    )


def _bind_target(operation, target: ExecutionTarget):
    return operation.model_validate(
        {
            **operation.model_dump(mode="python"),
            "target_fingerprint": target.fingerprint,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )


def _enqueue(store: SQLiteExecutionStore, operation):
    request = OperationRequest(operation=operation)
    service = ExecutionOperationService(store=store)
    service.create_operation(request)
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce=f"approval-{operation.operation_id}",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)
    return approval


class _Targets:
    def __init__(self, target):
        self.target = target

    def get_bound(self, **kwargs):
        return self.target

    def revalidate(self, target):
        return target


class _Workspace:
    def resolve(self, **kwargs):
        return Path(kwargs["snapshot_id"]).resolve()


class _TypedOperationStore(SQLiteExecutionStore):
    """Keeps strict typed operations available for Worker dispatch tests."""

    def __init__(self, database: Path) -> None:
        self._typed_operations = {}
        super().__init__(database)

    def save_operation(self, value):
        key = (value.tenant_id, value.project_id, value.operation_id)
        self._typed_operations[key] = value
        return super().save_operation(value)

    def get_operation(self, tenant_id: str, project_id: str, operation_id: str):
        key = (tenant_id, project_id, operation_id)
        return self._typed_operations.get(key) or super().get_operation(
            tenant_id,
            project_id,
            operation_id,
        )


class _Build:
    def execute(self, operation, context):
        return AdapterResult("SUCCEEDED", "synthetic adapter result")


def test_build_staging_excludes_old_output_and_preserves_original(tmp_path) -> None:
    operation = _build_operation_for_proposal(_build_proposal())
    request = operation.firmware_build_request
    assert request is not None and request.configuration is not None
    config_payload = request.configuration.model_dump(mode="python")
    config_payload["build_directory"] = "build"
    configuration = FirmwareBuildConfiguration.model_validate(config_payload)
    request_payload = request.model_dump(mode="python")
    request_payload.update(configuration=configuration)
    request_payload.pop("fingerprint")
    updated_request = FirmwareBuildRequest.create(**request_payload)
    operation_payload = operation.model_dump(mode="python")
    operation_payload.update(
        firmware_build_request=updated_request,
        firmware_build_request_fingerprint=updated_request.fingerprint,
        build_directory="build",
        fingerprint="sha256:" + "0" * 64,
    )
    operation = BuildOperation.model_validate(operation_payload)
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "main.c").write_text("source")
    (project_root / "build").mkdir()
    (project_root / "build" / "firmware.elf").write_bytes(b"old output")
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    worker = ExecutionWorker(worker_id="worker-a", store=store, registry=None, workspace_resolver=None, targets=None)

    source_root, state_root = worker._stage_build_workspace(operation, project_root)

    assert (source_root / "main.c").read_text() == "source"
    assert not (source_root / "build").exists()
    assert (project_root / "build" / "firmware.elf").read_bytes() == b"old output"
    assert state_root.is_relative_to(tmp_path / "build-sandboxes")
    assert not state_root.is_relative_to(project_root)


class _Test:
    def __init__(self, store: SQLiteExecutionStore) -> None:
        self._store = store
        self.calls: list[ExecutionTestOperation] = []

    def execute(self, operation, context):
        running = self._store.get_status(
            operation.tenant_id,
            operation.project_id,
            operation.operation_id,
        )
        assert running is not None
        assert running.status is OperationStatus.RUNNING
        self.calls.append(operation)
        return AdapterResult("SUCCEEDED", "synthetic test validation")


class _SuccessAdapter:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, operation, context):
        self.calls.append(operation)
        return AdapterResult("SUCCEEDED", "synthetic adapter result")


class _InvalidAdapter:
    def execute(self, operation, context):
        return None


@pytest.mark.parametrize("proposal_state", ["present", "missing", "unavailable", "stale", "missing_context"])
def test_worker_rejects_build_success_without_typed_verification_evidence(tmp_path, monkeypatch, proposal_state: str) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = ExecutionTarget(target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F446RE", board_family="NUCLEO-F446RE", mcu_family="STM32F446RE", stable_identity_hash="probe-1", candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=datetime.now(UTC))
    operation = _operation().model_validate({**_operation().model_dump(mode="python"), "target_fingerprint": target.fingerprint, "fingerprint": "sha256:" + "0" * 64})
    configuration = FirmwareBuildConfiguration(
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
        toolchain_version="3", configuration=configuration,
        source_artifacts=(FirmwareSourceArtifact(
            relative_path="main.c", content_fingerprint=FP,
            size_bytes=10, source_revision=FP,
        ),), expected_source_revision=FP, actual_source_revision=FP,
    )
    service = ExecutionOperationService(store=store)
    proposal = service.create_proposal(ExecutionProposal(
        proposal_id=operation.proposal_id,
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        requester_id=operation.requester_id,
        target_id=operation.target_id,
        target_fingerprint=operation.target_fingerprint,
        workspace_snapshot_id=operation.workspace_snapshot_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.BUILD,
        toolchain_id=operation.toolchain.toolchain_id,
        toolchain_version=operation.toolchain.tool_version,
        build_configuration=operation.configuration,
        created_at=operation.created_at,
        source_revision=FP,
        firmware_build_request_fingerprint=firmware.fingerprint,
    ))
    operation = BuildOperation.model_validate({
        **operation.model_dump(mode="python"),
        "proposal_fingerprint": proposal.fingerprint,
        "toolchain": ToolchainIdentity(
            toolchain_id="CMAKE_NINJA_BUILD", tool_version="3",
            tool_family="CMAKE", supported_operation=OperationType.BUILD,
            supported_target="STM32F446RE",
        ),
        "firmware_build_request_id": firmware.firmware_reference,
        "firmware_build_request_fingerprint": firmware.fingerprint,
        "firmware_spec_id": firmware.firmware_spec_id,
        "firmware_spec_fingerprint": firmware.firmware_spec_fingerprint,
        "implementation_id": firmware.implementation_id,
        "implementation_fingerprint": firmware.implementation_fingerprint,
        "source_artifacts": firmware.source_artifacts,
        "build_system": firmware.build_system,
        "firmware_build_request": firmware,
        "fingerprint": "sha256:" + "0" * 64,
    })
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    approval = service.issue_approval(operation=request, reviewer_id="reviewer-1", nonce="approval-1", expires_at=datetime.now(UTC) + timedelta(minutes=5))
    service.enqueue(operation=request, approval_id=approval.grant_id)
    if proposal_state == "missing":
        monkeypatch.setattr(store, "get_proposal", lambda *_args: None)
    elif proposal_state == "unavailable":
        def unavailable(*_args):
            raise RuntimeError("proposal store unavailable")
        monkeypatch.setattr(store, "get_proposal", unavailable)
    registry = ExecutionToolRegistry(TrustedToolDiscovery())
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "main.c").write_text("int main(void) { return 0; }")
    class _BuildWorkspace:
        def resolve(self, **_kwargs):
            return project_root
    worker = ExecutionWorker(worker_id="worker-a", store=store, registry=registry, workspace_resolver=_BuildWorkspace(), targets=_Targets(target), build=_Build(), build_context_revalidator=None if proposal_state == "missing_context" else lambda _operation: proposal_state != "stale")
    result = worker.run_once()
    assert result is not None
    assert result.status is OperationStatus.FAILED
    receipt = store.get_receipt("tenant-1", "project-1", operation.operation_id)
    assert receipt is None
    assert result.safe_detail == ("current build context unavailable" if proposal_state == "missing_context" else "build approval is stale" if proposal_state == "stale" else "proposal binding mismatch" if proposal_state != "present" else "build verification evidence unavailable")
    assert (project_root / "main.c").read_text() == "int main(void) { return 0; }"
    assert not (project_root / ".embedded-copilot-state").exists()
    operation_directory = hashlib.sha256(f"{operation.tenant_id}|{operation.project_id}|{operation.operation_id}".encode()).hexdigest()
    if proposal_state == "present":
        assert (tmp_path / "build-sandboxes" / operation_directory / "source" / "main.c").is_file()
    else:
        assert not (tmp_path / "build-sandboxes" / operation_directory).exists()
    if proposal_state == "present":
        assert store.consume_approval(tenant_id="tenant-1", project_id="project-1", grant_id=approval.grant_id, operation_fingerprint=operation.fingerprint, now=datetime.now(UTC)) is None


def test_flash_like_running_state_recovers_to_unknown_target_state(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    now = datetime.now(UTC)
    from embedded_copilot.execution_worker.contracts import OperationStatusView, RecoveryState
    store.save_status(OperationStatusView(operation_id="flash-1", tenant_id="tenant-1", project_id="project-1", operation_type=OperationType.FLASH, status=OperationStatus.RUNNING, approval_id="approval-1", worker_id="worker-a", recovery_state=RecoveryState.NONE, cancellation_requested=False, updated_at=now, safe_detail="running"))
    recovered = store.recover_running(now=now + timedelta(seconds=1))
    assert recovered[0].status.value == "RECOVERY_REQUIRED"
    assert recovered[0].recovery_state is RecoveryState.TARGET_STATE_UNKNOWN


def test_worker_dispatches_test_after_running_and_writes_success_receipt(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    target = _target()
    operation = _bind_target(_test_operation(), target)
    approval = _enqueue(store, operation)
    adapter = _Test(store)
    worker = ExecutionWorker(
        worker_id="worker-test",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
        test=adapter,
    )

    result = worker.run_once()
    receipt = store.get_receipt("tenant-1", "project-1", operation.operation_id)

    assert result is not None
    assert result.status is OperationStatus.SUCCEEDED
    assert len(adapter.calls) == 1
    assert type(adapter.calls[0]) is ExecutionTestOperation
    assert receipt is not None
    assert receipt.status is OperationStatus.SUCCEEDED
    assert receipt.tool_execution_status == "SUCCEEDED"
    assert receipt.target_runtime_verification_status == "NOT_VERIFIED"
    assert receipt.safe_diagnostics_summary == "synthetic test validation"
    assert receipt.correlation is not None
    assert receipt.correlation.request_id == "request-worker-1"
    assert receipt.correlation.trace_id == "trace-worker-1"
    assert receipt.correlation.approval_id == approval.grant_id
    assert receipt.correlation.workflow_run_id is None
    with sqlite3.connect(database) as connection:
        events = tuple(
            OperationEvent.model_validate_json(row[0])
            for row in connection.execute(
                "SELECT body FROM v66_operation_events ORDER BY sequence"
            ).fetchall()
        )
    assert tuple(event.event_type for event in events) == (
        OperationEventType.OPERATION_CREATED,
        OperationEventType.APPROVAL_ISSUED,
        OperationEventType.OPERATION_QUEUED,
        OperationEventType.OPERATION_CLAIMED,
        OperationEventType.OPERATION_STARTED,
        OperationEventType.OPERATION_SUCCEEDED,
    )
    assert events[-1].receipt_fingerprint == receipt.fingerprint
    assert store.consume_approval(
        tenant_id="tenant-1",
        project_id="project-1",
        grant_id=approval.grant_id,
        operation_fingerprint=operation.fingerprint,
        now=datetime.now(UTC),
    ) is None


def test_worker_default_test_adapter_writes_unavailable_receipt_and_fails_closed(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = _target()
    operation = _bind_target(_test_operation(), target)
    _enqueue(store, operation)
    worker = ExecutionWorker(
        worker_id="worker-test-default",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
    )

    result = worker.run_once()
    receipt = store.get_receipt("tenant-1", "project-1", operation.operation_id)

    assert result is not None
    assert result.status is OperationStatus.FAILED
    assert receipt is not None
    assert receipt.status is OperationStatus.FAILED
    assert receipt.tool_execution_status == "UNAVAILABLE"
    assert receipt.safe_diagnostics_summary == "TEST_ADAPTER_NOT_CONFIGURED"
    assert receipt.artifact is None
    assert receipt.evidence_references == ()
    assert "pass" not in receipt.safe_diagnostics_summary.casefold()


def test_worker_rejects_non_typed_adapter_result(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = _target()
    operation = _bind_target(_test_operation(), target)
    _enqueue(store, operation)
    worker = ExecutionWorker(
        worker_id="worker-invalid-adapter-result",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
        test=_InvalidAdapter(),
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is OperationStatus.FAILED
    assert result.safe_detail == "adapter result invalid"
    assert store.get_receipt(
        operation.tenant_id, operation.project_id, operation.operation_id
    ) is None


def test_terminal_transition_rolls_back_receipt_and_status_when_event_fails(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    target = _target()
    operation = _bind_target(_test_operation(), target)
    _enqueue(store, operation)
    original = store._append_event_in_transaction

    def fail_terminal(connection, **kwargs):
        if kwargs["event_type"] is OperationEventType.OPERATION_SUCCEEDED:
            raise sqlite3.IntegrityError("synthetic terminal event failure")
        return original(connection, **kwargs)

    monkeypatch.setattr(store, "_append_event_in_transaction", fail_terminal)
    worker = ExecutionWorker(
        worker_id="worker-test-rollback",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
        test=_Test(store),
    )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic terminal event failure"):
        worker.run_once()

    status = store.get_status("tenant-1", "project-1", operation.operation_id)
    assert status is not None
    assert status.status is OperationStatus.RUNNING
    assert store.get_receipt("tenant-1", "project-1", operation.operation_id) is None


def test_worker_rejects_cross_purpose_test_approval_before_running(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = _target()
    operation = _bind_target(_test_operation(), target)
    request = OperationRequest(operation=operation)
    service = ExecutionOperationService(store=store)
    service.create_operation(request)
    forged = OperationApprovalGrant(
        grant_id="approval-forged-build",
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        requester_id=operation.requester_id,
        approval_purpose=ApprovalPurpose.BUILD,
        operation_id=operation.operation_id,
        operation_type=OperationType.BUILD,
        operation_fingerprint=operation.fingerprint,
        proposal_id=operation.proposal_id,
        proposal_fingerprint=operation.proposal_fingerprint,
        target_id=operation.target_id,
        target_fingerprint=operation.target_fingerprint,
        workspace_snapshot_id=operation.workspace_snapshot_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        reviewer_id="reviewer-1",
        status=ApprovalStatus.ISSUED,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="approval-forged-build",
        issued_at=datetime.now(UTC),
    )
    store.save_approval(forged)
    waiting = store.get_status("tenant-1", "project-1", operation.operation_id)
    assert waiting is not None
    queued = waiting.model_validate(
        {
            **waiting.model_dump(mode="python"),
                "status": OperationStatus.QUEUED,
                "approval_id": forged.grant_id,
                "correlation": CorrelationContext.model_validate(
                    {
                        **waiting.correlation.model_dump(mode="python"),
                        "approval_id": forged.grant_id,
                    }
                ),
                "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store.save_status(queued)
    store.enqueue(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )
    adapter = _Test(store)
    worker = ExecutionWorker(
        worker_id="worker-test-forged",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
        test=adapter,
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is OperationStatus.FAILED
    assert result.safe_detail == "approval binding mismatch"
    assert adapter.calls == []
    assert store.get_receipt("tenant-1", "project-1", operation.operation_id) is None
    assert store.get_approval(
        "tenant-1", "project-1", forged.grant_id
    ).status is ApprovalStatus.ISSUED


def test_worker_restart_recovers_running_test_as_interrupted(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = _target()
    operation = _bind_target(_test_operation(), target)
    store.save_operation(operation)
    store.save_status(
        OperationStatusView(
            operation_id=operation.operation_id,
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_type=OperationType.TEST,
            status=OperationStatus.RUNNING,
            approval_id="approval-test-1",
            worker_id="worker-before-restart",
            recovery_state=RecoveryState.NONE,
            cancellation_requested=False,
            updated_at=datetime.now(UTC),
            safe_detail="test running",
        )
    )
    worker = ExecutionWorker(
        worker_id="worker-after-restart",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
    )

    recovered = worker.recover_after_restart()

    assert len(recovered) == 1
    assert recovered[0].status is OperationStatus.RECOVERY_REQUIRED
    assert recovered[0].recovery_state is RecoveryState.INTERRUPTED
    assert recovered[0].correlation is not None
    assert recovered[0].correlation.request_id == "request-worker-1"
    assert recovered[0].correlation.trace_id == "trace-worker-1"
    assert recovered[0].correlation.approval_id == "approval-test-1"


@pytest.mark.parametrize(
    ("operation_factory", "adapter_name"),
    (
        (_flash_operation, "flash"),
        (_uart_operation, "uart"),
        (_hil_operation, "hil"),
    ),
)
def test_existing_worker_dispatch_branches_keep_their_receipt_lifecycle(
    tmp_path,
    operation_factory,
    adapter_name: str,
) -> None:
    store_type = _TypedOperationStore if adapter_name == "hil" else SQLiteExecutionStore
    store = store_type(tmp_path / "execution.sqlite3")
    target = _target()
    operation = _bind_target(operation_factory(), target)
    _enqueue(store, operation)
    adapter = _SuccessAdapter()
    worker = ExecutionWorker(
        worker_id=f"worker-{adapter_name}",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=_Workspace(),
        targets=_Targets(target),
        **{adapter_name: adapter},
    )

    result = worker.run_once()
    receipt = store.get_receipt("tenant-1", "project-1", operation.operation_id)

    assert result is not None
    assert result.status is OperationStatus.SUCCEEDED
    assert len(adapter.calls) == 1
    assert adapter.calls[0].operation_type is operation.operation_type
    assert receipt is not None
    assert receipt.tool_execution_status == "SUCCEEDED"
