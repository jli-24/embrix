from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from ..contracts import identifier, utc_datetime
from ..exceptions import CapabilityAuditConflict, CapabilityAuditUnavailable
from ..models import (
    AgentCapabilityRequest,
    CapabilityAsyncMode,
    CapabilityAuditEvent,
    CapabilityBindingReference,
    CapabilityOutput,
    CapabilityPolicyDecision,
    CapabilityPolicyGrant,
    CapabilityRouteDecision,
    CapabilityType,
    validate_capability_output,
)
from ..policies import StaticCapabilityPolicy
from ..registry import CapabilityRegistry


def _clone(value):
    return type(value).model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FakeCapabilityBinding:
    def __init__(
        self,
        *,
        capability: CapabilityType,
        output: CapabilityOutput,
        async_mode: CapabilityAsyncMode = CapabilityAsyncMode.THREAD_BRIDGE,
        failure: Exception | None = None,
        cancel_async: bool = False,
    ) -> None:
        checked_output = validate_capability_output(output)
        if checked_output.capability is not capability:
            raise ValueError("fake capability output mismatch")
        self._reference = CapabilityBindingReference(
            capability=capability,
            binding_id=f"fake-{capability.value.casefold()}",
            binding_version="v4.3.2",
            async_mode=async_mode,
        )
        self._output = checked_output
        self._failure = failure
        self._cancel_async = cancel_async
        self.call_count = 0

    @property
    def reference(self) -> CapabilityBindingReference:
        return _clone(self._reference)

    def invoke(self, request: AgentCapabilityRequest) -> CapabilityOutput:
        AgentCapabilityRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        self.call_count += 1
        if self._failure is not None:
            raise self._failure
        return validate_capability_output(self._output)

    async def ainvoke(self, request: AgentCapabilityRequest) -> CapabilityOutput:
        if self._cancel_async:
            raise asyncio.CancelledError
        return self.invoke(request)


class FakeCapabilityPolicy:
    def __init__(
        self,
        grants: tuple[CapabilityPolicyGrant, ...],
        *,
        failure: Exception | None = None,
    ) -> None:
        self._policy = StaticCapabilityPolicy(grants)
        self._failure = failure

    def evaluate(
        self, request: AgentCapabilityRequest, route: CapabilityRouteDecision
    ) -> CapabilityPolicyDecision:
        if self._failure is not None:
            raise self._failure
        return self._policy.evaluate(request, route)


class FakeCapabilityRegistry:
    def __init__(
        self, bindings: tuple[FakeCapabilityBinding, ...], *, fail_reads: bool = False
    ) -> None:
        self._registry = CapabilityRegistry(bindings)
        self._fail_reads = fail_reads

    def resolve(self, capability: CapabilityType):
        if self._fail_reads:
            raise RuntimeError("fake registry unavailable")
        return self._registry.resolve(capability)


class FakeCapabilityAuditSink:
    def __init__(self, *, fail_writes: bool = False) -> None:
        self._events: dict[str, CapabilityAuditEvent] = {}
        self._fail_writes = fail_writes

    @property
    def events(self) -> tuple[CapabilityAuditEvent, ...]:
        return tuple(_clone(item) for item in self._events.values())

    def append(self, event: CapabilityAuditEvent) -> CapabilityAuditEvent:
        if self._fail_writes:
            raise CapabilityAuditUnavailable()
        checked = _clone(event)
        existing = self._events.get(checked.event_id)
        if existing is not None and existing != checked:
            raise CapabilityAuditConflict()
        self._events.setdefault(checked.event_id, checked)
        return _clone(self._events[checked.event_id])

    def get(self, event_id: str) -> CapabilityAuditEvent | None:
        checked_id = identifier(event_id, field="event_id")
        found = self._events.get(checked_id)
        return None if found is None else _clone(found)


class FixedCapabilityClock:
    def __init__(self, value: datetime) -> None:
        self._value = utc_datetime(value, field="clock_value")

    def now(self) -> datetime:
        return self._value


__all__ = (
    "FakeCapabilityAuditSink",
    "FakeCapabilityBinding",
    "FakeCapabilityPolicy",
    "FakeCapabilityRegistry",
    "FixedCapabilityClock",
)
