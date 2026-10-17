from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    DesignComponent,
    DesignEvidenceBinding,
    DesignPin,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthBuilder,
    FirmwareBinding,
    NetBinding,
    PeripheralBinding,
    PlanProvenance,
    TruthLevel,
    approve_architecture_design,
    generate_design_truth,
)
from embedded_copilot.engineering_requirements import generate_architecture_design

from .test_p0_architecture_design_contract import _approved_plan


def _approved_architecture():
    context, plan, _ = _approved_plan()
    proposed = generate_architecture_design(context, plan)
    return approve_architecture_design(
        proposed,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )[0]


def test_design_truth_requires_approved_architecture_and_is_deterministic() -> None:
    context, plan, _ = _approved_plan()
    proposed = generate_architecture_design(context, plan)
    with pytest.raises(ValueError, match="approved architecture"):
        generate_design_truth(proposed)

    architecture = approve_architecture_design(
        proposed,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )[0]
    truth = generate_design_truth(architecture)
    repeated = generate_design_truth(architecture)

    assert architecture.status is ArchitectureDesignStatus.APPROVED
    assert truth == repeated
    assert truth.design_revision == "v1"
    assert truth.components
    assert truth.interfaces
    assert next(item for item in truth.components if item.name == "STM32").type == "controller"
    assert truth.pins == ()
    assert truth.peripherals == ()
    assert truth.nets == ()
    assert truth.firmware_bindings == ()
    assert all(item.truth_level in set(TruthLevel) for item in truth.components)
    assert all(item.source is DesignSource.ARCHITECTURE for item in truth.evidence_bindings)
    assert truth.evidence_bindings[0].source_fingerprint == architecture.fingerprint
    assert not any(item.pin_name == "PA8" for item in truth.pins)
    assert not any(item.function == "TIM1_CH1" for item in truth.pins)


