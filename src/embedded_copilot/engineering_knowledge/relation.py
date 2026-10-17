from __future__ import annotations

import hashlib
import re

from .exceptions import EngineeringKnowledgeProjectionRejected
from .models import EngineeringKnowledgeNode, EngineeringRelation, RelationType

_RELATION = re.compile(
    r"^RELATION\s+(?P<kind>[A-Z_]+)\s+(?P<source>[A-Za-z0-9._:#/-]+)"
    r"\s*->\s*(?P<target>[A-Za-z0-9._:#/-]+)$"
)


def _relation_id(kind: str, source: str, target: str, memory_id: str) -> str:
    material = f"{kind}|{source}|{target}|{memory_id}".encode()
    return "relation-" + hashlib.sha256(material).hexdigest()[:32]


class DeterministicRelationProjector:
    """Projects only explicit, evidence-backed relation declarations."""

    def project(
        self,
        memories: tuple[object, ...],
        nodes: tuple[EngineeringKnowledgeNode, ...],
    ) -> tuple[EngineeringRelation, ...]:
        node_ids = frozenset(node.node_id for node in nodes)
        relations: list[EngineeringRelation] = []
        for memory in memories:
            for value in (memory.summary, memory.decision, memory.reason):
                if not value.startswith("RELATION"):
                    continue
                match = _RELATION.fullmatch(value)
                if match is None:
                    raise EngineeringKnowledgeProjectionRejected()
                try:
                    relation_type = RelationType(match.group("kind"))
                except ValueError as error:
                    raise EngineeringKnowledgeProjectionRejected() from error
                source_node_id = f"memory-{match.group('source')}"
                target_node_id = f"memory-{match.group('target')}"
                if (
                    source_node_id not in node_ids
                    or target_node_id not in node_ids
                ):
                    raise EngineeringKnowledgeProjectionRejected()
                relations.append(
                    EngineeringRelation.create(
                        relation_id=_relation_id(
                            relation_type.value,
                            source_node_id,
                            target_node_id,
                            memory.memory_id,
                        ),
                        source_node_id=source_node_id,
                        target_node_id=target_node_id,
                        relation_type=relation_type,
                        confidence=memory.confidence,
                        source_memory_id=memory.memory_id,
                        provenance=memory.source_reference,
                        evidence=memory.evidence,
                        verification_status=memory.status,
                    )
                )
        ordered = tuple(
            sorted(relations, key=lambda relation: relation.relation_id)
        )
        if len({relation.relation_id for relation in ordered}) != len(ordered):
            raise EngineeringKnowledgeProjectionRejected()
        return ordered


__all__ = ("DeterministicRelationProjector",)
