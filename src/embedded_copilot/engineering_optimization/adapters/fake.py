from __future__ import annotations

import copy
from datetime import datetime

from ..contracts import (
    OptimizationAnalysis as LegacyOptimizationAnalysis,
)
from ..contracts import (
    OptimizationApprovalRequest,
    OptimizationCategory,
    OptimizationStatus,
    OptimizationTarget,
)
from ..contracts import (
    OptimizationConfidence as LegacyOptimizationConfidence,
)
from ..contracts import (
    OptimizationFinding as LegacyOptimizationFinding,
)
from ..exceptions import OptimizationConflict, OptimizationUnavailable
from ..models import (
    EngineeringOptimizationOverlay,
    OptimizationAnalysis,
    OptimizationAuditEvent,
    OptimizationConfidence,
    OptimizationDecisionReference,
    OptimizationEvidenceReference,
    OptimizationFinding,
    OptimizationMemoryCandidateReference,
    OptimizationProposal,
    OptimizationRequest,
    OptimizationReviewRecord,
    OptimizationReviewRequest,
)


def _copy(value, model):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeOptimizationEvidencePort:
    __slots__ = ("_items",)

    def __init__(self, items: tuple[OptimizationEvidenceReference, ...]) -> None:
        self._items = {
            (item.project_id, item.evidence_id): _copy(
                item, OptimizationEvidenceReference
            )
            for item in items
        }

    def get_evidence(self, project_id: str, evidence_id: str):
        value = self._items.get((project_id, evidence_id))
        return None if value is None else _copy(value, OptimizationEvidenceReference)


class FakeOptimizationAnalysisPort:
    __slots__ = ("_clock",)

    def __init__(self, clock: FixedClock | None = None) -> None:
        self._clock = clock

    @staticmethod
    def _legacy_finding(project_id: str) -> LegacyOptimizationFinding:
        return LegacyOptimizationFinding.create(
            finding_id=f"finding:{project_id}:1",
            category=OptimizationCategory.PERFORMANCE,
            target=OptimizationTarget.TEST,
            current_state="Projected metrics are available.",
            suggested_direction="Review measured latency before proposing a bounded change.",
            risk="A change requires explicit human review.",
            confidence=LegacyOptimizationConfidence.PROJECTED,
            evidence_reference=f"evidence:{project_id}",
            status=OptimizationStatus.REVIEW_REQUIRED,
        )

    def get_snapshot(self, project_id: str) -> LegacyOptimizationAnalysis:
        if self._clock is not None:
            raise OptimizationUnavailable("legacy optimization mode is unavailable")
        return LegacyOptimizationAnalysis.create(
            project_id=project_id,
            findings=(self._legacy_finding(project_id),),
        )

    def analyze(
        self,
        request: OptimizationRequest,
        evidence: tuple[OptimizationEvidenceReference, ...],
    ) -> OptimizationAnalysis:
        if self._clock is None:
            raise OptimizationUnavailable("v4.6 optimization mode is unavailable")
        checked = _copy(request, OptimizationRequest)
        finding = OptimizationFinding(
            finding_id=f"optimization-finding-{checked.fingerprint[-16:]}",
            project_id=checked.project_id,
            optimization_type=checked.optimization_scope[0],
            finding="Evidence-bound optimization opportunity.",
            evidence_refs=evidence,
            confidence=OptimizationConfidence.EVIDENCE_BOUND,
            created_at=self._clock.now(),
        )
        return OptimizationAnalysis(
            analysis_id=f"optimization-analysis-{checked.fingerprint[-16:]}",
            project_id=checked.project_id,
            request_fingerprint=checked.fingerprint,
            findings=(finding,),
            created_at=self._clock.now(),
        )


