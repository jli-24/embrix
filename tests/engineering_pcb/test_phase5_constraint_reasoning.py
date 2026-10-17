import pytest

from embedded_copilot.engineering_pcb import (
    PCBConstraint,
    PCBConstraintType,
    PCBIntentBindingStatus,
    PCBSourceBinding,
    PCBConstraintSource,
    PCBApplicability,
)

from tests.engineering_pcb._phase5_bound_fixtures import bound_element
from tests.engineering_pcb.test_phase6_authoritative_facts import power_facts, swd_facts


FP = "sha256:" + "1" * 64


def source():
    return PCBSourceBinding.create(
        intent_id="intent-stm32-control",
        source_id="intent-stm32-control",
        source_fingerprint=FP,
        status=PCBIntentBindingStatus.BLOCKED,
        unknowns=("fixture source is not yet approved",),
    )


def bound_source():
    return PCBSourceBinding.create(
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
        schematic_design_id="schematic-1",
        schematic_design_revision="schematic-r1",
        schematic_design_fingerprint=FP,
        circuit_schematic_binding_id="binding-1",
        circuit_schematic_binding_fingerprint=FP,
        schematic_artifact_id="schematic-artifact-1",
        schematic_artifact_fingerprint=FP,
        erc_receipt_reference="erc-receipt-1",
        erc_result_reference="erc-result-1",
        power_facts=power_facts(),
        swd_facts=swd_facts(),
    )


def test_constraint_carries_reason_source_target_method_and_evidence_scope() -> None:
    binding = source()
    element = bound_element(
        intent_id="intent-stm32-control",
        circuit_net_id="circuit-net-canh",
        pcb_track_ids=("track-canh",),
        status=PCBIntentBindingStatus.BOUND,
    )
    constraint = PCBConstraint.create(
        constraint_id="constraint-can-pair",
        constraint_type=PCBConstraintType.DIFFERENTIAL_PAIR,
        scope="CAN physical routing",
        target_ids=("net-canh",),
        requirements=("CANH/CANL must remain an explicit differential pair",),
        source=PCBConstraintSource.INTERFACE_SPEC,
        evidence_refs=(),
        confidence=1.0,
        applicability=PCBApplicability.REQUIRED,
        revision="pcb-r1",
        source_fingerprint=FP,
        evaluation_method="DifferentialPairVerifier",
        intent_source_binding=binding,
        source_binding_fingerprint=binding.fingerprint,
        circuit_block_refs=("block-can",),
        expected_evidence_scope=("PCB_ROUTE_GEOMETRY",),
        element_bindings=(element,),
    )
    assert constraint.circuit_block_refs == ("block-can",)
    assert constraint.expected_evidence_scope == ("PCB_ROUTE_GEOMETRY",)


def test_bound_constraint_requires_source_and_evidence_scope() -> None:
    binding = source()
    with pytest.raises(ValueError, match="upstream source reference"):
        PCBConstraint.create(
            constraint_id="constraint-missing-source",
            constraint_type=PCBConstraintType.TESTABILITY,
            scope="SWD",
            target_ids=("header-swd",),
            requirements=("header accessible",),
            source=PCBConstraintSource.PROJECT_REQUIREMENT,
            evidence_refs=(),
            confidence=1.0,
            applicability=PCBApplicability.REQUIRED,
            revision="pcb-r1",
            source_fingerprint=FP,
            intent_source_binding=binding,
            source_binding_fingerprint=binding.fingerprint,
            expected_evidence_scope=("MECHANICAL_ACCESS",),
        )


def test_bound_constraint_requires_method_and_current_evidence() -> None:
    binding = bound_source()
    element = bound_element(
        intent_id=binding.intent_id,
        circuit_net_id="circuit-net-can",
        pcb_track_ids=("pcb-track-can",),
        status=PCBIntentBindingStatus.BOUND,
    )
    with pytest.raises(ValueError, match="evaluation method"):
        PCBConstraint.create(
            constraint_id="constraint-no-method",
            constraint_type=PCBConstraintType.DIFFERENTIAL_PAIR,
            scope="CAN",
            target_ids=("net-canh",),
            requirements=("CANH/CANL remain paired",),
            source=PCBConstraintSource.INTERFACE_SPEC,
            evidence_refs=(),
            confidence=1.0,
            applicability=PCBApplicability.REQUIRED,
            revision="pcb-r1",
            source_fingerprint=FP,
            intent_source_binding=binding,
            source_binding_fingerprint=binding.fingerprint,
            circuit_block_refs=("block-can",),
            expected_evidence_scope=("PCB_ROUTE_GEOMETRY",),
            element_bindings=(element,),
        )
