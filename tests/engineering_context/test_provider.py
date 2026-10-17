from embedded_copilot.engineering_context import (
    EngineeringContextProviderPort,
    EngineeringContextSourcePort,
)


def test_context_ports_are_read_only_and_standalone() -> None:
    assert hasattr(EngineeringContextProviderPort, "get_context")
    assert hasattr(EngineeringContextSourcePort, "read_memory")
    assert hasattr(EngineeringContextSourcePort, "read_graph")
    assert hasattr(EngineeringContextSourcePort, "read_knowledge")
    assert hasattr(EngineeringContextSourcePort, "read_multimodal")
    forbidden = {"write", "update", "delete", "promote", "execute", "invoke", "run"}
    assert not forbidden.intersection(dir(EngineeringContextProviderPort))
    assert not forbidden.intersection(dir(EngineeringContextSourcePort))
