import pytest

from embedded_copilot.engineering_pcb import (
    PCBApplicability,
    PCBElementBinding,
    PCBEngineeringFactResolution,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBIntentBindingStatus,
    PCBPowerFactRole,
    PCBPowerFactSet,
    PCBReadbackObjectKind,
    PCBReadbackObjectReference,
    PCBRequiredEngineeringFact,
    PCBSWDFactRole,
    PCBSWDFactSet,
    PCBSourceBinding,
)

FP = "sha256:" + "1" * 64
READBACK_FP = "sha256:" + "2" * 64


def evidence(source_id: str = "approved-design") -> PCBFoundationEvidence:
    return PCBFoundationEvidence.create(
        evidence_id=f"evidence-{source_id}",
        project_id="project-stm32-control",
        source_type=PCBFoundationEvidenceSource.DESIGN_DECISION,
        source_id=source_id,
        source_fingerprint=FP,
        claim="approved engineering fact",
        locator=f"design-decision:{source_id}",
    )


def fact(role, *, applicability=PCBApplicability.REQUIRED, resolution=PCBEngineeringFactResolution.CONFIRMED, reason=None):
    return PCBRequiredEngineeringFact.create(
        fact_id=f"fact-{role.value.lower().replace('_', '-')}",
        subject_id=f"subject-{role.value.lower().replace('_', '-')}",
        role=role,
        applicability=applicability,
        resolution=resolution,
        evidence_refs=(evidence(),) if resolution is PCBEngineeringFactResolution.CONFIRMED else (),
        reason=reason,
    )


def power_facts(overrides=None) -> PCBPowerFactSet:
    items = {role: fact(role) for role in PCBPowerFactRole}
    items.update(overrides or {})
    return PCBPowerFactSet.create(facts=tuple(items[role] for role in PCBPowerFactRole))


def swd_facts() -> PCBSWDFactSet:
    return PCBSWDFactSet.create(
        facts=tuple(
            fact(
                role,
                applicability=PCBApplicability.NOT_APPLICABLE if role is PCBSWDFactRole.NRST else PCBApplicability.REQUIRED,
            )
            for role in PCBSWDFactRole
        )
    )


def source_binding(**overrides):
    values = dict(
        intent_id="intent-stm32-control",
        source_id="intent-stm32-control",
        source_fingerprint=FP,
        status=PCBIntentBindingStatus.BOUND,
        tenant_id="tenant-golden",
        project_id="project-stm32-control",
        conversation_id="conversation-stm32-control",
        circuit_intent_id="intent-stm32-control",
        circuit_intent_revision="intent-r1",
        circuit_intent_fingerprint=FP,
        schematic_design_id="schematic-design-1",
        schematic_design_revision="schematic-r1",
        schematic_design_fingerprint=FP,
        circuit_schematic_binding_id="binding-1",
        circuit_schematic_binding_fingerprint=FP,
        schematic_artifact_id="artifact-schematic-1",
        schematic_artifact_fingerprint=FP,
        erc_receipt_reference="erc-receipt-1",
        erc_result_reference="erc-result-1",
        power_facts=power_facts(),
        swd_facts=swd_facts(),
    )
    values.update(overrides)
    return PCBSourceBinding.create(**values)


def readback_object(object_id: str, kind: PCBReadbackObjectKind) -> PCBReadbackObjectReference:
    artifact_evidence = PCBFoundationEvidence.create(
        evidence_id=f"evidence-{object_id}",
        project_id="project-stm32-control",
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id="pcb-artifact-1",
        source_fingerprint=FP,
        claim="object observed by current PCB read-back",
        locator=f"pcb-readback:{object_id}",
    )
    return PCBReadbackObjectReference.create(
        object_id=object_id,
        object_kind=kind,
        pcb_artifact_id="pcb-artifact-1",
        pcb_artifact_fingerprint=FP,
        readback_fingerprint=READBACK_FP,
        evidence_refs=(artifact_evidence,),
    )


def test_bound_source_requires_complete_authoritative_power_and_swd_facts() -> None:
    assert source_binding().status is PCBIntentBindingStatus.BOUND
    with pytest.raises(ValueError, match="authoritative Power and SWD facts"):
        source_binding(power_facts=None)


def test_unknown_power_fact_keeps_source_binding_blocked() -> None:
    unresolved = fact(
        PCBPowerFactRole.INPUT_CAPACITOR,
        applicability=PCBApplicability.UNKNOWN,
        resolution=PCBEngineeringFactResolution.UNKNOWN,
        reason="approved source does not specify the input capacitor",
    )
    with pytest.raises(ValueError, match="resolved Power and SWD facts"):
        source_binding(power_facts=power_facts({PCBPowerFactRole.INPUT_CAPACITOR: unresolved}))


def test_manual_pcb_object_ids_cannot_be_bound_without_readback() -> None:
    with pytest.raises(ValueError, match="current PCB artifact read-back"):
        PCBElementBinding.create(
            intent_id="intent-stm32-control",
            circuit_component_id="component-regulator",
            pcb_component_id="pcb-regulator",
            status=PCBIntentBindingStatus.BOUND,
        )


def test_bound_element_requires_every_physical_object_in_same_readback() -> None:
    component = readback_object("pcb-regulator", PCBReadbackObjectKind.COMPONENT)
    pad = readback_object("pad-vout", PCBReadbackObjectKind.PAD)
    binding = PCBElementBinding.create(
        intent_id="intent-stm32-control",
        circuit_component_id="component-regulator",
        circuit_pin_id="pin-vout",
        pcb_component_id="pcb-regulator",
        pcb_pad_ids=("pad-vout",),
        pcb_artifact_id="pcb-artifact-1",
        pcb_artifact_fingerprint=FP,
        readback_fingerprint=READBACK_FP,
        readback_objects=(component, pad),
        status=PCBIntentBindingStatus.BOUND,
    )
    assert binding.status is PCBIntentBindingStatus.BOUND
    with pytest.raises(ValueError, match="every PCB identity to exist in read-back"):
        PCBElementBinding.create(
            **{
                **binding.model_dump(mode="python", exclude={"fingerprint", "readback_objects"}),
                "readback_objects": (component,),
            }
        )
