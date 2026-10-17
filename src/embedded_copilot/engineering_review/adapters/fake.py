from __future__ import annotations

import copy

from ..contracts import (
    validate_human_decision,
    validate_review_id,
    validate_review_record,
    validate_review_request,
)
from ..exceptions import ReviewConflict, ReviewUnavailable
from ..models import HumanDecision, ReviewRecord, ReviewRequest
from ..ports import DecisionAuditSinkPort, ReviewStorePort


class FakeReviewStoreAdapter(ReviewStorePort):
    """Deterministic in-memory review store for offline tests."""

    def __init__(self) -> None:
        self._review_requests: dict[str, ReviewRequest] = {}
        self._decisions: dict[str, HumanDecision] = {}
        self._decision_ids: dict[str, str] = {}
        self._records: dict[str, ReviewRecord] = {}

    def save_review_request(self, request: ReviewRequest) -> ReviewRequest:
        checked = validate_review_request(request)
        existing = self._review_requests.get(checked.review_id)
        if existing is not None and existing != checked:
            raise ReviewConflict()
        self._review_requests[checked.review_id] = checked
        return validate_review_request(copy.deepcopy(checked))

    def get_review_request(self, review_id: str) -> ReviewRequest | None:
        checked_id = validate_review_id(review_id)
        value = self._review_requests.get(checked_id)
        return None if value is None else validate_review_request(copy.deepcopy(value))

    def save_decision(self, decision: HumanDecision) -> HumanDecision:
        checked = validate_human_decision(decision)
        request = self._review_requests.get(checked.review_id)
        prior_review_id = self._decision_ids.get(checked.decision_id)
        existing = self._decisions.get(checked.review_id)
        if (
            request is None
            or request.project_id != checked.project_id
            or checked.decided_at < request.created_at
            or prior_review_id is not None
            and prior_review_id != checked.review_id
            or existing is not None
            and existing != checked
        ):
            raise ReviewConflict()
        self._decision_ids[checked.decision_id] = checked.review_id
        self._decisions[checked.review_id] = checked
        return validate_human_decision(copy.deepcopy(checked))

    def get_decision(self, review_id: str) -> HumanDecision | None:
        checked_id = validate_review_id(review_id)
        value = self._decisions.get(checked_id)
        return None if value is None else validate_human_decision(copy.deepcopy(value))

    def save_record(self, record: ReviewRecord) -> ReviewRecord:
        checked = validate_review_record(record)
        key = checked.review_request.review_id
        request = self._review_requests.get(key)
        decision = self._decisions.get(key)
        existing = self._records.get(key)
        if (
            request != checked.review_request
            or decision != checked.decision
            or existing is not None
            and existing != checked
        ):
            raise ReviewConflict()
        self._records[key] = checked
        return validate_review_record(copy.deepcopy(checked))

    def get_record(self, review_id: str) -> ReviewRecord | None:
        checked_id = validate_review_id(review_id)
        value = self._records.get(checked_id)
        return None if value is None else validate_review_record(copy.deepcopy(value))


class FakeDecisionAuditSinkAdapter(DecisionAuditSinkPort):
    """Record-only audit sink with deterministic failure injection."""

    def __init__(self, *, failures: int = 0) -> None:
        if type(failures) is not int or failures < 0:
            raise TypeError("audit failures is invalid")
        self._failures_remaining = failures
        self._records: dict[str, ReviewRecord] = {}
        self.attempt_count = 0

    def record_decision(self, record: ReviewRecord) -> ReviewRecord:
        checked = validate_review_record(record)
        self.attempt_count += 1
        if self._failures_remaining:
            self._failures_remaining -= 1
            raise ReviewUnavailable()
        key = checked.review_request.review_id
        existing = self._records.get(key)
        if existing is not None and existing != checked:
            raise ReviewConflict()
        self._records[key] = checked
        return validate_review_record(copy.deepcopy(checked))

    def get_record(self, review_id: str) -> ReviewRecord | None:
        checked_id = validate_review_id(review_id)
        value = self._records.get(checked_id)
        return None if value is None else validate_review_record(copy.deepcopy(value))


__all__ = ("FakeDecisionAuditSinkAdapter", "FakeReviewStoreAdapter")
