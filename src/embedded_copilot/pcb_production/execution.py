from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from embedded_copilot.engineering_workspace_apply import ApplyOperation, ChangeIntent, ChangeType, WorkspaceApplyService
from embedded_copilot.engineering_workspace_apply.contracts import content_fingerprint
from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.execution_worker import ExecutionOperationService, OperationRequest, OperationType
from embedded_copilot.execution_worker.contracts import PCBWriteOperation, PcbDrcOperation

from .contracts import PCBParseValidationEvidence, PCBParseValidationStatus, PCBProductionRequest, PCBProposal, PCBProposalDecision
from .serializer import serialize_kicad_pcb


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _safe_pcb_preview(content: bytes) -> str:
    """Keep the review preview metadata-only while retaining the exact artifact."""

    del content
    return "Approved deterministic KiCad PCB artifact; content is held by the controlled apply receipt."


class PCBExecutionBridge:
    """Bind an approved structured PCB proposal to the existing execution lifecycle."""

    def __init__(
        self,
        *,
        workspace: WorkspaceApplyService,
        execution: ExecutionOperationService,
        footprint_root: Path | None = None,
        plane_nets: frozenset[str] = frozenset(),
        plane_layers: dict[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(workspace, WorkspaceApplyService) or not isinstance(execution, ExecutionOperationService):
            raise TypeError("canonical execution dependencies are required")
        self._workspace = workspace
        self._execution = execution
        if footprint_root is None:
            self._footprint_root = None
        else:
            raw_root = Path(footprint_root)
            if raw_root.is_symlink():
                raise ValueError("KiCad footprint root is invalid")
            resolved_root = raw_root.resolve(strict=True)
            if not resolved_root.is_dir():
                raise ValueError("KiCad footprint root is invalid")
            self._footprint_root = resolved_root
        self._plane_nets = plane_nets
        self._plane_layers = dict(plane_layers or {})
        self._clock = clock or (lambda: datetime.now(UTC))

    def approve_and_enqueue(self, *, production_request: PCBProductionRequest, proposal: PCBProposal, decision: PCBProposalDecision, tenant_id: str = "default-tenant") -> OperationRequest:
        request = PCBProductionRequest.model_validate(production_request.model_dump(mode="python"), strict=False)
        checked = PCBProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
        approved = PCBProposalDecision.model_validate(decision.model_dump(mode="python"), strict=False)
        if checked.request_id != request.request_id or checked.project_id != request.project_id or approved.proposal_id != checked.proposal_id or approved.proposal_fingerprint != checked.fingerprint or approved.decision != "APPROVED":
            raise ValueError("PCB proposal approval binding mismatch")
        content = serialize_kicad_pcb(
            checked,
            footprint_root=self._footprint_root,
            plane_nets=self._plane_nets,
            plane_layers=self._plane_layers,
        )
        content_fp = content_fingerprint(content)
        operation_id = _id("kicad-pcb-write", tenant_id, request.project_id, checked.fingerprint, content_fp)
        correlation = None
        if request.source_request_id is not None or request.trace_id is not None:
            if request.source_request_id is None or request.trace_id is None:
                raise ValueError("PCB production correlation is incomplete")
            correlation = CorrelationContext(
                request_id=request.source_request_id,
                trace_id=request.trace_id,
                project_id=request.project_id,
                task_id=request.implementation_task_id,
                operation_id=operation_id,
            )
        existing = self._execution.get_operation(tenant_id=tenant_id, project_id=request.project_id, operation_id=operation_id)
        if existing is not None:
            if not isinstance(existing, PCBWriteOperation):
                raise ValueError("PCB operation identity conflict")
            return OperationRequest(operation=existing)
        snapshot = self._workspace.snapshot(tenant_id=tenant_id, project_id=request.project_id, target_id=request.workspace_identity)
        proposal_view = type("ApprovedPCBProposal", (), {
            "status": "APPROVED",
            "proposal_id": checked.proposal_id,
            "proposal_fingerprint": checked.fingerprint,
            "tenant_id": tenant_id,
            "project_id": request.project_id,
            "requester_id": request.implementation_id,
        })()
        intent = ChangeIntent(
            relative_file_reference=request.target_relative_path,
            change_type=ChangeType.CREATE_FILE,
            before_fingerprint="sha256:" + "0" * 64,
            after_fingerprint=content_fp,
            safe_preview=_safe_pcb_preview(content),
            reason="Explicitly approved deterministic KiCad PCB production.",
            source_proposal_reference=checked.proposal_id,
            after_content=content.decode("utf-8"),
        )
        changeset = self._workspace.create_changeset(proposal=proposal_view, snapshot=snapshot, intents=(intent,), operation=ApplyOperation.WORKSPACE_APPLY)
        workspace_approval = self._workspace.issue_apply_approval(changeset=changeset, reviewer_id=approved.reviewer, nonce=_id("pcb-workspace-approval", approved.fingerprint, changeset.fingerprint))
        now = self._clock()
        operation = PCBWriteOperation(
            operation_id=operation_id,
            tenant_id=tenant_id,
            project_id=request.project_id,
            requester_id=request.requester,
            target_id=changeset.target.target_id,
            target_fingerprint=changeset.target_fingerprint,
            workspace_snapshot_id=_id("workspace-snapshot", snapshot.fingerprint),
            workspace_fingerprint=changeset.base_project_fingerprint,
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.fingerprint,
            operation_type=OperationType.KICAD_PCB_WRITE,
            created_at=now,
            expires_at=changeset.expires_at,
            nonce=_id("pcb-operation-nonce", approved.fingerprint, checked.fingerprint),
            conversation_id=request.conversation_id,
            implementation_id=request.implementation_id,
            implementation_task_id=request.implementation_task_id,
            authorization_id=request.authorization_id,
            authorization_request_id=request.authorization_request_id,
            pcb_request_id=request.request_id,
            pcb_request_fingerprint=request.fingerprint,
            pcb_proposal_id=checked.proposal_id,
            pcb_proposal_fingerprint=checked.fingerprint,
            schematic_artifact_id=request.schematic_artifact_id,
            schematic_fingerprint=request.schematic_artifact_fingerprint,
            erc_result_id=request.schematic_erc_result_id,
            erc_fingerprint=request.schematic_erc_fingerprint,
            design_truth_fingerprint=request.design_truth_fingerprint,
            target_relative_path=request.target_relative_path,
            content=content.decode("utf-8"),
            content_fingerprint=content_fp,
            changeset=changeset,
            workspace_approval=workspace_approval,
            correlation=correlation,
        )
        operation_request = OperationRequest(operation=operation)
        self._execution.create_operation(operation_request)
        grant = self._execution.issue_approval(operation=operation_request, reviewer_id=approved.reviewer, nonce=_id("pcb-operation-approval", approved.fingerprint, operation.fingerprint), expires_at=changeset.expires_at)
        self._execution.enqueue(operation=operation_request, approval_id=grant.grant_id)
        return operation_request

    def enqueue_drc(
        self,
        *,
        write_operation: PCBWriteOperation,
        receipt_fingerprint: str,
        reviewer_id: str,
        parse_evidence: PCBParseValidationEvidence,
    ) -> OperationRequest:
        source = PCBWriteOperation.model_validate(write_operation.model_dump(mode="python"), strict=False)
        checked_parse = PCBParseValidationEvidence.model_validate(
            parse_evidence.model_dump(mode="python")
            if isinstance(parse_evidence, PCBParseValidationEvidence)
            else parse_evidence,
            strict=False,
        )
        if (
            checked_parse.validation_status is not PCBParseValidationStatus.PARSE_VERIFIED
            or checked_parse.project_id != source.project_id
            or checked_parse.conversation_id != source.conversation_id
            or checked_parse.operation_id != source.operation_id
            or checked_parse.artifact_path != source.target_relative_path
            or checked_parse.artifact_fingerprint != source.content_fingerprint
            or not checked_parse.artifact_unchanged
        ):
            raise ValueError("PCB DRC requires a parse-verified current PCB artifact")
        receipt = self._execution.get_receipt(tenant_id=source.tenant_id, project_id=source.project_id, operation_id=source.operation_id)
        if not reviewer_id or receipt is None or receipt.fingerprint != receipt_fingerprint or receipt.operation_type is not OperationType.KICAD_PCB_WRITE or receipt.status.value != "SUCCEEDED" or receipt.pcb_artifact is None:
            raise ValueError("PCB DRC requires a successful PCB write receipt")
        operation_id = _id("kicad-pcb-drc", source.operation_id, receipt_fingerprint, source.content_fingerprint)
        correlation = None
        if source.correlation is not None:
            correlation = CorrelationContext(
                **{
                    **source.correlation.model_dump(mode="python"),
                    "operation_id": operation_id,
                    "approval_id": None,
                }
            )
        existing = self._execution.get_operation(tenant_id=source.tenant_id, project_id=source.project_id, operation_id=operation_id)
        if existing is not None:
            if not isinstance(existing, PcbDrcOperation):
                raise ValueError("PCB DRC operation identity conflict")
            return OperationRequest(operation=existing)
        now = self._clock()
        operation = PcbDrcOperation(
            operation_id=operation_id,
            tenant_id=source.tenant_id,
            project_id=source.project_id,
            requester_id=reviewer_id,
            target_id=source.target_id,
            target_fingerprint=source.target_fingerprint,
            workspace_snapshot_id=source.workspace_snapshot_id,
            workspace_fingerprint=source.workspace_fingerprint,
            proposal_id=source.pcb_proposal_id,
            proposal_fingerprint=source.pcb_proposal_fingerprint,
            operation_type=OperationType.KICAD_PCB_DRC,
            created_at=now,
            expires_at=max(source.expires_at, now + timedelta(minutes=15)),
            nonce=_id("pcb-drc-nonce", source.operation_id, receipt_fingerprint),
            conversation_id=source.conversation_id,
            pcb_artifact_ref=source.target_relative_path,
            pcb_content_fingerprint=source.content_fingerprint,
            source_write_operation_id=source.operation_id,
            source_write_receipt_fingerprint=receipt_fingerprint,
            target_relative_path=source.target_relative_path,
            correlation=correlation,
        )
        request = OperationRequest(operation=operation)
        self._execution.create_operation(request)
        grant = self._execution.issue_approval(operation=request, reviewer_id=reviewer_id, nonce=_id("pcb-drc-approval", operation.fingerprint), expires_at=operation.expires_at)
        self._execution.enqueue(operation=request, approval_id=grant.grant_id)
        return request


__all__ = ("PCBExecutionBridge",)
