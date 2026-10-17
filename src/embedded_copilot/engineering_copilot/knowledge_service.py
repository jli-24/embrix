from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    DesignComponent,
    DesignInterface,
    DesignSignal,
    DesignTruth,
    DesignTruthStatus,
    EngineeringKnowledgeEntity,
    EngineeringKnowledgeEntityType,
    EngineeringKnowledgeModel,
    EngineeringKnowledgeRelationship,
    EngineeringKnowledgeRelationshipType,
    KnowledgeConfidence,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _confidence(value: float) -> KnowledgeConfidence:
    if value >= 0.8:
        return KnowledgeConfidence.HIGH
    if value >= 0.5:
        return KnowledgeConfidence.MEDIUM
    return KnowledgeConfidence.LOW


def _component_type(value: DesignComponent) -> EngineeringKnowledgeEntityType:
    explicit = {value.type.upper(), value.category.upper()}
    if "SYSTEM" in explicit:
        return EngineeringKnowledgeEntityType.SYSTEM
    if "SUBSYSTEM" in explicit:
        return EngineeringKnowledgeEntityType.SUBSYSTEM
    return EngineeringKnowledgeEntityType.COMPONENT


def _interface_label(value: DesignInterface) -> str:
    return f"{value.protocol} Interface"


def _signal_label(value: DesignSignal) -> str:
    return value.name


def _entity(
    *,
    knowledge_id: str,
    source_ref: str,
    entity_type: EngineeringKnowledgeEntityType,
    name: str,
    description: str,
    responsibility: str,
    truth_level,
    confidence: float,
    evidence_bindings: tuple[str, ...],
) -> EngineeringKnowledgeEntity:
    return EngineeringKnowledgeEntity.create(
        entity_id=_id("knowledge-entity", knowledge_id, source_ref),
        knowledge_id=knowledge_id,
        entity_type=entity_type,
        name=name,
        description=description,
        responsibility=responsibility,
        truth_level=truth_level,
        confidence=_confidence(confidence),
        source_truth_refs=(source_ref,),
        evidence_bindings=evidence_bindings,
    )


def _relationship(
    *,
    knowledge_id: str,
    source_entity_id: str,
    target_entity_id: str,
    source_ref: str,
    description: str,
    evidence_bindings: tuple[str, ...],
) -> EngineeringKnowledgeRelationship:
    return EngineeringKnowledgeRelationship.create(
        relationship_id=_id(
            "knowledge-relationship",
            knowledge_id,
            source_ref,
            source_entity_id,
            target_entity_id,
        ),
        knowledge_id=knowledge_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=EngineeringKnowledgeRelationshipType.CONNECTS,
        description=description,
        evidence_bindings=evidence_bindings,
    )


def generate_knowledge_model(design_truth: DesignTruth) -> EngineeringKnowledgeModel:
    """Project an approved DesignTruth into a deterministic read-only model."""

    if type(design_truth) is not DesignTruth:
        raise TypeError("design truth is invalid")
    checked = validate_copy(design_truth, DesignTruth)
    if checked.status is not DesignTruthStatus.APPROVED:
        raise ValueError("knowledge model requires approved design truth")

    knowledge_id = _id(
        "knowledge",
        checked.project_id,
        checked.conversation_id,
        checked.fingerprint,
    )
    binding_ids = tuple(sorted(item.binding_id for item in checked.evidence_bindings))
    entities: list[EngineeringKnowledgeEntity] = []
    entity_by_truth_ref: dict[str, EngineeringKnowledgeEntity] = {}

    for item in checked.components:
        entity = _entity(
            knowledge_id=knowledge_id,
            source_ref=item.component_id,
            entity_type=_component_type(item),
            name=item.name,
            description=item.description,
            responsibility=item.category,
            truth_level=item.truth_level,
            confidence=item.confidence,
            evidence_bindings=item.evidence_refs,
        )
        entities.append(entity)
        entity_by_truth_ref[item.component_id] = entity

    for item in checked.interfaces:
        entity = _entity(
            knowledge_id=knowledge_id,
            source_ref=item.interface_id,
            entity_type=EngineeringKnowledgeEntityType.INTERFACE,
            name=_interface_label(item),
            description=f"{item.protocol} interface in {item.direction} direction",
            responsibility=item.direction,
            truth_level=item.truth_level,
            confidence=item.confidence,
            evidence_bindings=item.evidence_refs,
        )
        entities.append(entity)
        entity_by_truth_ref[item.interface_id] = entity

    for item in checked.signals:
        entity = _entity(
            knowledge_id=knowledge_id,
            source_ref=item.signal_id,
            entity_type=EngineeringKnowledgeEntityType.SIGNAL,
            name=_signal_label(item),
            description=item.type,
            responsibility=item.direction,
            truth_level=item.truth_level,
            confidence=item.confidence,
            evidence_bindings=item.evidence_refs,
        )
        entities.append(entity)
        entity_by_truth_ref[item.signal_id] = entity

    # IDs are knowledge-scoped. Source truth references keep equivalent
    # CREATE and REVERSE projections in the same stable order.
    entities = sorted(entities, key=lambda item: item.source_truth_refs)
    relationships: list[EngineeringKnowledgeRelationship] = []
    for item in checked.interfaces:
        source = entity_by_truth_ref.get(item.source)
        target = entity_by_truth_ref.get(item.target)
        if source is None or target is None:
            continue
        relationships.append(
            _relationship(
                knowledge_id=knowledge_id,
                source_entity_id=source.entity_id,
                target_entity_id=target.entity_id,
                source_ref=item.interface_id,
                description=f"{item.protocol} interface connects explicit design components",
                evidence_bindings=item.evidence_refs,
            )
        )
    for item in checked.signals:
        source = entity_by_truth_ref.get(item.source)
        if source is None:
            continue
        for target_ref in item.targets:
            target = entity_by_truth_ref.get(target_ref)
            if target is None:
                continue
            relationships.append(
                _relationship(
                    knowledge_id=knowledge_id,
                    source_entity_id=source.entity_id,
                    target_entity_id=target.entity_id,
                    source_ref=f"{item.signal_id}:{target_ref}",
                    description=f"{item.name} connects explicit design components",
                    evidence_bindings=item.evidence_refs,
                )
            )

    subsystems = tuple(
        item.entity_id
        for item in entities
        if item.entity_type is EngineeringKnowledgeEntityType.SUBSYSTEM
    )
    system_context = tuple(
        item.entity_id
        for item in entities
        if item.entity_type is EngineeringKnowledgeEntityType.SYSTEM
    )
    return EngineeringKnowledgeModel.create(
        schema_version="1.0",
        knowledge_id=knowledge_id,
        project_id=checked.project_id,
        conversation_id=checked.conversation_id,
        source_truth_fingerprint=checked.fingerprint,
        title="工程知识模型",
        summary="基于已批准 DesignTruth 的只读工程知识结构",
        system_context=system_context,
        subsystems=subsystems,
        entities=tuple(entities),
        relationships=tuple(relationships),
        learning_topics=(),
        modification_points=(),
        design_decisions=(),
        risks=(),
        evidence_bindings=binding_ids,
        provenance=tuple(
            sorted(
                checked.provenance,
                key=lambda item: (item.source_type, item.source_id, item.source_fingerprint),
            )
        ),
    )


__all__ = ("generate_knowledge_model",)
