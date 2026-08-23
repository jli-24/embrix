from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    ArtifactIdentity,
    BuildOperation,
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


def test_worker_claims_once_consumes_approval_and_writes_receipt(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target = ExecutionTarget(target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F446RE", board_family="NUCLEO-F446RE", mcu_family="STM32F446RE", stable_identity_hash="probe-1", candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=datetime.now(UTC))
    operation = _operation().model_validate({**_operation().model_dump(mode="python"), "target_fingerprint": target.fingerprint, "fingerprint": "sha256:" + "0" * 64})
    request = OperationRequest(operation=operation)
    service = ExecutionOperationService(store=store)
    service.create_operation(request)
    approval = service.issue_approval(operation=request, reviewer_id="reviewer-1", nonce="approval-1", expires_at=datetime.now(UTC) + timedelta(minutes=5))
    service.enqueue(operation=request, approval_id=approval.grant_id)
    registry = ExecutionToolRegistry(TrustedToolDiscovery())
    worker = ExecutionWorker(worker_id="worker-a", store=store, registry=registry, workspace_resolver=_Workspace(), targets=_Targets(target), build=_Build())
    result = worker.run_once()
    assert result is not None
    assert result.status.value == "SUCCEEDED"
    assert store.get_receipt("tenant-1", "project-1", operation.operation_id) is not None
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
