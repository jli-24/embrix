from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

import pytest

from embedded_copilot.v63_memory_governance import (
    SQLiteV63Store,
    V63Conflict,
    V63MemoryGovernanceService,
    V63NotFound,
    V63Rejected,
)
from embedded_copilot.v63_memory_governance.models import (
    EvidenceIdentity,
    EvolutionApplyRequest,
    EvolutionTarget,
    KnowledgeStatus,
    MemoryStatus,
    MemoryType,
    StructuredCandidateInput,
    TrustLevel,
)


FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64


def _evidence(*, tenant: str = "tenant-1", project: str = "project-1", source_fp: str = FP_A) -> EvidenceIdentity:
    return EvidenceIdentity(
        evidence_id="evidence-1",
        tenant_id=tenant,
        project_id=project,
        source_reference="src:evidence-1",
        source_fingerprint=source_fp,
        evidence_fingerprint=FP_B,
    )


def _service(tmp_path) -> V63MemoryGovernanceService:
    return V63MemoryGovernanceService(SQLiteV63Store(tmp_path / "v63.sqlite3"))


def _candidate(service: V63MemoryGovernanceService):
    return service.extract_memory_candidate(
        StructuredCandidateInput(
            tenant_id="tenant-1",
            requester_id="engineer-1",
            project_id="project-1",
            memory_type=MemoryType.DECISION,
            subject="power rail",
            statement="Use the approved 3V3 rail.",
            evidence=(_evidence(),),
            confidence=0.9,
            provenance=("decision:evidence-1",),
        )
    )


def test_deterministic_candidate_extraction_is_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    first = _candidate(service)
    second = _candidate(service)
    independent = _candidate(_service(tmp_path / "independent"))
    assert first == second
    assert first.status is MemoryStatus.CANDIDATE
    assert first.candidate_fingerprint == second.candidate_fingerprint
    assert first.candidate_fingerprint == independent.candidate_fingerprint


def test_review_requires_requester_and_is_fingerprint_bound(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = _candidate(service)
    with pytest.raises(V63Rejected):
        service.request_memory_review(
            tenant_id="tenant-1", project_id="project-1",
            candidate_id=candidate.memory_candidate_id, requester_id="other-engineer",
        )
    review = service.request_memory_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.memory_candidate_id, requester_id="engineer-1",
    )
    approved = service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    assert approved.status.value == "APPROVED"
    assert len(service.project_memories("tenant-1", "project-1")) == 1
    assert service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    ) == approved
    with pytest.raises(V63Conflict):
        service.decide_review(
            tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
            reviewer_id="other-reviewer", decision="REJECT",
        )


def test_review_replay_does_not_duplicate_promotion_records_or_audit_history(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = _candidate(service)
    review = service.request_memory_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.memory_candidate_id, requester_id="engineer-1",
    )
    first = service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    second = service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    assert second == first
    assert len(service.store.list_memories("tenant-1", "project-1")) == 1
    audit = service.store.list_audit("tenant-1", "project-1")
    assert sum(item.event_type == "MEMORY_APPROVED" for item in audit) == 1
    history = service.store.list_status_history("tenant-1", "project-1")
    assert sum(item.event_type == "MEMORY_APPROVED" for item in history) == 1


def test_evolution_proposal_retry_and_tombstone_retry_are_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = service.extract_knowledge_candidate(
        tenant_id="tenant-1", project_id="project-1", requester_id="engineer-1",
        knowledge_type="constraint", subject="rail", predicate="voltage", value="3V3",
        evidence=(_evidence(),), trust_level=TrustLevel.CANDIDATE, confidence=0.8,
    )
    review = service.request_knowledge_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.knowledge_candidate_id, requester_id="engineer-1",
    )
    service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    knowledge = service.store.list_knowledge("tenant-1", "project-1")[0]
    first = service.create_evolution_proposal(
        tenant_id="tenant-1", project_id="project-1", knowledge_id=knowledge.knowledge_id,
        created_by="engineer-1", target=EvolutionTarget.MEMORY, statement="Keep as project reference.",
    )
    second = service.create_evolution_proposal(
        tenant_id="tenant-1", project_id="project-1", knowledge_id=knowledge.knowledge_id,
        created_by="engineer-1", target=EvolutionTarget.MEMORY, statement="Keep as project reference.",
    )
    assert second == first
    assert len(service.store.list_proposals("tenant-1", "project-1")) == 1
    service.tombstone_project("tenant-1", "project-1")
    service.tombstone_project("tenant-1", "project-1")
    audit = service.store.list_audit("tenant-1", "project-1")
    history = service.store.list_status_history("tenant-1", "project-1")
    assert sum(item.event_type == "PROJECT_MEMORY_TOMBSTONED" for item in audit) == 1
    assert sum(item.event_type == "PROJECT_MEMORY_TOMBSTONED" for item in history) == 1


