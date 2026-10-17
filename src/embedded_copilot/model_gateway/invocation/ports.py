from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..governance import (
    BoundModelIdentity,
    ModelSelectionResult,
    ModelSelectionTaskProfile,
)
from .contracts import (
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationResult,
    TaskAuthorityReference,
)


@runtime_checkable
class ModelInvocationPort(Protocol):
    def invoke(self, request: ModelInvocationRequest, *, authority_source: TaskAuthorityPort | None = None) -> ModelInvocationResult: ...

    async def ainvoke(self, request: ModelInvocationRequest, *, authority_source: TaskAuthorityPort | None = None) -> ModelInvocationResult: ...


@runtime_checkable
class TaskAuthorityPort(Protocol):
    """Caller-bound read-only source that independently revalidates current Task authority."""

    def revalidate(self, reference: TaskAuthorityReference) -> TaskAuthorityReference: ...


@runtime_checkable
class ModelInvocationSelectionPort(Protocol):
    def select_governed_model(
        self,
        task_profile: ModelSelectionTaskProfile,
        *,
        request_override: BoundModelIdentity | None = None,
    ) -> ModelSelectionResult: ...


@runtime_checkable
class ModelInvocationReceiptPort(Protocol):
    def append(self, receipt: ModelInvocationReceipt) -> ModelInvocationReceipt: ...


__all__ = (
    "ModelInvocationPort",
    "ModelInvocationReceiptPort",
    "ModelInvocationSelectionPort",
    "TaskAuthorityPort",
)


@runtime_checkable
class TrustedInvocationBudgetPort(Protocol):
    """Host-only assembly seam. No public request can supply this port or qualification."""

    trusted_host_identity: str

    def prepare(self, *, request, snapshot, bound, protocol, payload: bytes, parameter: str, wire_target: tuple, now): ...

    async def aprepare(self, *, request, snapshot, bound, protocol, payload: bytes, parameter: str, wire_target: tuple, now): ...

    def qualify(self, *, envelope, evidence, request, snapshot, bound, protocol, payload: bytes, parameter: str, wire_target: tuple, now): ...


@runtime_checkable
class TrustedCounterBudgetPort(Protocol):
    """Explicit host-only two-role qualification; it never performs transport."""

    trusted_host_identity: str

    def prepare_counter(self, **kwargs): ...

    def qualify_counter(self, *, envelope, admission, **kwargs): ...

    def prepare_generation(self, *, envelope, admission, observation, **kwargs): ...

    def qualify_generation(self, *, envelope, admission, observation, evidence, **kwargs): ...
