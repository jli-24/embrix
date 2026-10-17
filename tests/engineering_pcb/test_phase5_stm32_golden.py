from embedded_copilot.engineering_pcb import PCBIntentBindingStatus
from tests.engineering_pcb._phase5_bound_fixtures import bound_element


def test_stm32_golden_blocks_are_explicitly_bindable() -> None:
    bindings = (
        bound_element(
            intent_id="intent-stm32-control",
            circuit_block_id="block-power",
            circuit_pin_id="circuit-regulator-vout",
            schematic_pin_binding_id="schematic-regulator-vout",
            pcb_component_id="pcb-regulator",
            pcb_pad_ids=("pad-vin", "pad-vout"),
            status=PCBIntentBindingStatus.BOUND,
        ),
        bound_element(
            intent_id="intent-stm32-control",
            circuit_block_id="block-can",
            schematic_net_id="net-canh",
            pcb_track_ids=("track-canh",),
            status=PCBIntentBindingStatus.BOUND,
        ),
        bound_element(
            intent_id="intent-stm32-control",
            circuit_block_id="block-swd",
            pcb_component_id="pcb-swd-header",
            pcb_zone_ids=("keepout-swd-access",),
            status=PCBIntentBindingStatus.BOUND,
        ),
    )
    assert {item.circuit_block_id for item in bindings} == {"block-power", "block-can", "block-swd"}
    assert all(item.status is PCBIntentBindingStatus.BOUND for item in bindings)