def test_cross_tenant_and_cross_project_reads_fail_closed(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = _candidate(service)
    assert service.store.get_memory_candidate("tenant-2", "project-1", candidate.memory_candidate_id) is None
    assert service.store.get_memory_candidate("tenant-1", "project-2", candidate.memory_candidate_id) is None
    with pytest.raises(V63NotFound):
        service.request_memory_review(
            tenant_id="tenant-2", project_id="project-1",
            candidate_id=candidate.memory_candidate_id, requester_id="engineer-1",
        )


def test_stale_and_revoked_memory_never_enter_active_projection(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = _candidate(service)
    review = service.request_memory_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.memory_candidate_id, requester_id="engineer-1",
    )
    service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    assert service.project_memories("tenant-1", "project-1", (_evidence(source_fp=FP_B),)) == ()
    memory = service.store.list_memories("tenant-1", "project-1")[0]
    service.store.save_memory(memory.model_copy(update={"status": MemoryStatus.REVOKED}))
    assert service.project_memories("tenant-1", "project-1") == ()


def test_knowledge_requires_evidence_and_evolution_apply_is_unavailable(tmp_path) -> None:
    service = _service(tmp_path)
    with pytest.raises(V63Rejected, match="INSUFFICIENT_EVIDENCE"):
        service.extract_knowledge_candidate(
            tenant_id="tenant-1", project_id="project-1", requester_id="engineer-1",
            knowledge_type="constraint", subject="rail", predicate="voltage", value="3V3",
            evidence=(),
        )
    candidate = service.extract_knowledge_candidate(
        tenant_id="tenant-1", project_id="project-1", requester_id="engineer-1",
        knowledge_type="constraint", subject="rail", predicate="voltage", value="3V3",
        evidence=(_evidence(),), trust_level=TrustLevel.CANDIDATE, confidence=0.8,
    )
    review = service.request_knowledge_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.knowledge_candidate_id, requester_id="engineer-1",
    )
    service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    knowledge = service.store.list_knowledge("tenant-1", "project-1")[0]
    proposal = service.create_evolution_proposal(
        tenant_id="tenant-1", project_id="project-1", knowledge_id=knowledge.knowledge_id,
        created_by="engineer-1", target=EvolutionTarget.MEMORY, statement="Project reference only.",
    )
    proposal_review = service.request_evolution_review(
        tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id,
        requester_id="engineer-1",
    )
    service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=proposal_review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    stored = service.store.get_proposal("tenant-1", "project-1", proposal.proposal_id)
    assert stored is not None and stored.status.value == "APPROVED"
    assert service.apply_evolution(EvolutionApplyRequest(
            request_id="apply-1", tenant_id="tenant-1", project_id="project-1",
            proposal_id=proposal.proposal_id, proposal_fingerprint=stored.proposal_fingerprint,
            review_id=proposal_review.review_id, requested_by="engineer-1",
            requested_at=datetime.now(UTC),
        )) == "SINK_UNAVAILABLE"


def test_tombstone_is_immediate_and_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    _candidate(service)
    service.tombstone_project("tenant-1", "project-1")
    service.tombstone_project("tenant-1", "project-1")
    assert service.is_project_inaccessible("tenant-1", "project-1")
    assert service.store.list_memory_candidates("tenant-1", "project-1") == ()
    with pytest.raises(V63Rejected):
        _candidate(service)


def test_sqlite_migration_and_restart_preserve_fingerprint(tmp_path) -> None:
    database = tmp_path / "restart.sqlite3"
    first = V63MemoryGovernanceService(SQLiteV63Store(database))
    candidate = _candidate(first)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    restarted = V63MemoryGovernanceService(SQLiteV63Store(database))
    loaded = restarted.store.get_memory_candidate("tenant-1", "project-1", candidate.memory_candidate_id)
    assert loaded is not None
    assert loaded.candidate_fingerprint == candidate.candidate_fingerprint
