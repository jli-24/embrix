from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    BuildOperation,
    CancellationRequest,
    OperationApprovalGrant,
    OperationRequest,
    OperationStatus,
    OperationStatusView,
    RecoveryState,
    TestOperation as ExecutionTestOperation,
    ToolchainIdentity,
    OperationType,
    TargetDiscoveryCandidate,
    ExecutionTarget,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService, ExecutionRejected
from embedded_copilot.execution_worker.store import SQLiteExecutionStore


FP = "sha256:" + "2" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _operation() -> BuildOperation:
    now = datetime.now(UTC)
    return BuildOperation(
        operation_id="operation-build-1", tenant_id="tenant-1", project_id="project-1", requester_id="requester-1",
        target_id="target-1", target_fingerprint=FP, workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id="proposal-1", proposal_fingerprint=FP, created_at=now, expires_at=now + timedelta(minutes=10),
        nonce="nonce-1", toolchain=ToolchainIdentity(toolchain_id="CMAKE_NINJA_BUILD", tool_version="3", tool_family="CMAKE_NINJA", supported_operation=OperationType.BUILD, supported_target="STM32F446RE"),
        configuration="DEBUG", expected_artifact_type="ELF",
    )


def _test_operation() -> ExecutionTestOperation:
    return ExecutionTestOperation(
        operation_id="operation-test-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-test-1",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
    )


def test_build_approval_is_purpose_bound_and_queue_is_persistent(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    request = OperationRequest(operation=_operation())
    service.create_operation(request)
    approval = service.issue_approval(operation=request, reviewer_id="reviewer-1", nonce="approval-nonce", expires_at=datetime.now(UTC) + timedelta(minutes=5))
    assert approval.approval_purpose.value == "BUILD"
    status = service.enqueue(operation=request, approval_id=approval.grant_id)
    assert status.status is OperationStatus.QUEUED
    restored = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    assert restored.get_status("tenant-1", "project-1", "operation-build-1").status is OperationStatus.QUEUED


def test_tombstone_denies_new_operation(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    store.tombstone_project("tenant-1", "project-1", datetime.now(UTC))
    with pytest.raises(ExecutionRejected, match="tombstoned"):
        ExecutionOperationService(store=store).create_operation(OperationRequest(operation=_operation()))


def test_discovery_is_not_a_trusted_target_and_binding_is_project_scoped(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    registry = __import__("embedded_copilot.execution_worker.targets", fromlist=["SQLiteTargetRegistry"]).SQLiteTargetRegistry(store)
    candidate = TargetDiscoveryCandidate(
        candidate_id="candidate-new-device", tenant_id="tenant-1", project_id="project-1",
        target_kind="STM32F446RE", model="STM32F446RE", observed_identity_hash="probe-new",
        discovered_at=datetime.now(UTC),
    )
    registry.discover(candidate)
    assert registry.get_bound(tenant_id="tenant-1", project_id="project-1", target_id="target-new") is None
    target = ExecutionTarget(
        target_id="target-new", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE", mcu_family="STM32F446RE", stable_identity_hash="probe-new",
        candidate_id=candidate.candidate_id, binding_status=TargetBindingStatus.BOUND,
        bound_by="operator-1", bound_at=datetime.now(UTC),
    )
    assert registry.bind(target).target_id == "target-new"
    assert registry.get_bound(tenant_id="tenant-2", project_id="project-1", target_id="target-new") is None


def test_test_operation_uses_existing_approval_queue_and_cancellation_lifecycle(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    request = OperationRequest(operation=operation)

    service.create_operation(request)
    waiting = service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="approval-test-1",
        expires_at=NOW + timedelta(minutes=5),
    )
    queued = service.enqueue(operation=request, approval_id=approval.grant_id)
    cancelled = service.request_cancellation(
        CancellationRequest(
            request_id="cancellation-test-1",
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            requester_id=operation.requester_id,
            reason="operator requested cancellation",
            requested_at=NOW + timedelta(seconds=1),
        )
    )

    assert waiting is not None
    assert waiting.status is OperationStatus.WAIT_APPROVAL
    assert approval.approval_purpose is ApprovalPurpose.TEST
    assert approval.operation_type is OperationType.TEST
    assert queued.status is OperationStatus.QUEUED
    assert cancelled.status is OperationStatus.CANCEL_REQUESTED
    assert cancelled.recovery_state is RecoveryState.NONE


@pytest.mark.parametrize(
    ("cross_type", "cross_purpose"),
    (
        (OperationType.BUILD, ApprovalPurpose.BUILD),
        (OperationType.FLASH, ApprovalPurpose.FLASH),
    ),
)
def test_test_operation_rejects_cross_purpose_approval(
    tmp_path,
    cross_type: OperationType,
    cross_purpose: ApprovalPurpose,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    grant = OperationApprovalGrant(
        grant_id=f"approval-{cross_type.value.casefold()}",
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        requester_id=operation.requester_id,
        approval_purpose=cross_purpose,
        operation_id=operation.operation_id,
        operation_type=cross_type,
        operation_fingerprint=operation.fingerprint,
        proposal_id=operation.proposal_id,
        proposal_fingerprint=operation.proposal_fingerprint,
        target_id=operation.target_id,
        target_fingerprint=operation.target_fingerprint,
        workspace_snapshot_id=operation.workspace_snapshot_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        reviewer_id="reviewer-1",
        status=ApprovalStatus.ISSUED,
        expires_at=NOW + timedelta(minutes=5),
        nonce=f"nonce-{cross_type.value.casefold()}",
        issued_at=NOW,
    )
    store.save_approval(grant)

    with pytest.raises(ExecutionRejected, match="approval binding mismatch"):
        service.enqueue(operation=request, approval_id=grant.grant_id)


@pytest.mark.parametrize(
    ("operation_type", "expected_status"),
    (
        (OperationType.BUILD, OperationStatus.CANCEL_REQUESTED),
        (OperationType.TEST, OperationStatus.CANCEL_REQUESTED),
        (OperationType.UART_OBSERVATION, OperationStatus.CANCEL_REQUESTED),
        (OperationType.HIL, OperationStatus.CANCEL_REQUESTED),
        (OperationType.FLASH, OperationStatus.CANCEL_PENDING),
    ),
)
def test_running_cancellation_preserves_existing_operation_semantics(
    tmp_path,
    operation_type: OperationType,
    expected_status: OperationStatus,
) -> None:
    store = SQLiteExecutionStore(tmp_path / f"{operation_type.value}.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation_id = f"operation-{operation_type.value.casefold()}"
    store.save_status(
        OperationStatusView(
            operation_id=operation_id,
            tenant_id="tenant-1",
            project_id="project-1",
            operation_type=operation_type,
            status=OperationStatus.RUNNING,
            approval_id="approval-1",
            worker_id="worker-1",
            recovery_state=RecoveryState.NONE,
            cancellation_requested=False,
            updated_at=NOW,
            safe_detail="operation running",
        )
    )

    updated = service.request_cancellation(
        CancellationRequest(
            request_id=f"cancel-{operation_type.value.casefold()}",
            tenant_id="tenant-1",
            project_id="project-1",
            operation_id=operation_id,
            requester_id="requester-1",
            reason="operator requested cancellation",
            requested_at=NOW + timedelta(seconds=1),
        )
    )

    assert updated.status is expected_status
