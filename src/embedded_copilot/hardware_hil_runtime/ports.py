from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.hardware_runtime import (
    FirmwareArtifactReference,
    HardwareTargetReference,
)

from .models import (
    EngineeringProposalReference,
    HardwareApprovalRecord,
    HardwareApprovalRequest,
    HardwareHILContextOverlay,
    HardwareObservation,
    HardwareObservationRequest,
    HILAuditEvent,
    HILExecutionReceipt,
    HILExecutionRequest,
    HILMemoryCandidateReference,
)


@runtime_checkable
class HardwareTargetPort(Protocol):
    def get_target(
        self, project_id: str, device_id: str
    ) -> HardwareTargetReference | None: ...


@runtime_checkable
class FirmwareArtifactPort(Protocol):
    def get_firmware(
        self, project_id: str, artifact_id: str
    ) -> FirmwareArtifactReference | None: ...


@runtime_checkable
class EngineeringProposalSourcePort(Protocol):
    def get_proposal(
        self, reference: EngineeringProposalReference
    ) -> EngineeringProposalReference | None: ...


@runtime_checkable
class HardwareApprovalPort(Protocol):
    def submit(self, request: HardwareApprovalRequest) -> HardwareApprovalRequest: ...
    def get_record(self, approval_id: str) -> HardwareApprovalRecord | None: ...


@runtime_checkable
class HILExecutionPort(Protocol):
    def execute(self, request: HILExecutionRequest) -> HILExecutionReceipt: ...


@runtime_checkable
class MeasurementPort(Protocol):
    def collect(self, request: HardwareObservationRequest) -> HardwareObservation: ...


@runtime_checkable
class ObservationSinkPort(Protocol):
    def append(self, observation: HardwareObservation) -> HardwareObservation: ...
    def get(self, observation_id: str) -> HardwareObservation | None: ...


@runtime_checkable
class HILContextProjectionPort(Protocol):
    def project(
        self, overlay: HardwareHILContextOverlay
    ) -> HardwareHILContextOverlay: ...


@runtime_checkable
class HILMemoryCandidatePort(Protocol):
    def create(
        self, overlay: HardwareHILContextOverlay
    ) -> tuple[HILMemoryCandidateReference, ...]: ...


@runtime_checkable
class HardwareAuditPort(Protocol):
    def append(self, event: HILAuditEvent) -> HILAuditEvent: ...
    def get(self, event_id: str) -> HILAuditEvent | None: ...


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
