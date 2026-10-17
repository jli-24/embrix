from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from embedded_copilot.engineering_copilot import (
    FirmwareSourceGenerationRequest,
    FirmwareSourceProposal,
    FirmwareSourceProposalDecision,
    FirmwareSourceProposalDecisionStatus,
)
from embedded_copilot.engineering_copilot.common import ZERO, validate_copy
from embedded_copilot.engineering_workspace_apply import (
    ApplyOperation,
    ChangeIntent,
    ChangeType,
    WorkspaceApplyService,
)
from embedded_copilot.engineering_workspace_apply.contracts import content_fingerprint
from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.execution_worker import (
    AuthorizedExecutionBinding,
    ExecutionOperationService,
    FirmwareSourceWriteFile,
    FirmwareSourceWriteOperation,
    OperationRequest,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class _ApprovedWorkspaceProposal:
    status: str
    proposal_id: str
    proposal_fingerprint: str
    tenant_id: str
    project_id: str
    requester_id: str
    validation_fingerprint: str
    evidence_fingerprint: str


class FirmwareSourceExecutionBridge:
    """Binds one approved source proposal to canonical Workspace and Worker state."""

    def __init__(
        self,
        *,
        workspace: WorkspaceApplyService,
        execution: ExecutionOperationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(workspace, WorkspaceApplyService):
            raise TypeError("canonical workspace apply service is required")
        if not isinstance(execution, ExecutionOperationService):
            raise TypeError("canonical execution operation service is required")
        self._workspace = workspace
        self._execution = execution
        self._clock = clock or (lambda: datetime.now(UTC))

    def approve_and_enqueue(
        self,
        *,
        generation_request: FirmwareSourceGenerationRequest,
        proposal: FirmwareSourceProposal,
        decision: FirmwareSourceProposalDecision,
    ) -> OperationRequest:
        request = validate_copy(generation_request, FirmwareSourceGenerationRequest)
        source_proposal = validate_copy(proposal, FirmwareSourceProposal)
        proposal_decision = validate_copy(decision, FirmwareSourceProposalDecision)
        if (
            source_proposal.generation_request_id != request.request_id
            or source_proposal.generation_request_fingerprint != request.fingerprint
            or source_proposal.project_id != request.project_id
            or source_proposal.conversation_id != request.conversation_id
            or source_proposal.requester != request.requester
            or proposal_decision.proposal_id != source_proposal.proposal_id
            or proposal_decision.proposal_fingerprint != source_proposal.fingerprint
            or proposal_decision.decision is not FirmwareSourceProposalDecisionStatus.APPROVED
            or proposal_decision.reviewer is None
            or proposal_decision.decided_at is None
        ):
            raise ValueError("firmware source proposal approval binding mismatch")
        operation_id = _id(
            "firmware-source-write",
            request.tenant_id,
            request.project_id,
            source_proposal.fingerprint,
            proposal_decision.fingerprint,
        )
        correlation = None
        if request.source_request_id is not None or request.trace_id is not None:
            if request.source_request_id is None or request.trace_id is None:
                raise ValueError("firmware source correlation is incomplete")
            correlation = CorrelationContext(
                request_id=request.source_request_id,
                trace_id=request.trace_id,
                project_id=request.project_id,
                task_id=request.implementation_task_id,
                operation_id=operation_id,
            )
        existing = self._execution.get_operation(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=operation_id,
        )
        if existing is not None:
            if not isinstance(existing, FirmwareSourceWriteOperation):
                raise ValueError("firmware source operation identity conflict")
            return OperationRequest(operation=existing)

        snapshot = self._workspace.snapshot(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            target_id="project-root",
        )
        approved = _ApprovedWorkspaceProposal(
            status="APPROVED",
            proposal_id=source_proposal.proposal_id,
            proposal_fingerprint=source_proposal.fingerprint,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            requester_id=request.requester,
            validation_fingerprint=source_proposal.validation_fingerprint,
            evidence_fingerprint=ZERO,
        )
        intents = tuple(
            ChangeIntent(
                relative_file_reference=item.relative_path,
                change_type=ChangeType.CREATE_FILE,
                before_fingerprint=ZERO,
                after_fingerprint=content_fingerprint(item.content.encode("utf-8")),
                safe_preview=item.content,
                reason="Explicitly approved deterministic firmware source generation.",
                source_proposal_reference=source_proposal.proposal_id,
                after_content=item.content,
            )
            for item in source_proposal.files
        )
        changeset = self._workspace.create_changeset(
            proposal=approved,
            snapshot=snapshot,
            intents=intents,
            operation=ApplyOperation.WORKSPACE_APPLY,
        )
        workspace_approval = self._workspace.issue_apply_approval(
            changeset=changeset,
            reviewer_id=proposal_decision.reviewer,
            nonce=_id("workspace-approval-nonce", proposal_decision.fingerprint, changeset.fingerprint),
        )
        files = tuple(
            FirmwareSourceWriteFile(
                relative_path=item.relative_path,
                content=item.content,
                content_fingerprint=item.content_fingerprint,
                size_bytes=item.size_bytes,
            )
            for item in source_proposal.files
        )
        authorized = AuthorizedExecutionBinding(
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            implementation_id=request.implementation_id,
            implementation_fingerprint=request.implementation_fingerprint,
            task_id=request.implementation_task_id,
            task_fingerprint=request.implementation_task_fingerprint,
            intent_id=request.execution_intent_id,
            intent_fingerprint=request.execution_intent_fingerprint,
            request_id=request.authorization_request_id,
            request_fingerprint=request.authorization_request_fingerprint,
            decision_id=request.authorization_decision_id,
            decision_fingerprint=request.authorization_decision_fingerprint,
            authorization_id=request.authorization_id,
            authorization_fingerprint=request.authorization_fingerprint,
            revision=request.implementation_revision,
        )
        now = self._clock()
        operation = FirmwareSourceWriteOperation(
            operation_id=operation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            requester_id=request.requester,
            target_id=changeset.target.target_id,
            target_fingerprint=changeset.target_fingerprint,
            workspace_snapshot_id=_id("workspace-snapshot", snapshot.fingerprint),
            workspace_fingerprint=changeset.base_project_fingerprint,
            proposal_id=source_proposal.proposal_id,
            proposal_fingerprint=source_proposal.fingerprint,
            created_at=now,
            expires_at=changeset.expires_at,
            nonce=_id("firmware-source-operation-nonce", proposal_decision.fingerprint),
            authorized_execution=authorized,
            files=files,
            changeset=changeset,
            workspace_approval=workspace_approval,
            correlation=correlation,
        )
        operation_request = OperationRequest(operation=operation)
        self._execution.create_operation(operation_request)
        operation_approval = self._execution.issue_approval(
            operation=operation_request,
            reviewer_id=proposal_decision.reviewer,
            nonce=_id("operation-approval-nonce", proposal_decision.fingerprint, operation.fingerprint),
            expires_at=changeset.expires_at,
        )
        self._execution.enqueue(
            operation=operation_request,
            approval_id=operation_approval.grant_id,
        )
        return operation_request


__all__ = ("FirmwareSourceExecutionBridge",)
