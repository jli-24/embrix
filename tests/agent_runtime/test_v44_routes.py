from embedded_copilot.agent_runtime import CapabilityType, classify_request

from .conftest import knowledge_request


def test_v44_route_priority_adds_eda_before_pcb() -> None:
    decision = classify_request(knowledge_request("Run DRC for this PCB board layout"))
    assert decision.selected_capability is CapabilityType.EDA_ANALYSIS
    assert decision.candidates[:2] == (
        CapabilityType.EDA_ANALYSIS,
        CapabilityType.PCB_ANALYSIS,
    )


def test_debug_boot_log_remains_higher_priority_than_hardware() -> None:
    decision = classify_request(
        knowledge_request("Debug error: panic in the boot log hardware test")
    )
    assert decision.selected_capability is CapabilityType.DEBUG_ANALYSIS
    assert CapabilityType.HARDWARE_TEST in decision.candidates
