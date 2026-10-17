from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.model_gateway.metrics import AccountUsageSnapshot
from embedded_copilot.model_gateway.profiles import (
    EndpointProfile,
    EndpointType,
    ProtocolFamily,
    UsageSnapshotStatus,
)
from embedded_copilot.model_gateway.usage import AccountUsageSnapshotCache


def _endpoint() -> EndpointProfile:
    return EndpointProfile(
        endpoint_id="endpoint-local",
        provider_id="local",
        display_name="Local",
        endpoint_type=EndpointType.LOCAL,
        protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
    )


def test_usage_cache_uses_ttl_and_manual_refresh() -> None:
    now = [100.0]

    class _Port:
        calls = 0

        def read(self, endpoint, credential_ref):
            self.calls += 1
            return AccountUsageSnapshot(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                status=UsageSnapshotStatus.AVAILABLE,
                usage_today=self.calls,
            )

    port = _Port()
    cache = AccountUsageSnapshotCache(port, ttl_seconds=10.0, clock=lambda: now[0])
    assert cache.read(_endpoint()).usage_today == 1
    assert cache.read(_endpoint()).usage_today == 1
    assert port.calls == 1
    now[0] = 111.0
    assert cache.read(_endpoint()).usage_today == 2
    assert cache.refresh(_endpoint()).usage_today == 3
    assert port.calls == 3


def test_usage_failure_returns_stale_or_unavailable_without_blocking() -> None:
    now = [100.0]

    class _Port:
        calls = 0

        def read(self, endpoint, credential_ref):
            self.calls += 1
            if self.calls == 1:
                return AccountUsageSnapshot(
                    provider_id=endpoint.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    status=UsageSnapshotStatus.AVAILABLE,
                )
            raise TimeoutError

    port = _Port()
    cache = AccountUsageSnapshotCache(port, ttl_seconds=1.0, clock=lambda: now[0])
    assert cache.read(_endpoint()).status is UsageSnapshotStatus.AVAILABLE
    now[0] = 102.0
    assert cache.refresh(_endpoint()).status is UsageSnapshotStatus.STALE
    assert cache.cached()[0].status is UsageSnapshotStatus.STALE

    unavailable = AccountUsageSnapshotCache(type("Failing", (), {"read": lambda *_: (_ for _ in ()).throw(ConnectionError())})())
    assert unavailable.read(_endpoint()).status is UsageSnapshotStatus.UNAVAILABLE


def test_usage_failure_separates_current_balance_from_last_known_balance() -> None:
    queried_at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    class _Port:
        calls = 0

        def read(self, endpoint, credential_ref):
            self.calls += 1
            if self.calls == 1:
                return AccountUsageSnapshot(
                    provider_id=endpoint.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    credential_ref=credential_ref,
                    balance=12.5,
                    updated_at=queried_at,
                    status=UsageSnapshotStatus.AVAILABLE,
                )
            raise TimeoutError

    port = _Port()
    cache = AccountUsageSnapshotCache(port, ttl_seconds=1.0, clock=lambda: 100.0)
    available = cache.read(_endpoint(), credential_ref="credential:local")
    stale = cache.refresh(_endpoint(), credential_ref="credential:local")

    assert available.current_balance == 12.5
    assert available.last_known_balance == 12.5
    assert available.last_success_at == queried_at
    assert stale.status is UsageSnapshotStatus.STALE
    assert stale.current_balance is None
    assert stale.last_known_balance == 12.5
    assert stale.last_success_at == queried_at
