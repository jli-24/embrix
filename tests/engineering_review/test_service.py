from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_evolution import (
    EvolutionProposalStatus,
    EvolutionTarget,
    KnowledgeEvolutionProposal,
)
from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_optimization_loop import (
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationTarget,
    ValidationFindingReference,
)
from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewConflict,
    ReviewDecision,
    ReviewNotFound,
    ReviewRejected,
    ReviewRequest,
    ReviewService,
    ReviewStatus,
    ReviewTargetNotFound,
    ReviewTargetReference,
    ReviewTargetType,
    ReviewUnavailable,
)
from embedded_copilot.engineering_review.adapters import (
    FakeDecisionAuditSinkAdapter,
    FakeReviewStoreAdapter,
)
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationSeverity,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def approved_knowledge(**overrides: object) -> ApprovedKnowledge:
    values: dict[str, object] = {
        "knowledge_id": "knowledge-1",
        "candidate_id": "candidate-1",
        "project_id": "project-1",
        "claim": "GPIO12 is reserved.",
        "evidence_refs": ("context:evidence-1",),
        "approved_by": "reviewer-0",
    }
    values.update(overrides)
    return ApprovedKnowledge.create(**values)


def validation_finding(**overrides: object) -> ValidationFinding:
    values: dict[str, object] = {
        "finding_id": "finding-1",
        "project_id": "project-1",
        "rule_id": "POWER-001",
        "category": "hardware_power",
        "severity": ValidationSeverity.WARNING,
        "status": ValidationFindingStatus.OPEN,
        "message": "possible voltage mismatch",
        "evidence_refs": ("knowledge://power-rule",),
    }
    values.update(overrides)
    return ValidationFinding.create(**values)


def optimization_proposal(**overrides: object) -> OptimizationProposal:
    source = validation_finding()
    values: dict[str, object] = {
        "proposal_id": "ignored-by-create",
        "project_id": "project-1",
        "source_finding_refs": (
            ValidationFindingReference.create(
                finding_id=source.finding_id,
                fingerprint=source.fingerprint,
            ),
        ),
        "target": OptimizationTarget.HARDWARE,
        "title": "Review power decoupling design",
        "rationale": "A voltage mismatch finding requires engineering review.",
        "expected_effects": ("Reduce rail mismatch risk.",),
        "evidence_refs": ("knowledge://power-rule",),
        "risk_notes": ("Requires hardware verification.",),
        "status": OptimizationProposalStatus.CREATED,
    }
    values.update(overrides)
    return OptimizationProposal.create(**values)


def evolution_proposal(**overrides: object) -> KnowledgeEvolutionProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-evolution-1",
        "request_id": "request-evolution-1",
        "project_id": "project-1",
        "source_knowledge_id": "knowledge-1",
        "source_knowledge_fingerprint": "sha256:" + "a" * 64,
        "source_candidate_id": "candidate-1",
        "source_approved_by": "reviewer-0",
        "target": EvolutionTarget.MEMORY,
        "claim": "GPIO12 is reserved.",
        "evidence_refs": ("context:evidence-1",),
        "created_by": "engineer-1",
        "created_at": NOW,
        "status": EvolutionProposalStatus.CREATED,
    }
    values.update(overrides)
    return KnowledgeEvolutionProposal.create(**values)


class StubTargetSource:
    def __init__(self, targets: tuple[object, ...]) -> None:
        self.targets = {self._key(target): target for target in targets}

    @staticmethod
    def _key(target: object) -> tuple[str, ReviewTargetType, str]:
        if type(target) is ApprovedKnowledge:
            return target.project_id, ReviewTargetType.KNOWLEDGE, target.knowledge_id
        if type(target) is ValidationFinding:
            return target.project_id, ReviewTargetType.VALIDATION, target.finding_id
        if type(target) is OptimizationProposal:
            return target.project_id, ReviewTargetType.OPTIMIZATION, target.proposal_id
        if type(target) is KnowledgeEvolutionProposal:
            return target.project_id, ReviewTargetType.EVOLUTION, target.proposal_id
        raise TypeError("unsupported target")

    def get_target(self, project_id, target_type, target_id):
        return copy.deepcopy(self.targets.get((project_id, target_type, target_id)))


def target_identity(target: object) -> tuple[ReviewTargetType, str, str]:
    if type(target) is ApprovedKnowledge:
        return ReviewTargetType.KNOWLEDGE, target.knowledge_id, target.fingerprint
    if type(target) is ValidationFinding:
        return ReviewTargetType.VALIDATION, target.finding_id, target.fingerprint
    if type(target) is OptimizationProposal:
        return ReviewTargetType.OPTIMIZATION, target.proposal_id, target.fingerprint
    if type(target) is KnowledgeEvolutionProposal:
        return ReviewTargetType.EVOLUTION, target.proposal_id, target.fingerprint
    raise TypeError("unsupported target")


