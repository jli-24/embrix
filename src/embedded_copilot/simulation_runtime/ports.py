from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from embedded_copilot.execution_worker.contracts import (
    OperationReceipt,
    OperationStatusView,
    SimulationOperation,
)

from .contracts import (
    PreparedSimulation,
    SimulationBackendCapability,
    SimulationEvidence,
    SimulationRequest,
    SimulationResult,
)


@dataclass(frozen=True, slots=True)
class SimulationAdapterExecutionContext:
    operation_id: str
    cancellation: object
    workspace_root: Path | None = None
    state_root: Path | None = None
    artifact_store: object | None = None


@runtime_checkable
class SimulationAdapterPort(Protocol):
    def probe(self) -> SimulationBackendCapability: ...

    def prepare(self, request: SimulationRequest) -> PreparedSimulation: ...

    def execute(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> SimulationResult: ...

    def cleanup(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> None: ...


@runtime_checkable
class SimulationOperationPort(Protocol):
    def create(self, operation: SimulationOperation) -> OperationStatusView: ...

    def get_status(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationStatusView | None: ...

    def get_receipt(
        self, *, tenant_id: str, project_id: str, operation_id: str
    ) -> OperationReceipt | None: ...


@runtime_checkable
class PreparedSimulationStorePort(Protocol):
    def put(self, key: str, value: PreparedSimulation) -> PreparedSimulation: ...

    def take(self, key: str) -> PreparedSimulation | None: ...


@runtime_checkable
class SimulationResultStorePort(Protocol):
    def put(self, operation_id: str, value: SimulationResult) -> SimulationResult: ...

    def get(self, operation_id: str) -> SimulationResult | None: ...


@runtime_checkable
class SimulationEvidenceStorePort(Protocol):
    def put(self, evidence: SimulationEvidence) -> SimulationEvidence: ...

    def get(self, evidence_id: str) -> SimulationEvidence | None: ...


@runtime_checkable
class SimulationClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = (
    "PreparedSimulationStorePort",
    "SimulationAdapterExecutionContext",
    "SimulationAdapterPort",
    "SimulationClockPort",
    "SimulationEvidenceStorePort",
    "SimulationOperationPort",
    "SimulationResultStorePort",
)
