from __future__ import annotations

import asyncio

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway.vision import (
    FakeVisionProviderAdapter,
    VisionRequest,
)


def _request() -> VisionRequest:
    return VisionRequest(
        request_id="vision-request-1",
        prompt="inspect board components",
        image_reference="workspace://attachment/001",
        context=EngineeringContextSnapshot.create(
            project_id="project-1",
            task_description="inspect board",
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=0.5,
            provenance=(),
            verification_status="UNVERIFIED",
        ),
        metadata=(),
    )


def test_fake_provider_is_deterministic_for_sync_and_async_calls() -> None:
    adapter = FakeVisionProviderAdapter()
    first = adapter.generate(_request())
    second = adapter.generate(_request())
    asynchronous = asyncio.run(adapter.agenerate(_request()))

    assert first == second == asynchronous
    assert (first.provider, first.model) == ("fake", "fake-vision")
    assert first.observation.objects == ("microcontroller",)
    assert first.observation.components == ("STM32",)
    assert first.observation.interfaces == ("UART",)
    assert first.observation.issues == ("possible_missing_decoupling",)
    assert first.observation.confidence == 0.85
    assert first.observation.evidence == ("fake-observation",)
