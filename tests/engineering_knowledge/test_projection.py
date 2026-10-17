from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_knowledge import (
    EngineeringKnowledgeGraphProjectionService,
    EngineeringKnowledgeProjectionRejected,
)
from embedded_copilot.engineering_memory.contracts import (
    ApprovalAudit,
    ApprovedEngineeringMemory,
    EngineeringMemoryType,
)

FP = "sha256:" + "a" * 64


def _memory(
    memory_id: str,
    *,
    summary: str = "approved decision",
    memory_type: EngineeringMemoryType = EngineeringMemoryType.DECISION,
) -> ApprovedEngineeringMemory:
    return ApprovedEngineeringMemory.create(
        memory_id=memory_id,
        project_id="project-1",
        source_reference=f"decision:{memory_id}",
        memory_type=memory_type,
        summary=summary,
        decision="selected",
        reason="evidence",
        confidence=0.9,
        evidence=(f"conversation:{memory_id}",),
        approval_audit=ApprovalAudit(
            approval_id=f"approval-{memory_id}",
            candidate_fingerprint=FP,
            reviewer="reviewer-1",
            decision="APPROVED",
            approved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )


class _MemoryProjection:
    def __init__(self, values: tuple[ApprovedEngineeringMemory, ...]) -> None:
        self.values = values
        self.requests: list[str] = []

    def list_approved(self, project_id: str):
        self.requests.append(project_id)
        return tuple(value.model_copy(deep=True) for value in self.values)


def test_projection_is_approved_only_and_read_only() -> None:
    source = _MemoryProjection((_memory("1"), _memory("2")))
    snapshot = EngineeringKnowledgeGraphProjectionService(source).project("project-1")
    assert snapshot is not None
    assert tuple(node.node_id for node in snapshot.nodes) == (
        "memory-1",
        "memory-2",
    )
    assert snapshot.relations == ()
    assert source.requests == ["project-1"]


def test_explicit_relation_is_projected_without_semantic_inference() -> None:
    source = _MemoryProjection(
        (
            _memory("1", summary="RELATION USES 1 -> 2"),
            _memory("2"),
        )
    )
    snapshot = EngineeringKnowledgeGraphProjectionService(source).project("project-1")
    assert snapshot is not None
    assert len(snapshot.relations) == 1
    assert snapshot.relations[0].source_node_id == "memory-1"
    assert snapshot.relations[0].target_node_id == "memory-2"
    assert snapshot.relations[0].provenance == "decision:1"
    assert snapshot.relations[0].evidence == ("conversation:1",)
    assert snapshot.relations[0].verification_status == "APPROVED"


def test_projection_rejects_project_mismatch() -> None:
    source = _MemoryProjection((_memory("1"),))
    with pytest.raises(EngineeringKnowledgeProjectionRejected):
        EngineeringKnowledgeGraphProjectionService(source).project("project-2")


def test_pending_projection_fails_closed() -> None:
    value = _memory("1")
    pending = value.model_construct(
        **{**value.model_dump(mode="python"), "status": "PENDING"}
    )
    source = _MemoryProjection((pending,))
    with pytest.raises(EngineeringKnowledgeProjectionRejected):
        EngineeringKnowledgeGraphProjectionService(source).project("project-1")


def test_unknown_relation_is_rejected() -> None:
    source = _MemoryProjection(
        (_memory("1", summary="RELATION INVENTS 1 -> 2"), _memory("2"))
    )
    with pytest.raises(EngineeringKnowledgeProjectionRejected):
        EngineeringKnowledgeGraphProjectionService(source).project("project-1")
