from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.execution_worker import OperationStatus, SchematicErcOperation
from embedded_copilot.execution_worker.contracts import OperationRequest
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker

from tests.execution_worker.test_schematic_erc import _operation


class _NoErcCall:
    def execute(self, operation):
        raise AssertionError("stale ERC operation must not reach adapter")


def test_stale_circuit_source_stops_worker_before_erc_adapter(tmp_path) -> None:
    now = datetime.now(UTC)
    payload = _operation().model_dump(mode="python")
    payload.update(
        created_at=now, expires_at=now + timedelta(minutes=15),
        fingerprint="sha256:" + "0" * 64,
        source_parse_operation_id="parse-operation-1",
        source_parse_receipt_fingerprint="sha256:" + "1" * 64,
    )
    operation = SchematicErcOperation.model_validate(payload)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    request = OperationRequest(operation=operation)
    service = ExecutionOperationService(
        store=store, schematic_evidence_authorizer=lambda _operation: True,
    )
    service.create_operation(request)
    approval = service.issue_approval(
        operation=request, reviewer_id="reviewer-1", nonce="approval-design-stale",
        expires_at=now + timedelta(minutes=5),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)
    worker = ExecutionWorker(
        worker_id="worker-design-stale", store=store, registry=None,
        workspace_resolver=None, targets=None, schematic_erc=_NoErcCall(),
        design_context_revalidator=lambda _operation: False,
    )
    status = worker.run_once()
    assert status is not None
    assert status.status is OperationStatus.FAILED
    assert status.safe_detail == "design approval is stale"
    assert status.receipt_fingerprint is None
    assert store.get_receipt(operation.tenant_id, operation.project_id, operation.operation_id) is None