def test_create_builder_uses_architecture_approval_without_evidence_review() -> None:
    proposed = generate_architecture_design(*_approved_plan()[:2])
    architecture, review = approve_architecture_design(
        proposed,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    builder = DesignTruthBuilder()

    with pytest.raises(ValueError, match="architecture review"):
        builder.build_from_approved_architecture(architecture)

    truth = builder.build_from_approved_architecture(
        architecture,
        architecture_review=review,
        conversation_id="conversation-1",
    )
    assert truth.origin.value == "CREATE"
    assert truth.conversation_id == "conversation-1"
    assert all(item.source is DesignSource.ARCHITECTURE for item in truth.evidence_bindings)


def test_design_truth_serialization_is_immutable_and_tamper_rejected() -> None:
    truth = generate_design_truth(_approved_architecture())
    restored = DesignTruth.model_validate(truth.model_dump(mode="json"), strict=False)
    assert restored == truth

    with pytest.raises((TypeError, ValidationError)):
        truth.design_revision = "v2"

    tampered = truth.model_dump(mode="python")
    tampered["components"][0]["name"] = "Unbound MCU"
    tampered["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        DesignTruth.model_validate(tampered, strict=False)


def test_design_truth_rejects_cross_project_and_invalid_evidence_bindings() -> None:
    truth = generate_design_truth(_approved_architecture())

    cross_project = truth.model_dump(mode="python")
    cross_project["project_id"] = "project-2"
    cross_project["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="identity"):
        DesignTruth.model_validate(cross_project, strict=False)

    tampered_evidence = truth.model_dump(mode="python")
    tampered_evidence["evidence_bindings"][0]["entity_id"] = "missing-entity"
    tampered_evidence["evidence_bindings"][0]["fingerprint"] = "sha256:" + "0" * 64
    tampered_evidence["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="identity"):
        DesignTruth.model_validate(tampered_evidence, strict=False)


def test_design_source_carries_safe_artifact_revision_and_locator() -> None:
    binding = DesignEvidenceBinding.create(
        binding_id="evidence-1",
        entity_id="pin-1",
        project_id="project-1",
        source=DesignSource.FIRMWARE,
        source_id="firmware-main",
        artifact_ref="firmware/main.c",
        locator="line:32",
        artifact_revision="v2",
        source_fingerprint="sha256:" + "1" * 64,
        relationship="declares hardware binding",
        truth_level=TruthLevel.EXPLICIT,
    )
    assert binding.source is DesignSource.FIRMWARE
    assert binding.locator == "line:32"
    assert binding.artifact_revision == "v2"

    with pytest.raises((ValueError, ValidationError)):
        DesignEvidenceBinding.create(
            binding_id="evidence-2",
            entity_id="pin-1",
            project_id="project-1",
            source=DesignSource.FIRMWARE,
            source_id="firmware-main",
            artifact_ref="C:\\private\\firmware\\main.c",
            source_fingerprint="sha256:" + "1" * 64,
            relationship="declares hardware binding",
            truth_level=TruthLevel.EXPLICIT,
        )


def test_source_backed_stm32_pin_mapping_is_representable_without_adapter_inference() -> None:
    project_id = "project-1"
    truth_id = "truth-stm32"
    component_id = "component-stm32"
    signal_id = "signal-motor-pwm-a"
    pin_id = "pin-pa8"
    peripheral_id = "peripheral-tim1-ch1"
    net_id = "net-motor-pwm-a"
    firmware_id = "firmware-motor-speed"
    source_fp = "sha256:" + "2" * 64
    evidence = tuple(
        DesignEvidenceBinding.create(
            binding_id=f"evidence-{index}",
            entity_id=entity_id,
            project_id=project_id,
            source=source,
            source_id=source_id,
            artifact_ref=artifact_ref,
            locator=locator,
            artifact_revision="v1",
            source_fingerprint=source_fp,
            relationship="source-backed mapping",
            truth_level=TruthLevel.EXPLICIT,
        )
        for index, (entity_id, source, source_id, artifact_ref, locator) in enumerate(
            (
                (truth_id, DesignSource.ARCHITECTURE, "architecture-1", "architecture-1", "architecture"),
                (component_id, DesignSource.SCHEMATIC, "schematic-1", "robot.kicad_sch", "U1"),
                (signal_id, DesignSource.SCHEMATIC, "schematic-1", "robot.kicad_sch", "MOTOR_PWM_A"),
                (pin_id, DesignSource.SCHEMATIC, "schematic-1", "robot.kicad_sch", "U1.PA8"),
                (peripheral_id, DesignSource.FIRMWARE, "firmware-1", "firmware/main.c", "line:32"),
                (net_id, DesignSource.SCHEMATIC, "schematic-1", "robot.kicad_sch", "MOTOR_PWM_A_NET"),
                (firmware_id, DesignSource.FIRMWARE, "firmware-1", "firmware/main.c", "line:48"),
            ),
            start=1,
        )
    )
    truth = DesignTruth.create(
        truth_id=truth_id,
        project_id=project_id,
        design_revision="v1",
        architecture_id="architecture-1",
        components=(DesignComponent.create(
            component_id=component_id,
            name="STM32F103C8T6",
            type="controller",
            category="MCU",
            description="STM32 controller",
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-2",),
        ),),
        signals=(DesignSignal.create(
            signal_id=signal_id,
            name="MOTOR_PWM_A",
            type="PWM",
            source=component_id,
            targets=("motor-driver",),
            protocol="TIM1",
            direction="SOURCE_TO_TARGET",
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-3",),
        ),),
        pins=(DesignPin.create(
            pin_id=pin_id,
            component_id=component_id,
            pin_name="PA8",
            pin_number="29",
            function="TIM1_CH1",
            signal_id=signal_id,
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-4",),
        ),),
        peripherals=(PeripheralBinding.create(
            binding_id=peripheral_id,
            peripheral="TIM1_CH1",
            component=component_id,
            pin_refs=(pin_id,),
            function="PWM Output",
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-5",),
        ),),
        nets=(NetBinding.create(
            binding_id=net_id,
            net_name="MOTOR_PWM_A_NET",
            nodes=("STM32_PA8", "TB6612_PWMA"),
            signal=signal_id,
            purpose="motor PWM control",
            confidence=1.0,
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-6",),
        ),),
        firmware_bindings=(FirmwareBinding.create(
            binding_id=firmware_id,
            firmware_symbol="motor_set_speed",
            source_file="firmware/main.c",
            hardware_refs=(pin_id, peripheral_id, net_id),
            function="set motor PWM duty cycle",
            truth_level=TruthLevel.EXPLICIT,
            evidence_refs=("evidence-7",),
        ),),
        evidence_bindings=evidence,
        provenance=(PlanProvenance.create(
            project_id=project_id,
            source_type="SCHEMATIC",
            source_id="schematic-1",
            source_fingerprint=source_fp,
        ),),
    )

    assert truth.pins[0].pin_name == "PA8"
    assert truth.pins[0].function == "TIM1_CH1"
    assert truth.peripherals[0].peripheral == "TIM1_CH1"
    assert truth.nets[0].net_name == "MOTOR_PWM_A_NET"
    assert truth.firmware_bindings[0].firmware_symbol == "motor_set_speed"
