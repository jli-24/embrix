from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from embedded_copilot.model_gateway import (
    AccountUsageSnapshot,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelDescriptor,
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderProfile,
    UsageSnapshotStatus,
)
from embedded_copilot.model_gateway.usage import AccountUsageSnapshotCache
from embedded_copilot.production_release.credentials import (
    InMemoryCredentialProvider,
    provider_connection_credential_purpose,
    provider_credential_purpose,
)
from embedded_copilot.production_release.model_discovery import (
    ModelDiscoveryFailure,
    OfficialModelDiscovery,
)
from embedded_copilot.production_release.model_pool import ProviderModelPool
from embedded_copilot.production_release.provider import ProviderConfigurationService
from embedded_copilot.production_release.provider_verification import (
    ProviderVerificationReceipt,
    ProviderVerificationReceiptStore,
    ProviderVerificationTarget,
    ResponseParseStatus,
    VerificationStatus,
    evaluate_provider_golden,
)


def _endpoint(endpoint_id: str, provider_id: str = "zhipu") -> EndpointProfile:
    return EndpointProfile(
        endpoint_id=endpoint_id,
        provider_id=provider_id,
        display_name=endpoint_id,
        endpoint_type=EndpointType.RELAY,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url=f"https://{endpoint_id}.example.test/v1",
        credential_ref=f"credential:{endpoint_id}",
    )


def _management_with_connections() -> tuple[ModelGatewayManagementService, object, object]:
    management = ModelGatewayManagementService()
    management.add_provider(ProviderProfile(provider_id="zhipu", display_name="Zhipu"))
    endpoint_a = _endpoint("endpoint-zhipu-a")
    endpoint_b = _endpoint("endpoint-zhipu-b")
    management.add_endpoint(endpoint_a)
    management.add_endpoint(endpoint_b)
    connection_a = management.create_connection(
        provider_id="zhipu",
        endpoint_id=endpoint_a.endpoint_id,
        connection_name="A",
        credential_ref=endpoint_a.credential_ref,
        connection_id="conn-zhipu-a",
    )
    connection_b = management.create_connection(
        provider_id="zhipu",
        endpoint_id=endpoint_b.endpoint_id,
        connection_name="B",
        credential_ref=endpoint_b.credential_ref,
        connection_id="conn-zhipu-b",
    )
    return management, connection_a, connection_b


class _Discovery:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def discover(
        self,
        endpoint: EndpointProfile,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> tuple[ModelDescriptor, ...]:
        self.calls.append((connection_id, credential_revision))
        return (
            ModelDescriptor(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                model_id=f"model-{endpoint.endpoint_id}",
                display_name=endpoint.endpoint_id,
                protocol_family=endpoint.protocol_family,
                availability=ModelAvailability.AVAILABLE,
                availability_source="PROVIDER_API",
                credential_revision=credential_revision,
                endpoint_revision=ProviderModelPool.endpoint_revision(endpoint),
            ),
        )


def test_connection_scoped_credential_rotation_does_not_touch_sibling(tmp_path: Path) -> None:
    management, connection_a, connection_b = _management_with_connections()
    credentials = InMemoryCredentialProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        model_gateway_management=management,
    )

    service.save_credential(SecretStr("secret-a-1"), connection_id=connection_a.connection_id)
    service.save_credential(SecretStr("secret-b-1"), connection_id=connection_b.connection_id)
    service.save_credential(SecretStr("secret-a-2"), connection_id=connection_a.connection_id)

    assert credentials.get(provider_connection_credential_purpose(connection_a.connection_id)) == b"secret-a-2"
    assert credentials.get(provider_connection_credential_purpose(connection_b.connection_id)) == b"secret-b-1"
    assert credentials.get(provider_credential_purpose("zhipu")) is None
    assert "secret-a-2" not in (tmp_path / "provider.json").read_text(encoding="utf-8")


def test_discovery_failure_evidence_contains_connection_id() -> None:
    credentials = InMemoryCredentialProvider()
    connection_id = "conn-zhipu-a"
    credentials.set(provider_connection_credential_purpose(connection_id), b"secret-a")
    endpoint = _endpoint("endpoint-zhipu-a").model_copy(
        update={
            "endpoint_type": EndpointType.OFFICIAL,
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
        }
    )
    discovery = OfficialModelDiscovery(
        credentials,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error": {"code": "busy"}})
        ),
    )

    with pytest.raises(ModelDiscoveryFailure) as captured:
        discovery.discover(endpoint, connection_id=connection_id, credential_revision="revision-a")

    assert captured.value.evidence.connection_id == connection_id
    assert captured.value.evidence.credential_revision == "revision-a"


def test_single_pool_isolates_connection_and_revision_context() -> None:
    management, connection_a, connection_b = _management_with_connections()
    discovery = _Discovery()
    pool = ProviderModelPool(management, discovery=discovery)
    endpoint_a = management.endpoint_profile(connection_a.endpoint_id)
    endpoint_b = management.endpoint_profile(connection_b.endpoint_id)
    assert endpoint_a is not None and endpoint_b is not None

    first = pool.refresh(
        endpoint_a,
        connection_id=connection_a.connection_id,
        credential_revision="revision-a-1",
        credential_configured=True,
    )
    second = pool.refresh(
        endpoint_b,
        connection_id=connection_b.connection_id,
        credential_revision="revision-b-1",
        credential_configured=True,
    )
    assert first.connection_id == connection_a.connection_id
    assert second.connection_id == connection_b.connection_id
    assert [item.remote_model_id for item in pool.usable(endpoint_a, connection_id=connection_a.connection_id, credential_revision="revision-a-1")] == ["model-endpoint-zhipu-a"]
    assert pool.usable(endpoint_a, connection_id=connection_a.connection_id, credential_revision="revision-a-2") == ()
    assert discovery.calls == [
        (connection_a.connection_id, "revision-a-1"),
        (connection_b.connection_id, "revision-b-1"),
    ]


