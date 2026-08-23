from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    ArtifactIdentity,
    BuildOperation,
    ExecutionTarget,
    FlashOperation,
    HilOperation,
    OperationReceipt,
    OperationRequest,
    TestOperation,
    UartObservationOperation,
)
from .registry import ToolResolution


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    workspace_root: Path
    state_root: Path
    target: ExecutionTarget
    tool: ToolResolution | None
    cancellation: object
    artifact_store: object | None = None
    target_connection: object | None = None


@dataclass(frozen=True, slots=True)
class AdapterResult:
    status: str
    summary: str
    artifact: ArtifactIdentity | None = None
    artifact_content: bytes | None = None
    flash_verification_status: str = "NOT_APPLICABLE"
    evidence_references: tuple[str, ...] = ()


@runtime_checkable
class WorkspaceResolverPort(Protocol):
    def resolve(self, *, tenant_id: str, project_id: str, snapshot_id: str, snapshot_fingerprint: str) -> Path: ...


@runtime_checkable
class TargetRegistryPort(Protocol):
    def get_bound(self, *, tenant_id: str, project_id: str, target_id: str) -> ExecutionTarget | None: ...
    def revalidate(self, target: ExecutionTarget) -> ExecutionTarget: ...


@runtime_checkable
class OperationAdapterPort(Protocol):
    def execute(self, operation: OperationRequest, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class BuildAdapterPort(Protocol):
    def execute(self, operation: BuildOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class FlashAdapterPort(Protocol):
    def execute(self, operation: FlashOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class TestAdapterPort(Protocol):
    def execute(self, operation: TestOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class UartAdapterPort(Protocol):
    def execute(self, operation: UartObservationOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class HilAdapterPort(Protocol):
    def execute(self, operation: HilOperation, context: ExecutionContext) -> AdapterResult: ...


__all__ = (
    "AdapterResult",
    "BuildAdapterPort",
    "ExecutionContext",
    "FlashAdapterPort",
    "HilAdapterPort",
    "OperationAdapterPort",
    "TargetRegistryPort",
    "TestAdapterPort",
    "UartAdapterPort",
    "WorkspaceResolverPort",
)
