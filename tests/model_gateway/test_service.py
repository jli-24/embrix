from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    LocalModelAdapter,
    ModelGatewayService,
    ModelGatewayProviderFailure,
    ModelGatewayUnavailable,
    ModelPolicy,
    ModelProviderNotImplemented,
    ModelProviderNetworkError,
    ModelProviderTimeout,
    ModelRequest,
    ModelResponse,
    OpenAIProviderAdapter,
    ProviderCapability,
)
from embedded_copilot.model_gateway.providers.openai_compatible import (
    OpenAICompatibleProviderAdapter,
)
from embedded_copilot.model_gateway.resilience import RetryPolicy


def _request(
    *,
    primary: str = "openai",
    fallbacks: tuple[str, ...] = (),
    endpoint_id: str | None = None,
) -> ModelRequest:
    snapshot = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="check GPIO12",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    return ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=snapshot,
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(
            primary_provider=primary,
            fallback_providers=fallbacks,
            model_name="test-model",
        ),
        endpoint_id=endpoint_id,
    )


class _Provider:
    def __init__(
        self, provider: str, content: str = "answer", error: Exception | None = None
    ) -> None:
        self.provider = provider
        self.content = content
        self.error = error
        self.calls = 0

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (ProviderCapability.TEXT,)

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.error:
            raise self.error
        return ModelResponse(
            content=self.content,
            model_name=request.model_policy.model_name,
            provider=self.provider,
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        return self.generate(request)


def test_explicit_fallback_order_and_no_implicit_fallback() -> None:
    first = _Provider("openai", error=TimeoutError())
    second = _Provider("local")
    service = ModelGatewayService(providers={"openai": first, "local": second})
    assert service.generate(_request(fallbacks=("local",))).provider == "local"
    assert (first.calls, second.calls) == (1, 1)
    with pytest.raises(ModelGatewayUnavailable):
        ModelGatewayService(providers={"openai": first, "local": second}).generate(
            _request()
        )


def test_provider_failure_falls_back_only_when_explicitly_declared() -> None:
    first = _Provider("openai", error=ModelProviderTimeout())
    second = _Provider("local")
    service = ModelGatewayService(providers={"openai": first, "local": second})

    response = service.generate(_request(fallbacks=("local",)))

    assert response.provider == "local"
    assert (first.calls, second.calls) == (1, 1)


def test_explicit_primary_endpoint_does_not_pin_an_explicit_fallback() -> None:
    first = _Provider("openai", error=ModelProviderTimeout())
    second = _Provider("local")
    service = ModelGatewayService(providers={"endpoint-openai": first, "local": second})

    response = service.generate(
        _request(fallbacks=("local",), endpoint_id="endpoint-openai")
    )

    assert response.provider == "local"
    assert (first.calls, second.calls) == (1, 1)


def test_resilient_gateway_retry_is_bounded_and_reuses_canonical_service() -> None:
    provider = _Provider("openai", error=TimeoutError())
    service = ModelGatewayService(
        providers={"openai": provider}, retry_policy=RetryPolicy(max_attempts=2)
    )
    with pytest.raises(ModelGatewayUnavailable):
        service.generate_resilient(_request())
    assert provider.calls == 2


def test_gateway_preserves_safe_transport_stage_across_provider_boundary() -> None:
    provider = _Provider(
        "openai",
        error=ModelProviderNetworkError(
            safe_transport_error_code="CONNECT_ERROR",
            safe_transport_stage="TLS",
        ),
    )

    with pytest.raises(ModelGatewayProviderFailure) as raised:
        ModelGatewayService(providers={"openai": provider}).generate(_request())

    assert raised.value.reason_code == "PROVIDER_TRANSPORT_FAILED"
    assert raised.value.safe_transport_error_code == "CONNECT_ERROR"
    assert raised.value.safe_transport_stage == "TLS"


def test_malformed_provider_response_and_cancellation_are_not_success() -> None:
    service = ModelGatewayService(providers={"openai": _Provider("local")})
    with pytest.raises(ModelGatewayUnavailable):
        service.generate(_request())
    cancelled = _Provider("openai", error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ModelGatewayService(providers={"openai": cancelled}).agenerate(_request())
        )


def test_injected_openai_compatible_transport_and_local_runner() -> None:
    provider = _Provider("openai")
    adapter = OpenAIProviderAdapter(transport=provider)
    assert adapter.generate(_request()).provider == "openai"
    with pytest.raises(ModelProviderNotImplemented):
        LocalModelAdapter(runner=_Provider("local")).generate(_request(primary="local"))


def test_legacy_openai_compatible_adapter_path_remains_injected_only() -> None:
    with pytest.raises(ModelGatewayUnavailable):
        OpenAICompatibleProviderAdapter().generate(_request())
    assert (
        OpenAICompatibleProviderAdapter(transport=_Provider("openai"))
        .generate(_request())
        .provider
        == "openai"
    )