def review_request(target: object, **overrides: object) -> ReviewRequest:
    target_type, target_id, target_fingerprint = target_identity(target)
    values: dict[str, object] = {
        "review_id": f"review-{target_type.value.lower()}",
        "project_id": "project-1",
        "target_reference": ReviewTargetReference.create(
            target_type=target_type,
            target_id=target_id,
            target_fingerprint=target_fingerprint,
        ),
        "requester": "engineer-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ReviewRequest.create(**values)


def human_decision(request: ReviewRequest, **overrides: object) -> HumanDecision:
    values: dict[str, object] = {
        "decision_id": f"decision-{request.review_id}",
        "review_id": request.review_id,
        "project_id": request.project_id,
        "decision": ReviewDecision.APPROVE,
        "reviewer": "reviewer-1",
        "comment": "Approved for the next engineering step.",
        "decided_at": NOW + timedelta(minutes=1),
    }
    values.update(overrides)
    return HumanDecision.create(**values)


def service_for(targets: tuple[object, ...], *, audit_failures: int = 0):
    source = StubTargetSource(targets)
    store = FakeReviewStoreAdapter()
    audit = FakeDecisionAuditSinkAdapter(failures=audit_failures)
    return ReviewService(source=source, store=store, audit_sink=audit), source, store, audit


@pytest.mark.parametrize(
    "target",
    (
        approved_knowledge(),
        validation_finding(),
        optimization_proposal(),
        evolution_proposal(),
    ),
)
def test_create_and_decide_supported_target(target: object) -> None:
    review, _, store, audit = service_for((target,))
    request = review_request(target)
    stored_request = review.create_review(request)
    record = review.decide(request.review_id, human_decision(request))

    assert stored_request == request
    assert stored_request is not request
    assert record.status is ReviewStatus.DECIDED
    assert record.review_request.target_reference.target_fingerprint == target.fingerprint
    assert store.get_record(request.review_id) == record
    assert audit.get_record(request.review_id) == record


def test_missing_target_fails_closed() -> None:
    target = approved_knowledge()
    review, _, _, _ = service_for(())
    with pytest.raises(ReviewTargetNotFound):
        review.create_review(review_request(target))


@pytest.mark.parametrize("mismatch", ("type", "project", "id", "fingerprint"))
def test_target_identity_mismatch_fails_closed(mismatch: str) -> None:
    target = approved_knowledge()
    request = review_request(target)

    class MismatchedSource:
        def get_target(self, project_id, target_type, target_id):
            if mismatch == "type":
                return validation_finding()
            if mismatch == "project":
                return approved_knowledge(project_id="project-other")
            if mismatch == "id":
                return approved_knowledge(knowledge_id="knowledge-other")
            return approved_knowledge(claim="Changed content.")

    review = ReviewService(
        source=MismatchedSource(),
        store=FakeReviewStoreAdapter(),
        audit_sink=FakeDecisionAuditSinkAdapter(),
    )
    with pytest.raises(ReviewRejected):
        review.create_review(request)


def test_duplicate_review_is_rejected_even_when_content_is_identical() -> None:
    target = approved_knowledge()
    review, _, _, _ = service_for((target,))
    request = review_request(target)
    review.create_review(request)
    with pytest.raises(ReviewConflict):
        review.create_review(request)


def test_decision_binding_and_time_order_fail_closed() -> None:
    target = approved_knowledge()
    review, _, _, _ = service_for((target,))
    request = review_request(target)
    review.create_review(request)

    with pytest.raises(ReviewRejected):
        review.decide(request.review_id, object())  # type: ignore[arg-type]
    with pytest.raises(ReviewRejected):
        review.decide(
            request.review_id,
            human_decision(request, review_id="review-other"),
        )
    with pytest.raises(ReviewRejected):
        review.decide(
            request.review_id,
            human_decision(request, project_id="project-other"),
        )
    with pytest.raises(ReviewRejected):
        review.decide(
            request.review_id,
            human_decision(request, decided_at=NOW - timedelta(seconds=1)),
        )


def test_target_fingerprint_is_rechecked_before_decision() -> None:
    target = approved_knowledge()
    review, source, store, _ = service_for((target,))
    request = review_request(target)
    review.create_review(request)
    changed = approved_knowledge(claim="Changed during review.")
    source.targets[source._key(changed)] = changed

    with pytest.raises(ReviewRejected):
        review.decide(request.review_id, human_decision(request))
    assert store.get_decision(request.review_id) is None
    assert store.get_record(request.review_id) is None


def test_different_repeated_decision_conflicts() -> None:
    target = approved_knowledge()
    review, _, _, _ = service_for((target,))
    request = review_request(target)
    review.create_review(request)
    review.decide(request.review_id, human_decision(request))

    with pytest.raises(ReviewConflict):
        review.decide(
            request.review_id,
            human_decision(
                request,
                decision_id="decision-other",
                decision=ReviewDecision.REJECT,
                comment="Rejected by human reviewer.",
            ),
        )


def test_identical_decision_recovers_after_audit_failure() -> None:
    target = approved_knowledge()
    review, _, store, audit = service_for((target,), audit_failures=1)
    request = review_request(target)
    decision = human_decision(request)
    review.create_review(request)

    with pytest.raises(ReviewUnavailable):
        review.decide(request.review_id, decision)
    stored = store.get_record(request.review_id)
    assert stored is not None
    assert audit.get_record(request.review_id) is None

    recovered = review.decide(request.review_id, decision)
    assert recovered == stored
    assert audit.get_record(request.review_id) == recovered
    assert audit.attempt_count == 2


def test_missing_review_and_inputs_are_immutable() -> None:
    target = approved_knowledge()
    review, _, _, _ = service_for((target,))
    request = review_request(target)
    decision = human_decision(request)
    original_request = copy.deepcopy(request)
    original_decision = copy.deepcopy(decision)

    with pytest.raises(ReviewNotFound):
        review.decide(request.review_id, decision)
    assert request == original_request
    assert decision == original_decision


def test_store_output_identity_mismatch_is_unavailable() -> None:
    target = approved_knowledge()
    request = review_request(target)

    class MismatchedStore(FakeReviewStoreAdapter):
        def get_review_request(self, review_id):
            return ReviewRequest.create(
                **{
                    **request.model_dump(mode="python"),
                    "review_id": "review-other",
                }
            )

    review = ReviewService(
        source=StubTargetSource((target,)),
        store=MismatchedStore(),
        audit_sink=FakeDecisionAuditSinkAdapter(),
    )
    with pytest.raises(ReviewUnavailable):
        review.decide(request.review_id, human_decision(request))
