from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable, Any

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService
from embedded_copilot.engineering_workspace_apply.exceptions import ApplyRejected
from embedded_copilot.engineering_workspace_apply.models import ChangeIntent


@runtime_checkable
class WorkspaceChangeMaterializerPort(Protocol):
    def materialize(self, *, proposal: object, snapshot: object) -> tuple[ChangeIntent, ...]: ...


class ProposalBackedWorkspaceApplyApi:
    """Application adapter joining v6.4 approved proposals to v6.5 apply.

    The materializer is explicit and injected. Agents never receive this adapter or
    the underlying WorkspaceApplyService.
    """

    def __init__(self, *, proposals: object, service: WorkspaceApplyService, materializer: WorkspaceChangeMaterializerPort) -> None:
        if not callable(getattr(proposals, "get_proposal", None)) or not isinstance(service, WorkspaceApplyService) or not isinstance(materializer, WorkspaceChangeMaterializerPort):
            raise TypeError("workspace apply adapter dependency is invalid")
        self._proposals = proposals
        self._service = service
        self._materializer = materializer

    def create_changeset(self, *, tenant_id: str, requester_id: str, request: Any):
        proposal = self._proposals.get_proposal(tenant_id, request.project_id, request.proposal_id)
        if proposal is None or proposal.proposal_fingerprint != request.proposal_fingerprint:
            raise ApplyRejected("approved proposal is unavailable or stale")
        snapshot = self._service.snapshot(tenant_id=tenant_id, project_id=request.project_id, target_id=request.target_id)
        intents = self._materializer.materialize(proposal=copy.deepcopy(proposal), snapshot=copy.deepcopy(snapshot))
        if not isinstance(intents, tuple) or not all(type(item) is ChangeIntent for item in intents):
            raise ApplyRejected("change materializer returned invalid intents")
        return self._service.create_changeset(proposal=proposal, snapshot=snapshot, intents=intents)

    def get_changeset(self, *, tenant_id: str, project_id: str, changeset_id: str):
        return self._service.get_changeset(tenant_id, project_id, changeset_id)

    def create_preview(self, *, tenant_id: str, requester_id: str, request: Any):
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        if changeset is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ApplyRejected("changeset is stale")
        return self._service.preview(changeset)

    def issue_apply_approval(self, *, tenant_id: str, requester_id: str, request: Any):
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        if changeset is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ApplyRejected("changeset is stale")
        return self._service.issue_apply_approval(changeset=changeset, reviewer_id=request.reviewer_id, nonce=request.nonce)

    def apply(self, *, tenant_id: str, requester_id: str, request: Any):
        changeset = self._service.get_changeset(tenant_id, request.project_id, request.changeset_id)
        approval = self._service.get_approval(tenant_id, request.project_id, request.approval_id)
        if changeset is None or approval is None or changeset.fingerprint != request.changeset_fingerprint:
            raise ApplyRejected("apply binding is unavailable")
        return getattr(self._service, "apply")(changeset=changeset, approval=approval)

    def get_receipt(self, *, tenant_id: str, project_id: str, operation_id: str):
        return self._service.get_receipt(project_id, operation_id)


__all__ = ("ProposalBackedWorkspaceApplyApi", "WorkspaceChangeMaterializerPort")
