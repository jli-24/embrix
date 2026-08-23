from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from .contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    BuildOperation,
    CancellationRequest,
    ExecutionProposal,
    OperationApprovalGrant,
    OperationReceipt,
    OperationRequest,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
    RecoveryDecision,
    TargetDiscoveryCandidate,
)
from .store import SQLiteExecutionStore


_APPROVAL_PURPOSE_BY_OPERATION = {
    OperationType.BUILD: ApprovalPurpose.BUILD,
    OperationType.TEST: ApprovalPurpose.TEST,
    OperationType.FLASH: ApprovalPurpose.FLASH,
    OperationType.UART_OBSERVATION: ApprovalPurpose.UART_OBSERVATION,
    OperationType.HIL: ApprovalPurpose.HIL,
}


class ExecutionRejected(RuntimeError):
    pass


class ExecutionUnavailable(RuntimeError):
    pass


class ExecutionOperationService:
    """Application service; it queues typed operations and never executes tools."""

    def __init__(self, *, store: SQLiteExecutionStore, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def create_proposal(self, proposal: ExecutionProposal) -> ExecutionProposal:
        if self._store.is_project_tombstoned(proposal.tenant_id, proposal.project_id):
            raise ExecutionRejected("project is tombstoned")
        return self._store.save_proposal(proposal)

    def discover_target(self, candidate: TargetDiscoveryCandidate) -> TargetDiscoveryCandidate:
        if self._store.is_project_tombstoned(candidate.tenant_id, candidate.project_id):
            raise ExecutionRejected("project is tombstoned")
        return self._store.save_candidate(candidate)

    def create_operation(self, request: OperationRequest) -> OperationRequest:
        operation = request.operation
        if self._store.is_project_tombstoned(operation.tenant_id, operation.project_id):
            raise ExecutionRejected("project is tombstoned")
        existing = self._store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
        if existing is not None:
            if existing.operation_type is not operation.operation_type:
                raise ExecutionRejected("operation identity conflict")
            return request
        now = self._clock()
        status = OperationStatusView(
            operation_id=operation.operation_id,
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_type=operation.operation_type,
            status=OperationStatus.WAIT_APPROVAL,
            approval_id=None,
            worker_id=None,
            recovery_state=RecoveryState.NONE,
            cancellation_requested=False,
            updated_at=now,
            safe_detail="operation awaits explicit capability approval",
        )
        self._store.create_operation_transition(operation, status, occurred_at=now)
        return request

    def get_operation(self, *, tenant_id: str, project_id: str, operation_id: str):
        return self._store.get_operation(tenant_id, project_id, operation_id)

    def list_operations(self, *, tenant_id: str, project_id: str, limit: int = 20):
        return self._store.list_operations(tenant_id, project_id, limit=limit)

    def get_artifact(self, *, tenant_id: str, project_id: str, artifact_id: str):
        return self._store.get_artifact(tenant_id, project_id, artifact_id)

    def issue_approval_for_id(self, *, tenant_id: str, project_id: str, operation_id: str, reviewer_id: str, nonce: str, expires_at: datetime) -> OperationApprovalGrant:
        operation = self.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
        if operation is None:
            raise ExecutionRejected("operation not found")
        return self.issue_approval(operation=OperationRequest(operation=operation), reviewer_id=reviewer_id, nonce=nonce, expires_at=expires_at)

    def enqueue_by_id(self, *, tenant_id: str, project_id: str, operation_id: str, approval_id: str) -> OperationStatusView:
        operation = self.get_operation(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
        if operation is None:
            raise ExecutionRejected("operation not found")
        return self.enqueue(operation=OperationRequest(operation=operation), approval_id=approval_id)

    def issue_approval(self, *, operation: OperationRequest, reviewer_id: str, nonce: str, expires_at: datetime) -> OperationApprovalGrant:
        op = operation.operation
        if self._store.is_project_tombstoned(op.tenant_id, op.project_id):
            raise ExecutionRejected("project is tombstoned")
        now = self._clock()
        if expires_at <= now:
            raise ExecutionRejected("approval expiry is invalid")
        purpose = _APPROVAL_PURPOSE_BY_OPERATION[op.operation_type]
        artifact_id = getattr(getattr(op, "artifact", None), "artifact_id", None)
        artifact_sha = getattr(getattr(op, "artifact", None), "artifact_sha256", None)
        grant = OperationApprovalGrant(
            grant_id=f"approval-{uuid4().hex}",
            tenant_id=op.tenant_id,
            project_id=op.project_id,
            requester_id=op.requester_id,
            approval_purpose=purpose,
            operation_id=op.operation_id,
            operation_type=op.operation_type,
            operation_fingerprint=op.fingerprint,
            proposal_id=op.proposal_id,
            proposal_fingerprint=op.proposal_fingerprint,
            target_id=op.target_id,
            target_fingerprint=op.target_fingerprint,
            workspace_snapshot_id=op.workspace_snapshot_id,
            workspace_fingerprint=op.workspace_fingerprint,
            artifact_id=artifact_id,
            artifact_sha256=artifact_sha,
            reviewer_id=reviewer_id,
            status=ApprovalStatus.ISSUED,
            expires_at=expires_at,
            nonce=nonce,
            issued_at=now,
        )
        return self._store.issue_approval_transition(op, grant)

    def enqueue(self, *, operation: OperationRequest, approval_id: str) -> OperationStatusView:
        op = operation.operation
        status = self._store.get_status(op.tenant_id, op.project_id, op.operation_id)
        approval = self._store.get_approval(op.tenant_id, op.project_id, approval_id)
        expected_purpose = _APPROVAL_PURPOSE_BY_OPERATION[op.operation_type]
        if status is None or status.status is not OperationStatus.WAIT_APPROVAL:
            raise ExecutionRejected("operation is not awaiting approval")
        if (
            approval is None
            or approval.operation_id != op.operation_id
            or approval.operation_type is not op.operation_type
            or approval.approval_purpose is not expected_purpose
            or approval.operation_fingerprint != op.fingerprint
            or approval.status is not ApprovalStatus.ISSUED
        ):
            raise ExecutionRejected("approval binding mismatch")
        try:
            return self._store.enqueue_transition(
                op, approval, occurred_at=self._clock()
            )
        except ValueError as exc:
            raise ExecutionRejected("approval binding mismatch") from exc

    def request_cancellation(self, request: CancellationRequest) -> OperationStatusView:
        status = self._store.get_status(request.tenant_id, request.project_id, request.operation_id)
        if status is None:
            raise ExecutionRejected("operation not found")
        cancellation_status = OperationStatus.CANCEL_PENDING if status.operation_type is OperationType.FLASH and status.status is OperationStatus.RUNNING else OperationStatus.CANCEL_REQUESTED
        updated = status.model_validate({**status.model_dump(mode="python"), "status": cancellation_status, "cancellation_requested": True, "updated_at": self._clock(), "fingerprint": "sha256:" + "0" * 64})
        operation = self._store.get_operation(
            request.tenant_id, request.project_id, request.operation_id
        )
        if operation is None:
            self._store.save_cancellation(request)
            return self._store.save_status(updated)
        try:
            return self._store.cancellation_transition(
                operation, request, status, updated
            )
        except ValueError as exc:
            raise ExecutionRejected("operation state changed") from exc

    def apply_recovery_decision(self, decision: RecoveryDecision) -> OperationStatusView:
        status = self._store.get_status(decision.tenant_id, decision.project_id, decision.operation_id)
        if status is None or status.status is not OperationStatus.RECOVERY_REQUIRED:
            raise ExecutionRejected("operation is not awaiting recovery")
        if status.operation_type is OperationType.FLASH and decision.decision != "REQUIRES_MANUAL_INTERVENTION":
            raise ExecutionRejected("flash recovery requires explicit manual intervention")
        updated_status = OperationStatus.FAILED if decision.decision != "MARK_TARGET_SAFE" else OperationStatus.CANCELLED
        updated = status.model_validate({**status.model_dump(mode="python"), "status": updated_status, "recovery_state": RecoveryState.RECOVERED, "updated_at": self._clock(), "fingerprint": "sha256:" + "0" * 64})
        operation = self._store.get_operation(
            decision.tenant_id, decision.project_id, decision.operation_id
        )
        if operation is None:
            self._store.save_recovery_decision(decision)
            return self._store.save_status(updated)
        try:
            return self._store.recovery_resolution_transition(
                operation, status, decision, updated
            )
        except ValueError as exc:
            raise ExecutionRejected("operation state changed") from exc

    def get_status(self, *, tenant_id: str, project_id: str, operation_id: str) -> OperationStatusView | None:
        return self._store.get_status(tenant_id, project_id, operation_id)

    def get_receipt(self, *, tenant_id: str, project_id: str, operation_id: str) -> OperationReceipt | None:
        return self._store.get_receipt(tenant_id, project_id, operation_id)

    def list_operation_events(
        self, *, tenant_id: str, project_id: str, operation_id: str, limit: int = 100
    ):
        return self._store.list_operation_events(
            tenant_id, project_id, operation_id, limit=limit
        )

    def read_operation_events_after(
        self,
        *,
        tenant_id: str,
        project_id: str,
        last_event_id: str | None,
        limit: int = 100,
    ):
        return self._store.read_operation_events_after(
            tenant_id,
            project_id,
            last_event_id=last_event_id,
            limit=limit,
        )


__all__ = ("ExecutionOperationService", "ExecutionRejected", "ExecutionUnavailable")
