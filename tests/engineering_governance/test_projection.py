from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextConflict,
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_governance import (
    CandidateSourceType,
    KnowledgeCandidateProjectionService,
)


def evidence(
    evidence_id: str,
    source_type: ContextSourceType,
    summary: str,
    source_reference: str,
    *,
    fact_key: str | None = None,
    confidence: float | None = 0.8,
) -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        project_id="project-1",
        source_type=source_type,
        subject="interface-1",
        summary=summary,
        fact_key=fact_key,
        fact_value="UART" if fact_key is not None else None,
        source_reference=source_reference,
        source_fingerprint="sha256:" + evidence_id[-1] * 64,
        confidence=confidence,
        verification_status="PROJECTED",
    )


def snapshot() -> EngineeringContextSnapshot:
    memory = evidence(
        "memory-1",
        ContextSourceType.ENGINEERING_MEMORY,
        "GPIO12 is reserved",
        "memory:item001",
        confidence=None,
    )
    graph = evidence(
        "graph-1",
        ContextSourceType.KNOWLEDGE_GRAPH,
        "configured interface",
        "graph:relation001",
        fact_key="interface-1.interface",
    )
    vision = evidence(
        "vision-1",
        ContextSourceType.MULTIMODAL_OBSERVATION,
        "observed component: STM32",
        "vision:obs001",
    )
    conflict = ContextConflict.create(
        project_id="project-1",
        source_a="graph-1",
        source_b="vision-1",
        reason="interface evidence differs",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="inspect board",
        memory_evidence=(memory,),
        graph_evidence=(graph,),
        knowledge_evidence=(),
        multimodal_evidence=(vision,),
        conflicts=(conflict,),
        confidence=0.6,
        provenance=(memory.source(), graph.source(), vision.source()),
        verification_status="REQUIRES_REVIEW",
    )


def test_projection_is_explicit_deterministic_and_non_mutating() -> None:
    value = snapshot()
    before = value.model_dump(mode="python")
    first = KnowledgeCandidateProjectionService().project(value)
    second = KnowledgeCandidateProjectionService().project(value)

    assert before == value.model_dump(mode="python")
    assert [(item.claim, item.source_type) for item in first] == [
        ("GPIO12 is reserved", CandidateSourceType.MEMORY),
        ("configured interface", CandidateSourceType.GRAPH),
        ("STM32 component observed", CandidateSourceType.MULTIMODAL),
        ("Interface evidence conflict detected", CandidateSourceType.MULTIMODAL),
    ]
    assert tuple(item.fingerprint for item in first) == tuple(
        item.fingerprint for item in second
    )
    assert first[-1].evidence_refs == ("graph:relation001", "vision:obs001")


def test_projection_rejects_conflicts_that_do_not_bind_to_explicit_evidence() -> None:
    value = snapshot()
    material = value.model_dump(mode="python")
    material.pop("context_id")
    material.pop("fingerprint")
    material["conflicts"] = (
        ContextConflict.create(
            project_id="project-1",
            source_a="missing-a",
            source_b="missing-b",
            reason="unbound conflict",
        ),
    )
    broken = EngineeringContextSnapshot.create(**material)
    from embedded_copilot.engineering_governance import GovernanceRejected

    with pytest.raises(GovernanceRejected):
        KnowledgeCandidateProjectionService().project(broken)