class FakeOptimizationApprovalPort:
    def _decide(
        self, request: OptimizationApprovalRequest, status: OptimizationStatus
    ) -> LegacyOptimizationFinding:
        project_id = request.finding_id.split(":", 2)[1]
        finding = FakeOptimizationAnalysisPort._legacy_finding(project_id)
        if (
            finding.finding_id != request.finding_id
            or finding.fingerprint != request.finding_fingerprint
        ):
            raise OptimizationConflict("legacy optimization finding mismatch")
        return LegacyOptimizationFinding.create(
            **{**finding.model_dump(mode="python"), "status": status}
        )

    def approve(
        self, request: OptimizationApprovalRequest
    ) -> LegacyOptimizationFinding:
        return self._decide(request, OptimizationStatus.APPROVED)

    def reject(self, request: OptimizationApprovalRequest) -> LegacyOptimizationFinding:
        return self._decide(request, OptimizationStatus.REJECTED)


class FakeOptimizationReviewPort:
    __slots__ = ("_records", "_requests")

    def __init__(self, records: tuple[OptimizationReviewRecord, ...] = ()) -> None:
        self._requests = {}
        self._records = {
            item.review_id: _copy(item, OptimizationReviewRecord) for item in records
        }

    def submit(self, request: OptimizationReviewRequest) -> OptimizationReviewRequest:
        checked = _copy(request, OptimizationReviewRequest)
        prior = self._requests.get(checked.review_id)
        if prior is not None and prior != checked:
            raise OptimizationConflict("optimization review conflict")
        self._requests[checked.review_id] = checked
        return _copy(checked, OptimizationReviewRequest)

    def get_request(self, review_id: str):
        value = self._requests.get(review_id)
        return None if value is None else _copy(value, OptimizationReviewRequest)

    def get_record(self, review_id: str):
        value = self._records.get(review_id)
        return None if value is None else _copy(value, OptimizationReviewRecord)


class FakeOptimizationDecisionPort:
    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items = {}

    def record(
        self, decision: OptimizationDecisionReference
    ) -> OptimizationDecisionReference:
        checked = _copy(decision, OptimizationDecisionReference)
        prior = self._items.get(checked.decision_id)
        if prior is not None and prior != checked:
            raise OptimizationConflict("optimization decision conflict")
        self._items[checked.decision_id] = checked
        return _copy(checked, OptimizationDecisionReference)

    def get(self, decision_id: str):
        value = self._items.get(decision_id)
        return None if value is None else _copy(value, OptimizationDecisionReference)


class FakeOptimizationProposalReader:
    __slots__ = ("_items",)

    def __init__(self, items: tuple[OptimizationProposal, ...]) -> None:
        self._items = {
            (item.project_id, item.proposal_id): _copy(item, OptimizationProposal)
            for item in items
        }

    def get_proposal(self, project_id: str, proposal_id: str):
        value = self._items.get((project_id, proposal_id))
        return None if value is None else _copy(value, OptimizationProposal)


class FakeOptimizationContextProjectionPort:
    def project(
        self, overlay: EngineeringOptimizationOverlay
    ) -> EngineeringOptimizationOverlay:
        return _copy(overlay, EngineeringOptimizationOverlay)


class FakeOptimizationMemoryPort:
    def create(
        self, overlay: EngineeringOptimizationOverlay
    ) -> tuple[OptimizationMemoryCandidateReference, ...]:
        checked = _copy(overlay, EngineeringOptimizationOverlay)
        return (
            OptimizationMemoryCandidateReference(
                project_id=checked.project_id,
                overlay_fingerprint=checked.fingerprint,
                candidate_id=f"optimization-memory-{checked.overlay_id}",
                candidate_fingerprint=checked.fingerprint,
            ),
        )


class FakeOptimizationAuditPort:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items, self.fail = {}, False

    def append(self, event: OptimizationAuditEvent) -> OptimizationAuditEvent:
        if self.fail:
            raise OptimizationUnavailable("injected optimization audit failure")
        checked = _copy(event, OptimizationAuditEvent)
        prior = self._items.get(checked.event_id)
        if prior is not None and prior != checked:
            raise OptimizationConflict("optimization audit conflict")
        self._items[checked.event_id] = checked
        return _copy(checked, OptimizationAuditEvent)

    def get(self, event_id: str):
        value = self._items.get(event_id)
        return None if value is None else _copy(value, OptimizationAuditEvent)
