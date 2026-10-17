from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import ReviewTarget
from .models import (
    HumanDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewTargetType,
)


@runtime_checkable
class ReviewTargetSourcePort(Protocol):
    def get_target(
        self,
        project_id: str,
        target_type: ReviewTargetType,
        target_id: str,
    ) -> ReviewTarget | None: ...


@runtime_checkable
class ReviewStorePort(Protocol):
    def save_review_request(self, request: ReviewRequest) -> ReviewRequest: ...

    def get_review_request(self, review_id: str) -> ReviewRequest | None: ...

    def save_decision(self, decision: HumanDecision) -> HumanDecision: ...

    def get_decision(self, review_id: str) -> HumanDecision | None: ...

    def save_record(self, record: ReviewRecord) -> ReviewRecord: ...

    def get_record(self, review_id: str) -> ReviewRecord | None: ...


@runtime_checkable
class DecisionAuditSinkPort(Protocol):
    def record_decision(self, record: ReviewRecord) -> ReviewRecord: ...


__all__ = (
    "DecisionAuditSinkPort",
    "ReviewStorePort",
    "ReviewTargetSourcePort",
)

