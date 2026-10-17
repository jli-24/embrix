from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.presets import provider_template_presets
from embedded_copilot.model_gateway.profiles import EndpointType, ProtocolFamily
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.model_discovery import OfficialModelDiscovery
from embedded_copilot.production_release.provider import ProviderConfigurationService


def _service(tmp_path: Path, *, status: int = 200) -> ProviderConfigurationService:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        if status != 200:
            return httpx.Response(status, json={"error": "redacted"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "relay-chat-large"},
                    {"id": "relay-embedding-ignored-by-client"},
                ]
            },
        )

    credentials = InMemoryCredentialProvider()
    return ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        http_transport=httpx.MockTransport(handler),
        model_gateway_management=ModelGatewayManagementService(),
        model_discovery=OfficialModelDiscovery(
            credentials, transport=httpx.MockTransport(handler)
        ),
    )


def test_add_templates_exclude_protocol_adapters() -> None:
    template_ids = {item.provider_id for item in provider_template_presets()}

    assert "custom-relay" in template_ids
    assert "local" in template_ids
    assert "openai-compatible" not in template_ids
    assert "anthropic-compatible" not in template_ids
    assert "custom" not in template_ids


def test_custom_relay_connection_has_stable_identity_and_preserves_model_provider(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    saved = service.save_configuration(
        provider="custom-relay",
        connection_name="My Relay",
        base_url="https://relay.example.com/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
    )
    provider_id = saved.provider

    assert provider_id.startswith("custom-my-relay-")
    assert saved.model == ""
    assert saved.connection_status.value == "PROVIDER_NOT_CONFIGURED"

    with_provider = service.save_credential(SecretStr("relay-secret"), provider=provider_id)
    assert with_provider.provider == provider_id
    assert with_provider.credential_status == "CONFIGURED"

    refreshed = service.refresh_model_pool(provider_id=provider_id)
    assert refreshed.status == "UPDATED"
    assert {item.provider_id for item in refreshed.models} == {provider_id}
    assert {item.remote_model_id for item in refreshed.models} == {"relay-chat-large"}
    endpoint = service._management.endpoint_profile(refreshed.endpoint_id)
    assert endpoint is not None
    assert endpoint.endpoint_type is EndpointType.RELAY
    assert endpoint.protocol_family is ProtocolFamily.OPENAI_CHAT


def test_custom_relay_discovery_failure_keeps_saved_connection(tmp_path: Path) -> None:
    service = _service(tmp_path, status=503)
    saved = service.save_configuration(
        provider="custom-relay",
        connection_name="Failure Relay",
        base_url="https://relay.example.com/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
    )
    provider_id = saved.provider
    service.save_credential(SecretStr("relay-secret"), provider=provider_id)

    refreshed = service.refresh_model_pool(provider_id=provider_id)

    assert refreshed.status == "UNAVAILABLE"
    assert service.projection(provider_id=provider_id).provider == provider_id
    assert json.dumps(service.projection(provider_id=provider_id).model_dump(mode="json"))


def test_custom_relay_connection_can_be_deactivated_without_deleting_history(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    saved = service.save_configuration(
        provider="custom-relay",
        connection_name="Delete Me Relay",
        base_url="https://relay.example.com/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
    )
    provider_id = saved.provider
    service.save_credential(SecretStr("relay-secret"), provider=provider_id)
    discovered = service.refresh_model_pool(provider_id=provider_id)
    history_ids = {item.model_profile_id for item in discovered.models}

    service.remove_connection(provider=provider_id)

    endpoint = service._management.endpoint_profile(f"endpoint-{provider_id}")
    assert endpoint is not None and endpoint.enabled is False
    assert provider_id not in service.configured_provider_ids()
    assert history_ids <= {item.model_profile_id for item in service._management.list_models()}
    assert service.projection().provider == "deepseek"


def test_custom_relay_identity_and_name_survive_service_restart(tmp_path: Path) -> None:
    credentials = InMemoryCredentialProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        model_gateway_management=ModelGatewayManagementService(),
    )
    saved = service.save_configuration(
        provider="custom-relay",
        connection_name="Persistent Relay",
        base_url="https://relay.example.com/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
    )
    provider_id = saved.provider
    service.save_credential(SecretStr("relay-secret"), provider=provider_id)

    restarted_management = ModelGatewayManagementService()
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        model_gateway_management=restarted_management,
    )

    restored = restarted_management.providers.get(provider_id)
    assert restored is not None
    assert restored.display_name == "Persistent Relay"
    assert restarted.configured_provider_ids() == (provider_id,)
