import pytest

from embedded_copilot.engineering_pcb import PCBIntentBindingStatus, PCBSourceBinding

from tests.engineering_pcb.test_phase6_authoritative_facts import power_facts, swd_facts


FP = "sha256:" + "1" * 64


def bound_source(**overrides):
    values = {
        "intent_id": "intent-stm32-control",
        "source_id": "intent-stm32-control",
        "source_fingerprint": FP,
        "status": PCBIntentBindingStatus.BOUND,
        "tenant_id": "tenant-golden",
        "project_id": "project-stm32-control",
        "conversation_id": "conversation-stm32-control",
        "circuit_intent_id": "intent-stm32-control",
        "circuit_intent_revision": "intent-r1",
        "circuit_intent_fingerprint": FP,
        "schematic_design_id": "schematic-design-1",
        "schematic_design_revision": "schematic-r1",
        "schematic_design_fingerprint": FP,
        "circuit_schematic_binding_id": "binding-1",
        "circuit_schematic_binding_fingerprint": FP,
        "schematic_artifact_id": "artifact-schematic-1",
        "schematic_artifact_fingerprint": FP,
        "erc_receipt_reference": "erc-receipt-1",
        "erc_result_reference": "erc-result-1",
        "power_facts": power_facts(),
        "swd_facts": swd_facts(),
    }
    values.update(overrides)
    return PCBSourceBinding.create(**values)


def test_source_lineage_is_complete_and_deterministic() -> None:
    first = bound_source()
    second = bound_source()
    assert first.fingerprint == second.fingerprint
    assert first.project_id == "project-stm32-control"
    assert first.schematic_design_revision == "schematic-r1"


def test_bound_source_rejects_missing_schematic_lineage() -> None:
    with pytest.raises(ValueError, match="complete PCB source lineage"):
        bound_source(schematic_design_fingerprint=None)

