from embedded_copilot.engineering_knowledge import EngineeringContextProviderPort
from embedded_copilot.engineering_knowledge import EngineeringContextRetrievalService


def test_context_provider_is_only_a_port() -> None:
    assert hasattr(EngineeringContextProviderPort, "get_context")
    assert not hasattr(EngineeringContextProviderPort, "write")
    assert not hasattr(EngineeringContextProviderPort, "update")


def test_context_retrieval_has_no_reverse_write_surface() -> None:
    assert hasattr(EngineeringContextRetrievalService, "get_context")
    assert not hasattr(EngineeringContextRetrievalService, "write")
    assert not hasattr(EngineeringContextRetrievalService, "update")
    assert not hasattr(EngineeringContextRetrievalService, "mutate")
