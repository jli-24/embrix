from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import uuid4

from .artifacts import ArtifactStore
from .contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    BuildOperation,
    CancellationRequest,
    ExecutionProposal,
    KiCadNativeFootprintMoveOperation,
    PCBWriteOperation,
    PcbDrcBaselineOperation,
    SchematicParseOperation,
    SchematicErcOperation,
    OperationApprovalGrant,
    OperationReceipt,
    OperationRequest,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
    RecoveryDecision,
    TargetDiscoveryCandidate,
    approval_purpose_for_operation,
)
from .store import SQLiteExecutionStore


class ExecutionRejected(RuntimeError):
    pass


class ExecutionUnavailable(RuntimeError):
    pass


def build_proposal_binding_matches(
    operation: BuildOperation, proposal: ExecutionProposal | None
) -> bool:
    """Revalidate the persisted Build binding at creation and Worker dispatch."""
    request = operation.firmware_build_request
    if proposal is None or request is None or request.configuration is None:
        return False
    return bool(
        proposal.tenant_id == operation.tenant_id
        and proposal.project_id == operation.project_id
        and proposal.proposal_id == operation.proposal_id
        and proposal.fingerprint == operation.proposal_fingerprint
        and proposal.requester_id == operation.requester_id
        and proposal.target_id == operation.target_id
        and proposal.target_fingerprint == operation.target_fingerprint
        and proposal.workspace_snapshot_id == operation.workspace_snapshot_id
        and proposal.workspace_fingerprint == operation.workspace_fingerprint
        and proposal.operation_type is OperationType.BUILD
        and proposal.toolchain_id == operation.toolchain.toolchain_id
        and proposal.toolchain_version == operation.toolchain.tool_version
        and proposal.build_configuration == operation.configuration
        and proposal.source_revision is not None
        and proposal.firmware_build_request_fingerprint is not None
        and operation.firmware_build_request_fingerprint == proposal.firmware_build_request_fingerprint
        and request.fingerprint == proposal.firmware_build_request_fingerprint
        and operation.source_artifacts
        and operation.source_artifacts == request.source_artifacts
        and operation.build_system == request.build_system
        and operation.build_directory == request.configuration.build_directory
        and operation.expected_artifact_type in request.configuration.expected_artifact_types
        and request.expected_source_revision == proposal.source_revision
        and request.actual_source_revision == proposal.source_revision
        and all(source.source_revision == proposal.source_revision for source in operation.source_artifacts)
    )


