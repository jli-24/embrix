from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    ModelGatewayProviderFailure,
    ModelGatewayService,
    ModelGatewayUnavailable,
    ModelPolicy,
    ModelRequest,
    ModelResponse,
    ProviderCapability,
)
from embedded_copilot.model_gateway.transport import (
    GatewayTransportBinding,
    ResolvedTransportEntry,
)
from embedded_copilot.production_release.provider import ReloadableModelGateway


def _request(identity: BoundModelIdentity) -> ModelRequest:
    context = EngineeringContextSnapshot.create(
        project_id="project-a",
        task_description="transport task",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    return ModelRequest(
        request_id="transport-request",
        task="transport task",
        engineering_context=context,
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(
            primary_provider=identity.provider_id, model_name=identity.model_id
        ),
        endpoint_id=identity.endpoint_id,
        credential_ref=identity.credential_ref,
        preferred_provider=identity.provider_id,
    )


class _Provider:
    capabilities = (ProviderCapability.TEXT,)

    def __init__(self, name: str, *, error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ModelResponse(
            content=self.name,
            model_name=request.model_policy.model_name,
            provider=request.model_policy.primary_provider,
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        return self.generate(request)


def _identity(endpoint: str, credential: str) -> BoundModelIdentity:
    return BoundModelIdentity(
        provider_id="deepseek",
        endpoint_id=endpoint,
        credential_ref=credential,
        model_profile_id=f"model-{endpoint}",
        model_id=f"remote-{endpoint}",
    )


def _entry(identity: BoundModelIdentity, provider: _Provider) -> ResolvedTransportEntry:
    return ResolvedTransportEntry(
        provider_id=identity.provider_id,
        endpoint_id=identity.endpoint_id,
        credential_ref=identity.credential_ref,
        adapter=provider,
        endpoint_revision="endpoint-r1",
        credential_revision="credential-r1",
    )


def test_transport_only_install_is_ready_without_legacy_policy() -> None:
    identity = _identity("endpoint-a", "credential:a")
    provider = _Provider("A")
    gateway = ModelGatewayService(providers={"deepseek": provider})
    binding = ReloadableModelGateway()

    binding.install_transport(gateway, (_entry(identity, provider),))

    assert binding.is_configured is True
    assert binding.transport_ready is True
    assert isinstance(binding.transport_binding, GatewayTransportBinding)
    with pytest.raises(ModelGatewayUnavailable, match="LEGACY_POLICY_UNAVAILABLE"):
        _ = binding.model_policy
    assert binding.generate_bound(identity, _request(identity)).content == "A"
    with pytest.raises(ModelGatewayUnavailable, match="LEGACY_POLICY_UNAVAILABLE"):
        binding.generate(_request(identity))


def test_bound_transport_revalidates_canonical_system_instruction_alias() -> None:
    identity = _identity("endpoint-a", "credential:a")
    provider = _Provider("A")
    gateway = ModelGatewayService(providers={"deepseek": provider})
    binding = ReloadableModelGateway()
    binding.install_transport(gateway, (_entry(identity, provider),))
    base = _request(identity)
    request = ModelRequest.model_validate({
        **base.model_dump(mode="python"),
        "system_instruction": "Use workspace://C:/safe/reference as context.",
        "system_prompt": None,
        "fingerprint": "sha256:" + "0" * 64,
    })

    assert binding.generate_bound(identity, request).content == "A"
    assert asyncio.run(binding.agenerate_bound(identity, request)).content == "A"
    assert provider.calls == 2


def test_exact_transport_key_isolates_same_provider_connections() -> None:
    identity_a = _identity("endpoint-a", "credential:a")
    identity_b = _identity("endpoint-b", "credential:b")
    provider_a = _Provider("A")
    provider_b = _Provider("B")
    gateway = ModelGatewayService(providers={"deepseek": provider_a})
    binding = ReloadableModelGateway()
    binding.install_transport(
        gateway, (_entry(identity_a, provider_a), _entry(identity_b, provider_b))
    )

    assert binding.generate_bound(identity_a, _request(identity_a)).content == "A"
    assert binding.generate_bound(identity_b, _request(identity_b)).content == "B"
    assert provider_a.calls == 1
    assert provider_b.calls == 1


def test_same_endpoint_url_with_different_credentials_remains_isolated() -> None:
    identity_a = _identity("endpoint-shared-url", "credential:a")
    identity_b = _identity("endpoint-shared-url", "credential:b")
    provider_a = _Provider("A")
    provider_b = _Provider("B")
    gateway = ModelGatewayService(providers={"deepseek": provider_a})
    binding = ReloadableModelGateway()
    binding.install_transport(
        gateway, (_entry(identity_a, provider_a), _entry(identity_b, provider_b))
    )

    assert binding.generate_bound(identity_a, _request(identity_a)).content == "A"
    assert binding.generate_bound(identity_b, _request(identity_b)).content == "B"
    assert provider_a.calls == provider_b.calls == 1


def test_legacy_policy_and_bound_transport_are_independent() -> None:
    identity = _identity("endpoint-a", "credential:a")
    provider = _Provider("X")
    gateway = ModelGatewayService(providers={"deepseek": provider})
    binding = ReloadableModelGateway()
    legacy = ModelPolicy(primary_provider="deepseek", model_name="legacy-model")
    binding.install(gateway, legacy)
    binding.install_transport(gateway, (_entry(identity, provider),))

    assert binding.model_policy.model_name == "legacy-model"
    assert binding.generate_bound(identity, _request(identity)).content == "X"
    assert binding.generate(_request(identity)).model_name == "legacy-model"


def test_wrong_credential_and_unknown_endpoint_fail_before_provider_call() -> None:
    identity = _identity("endpoint-a", "credential:a")
    provider = _Provider("A")
    gateway = ModelGatewayService(providers={"deepseek": provider})
    binding = ReloadableModelGateway()
    binding.install_transport(gateway, (_entry(identity, provider),))

    with pytest.raises(Exception) as wrong:
        binding.generate_bound(
            identity.model_copy(update={"credential_ref": "credential:wrong"}),
            _request(identity.model_copy(update={"credential_ref": "credential:wrong"})),
        )
    assert getattr(wrong.value, "reason_code", None) == "MODEL_PROTOCOL_ADAPTER_NOT_FOUND"
    unknown = _identity("endpoint-unknown", identity.credential_ref)
    with pytest.raises(Exception) as missing:
        binding.generate_bound(unknown, _request(unknown))
    assert getattr(missing.value, "reason_code", None) == "MODEL_PROTOCOL_ADAPTER_NOT_FOUND"
    assert provider.calls == 0


def test_rebind_replaces_target_while_preserving_other_connection() -> None:
    identity_a = _identity("endpoint-a", "credential:a")
    identity_b = _identity("endpoint-b", "credential:b")
    provider_a1 = _Provider("A1")
    provider_a2 = _Provider("A2")
    provider_b = _Provider("B")
    gateway = ModelGatewayService(providers={"deepseek": provider_a1})
    binding = ReloadableModelGateway()
    binding.install_transport(
        gateway, (_entry(identity_a, provider_a1), _entry(identity_b, provider_b))
    )
    binding.rebind_transport(
        gateway,
        (
            _entry(_identity("endpoint-a", "credential:a2"), provider_a2),
            _entry(identity_b, provider_b),
        ),
    )

    with pytest.raises(Exception) as old:
        binding.generate_bound(identity_a, _request(identity_a))
    assert getattr(old.value, "reason_code", None) == "MODEL_PROTOCOL_ADAPTER_NOT_FOUND"
    assert binding.generate_bound(identity_b, _request(identity_b)).content == "B"
    identity_a2 = _identity("endpoint-a", "credential:a2")
    assert binding.generate_bound(identity_a2, _request(identity_a2)).content == "A2"
    assert provider_a1.calls == 0


def test_async_bound_path_uses_same_exact_transport_without_fallback() -> None:
    identity_a = _identity("endpoint-a", "credential:a")
    identity_b = _identity("endpoint-b", "credential:b")
    provider_a = _Provider("A")
    provider_b = _Provider("B", error=ModelGatewayProviderFailure("PROVIDER_TRANSPORT_FAILED"))
    gateway = ModelGatewayService(providers={"deepseek": provider_a})
    binding = ReloadableModelGateway()
    binding.install_transport(
        gateway, (_entry(identity_a, provider_a), _entry(identity_b, provider_b))
    )

    async def run() -> None:
        assert (await binding.agenerate_bound(identity_a, _request(identity_a))).content == "A"
        with pytest.raises(ModelGatewayProviderFailure):
            await binding.agenerate_bound(identity_b, _request(identity_b))

    asyncio.run(run())
    assert provider_a.calls == 1
    assert provider_b.calls == 1
