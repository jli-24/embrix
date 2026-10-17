from __future__ import annotations

import copy

from .audit import append_optimization_audit
from .contracts import deterministic_id
from .exceptions import (
    OptimizationApprovalRequired,
    OptimizationRejected,
    OptimizationUnavailable,
)
from .models import (
    EngineeringOptimizationOverlay,
    OptimizationAnalysis,
    OptimizationAuditEventType,
    OptimizationDecisionReference,
    OptimizationEvidenceReference,
    OptimizationMemoryCandidateReference,
    OptimizationProposal,
    OptimizationRequest,
    OptimizationReviewDecision,
    OptimizationReviewRecord,
    OptimizationReviewRequest,
)
from .ports import (
    ClockPort,
    OptimizationAnalysisPort,
    OptimizationAuditPort,
    OptimizationContextProjectionPort,
    OptimizationDecisionPort,
    OptimizationEvidencePort,
    OptimizationMemoryPort,
    OptimizationReviewPort,
)


class EngineeringOptimizationService:
    __slots__ = (
        "_analysis",
        "_audit",
        "_clock",
        "_context",
        "_decisions",
        "_evidence",
        "_memory",
        "_reviews",
    )

    def __init__(
        self,
        *,
        evidence: OptimizationEvidencePort,
        analysis: OptimizationAnalysisPort,
        reviews: OptimizationReviewPort,
        decisions: OptimizationDecisionPort,
        context: OptimizationContextProjectionPort,
        memory: OptimizationMemoryPort,
        audit: OptimizationAuditPort,
        clock: ClockPort,
    ) -> None:
        values = (evidence, analysis, reviews, decisions, context, memory, audit, clock)
        ports = (
            OptimizationEvidencePort,
            OptimizationAnalysisPort,
            OptimizationReviewPort,
            OptimizationDecisionPort,
            OptimizationContextProjectionPort,
            OptimizationMemoryPort,
            OptimizationAuditPort,
            ClockPort,
        )
        if any(
            not isinstance(value, port)
            for value, port in zip(values, ports, strict=True)
        ):
            raise TypeError("optimization dependency is invalid")
        self._evidence, self._analysis, self._reviews, self._decisions = (
            evidence,
            analysis,
            reviews,
            decisions,
        )
        self._context, self._memory, self._audit, self._clock = (
            context,
            memory,
            audit,
            clock,
        )

    @staticmethod
    def _checked(value, model, field: str):
        if type(value) is not model:
            raise OptimizationRejected(f"{field} is invalid")
        try:
            return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except Exception as error:
            raise OptimizationRejected(f"{field} is invalid") from error

    def _evidence_set(
        self, request: OptimizationRequest
    ) -> tuple[OptimizationEvidenceReference, ...]:
        found = []
        for expected in request.evidence_refs:
            try:
                raw = self._evidence.get_evidence(
                    expected.project_id, expected.evidence_id
                )
            except Exception as error:
                raise OptimizationUnavailable(
                    "optimization evidence unavailable"
                ) from error
            if raw is None:
                raise OptimizationRejected("optimization evidence missing")
            checked = self._checked(
                raw, OptimizationEvidenceReference, "optimization evidence"
            )
            if checked != expected or checked.created_at > request.created_at:
                raise OptimizationRejected("optimization evidence drift")
            found.append(checked)
        return tuple(found)

    def analyze(self, request: OptimizationRequest) -> OptimizationAnalysis:
        checked = self._checked(request, OptimizationRequest, "optimization request")
        evidence = self._evidence_set(checked)
        try:
            raw = self._analysis.analyze(
                copy.deepcopy(checked), copy.deepcopy(evidence)
            )
        except Exception as error:
            raise OptimizationUnavailable(
                "optimization analysis unavailable"
            ) from error
        analysis = self._checked(raw, OptimizationAnalysis, "optimization analysis")
        if (
            analysis.project_id != checked.project_id
            or analysis.request_fingerprint != checked.fingerprint
            or analysis.created_at < checked.created_at
            or any(
                item.optimization_type not in checked.optimization_scope
                or item.evidence_refs != evidence
                for item in analysis.findings
            )
        ):
            raise OptimizationRejected("optimization analysis binding mismatch")
        return analysis

    def create_proposal(
        self,
        analysis: OptimizationAnalysis,
        finding_id: str,
        *,
        recommendation: str,
        risk,
        expected_impact: str,
    ) -> OptimizationProposal:
        checked = self._checked(analysis, OptimizationAnalysis, "optimization analysis")
        finding = next(
            (item for item in checked.findings if item.finding_id == finding_id), None
        )
        if finding is None:
            raise OptimizationRejected("optimization finding missing")
        proposal = OptimizationProposal(
            proposal_id=deterministic_id(
                "optimization-proposal", checked.fingerprint, finding.fingerprint
            ),
            project_id=checked.project_id,
            optimization_type=finding.optimization_type,
            evidence_refs=finding.evidence_refs,
            finding=finding.finding,
            recommendation=recommendation,
            risk=risk,
            expected_impact=expected_impact,
            confidence=finding.confidence,
            analysis_fingerprint=checked.fingerprint,
            provenance_fingerprint=checked.fingerprint,
            created_at=self._clock.now(),
        )
        append_optimization_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=OptimizationAuditEventType.OPTIMIZATION_CREATED,
            project_id=proposal.project_id,
            operation_fingerprint=proposal.fingerprint,
            result_fingerprint=proposal.fingerprint,
        )
        return proposal

    def submit_review(
        self, proposal: OptimizationProposal, reviewer: str
    ) -> OptimizationReviewRequest:
        checked = self._checked(proposal, OptimizationProposal, "optimization proposal")
        request = OptimizationReviewRequest(
            review_id=deterministic_id("optimization-review", checked.fingerprint),
            proposal=checked,
            requested_reviewer=reviewer,
            requested_at=self._clock.now(),
        )
        try:
            stored = self._checked(
                self._reviews.submit(copy.deepcopy(request)),
                OptimizationReviewRequest,
                "optimization review request",
            )
        except OptimizationRejected:
            raise
        except Exception as error:
            raise OptimizationUnavailable("optimization review unavailable") from error
        if stored != request:
            raise OptimizationRejected("optimization review request mismatch")
        append_optimization_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=OptimizationAuditEventType.REVIEW_SUBMITTED,
            project_id=checked.project_id,
            operation_fingerprint=request.fingerprint,
            result_fingerprint=stored.fingerprint,
        )
        return stored

    def record_decision(
        self, proposal: OptimizationProposal, review_id: str
    ) -> OptimizationDecisionReference | None:
        checked = self._checked(proposal, OptimizationProposal, "optimization proposal")
        try:
            submitted_raw = self._reviews.get_request(review_id)
            raw = self._reviews.get_record(review_id)
        except Exception as error:
            raise OptimizationUnavailable("optimization review unavailable") from error
        if submitted_raw is None or raw is None:
            raise OptimizationApprovalRequired("optimization review missing")
        submitted = self._checked(
            submitted_raw, OptimizationReviewRequest, "optimization review request"
        )
        record = self._checked(raw, OptimizationReviewRecord, "optimization review")
        if (
            submitted.review_id != review_id
            or submitted.proposal != checked
            or record.review_id
            != deterministic_id("optimization-review", checked.fingerprint)
            or record.proposal_id != checked.proposal_id
            or record.proposal_fingerprint != checked.fingerprint
            or record.project_id != checked.project_id
            or record.requested_reviewer != submitted.requested_reviewer
            or record.created_at < submitted.requested_at
            or record.created_at > self._clock.now()
            or self._clock.now() > record.expires_at
        ):
            raise OptimizationApprovalRequired("optimization review invalid")
        if record.decision is not OptimizationReviewDecision.APPROVE:
            append_optimization_audit(
                sink=self._audit,
                clock=self._clock,
                event_type=OptimizationAuditEventType.OPTIMIZATION_REJECTED,
                project_id=checked.project_id,
                operation_fingerprint=record.fingerprint,
                result_fingerprint=None,
            )
            return None
        decision = OptimizationDecisionReference(
            decision_id=deterministic_id("optimization-decision", record.fingerprint),
            project_id=checked.project_id,
            review_id=record.review_id,
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.fingerprint,
            reviewer=record.reviewer,
            decided_at=record.created_at,
        )
        try:
            stored = self._checked(
                self._decisions.record(copy.deepcopy(decision)),
                OptimizationDecisionReference,
                "optimization decision",
            )
        except OptimizationRejected:
            raise
        except Exception as error:
            raise OptimizationUnavailable(
                "optimization decision unavailable"
            ) from error
        if stored != decision:
            raise OptimizationRejected("optimization decision mismatch")
        append_optimization_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=OptimizationAuditEventType.OPTIMIZATION_APPROVED,
            project_id=checked.project_id,
            operation_fingerprint=record.fingerprint,
            result_fingerprint=stored.fingerprint,
        )
        return stored

    def project_context(
        self,
        proposal: OptimizationProposal,
        decision: OptimizationDecisionReference,
        *,
        base_context_fingerprint: str | None = None,
    ) -> EngineeringOptimizationOverlay:
        checked_proposal = self._checked(
            proposal, OptimizationProposal, "optimization proposal"
        )
        checked_decision = self._checked(
            decision, OptimizationDecisionReference, "optimization decision"
        )
        if (
            checked_decision.project_id != checked_proposal.project_id
            or checked_decision.proposal_id != checked_proposal.proposal_id
            or checked_decision.proposal_fingerprint != checked_proposal.fingerprint
        ):
            raise OptimizationRejected("optimization decision binding mismatch")
        overlay = EngineeringOptimizationOverlay(
            overlay_id=deterministic_id(
                "optimization-overlay", checked_decision.fingerprint
            ),
            project_id=checked_proposal.project_id,
            base_context_fingerprint=base_context_fingerprint,
            decision=checked_decision,
            proposal_fingerprint=checked_proposal.fingerprint,
            created_at=self._clock.now(),
        )
        try:
            projected = self._checked(
                self._context.project(copy.deepcopy(overlay)),
                EngineeringOptimizationOverlay,
                "optimization overlay",
            )
        except OptimizationRejected:
            raise
        except Exception as error:
            raise OptimizationUnavailable(
                "optimization projection unavailable"
            ) from error
        if projected != overlay:
            raise OptimizationRejected("optimization overlay changed")
        append_optimization_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=OptimizationAuditEventType.CONTEXT_PROJECTED,
            project_id=checked_proposal.project_id,
            operation_fingerprint=checked_decision.fingerprint,
            result_fingerprint=projected.fingerprint,
        )
        return projected

    def create_memory_candidate(
        self, overlay: EngineeringOptimizationOverlay
    ) -> tuple[OptimizationMemoryCandidateReference, ...]:
        checked = self._checked(
            overlay, EngineeringOptimizationOverlay, "optimization overlay"
        )
        try:
            raw = self._memory.create(copy.deepcopy(checked))
        except Exception as error:
            raise OptimizationUnavailable("optimization memory unavailable") from error
        if type(raw) is not tuple:
            raise OptimizationUnavailable(
                "optimization memory returned invalid candidates"
            )
        candidates = tuple(
            self._checked(
                item,
                OptimizationMemoryCandidateReference,
                "optimization memory candidate",
            )
            for item in raw
        )
        if not candidates or any(
            item.project_id != checked.project_id
            or item.overlay_fingerprint != checked.fingerprint
            or item.review_status != "REVIEW_REQUIRED"
            for item in candidates
        ):
            raise OptimizationRejected("optimization memory binding mismatch")
        append_optimization_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=OptimizationAuditEventType.MEMORY_CANDIDATE_CREATED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            result_fingerprint=candidates[0].fingerprint,
        )
        return candidates


__all__ = ("EngineeringOptimizationService",)
