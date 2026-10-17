from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import (
    FunctionalVerificationRecord,
    VerificationRequest,
    VerificationResult,
)


@runtime_checkable
class VerificationPort(Protocol):
    def verify(self, request: VerificationRequest) -> VerificationResult: ...


@runtime_checkable
class FunctionalVerificationStorePort(Protocol):
    """Atomic durable boundary for one functional result and its evidence."""

    def put(self, record: FunctionalVerificationRecord) -> FunctionalVerificationRecord: ...

    def get(self, project_id: str, verification_result_id: str) -> FunctionalVerificationRecord | None: ...


__all__ = ("FunctionalVerificationStorePort", "VerificationPort")
