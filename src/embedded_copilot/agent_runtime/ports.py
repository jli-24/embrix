from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    AgentCapabilityRequest,
    CapabilityAuditEvent,
    CapabilityBindingReference,
    CapabilityOutput,
    CapabilityPolicyDecision,
    CapabilityRouteDecision,
    CapabilityType,
)


@runtime_checkable
class CapabilityBindingPort(Protocol):
    @property
    def reference(self) -> CapabilityBindingReference: ...

    def invoke(self, request: AgentCapabilityRequest) -> CapabilityOutput: ...

    async def ainvoke(self, request: AgentCapabilityRequest) -> CapabilityOutput: ...


@runtime_checkable
class CapabilityPolicyPort(Protocol):
    def evaluate(
        self, request: AgentCapabilityRequest, route: CapabilityRouteDecision
    ) -> CapabilityPolicyDecision: ...


@runtime_checkable
class CapabilityRegistryPort(Protocol):
    def resolve(self, capability: CapabilityType) -> CapabilityBindingPort | None: ...


@runtime_checkable
class CapabilityAuditSinkPort(Protocol):
    def append(self, event: CapabilityAuditEvent) -> CapabilityAuditEvent: ...

    def get(self, event_id: str) -> CapabilityAuditEvent | None: ...


@runtime_checkable
class CapabilityClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
