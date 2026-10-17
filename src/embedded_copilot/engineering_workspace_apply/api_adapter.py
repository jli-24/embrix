from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from embedded_copilot.api.copilot_workspace_models import (
        ApplyApprovalRequest,
        ApplyOperationRequest,
        ChangeSetCreateRequest,
        PreviewRequest,
    )
from embedded_copilot.engineering_validation_proposal import ValidationProposalService

from .contracts import content_fingerprint
from .models import ApplyOperation, ChangeIntent, ChangeType
from .service import WorkspaceApplyService


_ZERO = "sha256:" + "0" * 64


class ProjectCatalogWorkspaceApplyPort:
    """API adapter around the production v6.5 service and approved proposals."""

    def __init__(self, *, service: WorkspaceApplyService, proposals: ValidationProposalService) -> None:
        self._service = service
        self._proposals = proposals

    def create_changeset(self, *, tenant_id: str, requester_id: str, request: ChangeSetCreateRequest):
        proposal = self._proposals.get_proposal(tenant_id, request.project_id, request.proposal_id)
        if proposal is None or proposal.proposal_fingerprint != request.proposal_fingerprint:
            raise ValueError("proposal fingerprint mismatch")
        if getattr(proposal.status, "value", proposal.status) != "APPROVED":
            raise ValueError("proposal approval is required")
        snapshot = self._service.snapshot(tenant_id=tenant_id, project_id=request.project_id, target_id=request.target_id)
        intents = tuple(self._intent(proposal, item) for item in proposal.proposed_changes)
        return self._service.create_changeset(proposal=proposal, snapshot=snapshot, intents=intents)

    def get_changeset(self, *, tenant_id: str, project_id: str, changeset_id: str):
        return self._service.get_changeset(tenant_id, project_id, changeset_id)

    def get_project_items(self, *, tenant_id: str, project_id: str, limit: int = 20):
        return self._service.list_changesets(tenant_id, project_id, limit=limit)

    def list_events(self, *, tenant_id: str, project_id: str, limit: int = 20):
        return self._service.list_audit(tenant_id, project_id, limit=limit)

    def create_preview(self, *, tenant_id: str, requester_id: str, request: PreviewRequest):
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        if changeset is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ValueError("changeset fingerprint mismatch")
        return self._service.preview(changeset)

    def issue_apply_approval(self, *, tenant_id: str, requester_id: str, request: ApplyApprovalRequest):
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        if changeset is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ValueError("changeset fingerprint mismatch")
        return self._service.issue_apply_approval(changeset=changeset, reviewer_id=request.reviewer_id, nonce=request.nonce)

    def apply(self, *, tenant_id: str, requester_id: str, request: ApplyOperationRequest):
        if request.operation is not ApplyOperation.WORKSPACE_APPLY:
            raise ValueError("workspace apply operation is required")
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        approval = self._service.get_approval(tenant_id, request.project_id, request.approval_id)
        if changeset is None or approval is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ValueError("apply binding mismatch")
        return self._service.apply(changeset=changeset, approval=approval)

    def get_receipt(self, *, tenant_id: str, project_id: str, operation_id: str):
        return self._service.get_receipt(project_id, operation_id)

    @staticmethod
    def _intent(proposal, change) -> ChangeIntent:
        if not change.file_reference:
            raise ValueError("workspace change requires a relative file reference")
        kind = str(change.change_kind).upper()
        change_type = ChangeType.MODIFY_FILE if "MODIFY" in kind else ChangeType.CREATE_FILE
        after_content = change.logical_change
        return ChangeIntent(
            relative_file_reference=change.file_reference,
            change_type=change_type,
            target_symbol=change.symbol,
            before_fingerprint=change.before_fingerprint or _ZERO,
            after_fingerprint=content_fingerprint(after_content.encode("utf-8")),
            safe_preview=change.logical_change,
            reason=change.expected_effect,
            source_proposal_reference=proposal.proposal_id,
            after_content=after_content,
        )


__all__ = ("ProjectCatalogWorkspaceApplyPort",)
