from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from embedded_copilot.execution_worker.contracts import (
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
    TestOperation as ExecutionTestOperation,
)
from embedded_copilot.execution_worker.store import SQLiteExecutionStore


NOW = datetime(2026, 1, 1, tzinfo=UTC)
FP = "sha256:" + "4" * 64


def _operation(*, operation_id: str = "operation-test-store-1") -> ExecutionTestOperation:
    return ExecutionTestOperation(
        operation_id=operation_id,
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
        nonce=f"nonce-{operation_id}",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
    )


def _all_mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(_all_mapping_keys(item) for item in value.values()),
            set(),
        )
    if isinstance(value, list):
        return set().union(*(_all_mapping_keys(item) for item in value), set())
    return set()


def test_test_operation_save_get_round_trip_preserves_concrete_type(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    operation = _operation()

    store.save_operation(operation)
    restored = SQLiteExecutionStore(database).get_operation(
        operation.tenant_id,
        operation.project_id,
        operation.operation_id,
    )

    assert type(restored) is ExecutionTestOperation
    assert restored == operation
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT body FROM v66_objects WHERE kind='operation' AND object_id=?",
            (operation.operation_id,),
        ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert not _all_mapping_keys(payload).intersection(
        {"command", "executable", "cwd", "environment", "script", "shell", "path"}
    )


def test_list_operations_keeps_test_operation(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    first = _operation(operation_id="operation-test-store-1")
    second = _operation(operation_id="operation-test-store-2")
    store.save_operation(first)
    store.save_operation(second)

    restored = store.list_operations("tenant-1", "project-1")

    assert restored == (first, second)
    assert all(type(item) is ExecutionTestOperation for item in restored)


def test_running_test_recovers_as_interrupted_without_retry(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    operation = _operation()
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
            updated_at=NOW,
            safe_detail="test was running",
        )
    )

    restarted = SQLiteExecutionStore(database)
    recovered = restarted.recover_running(now=NOW + timedelta(seconds=1))

    assert len(recovered) == 1
    assert recovered[0].operation_type is OperationType.TEST
    assert recovered[0].status is OperationStatus.RECOVERY_REQUIRED
    assert recovered[0].recovery_state is RecoveryState.INTERRUPTED
    assert type(
        restarted.get_operation("tenant-1", "project-1", operation.operation_id)
    ) is ExecutionTestOperation
