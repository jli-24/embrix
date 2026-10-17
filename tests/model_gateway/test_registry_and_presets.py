from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.presets import (
    manual_model_profile,
    provider_presets,
)
from embedded_copilot.model_gateway.profiles import (
    CapabilityStatus,
    DiscoveryStatus,
    EndpointProfile,
    EndpointType,
    ModelCapability,
    ProtocolFamily,
    ProviderProfile,
)
from embedded_copilot.model_gateway.registry import (
    CapabilityProbeResult,
    CapabilityProbeService,
    ModelDiscoveryService,
)


def test_provider_presets_cover_official_compatible_and_local_identities() -> None:
    presets = {item.provider_id: item for item in provider_presets()}
    assert {
        "openai",
        "anthropic",
        "google",
        "xai",
        "deepseek",
        "zhipu",
        "alibaba",
        "moonshot",
        "minimax",
        "custom",
        "openai-compatible",
        "anthropic-compatible",
        "local",
    } <= set(presets)
    assert presets["openai"].default_endpoint_template == "https://api.openai.com/v1"
    assert presets["local"].supported_protocols == (ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,)
    assert presets["openai-compatible"].model_discovery_mode.value == "MANUAL_MODEL_ID"
    assert presets["anthropic-compatible"].supported_protocols == (ProtocolFamily.ANTHROPIC_MESSAGES,)
    assert all("key" not in str(item.model_dump(mode="json")).casefold() for item in presets.values())


def test_manual_discovery_is_unverified_until_probe() -> None:
    model = manual_model_profile(
        model_profile_id="manual-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="llama3",
        display_name="Llama 3",
        capabilities=(ModelCapability.TEXT,),
    )
    assert model.verification_status.value == "UNVERIFIED"
    endpoint = EndpointProfile(
        endpoint_id="endpoint-local",
        provider_id="local",
        display_name="Ollama",
        endpoint_type=EndpointType.LOCAL,
        protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
    )

    class _Discovery:
        def discover(self, requested: EndpointProfile):
            assert requested.endpoint_id == endpoint.endpoint_id
            return (model,)

    discovered = ModelDiscoveryService(_Discovery()).discover(endpoint)
    assert discovered[0].discovery_status.value == "DISCOVERED"


def test_probe_result_binds_endpoint_and_model_identity() -> None:
    model = manual_model_profile(
        model_profile_id="manual-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="llama3",
        display_name="Llama 3",
    )
    endpoint = EndpointProfile(
        endpoint_id="endpoint-local",
        provider_id="local",
        display_name="Ollama",
        endpoint_type=EndpointType.LOCAL,
        protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
    )

    class _Probe:
        def probe(self, requested_endpoint, requested_model, probe_type):
            return CapabilityProbeResult(
                endpoint_id=requested_endpoint.endpoint_id,
                model_profile_id=requested_model.model_profile_id,
                probe_type=probe_type,
                probe_version="v1",
                timestamp=datetime.now(UTC),
                status=CapabilityStatus.PASS,
                provider_id=requested_model.provider_id,
                credential_ref="credential:local",
                model_id=requested_model.remote_model_id,
                credential_revision=requested_model.credential_revision,
                endpoint_revision=requested_model.endpoint_revision,
                response_fingerprint="sha256:" + "1" * 64,
            )

    result = CapabilityProbeService(_Probe()).probe(endpoint, model, "TEXT")
    assert result.status is CapabilityStatus.PASS
    assert result.response_fingerprint == "sha256:" + "1" * 64


def test_probe_result_uses_canonical_credential_reference_validation() -> None:
    common = {
        "endpoint_id": "endpoint-zhipu",
        "model_profile_id": "model-zhipu",
        "probe_type": "TEXT",
        "probe_version": "v1",
        "timestamp": datetime.now(UTC),
        "status": CapabilityStatus.PASS,
    }

    accepted = CapabilityProbeResult(
        **common,
        credential_ref="credential:zhipu",
    )
    assert accepted.credential_ref == "credential:zhipu"

    with pytest.raises(ValueError, match="credential_ref is invalid"):
        CapabilityProbeResult(
            **common,
            credential_ref="api_key:exposed-value",
        )


def test_management_updates_discovered_model_and_capability_provenance() -> None:
    service = ModelGatewayManagementService()
    service.add_provider(
        ProviderProfile(
            provider_id="local", display_name="Local", supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,)
        )
    )
    endpoint = EndpointProfile(
        endpoint_id="endpoint-local",
        provider_id="local",
        display_name="Local",
        endpoint_type=EndpointType.LOCAL,
        protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
    )
    service.add_endpoint(endpoint)
    model = manual_model_profile(
        model_profile_id="local-model",
        provider_id="local",
        endpoint_id=endpoint.endpoint_id,
        remote_model_id="llama3",
        display_name="Llama 3",
    )

    class _Discovery:
        def discover(self, requested: EndpointProfile):
            return (model,)

    class _Probe:
        def probe(self, requested_endpoint, requested_model, probe_type):
            return CapabilityProbeResult(
                endpoint_id=requested_endpoint.endpoint_id,
                model_profile_id=requested_model.model_profile_id,
                probe_type=probe_type,
                probe_version="v1",
                timestamp=datetime.now(UTC),
                status=CapabilityStatus.PASS,
                provider_id=requested_model.provider_id,
                credential_ref="credential:local",
                model_id=requested_model.remote_model_id,
                credential_revision=requested_model.credential_revision,
                endpoint_revision=requested_model.endpoint_revision,
            )

    service.set_discovery_port(_Discovery())
    service.set_capability_probe_port(_Probe())
    discovered = service.discover_models(endpoint.endpoint_id)
    assert discovered[0].discovery_status is DiscoveryStatus.DISCOVERED
    result = service.probe_capability(model.model_profile_id, "TEXT")
    assert result.status is CapabilityStatus.PASS
    assert service.models.get(model.model_profile_id).verification_status.value == "VERIFIED"
