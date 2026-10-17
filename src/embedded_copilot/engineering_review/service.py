from __future__ import annotations

import copy

from .contracts import (
    target_identity,
    validate_human_decision,
    validate_review_id,
    validate_review_record,
    validate_review_request,
    validate_target,
)
from .exceptions import (
    EngineeringReviewError,
    ReviewConflict,
    ReviewNotFound,
    ReviewRejected,
    ReviewTargetNotFound,
    ReviewUnavailable,
)
from .models import HumanDecision, ReviewRecord, ReviewRequest
from .ports import DecisionAuditSinkPort, ReviewStorePort, ReviewTargetSourcePort


class ReviewService:
    __slots__ = ("_audit_sink", "_source", "_store")

    def __init__(
        self,
        *,
        source: ReviewTargetSourcePort,
        store: ReviewStorePort,
        audit_sink: DecisionAuditSinkPort,
    ) -> None:
        if not isinstance(source, ReviewTargetSourcePort):
            raise TypeError("review target source is invalid")
        if not isinstance(store, ReviewStorePort):
            raise TypeError("review store is invalid")
        if not isinstance(audit_sink, DecisionAuditSinkPort):
            raise TypeError("decision audit sink is invalid")
        self._source = source
        self._store = store
        self._audit_sink = audit_sink

    def create_review(self, request: ReviewRequest) -> ReviewRequest:
        try:
            checked = validate_review_request(request)
        except Exception as error:
            raise ReviewRejected() from error

        existing = self._get_review_request(checked.review_id)
        if existing is not None:
            raise ReviewConflict()

        self._verify_target(checked)
        try:
            stored = validate_review_request(
                self._store.save_review_request(copy.deepcopy(checked))
            )
        except ReviewConflict:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error
        if stored != checked:
            raise ReviewUnavailable()
        return stored

    def decide(self, review_id: str, decision: HumanDecision) -> ReviewRecord:
        try:
            checked_id = validate_review_id(review_id)
            checked_decision = validate_human_decision(decision)
        except Exception as error:
            raise ReviewRejected() from error

        request = self._get_review_request(checked_id)
        if request is None:
            raise ReviewNotFound()
        if (
            checked_decision.review_id != checked_id
            or checked_decision.project_id != request.project_id
            or checked_decision.decided_at < request.created_at
        ):
            raise ReviewRejected()

        existing_decision = self._get_decision(checked_id)
        existing_record = self._get_record(checked_id)
        expected_record = ReviewRecord.create(
            review_request=request, decision=checked_decision
        )
        if existing_decision is not None or existing_record is not None:
            if existing_decision != checked_decision:
                raise ReviewConflict()
            if existing_record is not None and existing_record != expected_record:
                raise ReviewConflict()
            if existing_record is not None:
                return self._record_audit(existing_record)

        self._verify_target(request)
        try:
            stored_decision = validate_human_decision(
                self._store.save_decision(copy.deepcopy(checked_decision))
            )
        except ReviewConflict:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error
        if stored_decision != checked_decision:
            raise ReviewUnavailable()

        record = ReviewRecord.create(
            review_request=request, decision=stored_decision
        )
        try:
            stored_record = validate_review_record(
                self._store.save_record(copy.deepcopy(record))
            )
        except ReviewConflict:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error
        if stored_record != record:
            raise ReviewUnavailable()
        return self._record_audit(stored_record)

    def _verify_target(self, request: ReviewRequest) -> None:
        reference = request.target_reference
        try:
            value = self._source.get_target(
                copy.deepcopy(request.project_id),
                reference.target_type,
                copy.deepcopy(reference.target_id),
            )
        except Exception as error:
            raise ReviewUnavailable() from error
        if value is None:
            raise ReviewTargetNotFound()
        try:
            checked = validate_target(value, reference.target_type)
        except TypeError as error:
            raise ReviewRejected() from error
        except Exception as error:
            raise ReviewUnavailable() from error
        project_id, target_id, target_fingerprint = target_identity(
            checked, reference.target_type
        )
        if (
            project_id != request.project_id
            or target_id != reference.target_id
            or target_fingerprint != reference.target_fingerprint
        ):
            raise ReviewRejected()

    def _get_review_request(self, review_id: str) -> ReviewRequest | None:
        try:
            value = self._store.get_review_request(copy.deepcopy(review_id))
            if value is None:
                return None
            checked = validate_review_request(value)
            if checked.review_id != review_id:
                raise ReviewUnavailable()
            return checked
        except EngineeringReviewError:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error

    def _get_decision(self, review_id: str) -> HumanDecision | None:
        try:
            value = self._store.get_decision(copy.deepcopy(review_id))
            if value is None:
                return None
            checked = validate_human_decision(value)
            if checked.review_id != review_id:
                raise ReviewUnavailable()
            return checked
        except EngineeringReviewError:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error

    def _get_record(self, review_id: str) -> ReviewRecord | None:
        try:
            value = self._store.get_record(copy.deepcopy(review_id))
            if value is None:
                return None
            checked = validate_review_record(value)
            if checked.review_request.review_id != review_id:
                raise ReviewUnavailable()
            return checked
        except EngineeringReviewError:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error

    def _record_audit(self, record: ReviewRecord) -> ReviewRecord:
        try:
            audited = validate_review_record(
                self._audit_sink.record_decision(copy.deepcopy(record))
            )
        except ReviewConflict:
            raise
        except Exception as error:
            raise ReviewUnavailable() from error
        if audited != record:
            raise ReviewUnavailable()
        return audited


__all__ = ("ReviewService",)
