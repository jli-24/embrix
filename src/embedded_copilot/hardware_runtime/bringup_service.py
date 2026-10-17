from __future__ import annotations

from datetime import datetime

from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    OperationRequest,
)
from embedded_copilot.execution_worker.contracts import (
    HardwareBringupOperation,
    OperationApprovalGrant,
    OperationStatusView,
)


class HardwareBringupRuntimeService:
    """Application facade; it queues and approves typed operations, then delegates to ExecutionWorker."""

    def __init__(self, *, execution: ExecutionOperationService, worker: ExecutionWorker) -> None:
        self._execution = execution
        self._worker = worker

    def submit(
        self,
        operation: HardwareBringupOperation,
        *,
        reviewer_id: str,
        approval_nonce: str,
        approval_expires_at: datetime,
    ) -> OperationStatusView | None:
        request = OperationRequest(operation=operation)
        self._execution.create_operation(request)
        grant: OperationApprovalGrant = self._execution.issue_approval(
            operation=request,
            reviewer_id=reviewer_id,
            nonce=approval_nonce,
            expires_at=approval_expires_at,
        )
        self._execution.enqueue(operation=request, approval_id=grant.grant_id)
        return self._worker.run_once()

    def recover_after_restart(self) -> tuple[OperationStatusView, ...]:
        return self._worker.recover_after_restart()


__all__ = ("HardwareBringupRuntimeService",)
