from __future__ import annotations

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService

from .ports import AdapterResult
from .contracts import SchematicWriteOperation


class SchematicWriteAdapter:
    """Canonical adapter: delegates the approved schematic ChangeSet to WorkspaceApplyService."""

    def __init__(self, workspace: WorkspaceApplyService) -> None:
        if not isinstance(workspace, WorkspaceApplyService):
            raise TypeError("canonical workspace apply service is required")
        self._workspace = workspace

    def execute(self, operation: SchematicWriteOperation, context=None) -> AdapterResult:
        checked = SchematicWriteOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        receipt = self._workspace.apply(
            changeset=checked.changeset,
            approval=checked.workspace_approval,
            operation_id=checked.operation_id,
        )
        status = receipt.status.value
        return AdapterResult(
            status="SUCCEEDED" if status in {"APPLIED", "RECOVERED"} else "FAILED",
            summary="KiCad schematic workspace write applied." if status in {"APPLIED", "RECOVERED"} else "KiCad schematic workspace write failed closed.",
            workspace_apply_receipt=receipt,
        )


__all__ = ("SchematicWriteAdapter",)
