from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    EngineeringKnowledgeEntityType,
    LearningMode,
    TeachingContext,
    TeachingDifficulty,
    TeachingLearningStep,
    TeachingStepType,
    TeachingSubject,
    TeachingSubjectEntityType,
    generate_knowledge_model,
    generate_teaching_context,
)
from tests.engineering_copilot.test_knowledge_model import _truth


def test_teaching_context_is_strict_frozen_versioned_and_round_trips() -> None:
    model = generate_knowledge_model(_truth())

    context = generate_teaching_context(model)

    assert context.schema_version == "1.0"
    assert context.source_knowledge_schema_version == model.schema_version
    assert context.learning_mode is LearningMode.OVERVIEW
    assert context.scope.value == "OVERVIEW"
    assert context.difficulty is TeachingDifficulty.FOUNDATION
    assert isinstance(context.related_entities, tuple)
    assert isinstance(context.learning_path.steps, tuple)
    assert all(isinstance(step, TeachingLearningStep) for step in context.learning_path.steps)
    assert all(step.step_type in set(TeachingStepType) for step in context.learning_path.steps)
    assert all(step.step_summary for step in context.learning_path.steps)

    restored = TeachingContext.model_validate(context.model_dump(mode="json"), strict=False)
    assert restored == context

    with pytest.raises(ValidationError):
        TeachingContext.model_validate(
            {**context.model_dump(mode="python"), "unexpected": "value"}
        )

    with pytest.raises((TypeError, ValidationError)):
        context.learning_mode = LearningMode.DEEP_DIVE  # type: ignore[misc]


def test_subject_selection_is_exact_and_deterministic() -> None:
    model = generate_knowledge_model(_truth())

    default_context = generate_teaching_context(model)
    repeated_context = generate_teaching_context(model)

    assert default_context == repeated_context
    assert default_context.subject.entity_type is TeachingSubjectEntityType.SUBSYSTEM
    assert default_context.subject.entity_id in model.subsystems
    assert default_context.subject.subject_id != default_context.subject.entity_id

    component = next(
        entity
        for entity in model.entities
        if entity.entity_type is EngineeringKnowledgeEntityType.COMPONENT
    )
    engineering = generate_teaching_context(
        model,
        subject_id=component.entity_id,
        mode=LearningMode.ENGINEERING,
    )
    assert engineering.subject.entity_id == component.entity_id
    assert engineering.learning_mode is LearningMode.ENGINEERING
    assert engineering.difficulty is TeachingDifficulty.INTERMEDIATE

    with pytest.raises(ValueError):
        generate_teaching_context(model, subject_id=component.name)


def test_paths_are_source_driven_and_evidence_bound() -> None:
    model = generate_knowledge_model(_truth())
    context = generate_teaching_context(model, mode=LearningMode.ENGINEERING)
    model_bindings = set(model.evidence_bindings)
    relationship_endpoints = {
        endpoint
        for relationship in model.relationships
        for endpoint in (relationship.source_entity_id, relationship.target_entity_id)
    }

    assert context.source_knowledge_fingerprint == model.fingerprint
    assert set(context.evidence_bindings) <= model_bindings
    assert set(context.subject.evidence_bindings) <= model_bindings
    assert set(context.related_entities) <= relationship_endpoints
    assert all(set(step.related_entities) <= relationship_endpoints for step in context.learning_path.steps)
    assert all(set(step.evidence_refs) <= model_bindings for step in context.learning_path.steps)
    assert all(
        step.step_type is not TeachingStepType.VERIFICATION
        or step.evidence_refs
        for step in context.learning_path.steps
    )
    assert not any("STM32" in step.step_summary and "because" in step.step_summary for step in context.learning_path.steps)


def test_rejects_wrong_source_types_and_tampering() -> None:
    model = generate_knowledge_model(_truth())
    context = generate_teaching_context(model)

    with pytest.raises(TypeError):
        generate_teaching_context(object())  # type: ignore[arg-type]

    tampered = context.model_dump(mode="json")
    tampered["source_knowledge_schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        TeachingContext.model_validate(tampered, strict=False)

    tampered = context.model_dump(mode="json")
    tampered["subject"]["title"] = "forged"
    with pytest.raises(ValidationError):
        TeachingContext.model_validate(tampered, strict=False)

    with pytest.raises(ValidationError):
        TeachingSubject.create(
            subject_id="subject-risk",
            entity_id="entity-risk",
            entity_type="RISK",
            title="Risk",
            summary="Risk",
            related_entities=("entity-risk",),
            source_refs=("truth-risk",),
            evidence_bindings=("binding-risk",),
            confidence="LOW",
        )
