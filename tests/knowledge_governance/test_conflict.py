from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_context import ContextConflict
from embedded_copilot.engineering_knowledge import (
    EngineeringGraphSnapshot,
    EngineeringKnowledgeNode,
    EngineeringRelation,
    RelationType,
)
from embedded_copilot.knowledge_governance import (
    ConflictResolution,
    ConflictService,
    GovernanceRejected,
    InMemoryReviewStore,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def test_context_conflict_requires_explicit_human_resolution() -> None:
    conflict = ContextConflict.create(project_id="project-1", source_a="evidence-a", source_b="evidence-b", reason="explicit conflict")
    result = ConflictService(store=InMemoryReviewStore()).resolve_context_conflict(
        conflict, resolution=ConflictResolution.KEEP_BOTH, reviewer_identity="reviewer-1", resolved_at=NOW,
    )
    assert result.resolution is ConflictResolution.KEEP_BOTH
    assert result.source_a == "evidence-a"


def test_graph_conflict_rejects_nonconflict_relations() -> None:
    node_a = EngineeringKnowledgeNode.create(node_id="node-a", project_id="project-1", node_type="COMPONENT", entity_name="A", summary="A", source_memory_id="memory-a", source_reference="memory:a", confidence=0.8)
    node_b = EngineeringKnowledgeNode.create(node_id="node-b", project_id="project-1", node_type="COMPONENT", entity_name="B", summary="B", source_memory_id="memory-b", source_reference="memory:b", confidence=0.8)
    relation = EngineeringRelation.create(relation_id="relation-1", source_node_id="node-a", target_node_id="node-b", relation_type=RelationType.USES, source_memory_id="memory-a", confidence=0.8)
    snapshot = EngineeringGraphSnapshot.create(project_id="project-1", nodes=(node_a, node_b), relations=(relation,))
    with pytest.raises(GovernanceRejected):
        ConflictService(store=InMemoryReviewStore()).resolve_graph_conflict(
            snapshot, relation, resolution=ConflictResolution.ACCEPT_A, reviewer_identity="reviewer-1", resolved_at=NOW,
        )
