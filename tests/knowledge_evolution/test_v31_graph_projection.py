from __future__ import annotations

import pytest

from embedded_copilot.engineering_knowledge import (
    EngineeringGraphSnapshot,
    EngineeringKnowledgeNode,
    NodeType,
)
from embedded_copilot.knowledge_evolution import (
    EngineeringKnowledgeGraphEvolutionService,
)


class _GraphProjection:
    def __init__(self, snapshot: EngineeringGraphSnapshot) -> None:
        self.snapshot = snapshot
        self.requests: list[str] = []

    def project(self, project_id: str) -> EngineeringGraphSnapshot:
        self.requests.append(project_id)
        return self.snapshot.model_copy(deep=True)


def _snapshot() -> EngineeringGraphSnapshot:
    node = EngineeringKnowledgeNode.create(
        node_id="memory-1",
        project_id="project-1",
        node_type=NodeType.DECISION,
        entity_name="memory-1",
        summary="approved decision",
        source_memory_id="memory-1",
        source_reference="decision:1",
        confidence=0.9,
        verification_status="APPROVED",
    )
    return EngineeringGraphSnapshot.create(
        project_id="project-1",
        nodes=(node,),
        relations=(),
    )


def test_knowledge_evolution_consumes_graph_read_only() -> None:
    source = _GraphProjection(_snapshot())
    service = EngineeringKnowledgeGraphEvolutionService(source)

    result = service.get_snapshot("project-1")

    assert result is not None
    assert result.fingerprint == source.snapshot.fingerprint
    assert source.requests == ["project-1"]
    assert not hasattr(service, "update")
    assert not hasattr(service, "mutate")

    with pytest.raises((TypeError, ValueError)):
        result.project_id = "project-2"  # type: ignore[misc]