def _target(connection_id: str | None, revision: str = "revision-a") -> ProviderVerificationTarget:
    return ProviderVerificationTarget(
        provider_id="zhipu",
        connection_id=connection_id,
        endpoint_id="endpoint-zhipu-a",
        model_id="glm-4",
        credential_revision=revision,
        endpoint_revision="endpoint-revision-a",
        model_revision="model-revision-a",
        configuration_fingerprint="config-a",
    )


def _receipt(target: ProviderVerificationTarget) -> ProviderVerificationReceipt:
    now = datetime.now(UTC)
    return ProviderVerificationReceipt.create(
        provider_id=target.provider_id,
        connection_id=target.connection_id,
        endpoint_id=target.endpoint_id,
        model_id=target.model_id,
        credential_ref="credential:endpoint-zhipu-a",
        credential_revision=target.credential_revision,
        endpoint_revision=target.endpoint_revision,
        model_revision=target.model_revision,
        configuration_fingerprint=target.configuration_fingerprint,
        protocol_family="OPENAI_CHAT",
        requested_at=now,
        completed_at=now,
        http_status=200,
        requested_model=target.model_id,
        verified_model=target.model_id,
        response_parse_status=ResponseParseStatus.PASS,
        model_available=True,
        connection_status="AVAILABLE",
        verification_status=VerificationStatus.PASS,
    )


def test_verification_current_lookup_requires_connection_and_revision(tmp_path: Path) -> None:
    store = ProviderVerificationReceiptStore(tmp_path / "verification.sqlite3")
    target_a = _target("conn-zhipu-a")
    receipt = _receipt(target_a)
    store.append(receipt)

    assert store.find_current_for_configuration(target_a) == receipt
    assert store.find_current_for_configuration(_target("conn-zhipu-b")) is None
    assert store.find_current_for_configuration(_target("conn-zhipu-a", "revision-old")) is None
    assert evaluate_provider_golden(receipt, _target("conn-zhipu-b")).reason_code == "REVISION_MISMATCH"

    legacy = _receipt(_target(None))
    store.append(legacy)
    assert store.find_current_for_configuration(target_a) == receipt


class _UsagePort:
    def read(
        self,
        endpoint: EndpointProfile,
        credential_ref: str | None,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> AccountUsageSnapshot:
        balance = 10.0 if connection_id == "conn-zhipu-a" else 20.0
        return AccountUsageSnapshot(
            provider_id=endpoint.provider_id,
            endpoint_id=endpoint.endpoint_id,
            connection_id=connection_id,
            credential_ref=credential_ref,
            credential_revision=credential_revision,
            balance=balance,
            current_balance=balance,
            status=UsageSnapshotStatus.AVAILABLE,
        )


def test_usage_cache_keeps_same_provider_balances_separate() -> None:
    endpoint = _endpoint("endpoint-zhipu-a")
    cache = AccountUsageSnapshotCache(_UsagePort())
    first = cache.read(
        endpoint,
        credential_ref=endpoint.credential_ref,
        connection_id="conn-zhipu-a",
        credential_revision="revision-a",
    )
    second = cache.read(
        endpoint,
        credential_ref=endpoint.credential_ref,
        connection_id="conn-zhipu-b",
        credential_revision="revision-b",
    )
    assert (first.connection_id, first.current_balance) == ("conn-zhipu-a", 10.0)
    assert (second.connection_id, second.current_balance) == ("conn-zhipu-b", 20.0)
    assert {item.connection_id for item in cache.cached()} == {"conn-zhipu-a", "conn-zhipu-b"}


def test_disable_invalidates_connection_without_deleting_model_history(tmp_path: Path) -> None:
    management, connection_a, _ = _management_with_connections()
    credentials = InMemoryCredentialProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        model_gateway_management=management,
        model_discovery=_Discovery(),
    )
    service.save_credential(SecretStr("secret-a"), connection_id=connection_a.connection_id)
    refreshed = service.refresh_model_pool(connection_id=connection_a.connection_id)
    assert refreshed.models

    service.disable_connection(connection_a.connection_id)
    endpoint = management.endpoint_profile(connection_a.endpoint_id)
    assert endpoint is not None and endpoint.enabled is False
    assert management.list_models()
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_DISABLED"):
        service.model_pool(connection_id=connection_a.connection_id)
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_DISABLED"):
        service.refresh_model_pool(connection_id=connection_a.connection_id)


def test_provider_only_path_is_unique_only_and_fail_closed(tmp_path: Path) -> None:
    management, _, _ = _management_with_connections()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=InMemoryCredentialProvider(),
        model_gateway_management=management,
    )
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_AMBIGUOUS"):
        service.refresh_model_pool(provider_id="zhipu")
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_AMBIGUOUS"):
        service.latest_verification(provider_id="zhipu")
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_NOT_CONFIGURED|PROVIDER_NOT_CONFIGURED"):
        service.projection(provider_id="openai")
