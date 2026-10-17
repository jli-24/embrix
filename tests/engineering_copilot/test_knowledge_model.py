from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    DesignComponent,
    DesignEvidenceBinding,
    DesignInterface,
    DesignPin,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    EngineeringKnowledgeEntity,
    EngineeringKnowledgeEntityType,
    EngineeringKnowledgeModel,
    EngineeringKnowledgeRelationshipType,
    EngineeringLearningTopic,
    KnowledgeConfidence,
    PlanProvenance,
    TruthLevel,
    generate_knowledge_model,
)

FP = "sha256:" + "a" * 64


def _binding(
    binding_id: str,
    entity_id: str,
    *,
    project_id: str,
    source: DesignSource = DesignSource.SCHEMATIC,
) -> DesignEvidenceBinding:
    return DesignEvidenceBinding.create(
        binding_id=binding_id,
        entity_id=entity_id,
        project_id=project_id,
        source=source,
        source_id=f"source-{binding_id}",
        artifact_ref="artifact-1",
        locator=f"locator:{entity_id}",
        artifact_revision="v1",
        source_fingerprint=FP,
        relationship="explicit engineering evidence",
        truth_level=TruthLevel.EXPLICIT,
    )


def _truth(
    *,
    project_id: str = "project-1",
    conversation_id: str = "conversation-1",
    origin: DesignTruthOrigin = DesignTruthOrigin.CREATE,
) -> DesignTruth:
    subsystem = DesignComponent.create(
        component_id="component-subsystem",
        name="Motor Control Subsystem",
        type="SUBSYSTEM",
        category="SUBSYSTEM",
        description="Controls motor speed and direction.",
        confidence=0.95,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-subsystem",),
    )
    controller = DesignComponent.create(
        component_id="component-mcu",
        name="STM32F103",
        type="CONTROLLER",
        category="MCU",
        description="Main microcontroller.",
        confidence=0.75,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-mcu",),
    )
    driver = DesignComponent.create(
        component_id="component-driver",
        name="Motor Driver",
        type="DRIVER",
        category="DRIVER",
        description="Motor driver interface.",
        confidence=0.45,
        truth_level=TruthLevel.INFERRED,
        evidence_refs=("binding-driver",),
    )
    interface = DesignInterface.create(
        interface_id="interface-motor",
        source="component-mcu",
        target="component-driver",
        protocol="GPIO",
        direction="SOURCE_TO_TARGET",
        confidence=0.9,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-interface",),
    )
    signal = DesignSignal.create(
        signal_id="signal-motor-pwm",
        name="MOTOR_PWM",
        type="PWM",
        source="component-mcu",
        targets=("component-driver",),
        protocol="PWM",
        direction="SOURCE_TO_TARGET",
        confidence=0.9,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-signal",),
    )
    pin = DesignPin.create(
        pin_id="pin-pa8",
        component_id="component-mcu",
        pin_name="PA8",
        pin_number="PA8",
        function="MOTOR_PWM",
        signal_id="signal-motor-pwm",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-pin",),
    )
    bindings = tuple(
        _binding(binding_id, entity_id, project_id=project_id)
        for binding_id, entity_id in (
            ("binding-truth", "truth-1"),
            ("binding-subsystem", subsystem.component_id),
            ("binding-mcu", controller.component_id),
            ("binding-driver", driver.component_id),
            ("binding-interface", interface.interface_id),
            ("binding-signal", signal.signal_id),
            ("binding-pin", pin.pin_id),
        )
    )
    return DesignTruth.create(
        truth_id="truth-1",
        project_id=project_id,
        conversation_id=conversation_id,
        design_revision="v1",
        origin=origin,
        architecture_id="architecture-1" if origin is DesignTruthOrigin.CREATE else None,
        status=DesignTruthStatus.APPROVED,
        components=(subsystem, controller, driver),
        interfaces=(interface,),
        signals=(signal,),
        pins=(pin,),
        evidence_bindings=bindings,
        provenance=(
            PlanProvenance.create(
                project_id=project_id,
                source_type="SCHEMATIC",
                source_id="artifact-1",
                source_fingerprint=FP,
            ),
        ),
    )


