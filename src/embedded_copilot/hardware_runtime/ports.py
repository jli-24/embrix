from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    HardwareAuditEvent,
    HardwareBuildReceipt,
    HardwareBuildRequest,
    HardwareCapability,
    HardwareFlashReceipt,
    HardwareFlashRequest,
    HardwareObservation,
    HardwareObservationRequest,
    HardwareOperationPlan,
    HardwareResourceGrant,
    HardwareReviewRecord,
    HardwareReviewRequest,
    HardwareTargetReference,
)


@runtime_checkable
class HardwareTargetSourcePort(Protocol):
    def get_target(
        self, project_id: str, device_id: str
    ) -> HardwareTargetReference | None: ...


@runtime_checkable
class HardwareResourcePolicyPort(Protocol):
    def authorize(
        self, request: HardwareObservationRequest, capability: HardwareCapability
    ) -> HardwareResourceGrant: ...


@runtime_checkable
class HardwareBuildPort(Protocol):
    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt: ...


@runtime_checkable
class HardwareFlashPort(Protocol):
    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt: ...


@runtime_checkable
class HardwareMeasurementPort(Protocol):
    def observe(self, request: HardwareObservationRequest) -> HardwareObservation: ...


@runtime_checkable
class HardwareReviewPort(Protocol):
    def submit(self, request: HardwareReviewRequest) -> HardwareReviewRecord: ...

    def get_record(self, review_id: str) -> HardwareReviewRecord | None: ...


@runtime_checkable
class HardwareAuditSinkPort(Protocol):
    def append(self, event: HardwareAuditEvent) -> HardwareAuditEvent: ...

    def get(self, event_id: str) -> HardwareAuditEvent | None: ...


@runtime_checkable
class HardwareClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class HardwareOperationPlanStorePort(Protocol):
    def save(self, plan: HardwareOperationPlan) -> HardwareOperationPlan: ...

    def get(self, plan_id: str) -> HardwareOperationPlan | None: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
