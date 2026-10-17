from __future__ import annotations

import hashlib
from collections.abc import Callable

from .common import validate_copy
from .contracts import (
    EngineeringKnowledgeEntity,
    EngineeringKnowledgeEntityType,
    EngineeringKnowledgeModel,
    EngineeringKnowledgeRelationship,
    EngineeringKnowledgeRelationshipType,
    LearningMode,
    TeachingContext,
    TeachingDifficulty,
    TeachingLearningPath,
    TeachingLearningStep,
    TeachingScope,
    TeachingStepType,
    TeachingSubject,
    TeachingSubjectEntityType,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _entity_type(value: EngineeringKnowledgeEntityType) -> TeachingSubjectEntityType | None:
    try:
        return TeachingSubjectEntityType(value.value)
    except ValueError:
        return None


def _entity_ids(
    entities: tuple[EngineeringKnowledgeEntity, ...],
    allowed: set[EngineeringKnowledgeEntityType],
) -> tuple[str, ...]:
    return tuple(item.entity_id for item in entities if item.entity_type in allowed)


def _relation_endpoints(
    relationships: tuple[EngineeringKnowledgeRelationship, ...],
    allowed: set[EngineeringKnowledgeRelationshipType],
) -> tuple[str, ...]:
    values: list[str] = []
    for item in relationships:
        if item.relationship_type in allowed:
            values.extend((item.source_entity_id, item.target_entity_id))
    return _unique(values)


def _evidence_for(
    entities: tuple[EngineeringKnowledgeEntity, ...],
    relationships: tuple[EngineeringKnowledgeRelationship, ...],
    selected_ids: set[str],
) -> tuple[str, ...]:
    values: list[str] = []
    for item in entities:
        if item.entity_id in selected_ids:
            values.extend(item.evidence_bindings)
    for item in relationships:
        if item.source_entity_id in selected_ids or item.target_entity_id in selected_ids:
            values.extend(item.evidence_bindings)
    return _unique(values)


def _ordered_ids(
    entities: tuple[EngineeringKnowledgeEntity, ...], values: tuple[str, ...]
) -> tuple[str, ...]:
    selected = set(values)
    return tuple(item.entity_id for item in entities if item.entity_id in selected)


def _subject_related(
    subject_id: str, relationships: tuple[EngineeringKnowledgeRelationship, ...]
) -> tuple[str, ...]:
    values: list[str] = []
    for item in relationships:
        if item.source_entity_id == subject_id:
            values.append(item.target_entity_id)
        elif item.target_entity_id == subject_id:
            values.append(item.source_entity_id)
    return _unique(values)


def _subject_step_type(entity: EngineeringKnowledgeEntity) -> TeachingStepType:
    if entity.entity_type in {
        EngineeringKnowledgeEntityType.SYSTEM,
        EngineeringKnowledgeEntityType.SUBSYSTEM,
    }:
        return TeachingStepType.SYSTEM_OVERVIEW
    if entity.entity_type is EngineeringKnowledgeEntityType.COMPONENT:
        return TeachingStepType.COMPONENT
    if entity.entity_type is EngineeringKnowledgeEntityType.INTERFACE:
        return TeachingStepType.INTERFACE
    if entity.entity_type is EngineeringKnowledgeEntityType.SIGNAL:
        return TeachingStepType.SIGNAL
    return TeachingStepType.ARCHITECTURE


_STEP_SUMMARIES = {
    TeachingStepType.SYSTEM_OVERVIEW: "确认系统范围与系统级实体",
    TeachingStepType.ARCHITECTURE: "确认实体层级与结构关系",
    TeachingStepType.COMPONENT: "确认组件实体及其已绑定证据",
    TeachingStepType.INTERFACE: "确认接口实体及其已确认连接",
    TeachingStepType.SIGNAL: "确认信号实体及其已确认连接",
    TeachingStepType.VERIFICATION: "确认知识实体的证据绑定",
}


def _stage(
    *,
    key: str,
    title: str,
    step_type: TeachingStepType,
    condition: Callable[[EngineeringKnowledgeModel], bool],
    entities: Callable[[EngineeringKnowledgeModel, str], tuple[str, ...]],
) -> tuple[str, str, TeachingStepType, Callable[[EngineeringKnowledgeModel], bool], Callable[[EngineeringKnowledgeModel, str], tuple[str, ...]]]:
    return key, title, step_type, condition, entities


def _all_types(*values: EngineeringKnowledgeEntityType) -> set[EngineeringKnowledgeEntityType]:
    return set(values)


def _all_relationships(*values: EngineeringKnowledgeRelationshipType) -> set[EngineeringKnowledgeRelationshipType]:
    return set(values)


def _stage_entities(
    model: EngineeringKnowledgeModel,
    subject_id: str,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    return _ordered_ids(model.entities, _unique((subject_id, *values)))


def _path_steps(
    model: EngineeringKnowledgeModel,
    subject_id: str,
    mode: LearningMode,
) -> tuple[TeachingLearningStep, ...]:
    entities = model.entities
    relationships = model.relationships
    relationship_types = {item.relationship_type for item in relationships}
    relationship_endpoints = set(
        _relation_endpoints(relationships, set(EngineeringKnowledgeRelationshipType))
    )
    component_ids = _entity_ids(entities, _all_types(EngineeringKnowledgeEntityType.COMPONENT))
    structure_ids = _entity_ids(
        entities,
        _all_types(
            EngineeringKnowledgeEntityType.SYSTEM,
            EngineeringKnowledgeEntityType.SUBSYSTEM,
            EngineeringKnowledgeEntityType.COMPONENT,
        ),
    )
    interface_ids = _entity_ids(entities, _all_types(EngineeringKnowledgeEntityType.INTERFACE))
    signal_ids = _entity_ids(entities, _all_types(EngineeringKnowledgeEntityType.SIGNAL))
    function_ids = _entity_ids(entities, _all_types(EngineeringKnowledgeEntityType.FUNCTION))
    control_endpoints = _relation_endpoints(
        relationships,
        _all_relationships(
            EngineeringKnowledgeRelationshipType.CONTROLS,
            EngineeringKnowledgeRelationshipType.DEPENDS_ON,
            EngineeringKnowledgeRelationshipType.IMPLEMENTS,
        ),
    )
    architecture_endpoints = _relation_endpoints(
        relationships,
        _all_relationships(EngineeringKnowledgeRelationshipType.CONTAINS),
    )

    def has_evidence(current: EngineeringKnowledgeModel) -> bool:
        return bool(current.evidence_bindings)

    def fixed(values: tuple[str, ...]) -> Callable[[EngineeringKnowledgeModel, str], tuple[str, ...]]:
        return lambda current, selected: _stage_entities(current, selected, values)

    overview = (
        _stage(
            key="system-overview",
            title="System Overview",
            step_type=TeachingStepType.SYSTEM_OVERVIEW,
            condition=lambda current: bool(
                _entity_ids(
                    current.entities,
                    _all_types(
                        EngineeringKnowledgeEntityType.SYSTEM,
                        EngineeringKnowledgeEntityType.SUBSYSTEM,
                    ),
                )
            ),
            entities=lambda current, selected: fixed(
                _entity_ids(
                    current.entities,
                    _all_types(
                        EngineeringKnowledgeEntityType.SYSTEM,
                        EngineeringKnowledgeEntityType.SUBSYSTEM,
                    ),
                )
            )(current, selected),
        ),
        _stage(
            key="hardware-structure",
            title="Hardware Structure",
            step_type=TeachingStepType.ARCHITECTURE,
            condition=lambda current: bool(structure_ids),
            entities=fixed(structure_ids),
        ),
        _stage(
            key="control-flow",
            title="Control Flow",
            step_type=TeachingStepType.ARCHITECTURE,
            condition=lambda current: bool(control_endpoints),
            entities=fixed(control_endpoints),
        ),
        _stage(
            key="key-interfaces",
            title="Key Interfaces",
            step_type=TeachingStepType.INTERFACE,
            condition=lambda current: bool(interface_ids),
            entities=fixed(interface_ids),
        ),
        _stage(
            key="signals",
            title="Signals",
            step_type=TeachingStepType.SIGNAL,
            condition=lambda current: bool(signal_ids),
            entities=fixed(signal_ids),
        ),
        _stage(
            key="firmware-relationship",
            title="Firmware Relationship",
            step_type=TeachingStepType.ARCHITECTURE,
            condition=lambda current: bool(function_ids)
            or EngineeringKnowledgeRelationshipType.IMPLEMENTS in relationship_types,
            entities=fixed(function_ids + control_endpoints),
        ),
    )
    engineering = (
        _stage(
            key="requirement",
            title="Requirement",
            step_type=TeachingStepType.SYSTEM_OVERVIEW,
            condition=lambda current: False,
            entities=fixed(()),
        ),
        _stage(
            key="architecture",
            title="Architecture",
            step_type=TeachingStepType.ARCHITECTURE,
            condition=lambda current: bool(structure_ids)
            or EngineeringKnowledgeRelationshipType.CONTAINS in relationship_types,
            entities=fixed(structure_ids + architecture_endpoints),
        ),
        _stage(
            key="component",
            title="Component",
            step_type=TeachingStepType.COMPONENT,
            condition=lambda current: bool(component_ids),
            entities=fixed(component_ids),
        ),
        _stage(
            key="interface",
            title="Interface",
            step_type=TeachingStepType.INTERFACE,
            condition=lambda current: bool(interface_ids),
            entities=fixed(interface_ids),
        ),
        _stage(
            key="signal",
            title="Signal",
            step_type=TeachingStepType.SIGNAL,
            condition=lambda current: bool(signal_ids),
            entities=fixed(signal_ids),
        ),
        _stage(
            key="verification",
            title="Verification",
            step_type=TeachingStepType.VERIFICATION,
            condition=has_evidence,
            entities=lambda current, selected: _stage_entities(
                current, selected, tuple(item.entity_id for item in current.entities if item.evidence_bindings)
            ),
        ),
    )
    deep_dive = (
        _stage(
            key="subject-detail",
            title="Subject Detail",
            step_type=_subject_step_type(next(item for item in entities if item.entity_id == subject_id)),
            condition=lambda current: True,
            entities=fixed((subject_id,)),
        ),
        _stage(
            key="related-objects",
            title="Related Objects",
            step_type=TeachingStepType.ARCHITECTURE,
            condition=lambda current: bool(_subject_related(subject_id, current.relationships)),
            entities=lambda current, selected: _stage_entities(
                current, selected, _subject_related(selected, current.relationships)
            ),
        ),
        _stage(
            key="evidence-trace",
            title="Evidence Trace",
            step_type=TeachingStepType.VERIFICATION,
            condition=has_evidence,
            entities=lambda current, selected: _stage_entities(
                current, selected, tuple(item.entity_id for item in current.entities if item.evidence_bindings)
            ),
        ),
    )
    selected_stages = {
        LearningMode.OVERVIEW: overview,
        LearningMode.ENGINEERING: engineering,
        LearningMode.DEEP_DIVE: deep_dive,
    }[mode]
    steps: list[TeachingLearningStep] = []
    for key, title, step_type, condition, entity_selector in selected_stages:
        if not condition(model):
            continue
        selected_entities = entity_selector(model, subject_id)
        related_entities = tuple(
            entity_id for entity_id in selected_entities if entity_id in relationship_endpoints
        )
        evidence_refs = _evidence_for(entities, relationships, set(selected_entities))
        steps.append(
            TeachingLearningStep.create(
                step_id=_id("teaching-step", model.knowledge_id, model.fingerprint, subject_id, mode.value, key),
                step_type=step_type,
                title=title,
                step_summary=_STEP_SUMMARIES[step_type],
                related_entities=related_entities,
                order=len(steps) + 1,
                evidence_refs=evidence_refs,
            )
        )
    return tuple(steps)


def generate_teaching_context(
    knowledge_model: EngineeringKnowledgeModel,
    subject_id: str | None = None,
    mode: LearningMode = LearningMode.OVERVIEW,
) -> TeachingContext:
    """Project a validated Knowledge Model into a structural teaching context."""

    if type(knowledge_model) is not EngineeringKnowledgeModel:
        raise TypeError("knowledge model is invalid")
    if type(mode) is not LearningMode:
        raise TypeError("learning mode is invalid")
    if subject_id is not None and type(subject_id) is not str:
        raise TypeError("subject id is invalid")
    checked = validate_copy(knowledge_model, EngineeringKnowledgeModel)
    if checked.schema_version != "1.0":
        raise ValueError("knowledge model schema version is unsupported")

    if subject_id is None:
        priority = (
            EngineeringKnowledgeEntityType.SYSTEM,
            EngineeringKnowledgeEntityType.SUBSYSTEM,
            EngineeringKnowledgeEntityType.COMPONENT,
            EngineeringKnowledgeEntityType.INTERFACE,
            EngineeringKnowledgeEntityType.SIGNAL,
            EngineeringKnowledgeEntityType.FUNCTION,
            EngineeringKnowledgeEntityType.DESIGN_DECISION,
        )
        subject = next(
            (
                item
                for entity_type in priority
                for item in checked.entities
                if item.entity_type is entity_type
            ),
            None,
        )
    else:
        subject = next((item for item in checked.entities if item.entity_id == subject_id), None)
    if subject is None:
        raise ValueError("teaching subject is unavailable")
    teaching_entity_type = _entity_type(subject.entity_type)
    if teaching_entity_type is None:
        raise ValueError("knowledge entity type is not teachable")

    related_entities = _ordered_ids(
        checked.entities,
        _subject_related(subject.entity_id, checked.relationships),
    )
    context_id = _id(
        "teaching-context",
        checked.knowledge_id,
        checked.fingerprint,
        subject.entity_id,
        mode.value,
    )
    subject_projection = TeachingSubject.create(
        subject_id=_id("teaching-subject", checked.knowledge_id, subject.entity_id),
        entity_id=subject.entity_id,
        entity_type=teaching_entity_type,
        title=subject.name,
        summary=subject.description,
        related_entities=related_entities,
        source_refs=subject.source_truth_refs,
        evidence_bindings=subject.evidence_bindings,
        confidence=subject.confidence,
    )
    steps = _path_steps(checked, subject.entity_id, mode)
    context_evidence = _unique(
        (
            *subject.evidence_bindings,
            *(
                binding
                for step in steps
                for binding in step.evidence_refs
            ),
        )
    )
    path = TeachingLearningPath.create(
        path_id=_id("teaching-path", context_id),
        teaching_context_id=context_id,
        steps=steps,
    )
    difficulty = {
        LearningMode.OVERVIEW: TeachingDifficulty.FOUNDATION,
        LearningMode.ENGINEERING: TeachingDifficulty.INTERMEDIATE,
        LearningMode.DEEP_DIVE: TeachingDifficulty.ADVANCED,
    }[mode]
    return TeachingContext.create(
        schema_version="1.0",
        teaching_context_id=context_id,
        project_id=checked.project_id,
        conversation_id=checked.conversation_id,
        knowledge_id=checked.knowledge_id,
        subject=subject_projection,
        scope=TeachingScope(mode.value),
        difficulty=difficulty,
        learning_mode=mode,
        related_entities=related_entities,
        learning_path=path,
        evidence_bindings=context_evidence,
        source_knowledge_schema_version=checked.schema_version,
        source_knowledge_fingerprint=checked.fingerprint,
    )


__all__ = ("generate_teaching_context",)
