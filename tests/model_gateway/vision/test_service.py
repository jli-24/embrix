from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import ModelGatewayRejected
from embedded_copilot.model_gateway.vision import (
    FakeVisionProviderAdapter,
    VisionModelProviderPort,
    VisionRequest,
    VisionResponse,
    VisionService,
)
from embedded_copilot.model_gateway.vision.exceptions import VisionRateLimitError


def _request() -> VisionRequest:
    return VisionRequest(
        request_id="vision-request-1",
        prompt="inspect board components",
        image_reference="s3://engineering/image.png",
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


class _MalformedProvider:
    provider = "fake"
    model = "fake-vision"

    def generate(self, request: VisionRequest) -> object:
        del request
        return object()

    async def agenerate(self, request: VisionRequest) -> object:
        return self.generate(request)


class _MismatchedProvider:
    provider = "fake"
    model = "fake-vision"

    def generate(self, request: VisionRequest) -> VisionResponse:
        response = FakeVisionProviderAdapter().generate(request)
        return VisionResponse(
            provider="other",
            model=response.model,
            observation=response.observation,
            metadata=response.metadata,
        )

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        return self.generate(request)


class _CancelledProvider:
    provider = "fake"
    model = "fake-vision"

    def generate(self, request: VisionRequest) -> VisionResponse:
        del request
        raise asyncio.CancelledError()

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        del request
        raise asyncio.CancelledError()


class _RateLimitedProvider:
    provider = "fake"
    model = "fake-vision"

    def generate(self, request: VisionRequest) -> VisionResponse:
        del request
        raise VisionRateLimitError()

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        del request
        raise VisionRateLimitError()


def test_service_uses_only_the_injected_provider() -> None:
    provider: VisionModelProviderPort = FakeVisionProviderAdapter()
    service = VisionService(provider=provider)

    response = service.generate(_request())

    assert response.provider == provider.provider
    assert response.model == provider.model
    assert response.observation.observation_id == "observation-fake-1"
    with pytest.raises(TypeError):
        VisionService(provider=None)  # type: ignore[arg-type]


def test_service_fails_closed_for_malformed_or_mismatched_responses() -> None:
    with pytest.raises(ModelGatewayRejected):
        VisionService(provider=_MalformedProvider()).generate(_request())
    with pytest.raises(ModelGatewayRejected):
        VisionService(provider=_MismatchedProvider()).generate(_request())
    with pytest.raises(TypeError):
        VisionService(provider=object())  # type: ignore[arg-type]


def test_service_propagates_cancellation() -> None:
    service = VisionService(provider=_CancelledProvider())
    with pytest.raises(asyncio.CancelledError):
        service.generate(_request())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.agenerate(_request()))


def test_service_preserves_sanitized_vision_provider_errors() -> None:
    service = VisionService(provider=_RateLimitedProvider())

    with pytest.raises(VisionRateLimitError):
        service.generate(_request())
    with pytest.raises(VisionRateLimitError):
        asyncio.run(service.agenerate(_request()))
