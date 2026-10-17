from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from embedded_copilot.execution_worker import ExecutionOperationService, ExecutionWorker
from embedded_copilot.execution_worker.contracts import (
    KiCadManufacturingOperation,
    OperationRequest,
    OperationStatusView,
    OperationType,
)

from .contracts import FabricationSourceRevision, ManufacturingSpecification
from .entry_gate import ManufacturingEntryGateReceipt

_TOOL_BY_KIND = {
    "GERBER": (OperationType.KICAD_GERBER_EXPORT, "KICAD_GERBER_EXPORT"),
    "DRILL": (OperationType.KICAD_DRILL_EXPORT, "KICAD_DRILL_EXPORT"),
    "POSITION": (OperationType.KICAD_POSITION_EXPORT, "KICAD_POSITION_EXPORT"),
    "PACKAGE": (OperationType.MANUFACTURING_PACKAGE_BUILD, "MANUFACTURING_PACKAGE_BUILD"),
}


class ManufacturingExecutionBridge:
    """Create and submit only typed manufacturing operations."""

    def __init__(self, *, operations: ExecutionOperationService, worker: ExecutionWorker) -> None:
        self._operations = operations
        self._worker = worker

    @staticmethod
    def build_operation(
        *,
        source: FabricationSourceRevision,
        specification: ManufacturingSpecification,
        tenant_id: str,
        requester_id: str,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        conversation_id: str,
        export_kind: str,
        source_pcb_relative_path: str,
        output_workspace_relative: str,
        layer_list: tuple[str, ...] = (),
        operation_id: str | None = None,
        nonce: str | None = None,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
        entry_gate_receipt: ManufacturingEntryGateReceipt | None = None,
    ) -> OperationRequest:
        try:
            operation_type, tool_id = _TOOL_BY_KIND[export_kind.upper()]
        except KeyError:
            raise ValueError("manufacturing export kind is unsupported") from None
        timestamp = (created_at or datetime.now(UTC)).astimezone(UTC)
        return OperationRequest(
            operation=KiCadManufacturingOperation(
                operation_id=operation_id or f"manufacturing-operation-{uuid4().hex}",
                tenant_id=tenant_id,
                project_id=source.project_id,
                requester_id=requester_id,
                target_id=target_id,
                target_fingerprint=target_fingerprint,
                workspace_snapshot_id=workspace_snapshot_id,
                workspace_fingerprint=workspace_fingerprint,
                proposal_id=proposal_id,
                proposal_fingerprint=proposal_fingerprint,
                operation_type=operation_type,
                created_at=timestamp,
                expires_at=(expires_at or timestamp + timedelta(minutes=15)).astimezone(UTC),
                nonce=nonce or uuid4().hex,
                conversation_id=conversation_id,
                tool_id=tool_id,
                tool_version="10.0.5" if export_kind.upper() != "PACKAGE" else "1",
                export_kind=export_kind.upper(),
                project_revision=source.project_revision,
                pcb_artifact_id=source.pcb_artifact_id,
                pcb_sha256=source.pcb_sha256,
                manufacturing_specification_id=specification.specification_id,
                manufacturing_specification_fingerprint=specification.fingerprint,
                pcb_state_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.pcb_state_fingerprint),
                g7_summary_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.summary_fingerprint),
                ready_for_fab_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.ready_for_fab_fingerprint),
                ready_for_fab_target_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.ready_for_fab_target_fingerprint),
                human_approval_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.approval_fingerprint),
                manufacturer_profile_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.manufacturer_profile_fingerprint),
                dfm_fingerprint=(None if entry_gate_receipt is None else entry_gate_receipt.dfm_fingerprint),
                source_pcb_relative_path=source_pcb_relative_path,
                output_workspace_relative=output_workspace_relative,
                layer_list=layer_list,
            )
        )

    def submit_approved(
        self,
        request: OperationRequest,
        *,
        reviewer_id: str,
        approval_nonce: str,
        expires_at: datetime,
        run_worker: bool = True,
    ) -> OperationStatusView:
        self._operations.create_operation(request)
        approval = self._operations.issue_approval(
            operation=request,
            reviewer_id=reviewer_id,
            nonce=approval_nonce,
            expires_at=expires_at,
        )
        queued = self._operations.enqueue(operation=request, approval_id=approval.grant_id)
        if not run_worker:
            return queued
        completed = self._worker.run_once()
        if completed is None:
            raise RuntimeError("manufacturing operation was not claimed")
        return completed


__all__ = ("ManufacturingExecutionBridge",)
