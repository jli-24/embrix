from __future__ import annotations

from embedded_copilot.execution_worker.contracts import (
    OperationReceipt,
    OperationRequest,
    OperationStatusView,
    SimulationOperation,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService


class ExecutionOperationAdapter:
    """Narrow simulation-facing projection of the existing operation service."""

    __slots__ = ("_service",)

    def __init__(self, service: ExecutionOperationService) -> None:
        if type(service) is not ExecutionOperationService:
            raise TypeError("execution operation service is invalid")
        self._service = service

    def create(self, operation: SimulationOperation) -> OperationStatusView:
        if type(operation) is not SimulationOperation:
            raise TypeError("simulation operation is invalid")
        self._service.create_operation(OperationRequest(operation=operation))
        status = self.get_status(
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
        )
        if status is None:
            raise RuntimeError("execution operation status unavailable")
        return status

    def get_status(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationStatusView | None:
        return self._service.get_status(
            tenant_id=tenant_id,
            project_id=project_id,
            operation_id=operation_id,
        )

    def get_receipt(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationReceipt | None:
        return self._service.get_receipt(
            tenant_id=tenant_id,
            project_id=project_id,
            operation_id=operation_id,
        )


__all__ = ("ExecutionOperationAdapter",)
