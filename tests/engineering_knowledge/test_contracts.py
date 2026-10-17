from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_knowledge import (
    EngineeringRelation,
    EngineeringKnowledgeNode,
    NodeType,
    RelationType,
)


def _node(entity_name: str = "cafe\u0301") -> EngineeringKnowledgeNode:
    return EngineeringKnowledgeNode.create(
        node_id="memory-1",
        project_id="project-1",
        node_type=NodeType.DECISION,
        entity_name=entity_name,
        summary="approved decision",
        source_memory_id="memory-1",
        source_reference="decision:1",
        confidence=0.9,
        verification_status="APPROVED",
    )


def test_node_is_nfc_normalized_frozen_and_fingerprinted() -> None:
    node = _node()
    assert node.entity_name == "café"
    assert node.fingerprint.startswith("sha256:")
    with pytest.raises(ValidationError):
        node.entity_name = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EngineeringKnowledgeNode.model_validate(
            {**node.model_dump(mode="python"), "unexpected": "value"}
        )


def test_node_rejects_non_tuple_or_tampered_fingerprint() -> None:
    node = _node("board")
    values = node.model_dump(mode="python")
    values["fingerprint"] = "sha256:" + "a" * 64
    with pytest.raises(ValidationError):
        EngineeringKnowledgeNode.model_validate(values)


def test_contract_model_configuration_is_strict() -> None:
    node = _node("board")
    assert node.model_config["frozen"] is True
    assert node.model_config["strict"] is True
    assert node.model_config["extra"] == "forbid"
    assert node.model_config["revalidate_instances"] == "always"


def test_relation_retains_evidence_provenance_and_verification_status() -> None:
    relation = EngineeringRelation.create(
        relation_id="relation-1",
        source_node_id="memory-1",
        target_node_id="memory-2",
        relation_type=RelationType.USES,
        confidence=0.9,
        source_memory_id="memory-1",
        provenance="decision:1",
        evidence=("conversation:1",),
        verification_status="APPROVED",
    )
    assert relation.provenance == "decision:1"
    assert relation.evidence == ("conversation:1",)
    assert relation.verification_status == "APPROVED"

    values = relation.model_dump(mode="python")
    values["evidence"] = ["conversation:1"]
    with pytest.raises(ValidationError):
        EngineeringRelation.model_validate(values)

    values = relation.model_dump(mode="python")
    values["provenance"] = "decision:2"
    with pytest.raises(ValidationError):
        EngineeringRelation.model_validate(values)
