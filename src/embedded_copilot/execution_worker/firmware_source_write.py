from __future__ import annotations

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService

from .contracts import FirmwareSourceWriteOperation, OperationRequest
from .ports import AdapterResult


class FirmwareSourceWriteAdapter:
    """Delegates source mutation to the one canonical Workspace apply service."""

    def __init__(self, workspace: WorkspaceApplyService) -> None:
        if not isinstance(workspace, WorkspaceApplyService):
            raise TypeError("canonical workspace apply service is required")
        self._workspace = workspace

    def execute(self, operation: FirmwareSourceWriteOperation) -> AdapterResult:
        checked = OperationRequest(operation=operation).operation
        if not isinstance(checked, FirmwareSourceWriteOperation):
            raise ValueError("firmware source write operation is required")
        receipt = self._workspace.apply(
            changeset=checked.changeset,
            approval=checked.workspace_approval,
            operation_id=checked.operation_id,
        )
        if receipt.status.value != "APPLIED" or receipt.validation_status.value != "PASSED":
            return AdapterResult(
                "FAILED",
                "canonical workspace apply did not complete",
                workspace_apply_receipt=receipt,
            )
        return AdapterResult(
            "SUCCEEDED",
            "firmware source files created through canonical workspace apply",
            workspace_apply_receipt=receipt,
        )


__all__ = ("FirmwareSourceWriteAdapter",)
