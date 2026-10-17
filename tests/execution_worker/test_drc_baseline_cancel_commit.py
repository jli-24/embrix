"""Offline cancellation committed before the final SQLite write lock; no EDA."""
from embedded_copilot.execution_worker.contracts import (
    ApprovalStatus,
    CancellationRequest,
    OperationStatus,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from tests.execution_worker.test_drc_baseline_worker import setup


def test_cancel_commits_after_collection_before_final_transaction_without_replay(tmp_path):
    worker, store, artifacts, operation, grant, calls, _ = setup(tmp_path)
    # Independent connection goes through the real public cancellation service.
    cancel_store = SQLiteExecutionStore(store._database)
    service = ExecutionOperationService(store=cancel_store, clock=lambda: operation.created_at)
    terminal = store.terminal_transition
    injected = []

    def cancel_before_lock(*args, **kwargs):
        if args[2].status is OperationStatus.SUCCEEDED:
            assert not injected
            # Collection is complete, but BEGIN IMMEDIATE has not been acquired.
            for role in ("raw-report", "context", "workspace-after"):
                identity = store.get_artifact(operation.tenant_id, operation.project_id,
                                              operation.operation_id + "-" + role)
                assert identity is not None and artifacts.read(identity)
            result = service.request_cancellation(CancellationRequest(
                request_id="baseline-cancel-before-commit", tenant_id=operation.tenant_id,
                project_id=operation.project_id, operation_id=operation.operation_id,
                requester_id=operation.requester_id, reason="offline final commit probe",
                requested_at=operation.created_at,
            ))
            assert result.status is OperationStatus.CANCEL_REQUESTED
            assert result.cancellation_requested
            assert store.get_status(operation.tenant_id, operation.project_id,
                                    operation.operation_id) == result
            injected.append(result.fingerprint)
        return terminal(*args, **kwargs)

    store.terminal_transition = cancel_before_lock
    status = worker.run_once()
    assert len(injected) == 1 and len(calls) == 1
    assert status.status is OperationStatus.CANCELLED and status.cancellation_requested
    receipt = store.get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.status is OperationStatus.CANCELLED and receipt.artifact is None
    assert operation.operation_id + "-raw-report" in receipt.evidence_references
    for reference in receipt.evidence_references:
        assert artifacts.read(store.get_artifact(operation.tenant_id, operation.project_id, reference))
    assert store.get_approval(operation.tenant_id, operation.project_id, grant.grant_id).status is ApprovalStatus.CONSUMED
    fresh = SQLiteExecutionStore(store._database)
    assert fresh.get_status(operation.tenant_id, operation.project_id, operation.operation_id) == status
    assert fresh.get_receipt(operation.tenant_id, operation.project_id, operation.operation_id) == receipt
    worker._store = fresh
    assert worker.run_once() is None and worker.recover_after_restart() == ()
    assert len(calls) == 1
