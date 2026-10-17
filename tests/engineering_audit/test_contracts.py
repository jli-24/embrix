from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_audit import (
    AuditDecision,
    AuditEvent,
    AuditEventType,
    AuditSourceType,
    EvidenceCoverageStatus,
    ReviewRecordReference,
    RiskAuditRequest,
)
from embedded_copilot.engineering_review import ReviewTargetType

from ._factories import (
    NOW,
    audit_event,
    audit_trail,
    digest,
    evidence_bundle,
    review_record,
    review_reference,
)


@pytest.mark.parametrize("event_type", tuple(AuditEventType))
def test_all_declared_audit_event_types_are_supported(
    event_type: AuditEventType,
) -> None:
    value = audit_event(event_type=event_type)
    assert value.event_type is event_type


@pytest.mark.parametrize("source_type", tuple(AuditSourceType))
def test_all_declared_source_types_are_supported(
    source_type: AuditSourceType,
) -> None:
    record = review_record(target_type=ReviewTargetType(source_type.value))
    assert evidence_bundle(record).source_type is source_type


@pytest.mark.parametrize(
    ("event_type", "decision"),
    (
        (AuditEventType.REVIEW_CREATED, AuditDecision.PENDING),
        (AuditEventType.REVIEW_APPROVED, AuditDecision.APPROVE),
        (AuditEventType.REVIEW_REJECTED, AuditDecision.REJECT),
        (AuditEventType.REVIEW_DEFERRED, AuditDecision.REQUEST_CHANGE),
        (AuditEventType.RISK_IDENTIFIED, AuditDecision.RISK_IDENTIFIED),
        (AuditEventType.RISK_REMAINED, AuditDecision.RISK_REMAINED),
    ),
)
def test_event_type_has_exact_decision_mapping(
    event_type: AuditEventType, decision: AuditDecision
) -> None:
    assert audit_event(event_type=event_type).decision is decision


@pytest.mark.parametrize(
    "factory",
    (
        review_reference,
        evidence_bundle,
        audit_event,
        audit_trail,
    ),
)
def test_core_contracts_are_frozen(factory) -> None:
    value = factory()
    field = next(iter(type(value).model_fields))
    with pytest.raises((TypeError, ValidationError)):
        setattr(value, field, getattr(value, field))


def test_contracts_are_strict_and_forbid_extra_fields() -> None:
    event = audit_event()
    with pytest.raises(ValidationError):
        AuditEvent.model_validate(
            {**event.model_dump(mode="python"), "event_type": "REVIEW_CREATED"}
        )
    with pytest.raises(ValidationError):
        AuditEvent.model_validate(
            {**event.model_dump(mode="python"), "unexpected": "value"}
        )


def test_review_reference_requires_valid_fingerprint() -> None:
    with pytest.raises((ValueError, ValidationError)):
        ReviewRecordReference.create(review_id="review-1", fingerprint="invalid")


def test_review_reference_is_nfc_normalized_and_trimmed() -> None:
    value = ReviewRecordReference.create(
        review_id=" review-Cafe\u0301 ", fingerprint=digest("review")
    )
    assert value.review_id == "review-Caf\u00e9"


@pytest.mark.parametrize(
    ("status", "refs"),
    (
        (EvidenceCoverageStatus.COMPLETE, ("evidence://rule",)),
        (EvidenceCoverageStatus.MISSING, ()),
    ),
)
def test_evidence_bundle_status_matches_engineering_references(
    status: EvidenceCoverageStatus, refs: tuple[str, ...]
) -> None:
    value = evidence_bundle(status=status, evidence_refs=refs)
    assert value.status is status
    assert value.evidence_refs == refs


