import pytest

from embedded_copilot.engineering_pcb import PCBElementBinding, PCBIntentBindingStatus
from tests.engineering_pcb._phase5_bound_fixtures import bound_element


def test_element_binding_keeps_explicit_cross_layer_identity() -> None:
    binding = bound_element(
        intent_id="intent-stm32-control",
        circuit_block_id="block-can",
        circuit_component_id="component-transceiver",
        circuit_pin_id="pin-canh",
        circuit_net_id="net-canh",
        schematic_component_instance_id="sch-u2",
        schematic_pin_binding_id="sch-pin-u2-canh",
        schematic_net_id="sch-net-canh",
        pcb_component_id="pcb-component-u2",
        pcb_footprint_id="pcb-footprint-u2",
        pcb_pad_ids=("pcb-pad-1",),
        pcb_track_ids=("pcb-track-canh",),
        status=PCBIntentBindingStatus.BOUND,
    )
    assert binding.circuit_block_id == "block-can"
    assert binding.pcb_pad_ids == ("pcb-pad-1",)


def test_ambiguous_element_binding_is_not_auto_bound() -> None:
    with pytest.raises(ValueError, match="unknowns"):
        PCBElementBinding.create(
            intent_id="intent-stm32-control",
            pcb_component_id="pcb-component-u2",
            status=PCBIntentBindingStatus.UNKNOWN,
        )


def test_bound_multi_pad_binding_requires_explicit_pin_identity() -> None:
    with pytest.raises(ValueError, match="multiple PCB pads"):
        PCBElementBinding.create(
            intent_id="intent-stm32-control",
            circuit_component_id="component-mcu",
            schematic_component_instance_id="sch-u1",
            pcb_component_id="pcb-u1",
            pcb_pad_ids=("pad-a", "pad-b"),
            status=PCBIntentBindingStatus.BOUND,
        )
