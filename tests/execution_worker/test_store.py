from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.execution_worker.contracts import (
    OperationEvent,
    OperationEventType,
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
    with sqlite3.connect(database) as connection:
        event = OperationEvent.model_validate_json(
            connection.execute(
                "SELECT body FROM v66_operation_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert event.event_type is OperationEventType.RECOVERY_REQUIRED
    assert event.recovery_state is RecoveryState.INTERRUPTED


def test_event_table_is_added_to_existing_v66_database(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE v66_objects (kind TEXT NOT NULL, tenant_id TEXT NOT NULL, "
            "project_id TEXT NOT NULL, object_id TEXT NOT NULL, body TEXT NOT NULL, "
            "PRIMARY KEY(kind, tenant_id, project_id, object_id))"
        )

    SQLiteExecutionStore(database)

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "v66_operation_events" in tables


def test_append_event_allocates_project_scoped_sequence_and_canonical_body(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    operation = _operation()
    other_project = ExecutionTestOperation.model_validate(
        {
            **operation.model_dump(mode="python"),
            "operation_id": "operation-other-project",
            "project_id": "project-2",
            "nonce": "nonce-other-project",
            "fingerprint": "sha256:" + "0" * 64,
        }
    )

    first = store._append_event(
        operation=operation,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW,
        event_id="operation-event-1",
    )
    second = store._append_event(
        operation=operation,
        event_type=OperationEventType.APPROVAL_ISSUED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="APPROVAL_ISSUED",
        occurred_at=NOW + timedelta(seconds=1),
        event_id="operation-event-2",
    )
    isolated = store._append_event(
        operation=other_project,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW,
        event_id="operation-event-1",
    )

    assert (first.sequence, second.sequence, isolated.sequence) == (1, 2, 1)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT body FROM v66_operation_events WHERE tenant_id=? AND project_id=? "
            "ORDER BY sequence",
            (operation.tenant_id, operation.project_id),
        ).fetchall()
    restored = tuple(OperationEvent.model_validate_json(row[0]) for row in rows)
    assert restored == (first, second)
    assert not _all_mapping_keys(json.loads(rows[0][0])).intersection(
        {"command", "executable", "cwd", "environment", "script", "shell", "path"}
    )


def test_event_log_rejects_duplicate_identity_and_tampered_body(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    operation = _operation()
    store._append_event(
        operation=operation,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW,
        event_id="operation-event-1",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store._append_event(
            operation=operation,
            event_type=OperationEventType.APPROVAL_ISSUED,
            status=OperationStatus.WAIT_APPROVAL,
            recovery_state=RecoveryState.NONE,
            reason_code="APPROVAL_ISSUED",
            occurred_at=NOW,
            event_id="operation-event-1",
        )

    with sqlite3.connect(database) as connection:
        raw = connection.execute(
            "SELECT body FROM v66_operation_events WHERE event_id='operation-event-1'"
        ).fetchone()[0]
    payload = json.loads(raw)
    payload["reason_code"] = "TAMPERED"
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        OperationEvent.model_validate_json(json.dumps(payload))


def test_event_queries_are_ordered_paginated_and_operation_scoped(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    first_operation = _operation(operation_id="operation-test-store-1")
    second_operation = _operation(operation_id="operation-test-store-2")
    first = store._append_event(
        operation=first_operation,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW,
        event_id="operation-event-query-1",
    )
    second = store._append_event(
        operation=second_operation,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW + timedelta(seconds=1),
        event_id="operation-event-query-2",
    )
    third = store._append_event(
        operation=first_operation,
        event_type=OperationEventType.APPROVAL_ISSUED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="APPROVAL_ISSUED",
        occurred_at=NOW + timedelta(seconds=2),
        event_id="operation-event-query-3",
    )

    assert store.list_operation_events(
        "tenant-1", "project-1", first_operation.operation_id
    ) == (first, third)
    assert store.read_operation_events_after(
        "tenant-1", "project-1", last_event_id=None, limit=2
    ) == (first, second)
    assert store.read_operation_events_after(
        "tenant-1", "project-1", last_event_id=second.event_id, limit=10
    ) == (third,)


def test_event_cursor_is_scope_bound_and_tampered_rows_fail_closed(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    operation = _operation()
    event = store._append_event(
        operation=operation,
        event_type=OperationEventType.OPERATION_CREATED,
        status=OperationStatus.WAIT_APPROVAL,
        recovery_state=RecoveryState.NONE,
        reason_code="AWAITING_EXPLICIT_APPROVAL",
        occurred_at=NOW,
        event_id="operation-event-scope-1",
    )

    assert store.read_operation_events_after(
        "tenant-1", "project-other", last_event_id=event.event_id
    ) is None
    assert store.read_operation_events_after(
        "tenant-other", "project-1", last_event_id=event.event_id
    ) is None
    assert store.read_operation_events_after(
        "tenant-1", "project-1", last_event_id="operation-event-unknown"
    ) is None
    assert store.read_operation_events_after(
        "tenant-1", "project-1", last_event_id="../tampered-cursor"
    ) is None

    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT body FROM v66_operation_events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()[0]
        )
        payload["reason_code"] = "TAMPERED"
        connection.execute(
            "UPDATE v66_operation_events SET body=? WHERE event_id=?",
            (json.dumps(payload), event.event_id),
        )
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        store.list_operation_events(
            "tenant-1", "project-1", operation.operation_id
        )


@pytest.mark.parametrize("limit", (0, 101, True, "10"))
def test_event_queries_reject_invalid_limits(tmp_path, limit) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    with pytest.raises(ValueError, match="limit"):
        store.read_operation_events_after(
            "tenant-1", "project-1", last_event_id=None, limit=limit
        )
