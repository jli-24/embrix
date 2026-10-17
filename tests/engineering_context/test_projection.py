from __future__ import annotations

import hashlib
import json

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    ReadOnlyProjectionSource,
    project_graph_snapshot,
    project_knowledge_evidence,
    project_memory_projection,
    project_multimodal_projection,
)
from embedded_copilot.engineering_knowledge import (
    EngineeringGraphSnapshot,
    EngineeringKnowledgeNode,
    EngineeringRelation,
    NodeType,
    RelationType,
)
from embedded_copilot.engineering_memory.projection import ApprovedMemoryProjection
from embedded_copilot.knowledge.source import KnowledgeEvidence, KnowledgeSourceType
from embedded_copilot.multimodal_input import (
    EngineeringInterpretation,
    InputType,
    MultimodalAnalysisProjection,
    VisionObservation,
)


def _memory() -> ApprovedMemoryProjection:
    material = {
        "project_id": "project-1",
        "memory_id": "memory-1",
        "record_id": "record-1",
        "memory_type": "DECISION",
        "status": "APPROVED",
        "source_reference": "decision:1",
        "source_revision": "revision:1",
        "title": "GPIO12",
        "summary": "GPIO12 is reserved",
        "evidence_references": ("decision:1",),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return ApprovedMemoryProjection.model_validate(
        {**material, "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()}
    )


def test_projection_adapters_revalidate_memory_and_knowledge() -> None:
    memory = project_memory_projection((_memory(),), "project-1")
    official = KnowledgeEvidence(
        source_id="datasheet-1",
        source_type=KnowledgeSourceType.DATASHEET,
        summary="GPIO12 is reserved",
        relevance_score=0.8,
    )
    web = KnowledgeEvidence(
        source_id="web-1",
        source_type=KnowledgeSourceType.WEB,
        summary="GPIO12 may be used",
        relevance_score=0.7,
    )
    knowledge = project_knowledge_evidence((official, web), "project-1")
    assert memory[0].verification_status == "APPROVED"
    assert knowledge[0].verification_status == "VERIFIED"
    assert knowledge[1].verification_status == "UNVERIFIED"


def test_graph_conflicts_are_projected_explicitly() -> None:
    nodes = tuple(
        EngineeringKnowledgeNode.create(
            node_id=node_id,
            project_id="project-1",
            node_type=NodeType.CONSTRAINT,
            entity_name="GPIO12",
            summary=summary,
            source_memory_id=memory_id,
            source_reference=f"decision:{memory_id}",
            confidence=0.9,
            verification_status="APPROVED",
        )
        for node_id, memory_id, summary in (
            ("node-a", "memory-a", "GPIO12 is reserved"),
            ("node-b", "memory-b", "GPIO12 is SPI flash"),
        )
    )
    relation = EngineeringRelation.create(
        relation_id="relation-1",
        source_node_id="node-a",
        target_node_id="node-b",
        relation_type=RelationType.CONFLICTS_WITH,
        confidence=0.9,
        source_memory_id="memory-a",
    )
    graph = EngineeringGraphSnapshot.create(
        project_id="project-1", nodes=nodes, relations=(relation,)
    )
    projected = project_graph_snapshot(graph, "project-1")
    assert len(projected.evidence) == 2
    assert len(projected.conflicts) == 1
    assert projected.conflicts[0].source_a == "graph-node-a"
    assert projected.conflicts[0].source_b == "graph-node-b"


def test_multimodal_projection_uses_observation_only() -> None:
    observation = VisionObservation.create(
        observation_id="observation-1",
        project_id="project-1",
        source_reference="image:1",
        observation_type=InputType.PCB,
        content=("GPIO12 trace observed",),
        confidence=0.7,
    )
    interpretation = EngineeringInterpretation.create(
        interpretation_id="interpretation-1",
        observation_reference="observation-1",
        summary="Unverified interpretation must not become context evidence",
        risk="Needs review",
        confidence=0.6,
    )
    projection = MultimodalAnalysisProjection.create(
        observation=observation, interpretation=interpretation
    )
    evidence = project_multimodal_projection((projection,), "project-1")
    assert len(evidence) == 1
    assert evidence[0].summary == "GPIO12 trace observed"
    assert "interpretation" not in evidence[0].summary.casefold()


def test_projection_source_reads_graph_port_once() -> None:
    class MemoryPort:
        def list_approved(self, project_id):
            return (_memory(),)

    class GraphPort:
        def __init__(self):
            self.calls = 0

        def project(self, project_id):
            self.calls += 1

    graph = GraphPort()
    source = ReadOnlyProjectionSource(memory_port=MemoryPort(), graph_port=graph)
    projection = source.read_graph(
        EngineeringContextRequest(project_id="project-1", task="GPIO12")
    )
    assert projection.evidence == ()
    assert projection.conflicts == ()
    assert graph.calls == 1
