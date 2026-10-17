"""C2-I fake-only operation lifecycle; no native file or tool is touched."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest

from embedded_copilot.execution_worker.contracts import OperationRequest, OperationStatus
from embedded_copilot.execution_worker.ports import AdapterResult
from embedded_copilot.execution_worker.service import ExecutionOperationService, ExecutionUnavailable
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from tests.execution_worker.test_c2i_native_move_contract import NOW, _operation


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, operation: object) -> AdapterResult:
        self.calls.append(operation)
        return AdapterResult(status="SUCCEEDED", summary="offline fake only")


def _queued(tmp_path: Path):
    database = tmp_path / "worker.sqlite3"
    store = SQLiteExecutionStore(database)
    service = ExecutionOperationService(
        store=store, clock=lambda: NOW + timedelta(minutes=1), native_move_authorizer=lambda _: True,
    )
    operation = _operation()
    request = OperationRequest(operation=operation)
    assert service.create_operation(request) == request
    assert service.create_operation(request) == request
    approval = service.issue_approval(
        operation=request, reviewer_id="reviewer-1", nonce="approval-nonce",
        expires_at=NOW + timedelta(minutes=5),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)
    return store, service, operation


def _worker(store: SQLiteExecutionStore, *, adapter=None, revalidator=None) -> ExecutionWorker:
    return ExecutionWorker(
        worker_id="worker-c2i", store=store, registry=None, workspace_resolver=None,
        targets=None, native_move_adapter=adapter, native_move_revalidator=revalidator,
        clock=lambda: NOW + timedelta(minutes=2),
    )


def test_c2i_fake_success_restart_receipt_is_explicitly_offline(tmp_path: Path) -> None:
    store, _, operation = _queued(tmp_path)
    adapter = FakeAdapter()
    worker = _worker(store, adapter=adapter, revalidator=lambda _: True)
    status = worker.run_once()
    assert status is not None and status.status is OperationStatus.SUCCEEDED
    assert adapter.calls == [operation]
    reopened = SQLiteExecutionStore(tmp_path / "worker.sqlite3")
    restored = reopened.get_operation("tenant-1", "project-1", operation.operation_id)
    receipt = reopened.get_receipt("tenant-1", "project-1", operation.operation_id)
    assert restored == operation
    assert receipt is not None and receipt.native_move_offline is not None
    assert receipt.native_move_offline.candidate_id == operation.proposal_id
    assert receipt.native_move_offline.mapping_review_fingerprint == operation.mapping_review_fingerprint
    assert receipt.native_move_offline.native_read_back_status == "NOT_RUN"
    assert receipt.native_move_offline.drc_status == "NOT_RUN"
    assert receipt.native_move_offline.circuit_verified is False
    assert _worker(reopened, adapter=adapter, revalidator=lambda _: True).run_once() is None
    assert adapter.calls == [operation]


def test_c2i_default_service_cannot_approve_persisted_fake_operation(tmp_path: Path) -> None:
    store, _, operation = _queued(tmp_path)
    default = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "worker.sqlite3"))
    with pytest.raises(ExecutionUnavailable):
        default.issue_approval_for_id(
            tenant_id="tenant-1", project_id="project-1", operation_id=operation.operation_id,
            reviewer_id="reviewer-1", nonce="different", expires_at=NOW + timedelta(minutes=6),
        )


def test_c2i_missing_revalidator_or_second_check_change_never_calls_fake(tmp_path: Path) -> None:
    store, _, operation = _queued(tmp_path)
    adapter = FakeAdapter()
    status = _worker(store, adapter=adapter).run_once()
    assert status is not None and status.status is OperationStatus.FAILED
    assert adapter.calls == []

    other = tmp_path / "other"
    other.mkdir()
    store2, _, _ = _queued(other)
    checks = iter((True, False))
    status2 = _worker(store2, adapter=adapter, revalidator=lambda _: next(checks)).run_once()
    assert status2 is not None and status2.status is OperationStatus.FAILED
    assert adapter.calls == []
    assert store2.get_receipt("tenant-1", "project-1", operation.operation_id) is None


def test_c2i_concurrent_claim_runs_fake_once(tmp_path: Path) -> None:
    store, _, operation = _queued(tmp_path)
    adapter = FakeAdapter()
    workers = (_worker(store, adapter=adapter, revalidator=lambda _: True),
               _worker(store, adapter=adapter, revalidator=lambda _: True))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.run_once(), workers))
    assert sum(result is not None for result in results) == 1
    assert adapter.calls == [operation]


def test_c2i_concurrent_same_review_create_is_one_operation(tmp_path: Path) -> None:
    path = tmp_path / "worker.sqlite3"
    operation = _operation()
    request = OperationRequest(operation=operation)
    stores = (SQLiteExecutionStore(path), SQLiteExecutionStore(path))
    barrier = Barrier(2)
    services = []
    for store in stores:
        original = store.get_status

        def synced_get_status(*args, _original=original, _seen=[False]):
            value = _original(*args)
            if not _seen[0]:
                _seen[0] = True
                barrier.wait(timeout=5)
            return value

        store.get_status = synced_get_status
        services.append(ExecutionOperationService(
            store=store, clock=lambda: NOW + timedelta(minutes=1),
            native_move_authorizer=lambda _: True,
        ))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda service: service.create_operation(request), services))
    assert results == [request, request]
    reopened = SQLiteExecutionStore(path)
    assert reopened.get_operation("tenant-1", "project-1", operation.operation_id) == operation
    assert len(reopened.list_operations("tenant-1", "project-1")) == 1


def test_c2i_interrupted_fake_execution_requires_recovery_after_restart(tmp_path: Path) -> None:
    store, _, operation = _queued(tmp_path)

    class InterruptedFake:
        def execute(self, _operation: object) -> AdapterResult:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _worker(store, adapter=InterruptedFake(), revalidator=lambda _: True).run_once()
    reopened = SQLiteExecutionStore(tmp_path / "worker.sqlite3")
    recovered = _worker(reopened, adapter=FakeAdapter(), revalidator=lambda _: True).recover_after_restart()
    assert len(recovered) == 1
    assert recovered[0].operation_id == operation.operation_id
    assert recovered[0].status is OperationStatus.RECOVERY_REQUIRED
    assert reopened.get_receipt("tenant-1", "project-1", operation.operation_id) is None