@pytest.mark.parametrize(
    ("status", "refs"),
    (
        (EvidenceCoverageStatus.COMPLETE, ()),
        (EvidenceCoverageStatus.MISSING, ("evidence://rule",)),
    ),
)
def test_evidence_bundle_rejects_inconsistent_coverage(
    status: EvidenceCoverageStatus, refs: tuple[str, ...]
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        evidence_bundle(status=status, evidence_refs=refs)


def test_evidence_refs_are_tuple_only_unique_and_sorted() -> None:
    value = evidence_bundle(
        evidence_refs=("evidence://z", "evidence://a")
    )
    assert value.evidence_refs == ("evidence://a", "evidence://z")
    with pytest.raises((TypeError, ValidationError)):
        evidence_bundle(evidence_refs=["evidence://a"])  # type: ignore[arg-type]
    with pytest.raises((ValueError, ValidationError)):
        evidence_bundle(evidence_refs=("evidence://a", "evidence://a"))


def test_audit_event_requires_non_empty_provenance_evidence() -> None:
    value = audit_event()
    payload = {**value.model_dump(mode="python"), "evidence_refs": ()}
    with pytest.raises(ValidationError):
        AuditEvent.model_validate(payload)


def test_audit_event_normalizes_reason_and_actor() -> None:
    value = AuditEvent.create(
        **{
            **audit_event().model_dump(mode="python"),
            "actor_id": " reviewer-Cafe\u0301 ",
            "reason": "  Cafe\u0301 review recorded.  ",
        }
    )
    assert value.actor_id == "reviewer-Caf\u00e9"
    assert value.reason == "Caf\u00e9 review recorded."


@pytest.mark.parametrize(
    "reason",
    ("token=secret", "credential: value", "hidden reasoning: text"),
)
def test_audit_event_rejects_sensitive_reason(reason: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        AuditEvent.create(
            **{**audit_event().model_dump(mode="python"), "reason": reason}
        )


def test_audit_event_requires_utc_timestamp() -> None:
    with pytest.raises((ValueError, ValidationError)):
        AuditEvent.create(
            **{
                **audit_event().model_dump(mode="python"),
                "timestamp": NOW.replace(tzinfo=None),
            }
        )


@pytest.mark.parametrize(
    "model_factory",
    (audit_event, evidence_bundle, audit_trail),
)
def test_identity_contracts_reject_fingerprint_tampering(model_factory) -> None:
    value = model_factory()
    payload = {
        **value.model_dump(mode="python"),
        "fingerprint": "sha256:" + "f" * 64,
    }
    with pytest.raises(ValidationError):
        type(value).model_validate(payload)


def test_unknown_event_type_fails_closed() -> None:
    value = audit_event()
    with pytest.raises((ValueError, ValidationError)):
        AuditEvent.create(
            **{**value.model_dump(mode="python"), "event_type": "UNKNOWN"}
        )


def test_event_decision_mismatch_fails_closed() -> None:
    value = audit_event(event_type=AuditEventType.REVIEW_APPROVED)
    with pytest.raises((ValueError, ValidationError)):
        AuditEvent.create(
            **{**value.model_dump(mode="python"), "decision": AuditDecision.REJECT}
        )


def test_risk_request_is_fingerprinted_and_only_accepts_risk_events() -> None:
    record = review_record()
    value = RiskAuditRequest.create(
        request_id="risk-request-1",
        review_reference=review_reference(record),
        event_type=AuditEventType.RISK_IDENTIFIED,
        actor_id="risk-reviewer-1",
        reason="Power rail risk remains unresolved.",
        timestamp=record.decision.decided_at + timedelta(minutes=1),
    )
    assert value.fingerprint.startswith("sha256:")
    with pytest.raises((ValueError, ValidationError)):
        RiskAuditRequest.create(
            **{
                **value.model_dump(mode="python"),
                "event_type": AuditEventType.REVIEW_APPROVED,
            }
        )


def test_risk_request_revalidates_and_copies_review_reference() -> None:
    reference = review_reference()
    value = RiskAuditRequest.create(
        request_id="risk-request-1",
        review_reference=reference,
        event_type=AuditEventType.RISK_IDENTIFIED,
        actor_id="risk-reviewer-1",
        reason="Power rail risk remains unresolved.",
        timestamp=NOW + timedelta(minutes=20),
    )
    assert value.review_reference == reference
    assert value.review_reference is not reference