class ExecutionOperationService:
    """Application service; it queues typed operations and never executes tools."""

    def __init__(self, *, store: SQLiteExecutionStore, clock: Callable[[], datetime] | None = None, artifact_store: ArtifactStore | None = None, native_move_authorizer: Callable[[object], bool] | None = None, schematic_evidence_authorizer: Callable[[object], bool] | None = None, drc_baseline_revalidator: Callable[[object], bool] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._artifact_store = artifact_store
        self._native_move_authorizer = native_move_authorizer
        self._schematic_evidence_authorizer = schematic_evidence_authorizer
        self._drc_baseline_revalidator = drc_baseline_revalidator
        self._pcb_write_authorizer: Callable[[object], bool] | None = None

    def set_drc_baseline_revalidator(self, revalidator: Callable[[object], bool]) -> None:
        if not callable(revalidator):
            raise TypeError("DRC baseline revalidator is invalid")
        self._drc_baseline_revalidator = revalidator

    def _require_drc_baseline_current(self, operation: object) -> None:
        if not isinstance(operation, PcbDrcBaselineOperation):
            return
        if self._clock() >= operation.expires_at:
            raise ExecutionRejected("DRC baseline operation expired")
        if not callable(self._drc_baseline_revalidator):
            raise ExecutionUnavailable("DRC_BASELINE_UNAVAILABLE")
        try:
            current = self._drc_baseline_revalidator(operation)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            raise ExecutionRejected("DRC baseline binding is stale") from error
        if current is not True:
            raise ExecutionRejected("DRC baseline binding is stale")

    def _require_native_move_authorized(self, operation: object) -> None:
        if isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is None:
            raise ExecutionRejected("schematic ERC requires canonical parse evidence")
        if isinstance(operation, (SchematicParseOperation, SchematicErcOperation)):
            if self._clock() >= operation.expires_at:
                raise ExecutionRejected("schematic evidence operation expired")
            if self._schematic_evidence_authorizer is None:
                raise ExecutionUnavailable("schematic evidence source is unavailable")
            try:
                current = self._schematic_evidence_authorizer(operation)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                raise ExecutionRejected("schematic evidence source is stale") from error
            if current is not True:
                raise ExecutionRejected("schematic evidence source is stale")
        if not isinstance(operation, KiCadNativeFootprintMoveOperation):
            return
        if self._native_move_authorizer is None:
            raise ExecutionUnavailable("native footprint move is unavailable")
        try:
            allowed = self._native_move_authorizer(operation)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ExecutionRejected("native footprint move binding is stale") from error
        if allowed is not True:
            raise ExecutionRejected("native footprint move binding is stale")

    def set_native_move_authorizer(self, authorizer: Callable[[object], bool]) -> None:
        if not callable(authorizer):
            raise TypeError("native move authorizer is invalid")
        self._native_move_authorizer = authorizer

    def set_pcb_write_authorizer(self, authorizer: Callable[[object], bool]) -> None:
        self._pcb_write_authorizer = authorizer

    def _require_pcb_write_target_allowed(self, operation: object) -> None:
        if not isinstance(operation, PCBWriteOperation) or self._pcb_write_authorizer is None:
            return
        try:
            allowed = self._pcb_write_authorizer(operation)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ExecutionRejected("PCB write target check unavailable") from error
        if allowed is not True:
            raise ExecutionRejected("PCB write target is reserved by C1 import")

    @property
    def artifact_store(self) -> ArtifactStore | None:
        return self._artifact_store

    def set_schematic_evidence_authorizer(self, authorizer: Callable[[object], bool]) -> None:
        self._schematic_evidence_authorizer = authorizer

    @property
    def store(self) -> SQLiteExecutionStore:
        return self._store

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
        self._require_drc_baseline_current(operation)
        self._require_native_move_authorized(operation)
        self._require_pcb_write_target_allowed(operation)
        if self._store.is_project_tombstoned(operation.tenant_id, operation.project_id):
            raise ExecutionRejected("project is tombstoned")
        proposal = self._store.get_proposal(
            operation.tenant_id, operation.project_id, operation.proposal_id
        )
        if isinstance(operation, BuildOperation) and proposal is None:
            raise ExecutionRejected("proposal not found")
        if proposal is not None and (
            operation.proposal_fingerprint != proposal.fingerprint
            or operation.requester_id != proposal.requester_id
            or operation.target_id != proposal.target_id
            or operation.target_fingerprint != proposal.target_fingerprint
            or operation.workspace_snapshot_id != proposal.workspace_snapshot_id
            or operation.workspace_fingerprint != proposal.workspace_fingerprint
            or operation.operation_type is not proposal.operation_type
            or (
                isinstance(operation, BuildOperation)
                and not build_proposal_binding_matches(operation, proposal)
            )
        ):
            raise ExecutionRejected("proposal identity conflict")
        existing = self._store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
        if existing is not None:
            persisted = self._store.get_operation(
                operation.tenant_id, operation.project_id, operation.operation_id
            )
            if (
                existing.operation_type is not operation.operation_type
                or persisted != operation
            ):
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
            correlation=operation.correlation,
        )
        try:
            self._store.create_operation_transition(operation, status, occurred_at=now)
        except sqlite3.IntegrityError:
            if not isinstance(operation, KiCadNativeFootprintMoveOperation):
                raise
            # Two valid requests for the same immutable review can race after
            # both observe an empty head. The operation ID is deterministic;
            # only an exact persisted copy is an idempotent result.
            persisted = self._store.get_operation(
                operation.tenant_id, operation.project_id, operation.operation_id,
            )
            if persisted != operation:
                raise ExecutionRejected("operation identity conflict") from None
        return request

    def get_operation(self, *, tenant_id: str, project_id: str, operation_id: str):
        return self._store.get_operation(tenant_id, project_id, operation_id)

    def list_operations(self, *, tenant_id: str, project_id: str, limit: int = 20):
        return self._store.list_operations(tenant_id, project_id, limit=limit)

    def get_artifact(self, *, tenant_id: str, project_id: str, artifact_id: str):
        identity = self._store.get_artifact(tenant_id, project_id, artifact_id)
        if identity is None:
            return None
        if (identity.tenant_id, identity.project_id, identity.artifact_id) != (tenant_id, project_id, artifact_id):
            raise ExecutionRejected("artifact identity mismatch")
        if self._artifact_store is None:
            raise ExecutionUnavailable("artifact store unavailable")
        try:
            content = self._artifact_store.read(identity)
        except FileNotFoundError as exc:
            raise ExecutionUnavailable("artifact blob unavailable") from exc
        except (OSError, ValueError) as exc:
            raise ExecutionRejected("artifact blob verification failed") from exc
        if len(content) != identity.size_bytes:
            raise ExecutionRejected("artifact size mismatch")
        return identity

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
        self._require_drc_baseline_current(op)
        self._require_native_move_authorized(op)
        self._require_pcb_write_target_allowed(op)
        if self._store.is_project_tombstoned(op.tenant_id, op.project_id):
            raise ExecutionRejected("project is tombstoned")
        now = self._clock()
        if expires_at <= now:
            raise ExecutionRejected("approval expiry is invalid")
        purpose = approval_purpose_for_operation(op.operation_type)
        artifact_id = getattr(getattr(op, "artifact", None), "artifact_id", None)
        artifact_sha = getattr(getattr(op, "artifact", None), "artifact_sha256", None)
        grant_id = f"approval-{uuid4().hex}"
        if op.operation_type in {
            OperationType.FIRMWARE_SOURCE_WRITE,
            OperationType.KICAD_SCHEMATIC_WRITE,
            OperationType.KICAD_SCHEMATIC_ERC,
            OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,
        }:
            grant_id = "approval-" + hashlib.sha256(
                f"{op.fingerprint}|{reviewer_id}|{nonce}|{expires_at.isoformat()}".encode("utf-8")
            ).hexdigest()[:32]
        grant = OperationApprovalGrant(
            grant_id=grant_id,
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
        self._require_drc_baseline_current(op)
        self._require_native_move_authorized(op)
        self._require_pcb_write_target_allowed(op)
        status = self._store.get_status(op.tenant_id, op.project_id, op.operation_id)
        approval = self._store.get_approval(op.tenant_id, op.project_id, approval_id)
        expected_purpose = approval_purpose_for_operation(op.operation_type)
        if status is None or status.status is not OperationStatus.WAIT_APPROVAL:
            raise ExecutionRejected("operation is not awaiting approval")
        if (
            approval is None
            or approval.operation_id != op.operation_id
            or approval.operation_type is not op.operation_type
            or approval.approval_purpose is not expected_purpose
            or approval.operation_fingerprint != op.fingerprint
            or approval.requester_id != op.requester_id
            or approval.proposal_id != op.proposal_id
            or approval.proposal_fingerprint != op.proposal_fingerprint
            or approval.target_id != op.target_id
            or approval.target_fingerprint != op.target_fingerprint
            or approval.workspace_snapshot_id != op.workspace_snapshot_id
            or approval.workspace_fingerprint != op.workspace_fingerprint
            or approval.status is not ApprovalStatus.ISSUED
            or self._clock() >= approval.expires_at
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
        receipt = self._store.get_receipt(tenant_id, project_id, operation_id)
        if receipt is not None and receipt.artifact is not None:
            artifact = self.get_artifact(
                tenant_id=tenant_id, project_id=project_id,
                artifact_id=receipt.artifact.artifact_id,
            )
            if artifact != receipt.artifact:
                raise ExecutionRejected("receipt artifact identity mismatch")
        if receipt is not None and receipt.build_evidence is not None:
            evidence = receipt.build_evidence
            output_ids = set(evidence.output_artifact_ids)
            if (
                (receipt.artifact is not None and receipt.artifact.artifact_id not in output_ids)
                or (evidence.log_artifact_id is not None and evidence.log_artifact_id not in output_ids)
                or (evidence.raw_evidence_artifact_id is not None and evidence.raw_evidence_artifact_id not in output_ids)
            ):
                raise ExecutionRejected("build evidence artifact binding mismatch")
            for artifact_id in output_ids:
                artifact = self.get_artifact(
                    tenant_id=tenant_id, project_id=project_id, artifact_id=artifact_id,
                )
                if artifact is None or artifact.build_operation_id != operation_id:
                    raise ExecutionRejected("build evidence artifact identity mismatch")
        return receipt

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
