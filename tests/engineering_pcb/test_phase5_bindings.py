import pytest

from embedded_copilot.engineering_pcb import (
    PCBElementBinding,
    PCBIntentBindingStatus,
    PCBIntentSourceBinding,
    PCBSourceBinding,
)

from tests.engineering_pcb._phase5_bound_fixtures import bound_element


FP = "sha256:" + "1" * 64


def test_source_binding_has_neutral_alias_and_explicit_statuses() -> None:
    binding = PCBSourceBinding.create(
        intent_id="intent-1",
        source_id="conversation-1",
        source_fingerprint=FP,
        status=PCBIntentBindingStatus.BLOCKED,
    )
    assert isinstance(binding, PCBIntentSourceBinding)
    assert binding.status is PCBIntentBindingStatus.BLOCKED


def test_element_binding_supports_multi_unit_and_multi_pad_without_guessing() -> None:
    binding = bound_element(
        intent_id="intent-1",
        element_id="component-u1-unit-a",
        component_id="component-u1",
        symbol_id="symbol-u1-a",
        pin_id="pin-u1-vss",
        footprint_id="footprint-u1",
        pad_ids=("pad-1", "pad-2"),
        unit_id="unit-a",
        circuit_component_id="circuit-u1",
        circuit_pin_id="circuit-pin-u1-vss",
        schematic_component_instance_id="sch-u1",
        schematic_pin_binding_id="sch-pin-u1-vss",
        pcb_component_id="pcb-u1",
        pcb_footprint_id="pcb-footprint-u1",
        pcb_pad_ids=("pcb-pad-1", "pcb-pad-2"),
        status=PCBIntentBindingStatus.BOUND,
    )
    assert binding.pad_ids == ("pad-1", "pad-2")
    assert binding.unit_id == "unit-a"
    assert binding.fingerprint.startswith("sha256:")


@pytest.mark.parametrize("status, field", [(PCBIntentBindingStatus.UNKNOWN, "unknowns"), (PCBIntentBindingStatus.CONFLICT, "conflicts")])
def test_unresolved_binding_status_requires_explicit_reason(status, field) -> None:
    with pytest.raises(ValueError, match=field):
        PCBElementBinding.create(
            intent_id="intent-1",
            net_id="net-1",
            status=status,
        )


def test_binding_fingerprint_changes_with_explicit_identity() -> None:
    base = bound_element(
        intent_id="intent-1",
        circuit_pin_id="circuit-pin-1",
        pcb_pad_ids=("pad-1",),
        status=PCBIntentBindingStatus.BOUND,
    )
    changed = bound_element(
        intent_id="intent-1",
        circuit_pin_id="circuit-pin-1",
        pcb_pad_ids=("pad-2",),
        status=PCBIntentBindingStatus.BOUND,
    )
    assert base.fingerprint != changed.fingerprint
