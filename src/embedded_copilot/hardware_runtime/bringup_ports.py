from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from embedded_copilot.execution_worker.contracts import HardwareBringupOperation
from embedded_copilot.execution_worker.ports import ExecutionContext

from .bringup_contracts import HardwareEvidence, HardwareToolResult


@runtime_checkable
class HardwareBringupBackendPort(Protocol):
    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> HardwareToolResult: ...


@runtime_checkable
class HardwareEvidenceStorePort(Protocol):
    def save(self, value: HardwareEvidence) -> HardwareEvidence: ...

    def get(self, evidence_id: str) -> HardwareEvidence | None: ...


@runtime_checkable
class HardwareCommandRunnerPort(Protocol):
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, cancellation: object) -> tuple[int, bytes, bytes]: ...


@runtime_checkable
class HardwareUartTransportPort(Protocol):
    def read(self, port_binding: str, baud_rate: int, *, duration_seconds: int, max_bytes: int, cancellation: object) -> bytes: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
