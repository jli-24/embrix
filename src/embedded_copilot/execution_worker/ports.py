from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    ArtifactIdentity,
    BuildEvidence,
    BuildOperation,
    ExecutionTarget,
    FlashOperation,
    HardwareBringupOperation,
    HilOperation,
    KiCadManufacturingOperation,
    OperationReceipt,
    OperationRequest,
    NativeMoveProductionResultV1,
    SimulationOperation,
    TestOperation,
    UartObservationOperation,
)
from embedded_copilot.firmware_engineering import FirmwareBuildResult
from .registry import ToolResolution
from embedded_copilot.pcb_production import PCBArtifactRecord, PCBDrcResult


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
    workspace_apply_receipt: object | None = None
    schematic_erc_result: object | None = None
    pcb_artifact: PCBArtifactRecord | None = None
    pcb_drc_result: PCBDrcResult | None = None
    build_result: FirmwareBuildResult | None = None
    build_evidence: BuildEvidence | None = None
    additional_artifacts: tuple[tuple[ArtifactIdentity, bytes], ...] = ()
    schematic_xml_result: object | None = None
    native_move_production: NativeMoveProductionResultV1 | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "SUCCEEDED",
            "FAILED",
            "UNAVAILABLE",
            "CANCELLED",
            "TIMED_OUT",
        }:
            raise ValueError("adapter result status is invalid")
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > 1024:
            raise ValueError("adapter result summary is invalid")
        if not isinstance(self.evidence_references, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.evidence_references
        ):
            raise ValueError("adapter result evidence references are invalid")
        if self.artifact_content is not None and not isinstance(self.artifact_content, bytes):
            raise TypeError("adapter result artifact content is invalid")
        if not isinstance(self.additional_artifacts, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], ArtifactIdentity)
            or not isinstance(item[1], bytes)
            for item in self.additional_artifacts
        ):
            raise ValueError("adapter result additional artifacts are invalid")


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
class HardwareBringupAdapterPort(Protocol):
    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class TestAdapterPort(Protocol):
    def execute(self, operation: TestOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class SimulationAdapterPort(Protocol):
    def execute(self, operation: SimulationOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class UartAdapterPort(Protocol):
    def execute(self, operation: UartObservationOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class HilAdapterPort(Protocol):
    def execute(self, operation: HilOperation, context: ExecutionContext) -> AdapterResult: ...


@runtime_checkable
class ManufacturingAdapterPort(Protocol):
    def execute(self, operation: KiCadManufacturingOperation, context: ExecutionContext) -> AdapterResult: ...


__all__ = (
    "AdapterResult",
    "BuildAdapterPort",
    "ExecutionContext",
    "FlashAdapterPort",
    "HardwareBringupAdapterPort",
    "HilAdapterPort",
    "ManufacturingAdapterPort",
    "OperationAdapterPort",
    "SimulationAdapterPort",
    "TargetRegistryPort",
    "TestAdapterPort",
    "UartAdapterPort",
    "WorkspaceResolverPort",
)
