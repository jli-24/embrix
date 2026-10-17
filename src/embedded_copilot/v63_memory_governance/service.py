from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from typing import Iterable

from .models import (
    ApprovedKnowledge,
    EngineeringMemory,
    EvidenceIdentity,
    EvolutionApplyRequest,
    EvolutionStatus,
    EvolutionTarget,
    Freshness,
    KnowledgeCandidate,
    KnowledgeEvolutionProposal,
    KnowledgeProjection,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryProjection,
    MemoryStatus,
    ReviewItem,
    ReviewStatus,
    ReviewTargetType,
    StructuredCandidateInput,
    TrustLevel,
    V63AuditEvent,
    canonical_fingerprint,
)
from .sqlite import SQLiteV63Store


class V63Error(Exception):
    pass


class V63Rejected(V63Error):
    pass


class V63NotFound(V63Error):
    pass


class V63Conflict(V63Error):
    pass


class V63Unavailable(V63Error):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str, *values: str) -> str:
    return prefix + hashlib.sha256(":".join(values).encode("utf-8")).hexdigest()[:40]


class V63MemoryGovernanceService:
    """Deterministic v6.3 service boundary for memory, knowledge and review."""

    def __init__(self, store: SQLiteV63Store, *, clock=_now) -> None:
        if not isinstance(store, SQLiteV63Store):
            raise TypeError("v6.3 store is invalid")
        self.store = store
        self.clock = clock

    def register_evidence(self, evidence: EvidenceIdentity) -> EvidenceIdentity:
        checked = EvidenceIdentity.model_validate(copy.deepcopy(evidence.model_dump(mode="python")))
        if self.store.is_tombstoned(checked.tenant_id, checked.project_id):
            raise V63Rejected("project is deleted")
        existing = self.store.get_evidence(checked.tenant_id, checked.project_id, checked.evidence_id)
        if existing is not None and existing != checked:
            raise V63Conflict("evidence identity conflict")
        return self.store.save_evidence(checked)

    def extract_memory_candidate(self, source: StructuredCandidateInput) -> MemoryCandidate:
        checked = StructuredCandidateInput.model_validate(copy.deepcopy(source.model_dump(mode="python")))
        if self.store.is_tombstoned(checked.tenant_id, checked.project_id):
            raise V63Rejected("project is deleted")
        for evidence in checked.evidence:
            self.register_evidence(evidence)
        candidate_id = _id("memory-candidate-", checked.project_id, checked.subject, checked.statement)
        existing = self.store.get_memory_candidate(checked.tenant_id, checked.project_id, candidate_id)
        if existing is not None:
            if existing.requester_id != checked.requester_id or existing.memory_type is not checked.memory_type:
                raise V63Conflict("candidate identity conflict")
            return existing
        candidate = MemoryCandidate.create(
            memory_candidate_id=candidate_id,
            tenant_id=checked.tenant_id,
            requester_id=checked.requester_id,
            project_id=checked.project_id,
            memory_type=checked.memory_type,
            subject=checked.subject,
            statement=checked.statement,
            evidence_ids=tuple(item.evidence_id for item in checked.evidence),
            source_references=tuple(item.source_reference for item in checked.evidence),
            source_fingerprints=tuple(item.source_fingerprint for item in checked.evidence),
            confidence=checked.confidence,
            limitations=checked.limitations,
            provenance=checked.provenance or tuple(item.evidence_id for item in checked.evidence),
            created_at=self.clock(),
        )
        stored = self.store.save_memory_candidate(candidate)
        self._audit("MEMORY_CANDIDATE_CREATED", candidate, candidate.candidate_fingerprint, candidate.evidence_ids)
        return stored

    def extract_knowledge_candidate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        requester_id: str,
        knowledge_type: str,
        subject: str,
        predicate: str,
        value: str,
        evidence: tuple[EvidenceIdentity, ...],
        trust_level: TrustLevel = TrustLevel.CANDIDATE,
        confidence: float = 0.0,
        limitations: tuple[str, ...] = (),
        provenance: tuple[str, ...] = (),
    ) -> KnowledgeCandidate:
        if not evidence:
            raise V63Rejected("INSUFFICIENT_EVIDENCE")
        checked = tuple(EvidenceIdentity.model_validate(copy.deepcopy(item.model_dump(mode="python"))) for item in evidence)
        if any(item.tenant_id != tenant_id or item.project_id != project_id for item in checked):
            raise V63Rejected("evidence identity mismatch")
        for item in checked:
            self.register_evidence(item)
        candidate_id = _id("knowledge-candidate-", project_id, subject, predicate, value)
        existing = self.store.get_knowledge_candidate(tenant_id, project_id, candidate_id)
        if existing is not None:
            if existing.knowledge_type != knowledge_type or existing.requester_id != requester_id:
                raise V63Conflict("knowledge candidate identity conflict")
            return existing
        candidate = KnowledgeCandidate.create(
            knowledge_candidate_id=candidate_id,
            tenant_id=tenant_id,
            requester_id=requester_id,
            project_id=project_id,
            knowledge_type=knowledge_type,
            subject=subject,
            predicate=predicate,
            value=value,
            evidence_ids=tuple(item.evidence_id for item in checked),
            source_references=tuple(item.source_reference for item in checked),
            source_fingerprints=tuple(item.source_fingerprint for item in checked),
            trust_level=trust_level,
            confidence=confidence,
            limitations=limitations,
            provenance=provenance or tuple(item.evidence_id for item in checked),
            created_at=self.clock(),
        )
        stored = self.store.save_knowledge_candidate(candidate)
        self._audit("KNOWLEDGE_CANDIDATE_CREATED", candidate, candidate.candidate_fingerprint, candidate.evidence_ids)
        return stored

    def request_memory_review(self, *, tenant_id: str, project_id: str, candidate_id: str, requester_id: str) -> ReviewItem:
        candidate = self.store.get_memory_candidate(tenant_id, project_id, candidate_id)
        if candidate is None:
            raise V63NotFound("memory candidate not found")
        if candidate.requester_id != requester_id or candidate.status not in {MemoryStatus.CANDIDATE, MemoryStatus.WAIT_REVIEW}:
            raise V63Rejected("candidate review is not allowed")
        updated = candidate.with_status(MemoryStatus.WAIT_REVIEW)
        self.store.save_memory_candidate(updated)
        return self._create_review(
            tenant_id=tenant_id,
            project_id=project_id,
            requester_id=requester_id,
            target_type=ReviewTargetType.MEMORY_CANDIDATE,
            target_id=candidate.memory_candidate_id,
            target_fingerprint=candidate.candidate_fingerprint,
            evidence_fingerprints=candidate.source_fingerprints,
        )

    def request_knowledge_review(self, *, tenant_id: str, project_id: str, candidate_id: str, requester_id: str) -> ReviewItem:
        candidate = self.store.get_knowledge_candidate(tenant_id, project_id, candidate_id)
        if candidate is None:
            raise V63NotFound("knowledge candidate not found")
        if candidate.requester_id != requester_id or candidate.status not in {KnowledgeStatus.CANDIDATE, KnowledgeStatus.WAIT_REVIEW}:
            raise V63Rejected("candidate review is not allowed")
        self.store.save_knowledge_candidate(candidate.model_copy(update={"status": KnowledgeStatus.WAIT_REVIEW}))
        return self._create_review(
            tenant_id=tenant_id,
            project_id=project_id,
            requester_id=requester_id,
            target_type=ReviewTargetType.KNOWLEDGE_CANDIDATE,
            target_id=candidate.knowledge_candidate_id,
            target_fingerprint=candidate.candidate_fingerprint,
            evidence_fingerprints=candidate.source_fingerprints,
        )

    def _create_review(self, *, tenant_id: str, project_id: str, requester_id: str, target_type: ReviewTargetType, target_id: str, target_fingerprint: str, evidence_fingerprints: tuple[str, ...]) -> ReviewItem:
        review_id = _id("review-", tenant_id, project_id, target_type.value, target_id, target_fingerprint)
        current = self.store.get_review(tenant_id, project_id, review_id)
        if current is not None:
            return current
        review = ReviewItem.create(
            review_id=review_id,
            tenant_id=tenant_id,
            project_id=project_id,
            requester_id=requester_id,
            target_type=target_type,
            target_id=target_id,
            target_fingerprint=target_fingerprint,
            evidence_fingerprints=tuple(sorted(evidence_fingerprints)),
            created_at=self.clock(),
        )
        stored = self.store.save_review(review)
        self._audit("MEMORY_REVIEW_REQUESTED" if target_type is ReviewTargetType.MEMORY_CANDIDATE else "KNOWLEDGE_REVIEW_REQUESTED", review, target_fingerprint, ())
        return stored

    def decide_review(self, *, tenant_id: str, project_id: str, review_id: str, reviewer_id: str, decision: LiteralDecision) -> ReviewItem:
        review = self.store.get_review(tenant_id, project_id, review_id)
        if review is None:
            raise V63NotFound("review not found")
        if review.status is not ReviewStatus.PENDING:
            if review.reviewer_id == reviewer_id and review.decision == decision:
                return review
            raise V63Conflict("review replay or reuse rejected")
        current_fp, current_evidence = self._target_identity(review)
        if current_fp != review.target_fingerprint or current_evidence != review.evidence_fingerprints:
            raise V63Rejected("stale review binding")
        status = ReviewStatus.APPROVED if decision == "APPROVE" else ReviewStatus.REJECTED
        values = review.model_dump(mode="python")
        values.update(
            status=status,
            reviewer_id=reviewer_id,
            decision=decision,
            decided_at=self.clock(),
        )
        decided = ReviewItem.create(**values)
        self.store.save_review(decided)
        self._apply_review(review, decided)
        self._audit(
            "MEMORY_APPROVED" if review.target_type is ReviewTargetType.MEMORY_CANDIDATE and status is ReviewStatus.APPROVED else
            "MEMORY_REJECTED" if review.target_type is ReviewTargetType.MEMORY_CANDIDATE else
            "KNOWLEDGE_APPROVED" if status is ReviewStatus.APPROVED else "KNOWLEDGE_REJECTED",
            decided, decided.target_fingerprint, (),
        )
        return decided

    def _target_identity(self, review: ReviewItem) -> tuple[str, tuple[str, ...]]:
        if review.target_type is ReviewTargetType.MEMORY_CANDIDATE:
            target = self.store.get_memory_candidate(review.tenant_id, review.project_id, review.target_id)
            if target is None:
                raise V63NotFound("memory candidate not found")
            return target.candidate_fingerprint, tuple(sorted(target.source_fingerprints))
        if review.target_type is ReviewTargetType.KNOWLEDGE_CANDIDATE:
            target = self.store.get_knowledge_candidate(review.tenant_id, review.project_id, review.target_id)
            if target is None:
                raise V63NotFound("knowledge candidate not found")
            return target.candidate_fingerprint, tuple(sorted(target.source_fingerprints))
        proposal = self.store.get_proposal(review.tenant_id, review.project_id, review.target_id)
        if proposal is None:
            raise V63NotFound("evolution proposal not found")
        return proposal.proposal_fingerprint, ()

    def _apply_review(self, review: ReviewItem, decided: ReviewItem) -> None:
        if review.target_type is ReviewTargetType.MEMORY_CANDIDATE:
            candidate = self.store.get_memory_candidate(review.tenant_id, review.project_id, review.target_id)
            if candidate is None:
                raise V63NotFound("memory candidate not found")
            if decided.status is ReviewStatus.REJECTED:
                self.store.save_memory_candidate(candidate.with_status(MemoryStatus.REJECTED))
                return
            now = self.clock()
            memory_id = _id("memory-", candidate.tenant_id, candidate.project_id, candidate.candidate_fingerprint)
            provisional = EngineeringMemory.model_construct(
                memory_id=memory_id, tenant_id=candidate.tenant_id, project_id=candidate.project_id,
                memory_type=candidate.memory_type, subject=candidate.subject, statement=candidate.statement,
                evidence_ids=tuple(sorted(candidate.evidence_ids)), source_references=tuple(sorted(candidate.source_references)),
                source_fingerprints=tuple(sorted(candidate.source_fingerprints)), confidence=candidate.confidence,
                limitations=candidate.limitations, review_id=decided.review_id, approved_by=decided.reviewer_id,
                approved_at=decided.decided_at, status=MemoryStatus.ACTIVE, memory_fingerprint="sha256:" + "0" * 64,
                created_at=candidate.created_at, updated_at=now, provenance=candidate.provenance,
            )
            memory = EngineeringMemory.model_validate({**provisional.model_dump(mode="python"), "memory_fingerprint": canonical_fingerprint(provisional, exclude={"memory_fingerprint", "status", "updated_at"})})
            self.store.save_memory_candidate(candidate.with_status(MemoryStatus.APPROVED))
            self.store.save_memory(memory)
            return
        if review.target_type is ReviewTargetType.KNOWLEDGE_CANDIDATE:
            candidate = self.store.get_knowledge_candidate(review.tenant_id, review.project_id, review.target_id)
            if candidate is None:
                raise V63NotFound("knowledge candidate not found")
            if decided.status is ReviewStatus.REJECTED:
                self.store.save_knowledge_candidate(candidate.model_copy(update={"status": KnowledgeStatus.REJECTED}))
                return
            now = self.clock()
            knowledge_id = _id("knowledge-", candidate.tenant_id, candidate.project_id, candidate.candidate_fingerprint)
            provisional = ApprovedKnowledge.model_construct(
                knowledge_id=knowledge_id, tenant_id=candidate.tenant_id, project_id=candidate.project_id,
                scope_identity=project_scope(candidate.project_id), subject=candidate.subject, predicate=candidate.predicate,
                value=candidate.value, evidence_ids=tuple(sorted(candidate.evidence_ids)), source_references=tuple(sorted(candidate.source_references)),
                source_fingerprints=tuple(sorted(candidate.source_fingerprints)), verification_status="VERIFIED", review_id=decided.review_id,
                approved_by=decided.reviewer_id, approved_at=decided.decided_at,
                knowledge_fingerprint="sha256:" + "0" * 64, status=KnowledgeStatus.ACTIVE,
                created_at=candidate.created_at, updated_at=now, provenance=candidate.provenance,
            )
            knowledge = ApprovedKnowledge.model_validate({**provisional.model_dump(mode="python"), "knowledge_fingerprint": canonical_fingerprint(provisional, exclude={"knowledge_fingerprint", "status", "updated_at"})})
            self.store.save_knowledge_candidate(candidate.model_copy(update={"status": KnowledgeStatus.APPROVED}))
            self.store.save_knowledge(knowledge)
            return
        proposal = self.store.get_proposal(review.tenant_id, review.project_id, review.target_id)
        if proposal is None:
            raise V63NotFound("evolution proposal not found")
        if decided.status is ReviewStatus.REJECTED:
            self.store.save_proposal(proposal.model_copy(update={"status": EvolutionStatus.REJECTED}))
        else:
            self.store.save_proposal(proposal.model_copy(update={"status": EvolutionStatus.APPROVED, "review_id": decided.review_id, "approved_by": decided.reviewer_id, "approved_at": decided.decided_at}))

    def _audit(self, event_type: str, value: object, fingerprint: str, evidence_ids: tuple[str, ...]) -> None:
        tenant_id = getattr(value, "tenant_id")
        project_id = getattr(value, "project_id")
        object_id = getattr(value, "review_id", None) or getattr(value, "memory_candidate_id", None) or getattr(value, "knowledge_candidate_id", None) or getattr(value, "memory_id", None) or getattr(value, "knowledge_id", None) or getattr(value, "proposal_id", None) or getattr(value, "event_id", None)
        event = V63AuditEvent(
            event_id=_id("audit-", event_type, tenant_id, project_id, str(object_id), fingerprint),
            event_type=event_type, tenant_id=tenant_id, project_id=project_id,
            object_id=str(object_id), fingerprint=fingerprint, evidence_ids=evidence_ids,
            timestamp=self.clock(),
        )
        self.store.save_audit(event)
        self.store.append_status_history(event)

    def list_reviews(self, tenant_id: str, project_id: str, *, status: ReviewStatus | None = None, target_type: ReviewTargetType | None = None, limit: int | None = None) -> tuple[ReviewItem, ...]:
        values = self.store.list_reviews(tenant_id, project_id, limit=limit)
        return tuple(item for item in values if (status is None or item.status is status) and (target_type is None or item.target_type is target_type))

    def project_memories(self, tenant_id: str, project_id: str, current_evidence: tuple[EvidenceIdentity, ...] = (), *, include_stale: bool = False, limit: int | None = None) -> tuple[MemoryProjection, ...]:
        current = {item.source_fingerprint for item in current_evidence}
        result: list[MemoryProjection] = []
        for memory in self.store.list_memories(tenant_id, project_id, limit=limit):
            freshness = Freshness.CURRENT
            status = memory.status
            if current_evidence and not set(memory.source_fingerprints).issubset(current):
                freshness = Freshness.STALE
                status = MemoryStatus.STALE
            if status is not MemoryStatus.ACTIVE and not (include_stale and status is MemoryStatus.STALE):
                continue
            result.append(MemoryProjection(
                memory_id=memory.memory_id, tenant_id=memory.tenant_id, project_id=memory.project_id,
                memory_type=memory.memory_type, subject=memory.subject, statement=memory.statement,
                status=status, confidence=memory.confidence, evidence_count=len(memory.evidence_ids),
                review_id=memory.review_id, freshness=freshness,
                source_references=memory.source_references, fingerprint=memory.memory_fingerprint,
            ))
        return tuple(result)

    def project_knowledge(self, tenant_id: str, project_id: str, current_evidence: tuple[EvidenceIdentity, ...] = (), *, limit: int | None = None) -> tuple[KnowledgeProjection, ...]:
        current = {item.source_fingerprint for item in current_evidence}
        result: list[KnowledgeProjection] = []
        for knowledge in self.store.list_knowledge(tenant_id, project_id, limit=limit):
            if knowledge.status is not KnowledgeStatus.ACTIVE:
                continue
            if current_evidence and not set(knowledge.source_fingerprints).issubset(current):
                continue
            result.append(KnowledgeProjection(
                knowledge_id=knowledge.knowledge_id, tenant_id=knowledge.tenant_id, project_id=knowledge.project_id,
                subject=knowledge.subject, predicate=knowledge.predicate, value=knowledge.value,
                status=knowledge.status, verification_status=knowledge.verification_status,
                evidence_count=len(knowledge.evidence_ids), review_id=knowledge.review_id,
                source_references=knowledge.source_references, fingerprint=knowledge.knowledge_fingerprint,
            ))
        return tuple(result)

    def create_evolution_proposal(self, *, tenant_id: str, project_id: str, knowledge_id: str, created_by: str, target: EvolutionTarget, statement: str) -> KnowledgeEvolutionProposal:
        knowledge = self.store.get_knowledge(tenant_id, project_id, knowledge_id)
        if knowledge is None or knowledge.status is not KnowledgeStatus.ACTIVE:
            raise V63Rejected("approved knowledge is unavailable")
        proposal = KnowledgeEvolutionProposal.create(
            proposal_id=_id("evolution-", tenant_id, project_id, knowledge.knowledge_id, target.value, statement),
            tenant_id=tenant_id, project_id=project_id, source_knowledge_id=knowledge.knowledge_id,
            source_knowledge_fingerprint=knowledge.knowledge_fingerprint, target=target, statement=statement,
            evidence_ids=knowledge.evidence_ids, created_by=created_by, created_at=self.clock(),
        )
        existing = self.store.get_proposal(tenant_id, project_id, proposal.proposal_id)
        if existing is not None:
            return existing
        stored = self.store.save_proposal(proposal)
        self._audit("EVOLUTION_PROPOSED", stored, stored.proposal_fingerprint, stored.evidence_ids)
        return stored

    def request_evolution_review(self, *, tenant_id: str, project_id: str, proposal_id: str, requester_id: str) -> ReviewItem:
        proposal = self.store.get_proposal(tenant_id, project_id, proposal_id)
        if proposal is None or proposal.status is not EvolutionStatus.CREATED:
            raise V63Rejected("proposal is not reviewable")
        return self._create_review(tenant_id=tenant_id, project_id=project_id, requester_id=requester_id, target_type=ReviewTargetType.KNOWLEDGE_EVOLUTION_PROPOSAL, target_id=proposal.proposal_id, target_fingerprint=proposal.proposal_fingerprint, evidence_fingerprints=())

    def apply_evolution(self, request: EvolutionApplyRequest) -> str:
        proposal = self.store.get_proposal(request.tenant_id, request.project_id, request.proposal_id)
        if proposal is None or proposal.status is not EvolutionStatus.APPROVED or proposal.proposal_fingerprint != request.proposal_fingerprint or proposal.review_id != request.review_id:
            raise V63Rejected("evolution apply is unavailable")
        return "SINK_UNAVAILABLE"

    def tombstone_project(self, tenant_id: str, project_id: str) -> None:
        inserted = self.store.tombstone_project(tenant_id, project_id, self.clock().isoformat())
        if inserted:
            self._audit("PROJECT_MEMORY_TOMBSTONED", type("Tombstone", (), {"tenant_id": tenant_id, "project_id": project_id, "event_id": project_id})(), "sha256:" + hashlib.sha256(f"{tenant_id}:{project_id}".encode()).hexdigest(), ())

    def is_project_inaccessible(self, tenant_id: str, project_id: str) -> bool:
        return self.store.is_tombstoned(tenant_id, project_id)


def project_scope(project_id: str) -> str:
    return "project:" + project_id


LiteralDecision = str


__all__ = ("V63Conflict", "V63Error", "V63MemoryGovernanceService", "V63NotFound", "V63Rejected", "V63Unavailable")
