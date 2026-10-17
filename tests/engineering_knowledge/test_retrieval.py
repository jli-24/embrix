from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_knowledge import (
    EngineeringContextQuery,
    EngineeringContextRetrievalService,
    EngineeringKnowledgeGraphProjectionService,
)
from embedded_copilot.engineering_memory.contracts import (
    ApprovalAudit,
    ApprovedEngineeringMemory,
    EngineeringMemoryType,
)


class _Projection:
    def __init__(self, values):
        self.values = tuple(values)

    def list_approved(self, project_id: str):
        return tuple(value.model_copy(deep=True) for value in self.values)


def _memory(memory_id: str, summary: str) -> ApprovedEngineeringMemory:
    return ApprovedEngineeringMemory.create(
        memory_id=memory_id,
        project_id="project-1",
        source_reference=f"decision:{memory_id}",
        memory_type=EngineeringMemoryType.DECISION,
        summary=summary,
        decision="selected",
        reason="evidence",
        confidence=0.8,
        evidence=(),
        approval_audit=ApprovalAudit(
            approval_id=f"approval-{memory_id}",
            candidate_fingerprint="sha256:" + "b" * 64,
            reviewer="reviewer-1",
            decision="APPROVED",
            approved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )


def test_retrieval_is_deterministic_and_project_bound() -> None:
    projection = EngineeringKnowledgeGraphProjectionService(
        _Projection(
            (
                _memory("1", "camera interface decision"),
                _memory("2", "power constraint decision"),
            )
        )
    )
    service = EngineeringContextRetrievalService(projection)
    query = EngineeringContextQuery(project_id="project-1", query="camera")
    snapshots = [service.get_context(query) for _ in range(100)]
    assert all(snapshot is not None for snapshot in snapshots)
    assert len({snapshot.fingerprint for snapshot in snapshots}) == 1
    assert snapshots[0].related_nodes[0].summary == "camera interface decision"