def test_knowledge_contract_is_strict_frozen_tuple_only_and_versioned() -> None:
    model = generate_knowledge_model(_truth())

    assert model.schema_version == "1.0"
    assert model.model_config["strict"] is True
    assert model.model_config["frozen"] is True
    assert model.model_config["extra"] == "forbid"
    assert model.entities[0].confidence in tuple(KnowledgeConfidence)
    assert model.entities[0].truth_level is not None
    restored = EngineeringKnowledgeModel.model_validate(
        model.model_dump(mode="json"), strict=False
    )
    assert restored == model

    with pytest.raises((TypeError, ValidationError)):
        model.summary = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EngineeringKnowledgeModel.model_validate(
            {**model.model_dump(mode="python"), "unexpected": "value"}
        )
    with pytest.raises(ValidationError):
        EngineeringKnowledgeEntity.model_validate(
            {
                **model.entities[0].model_dump(mode="python"),
                "confidence": "HIGH",
            }
        )


def test_design_truth_without_status_fingerprint_is_migrated_as_approved() -> None:
    truth = _truth()
    legacy = truth.model_dump(mode="json", exclude={"fingerprint"})
    legacy.pop("status")
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(
            legacy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    restored = DesignTruth.model_validate(legacy, strict=False)

    assert restored.status is DesignTruthStatus.APPROVED
    assert restored.fingerprint == legacy["fingerprint"]


def test_learning_topic_retains_source_refs_without_llm_generation() -> None:
    topic = EngineeringLearningTopic.create(
        topic_id="topic-i2c",
        title="I2C Communication",
        description="Understand the explicit I2C interface.",
        related_entities=("entity-interface",),
        difficulty="INTERMEDIATE",
        source_refs=("interface-i2c",),
        evidence_bindings=("binding-i2c",),
    )

    assert topic.source_refs == ("interface-i2c",)
    assert topic.evidence_bindings == ("binding-i2c",)
    restored = EngineeringLearningTopic.model_validate(
        {**topic.model_dump(mode="json"), "source_refs": ["interface-i2c"]},
        strict=False,
    )
    assert restored.source_refs == ("interface-i2c",)


def test_service_projects_only_explicit_objects_and_allowed_connects() -> None:
    model = generate_knowledge_model(_truth())
    by_name = {item.name: item for item in model.entities}

    assert by_name["Motor Control Subsystem"].entity_type is EngineeringKnowledgeEntityType.SUBSYSTEM
    assert by_name["STM32F103"].entity_type is EngineeringKnowledgeEntityType.COMPONENT
    assert by_name["STM32F103"].confidence is KnowledgeConfidence.MEDIUM
    assert by_name["Motor Driver"].confidence is KnowledgeConfidence.LOW
    assert model.subsystems == (by_name["Motor Control Subsystem"].entity_id,)
    assert any(item.name == "MOTOR_PWM" for item in model.entities)
    assert all(
        item.relationship_type is EngineeringKnowledgeRelationshipType.CONNECTS
        for item in model.relationships
    )
    assert all(item.relationship_type.value != "DRIVES" for item in model.relationships)
    assert model.learning_topics == ()
    assert model.modification_points == ()
    assert model.design_decisions == ()
    assert model.risks == ()
    assert "PID" not in {item.name for item in model.entities}
    assert "TIM1_CH1" not in {item.name for item in model.entities}

    binding_ids = {item.binding_id for item in _truth().evidence_bindings}
    assert set(model.evidence_bindings) == binding_ids
    assert all(set(item.evidence_bindings) <= binding_ids for item in model.entities)
    assert all(set(item.evidence_bindings) <= binding_ids for item in model.relationships)


def test_service_isolated_by_project_and_conversation_and_origin_agnostic() -> None:
    first = generate_knowledge_model(_truth())
    other_project = generate_knowledge_model(_truth(project_id="project-2"))
    other_conversation = generate_knowledge_model(
        _truth(conversation_id="conversation-2")
    )
    reverse = generate_knowledge_model(_truth(origin=DesignTruthOrigin.REVERSE))

    assert first.knowledge_id != other_project.knowledge_id
    assert first.knowledge_id != other_conversation.knowledge_id
    assert first.project_id != other_project.project_id
    assert first.conversation_id != other_conversation.conversation_id
    assert "origin" not in type(reverse).model_fields
    assert tuple(item.name for item in reverse.entities) == tuple(
        item.name for item in first.entities
    )


def test_service_rejects_non_approved_truth_and_non_truth_inputs() -> None:
    truth = _truth()
    non_approved = DesignTruth.model_construct(
        **{**truth.model_dump(mode="python"), "status": "DRAFT"}
    )
    with pytest.raises((TypeError, ValidationError, ValueError)):
        generate_knowledge_model(non_approved)

    with pytest.raises(TypeError):
        generate_knowledge_model(object())  # type: ignore[arg-type]
