from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from embedded_copilot.model_gateway.governance import BoundModelIdentity
from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.persistence import SQLiteModelGatewayStore
from embedded_copilot.model_gateway.presets import manual_model_profile
from embedded_copilot.model_gateway.profiles import (
    CapabilityStatus,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCapability,
    ProtocolFamily,
    ProviderProfile,
)
from embedded_copilot.model_gateway.registry import CapabilityProbeResult
from embedded_copilot.production_release.model_pool import ProviderModelPool


def _service(path: Path | None = None) -> ModelGatewayManagementService:
    service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(path) if path else None
    )
    service.add_provider(
        ProviderProfile(
            provider_id="local",
            display_name="Local",
            supported_protocols=(ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-local",
            provider_id="local",
            display_name="Local",
            endpoint_type=EndpointType.LOCAL,
            protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
            base_url="http://127.0.0.1:11434/v1",
        )
    )
    model = manual_model_profile(
        model_profile_id="local-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="local-chat",
        display_name="Local chat",
    )
    service.add_model(
        model.model_copy(
            update={
                "availability": ModelAvailability.AVAILABLE,
                "availability_status": ModelAvailability.AVAILABLE,
                "credential_revision": "revision-a",
                "endpoint_revision": "endpoint-a",
            }
        )
    )
    return service


def _identity() -> BoundModelIdentity:
    return BoundModelIdentity(
        provider_id="local",
        endpoint_id="endpoint-local",
        credential_ref="credential:local",
        model_profile_id="local-model",
        model_id="local-chat",
    )


class _Probe:
    def __init__(
        self,
        *,
        status: CapabilityStatus = CapabilityStatus.PASS,
        credential_revision: str = "revision-a",
        model_id: str = "local-chat",
    ) -> None:
        self.status = status
        self.credential_revision = credential_revision
        self.model_id = model_id

    def probe(
        self, endpoint: EndpointProfile, model, probe_type: str
    ) -> CapabilityProbeResult:
        return CapabilityProbeResult(
            endpoint_id=endpoint.endpoint_id,
            model_profile_id=model.model_profile_id,
            probe_type=probe_type,
            probe_version="test-v1",
            timestamp=datetime(2026, 9, 28, tzinfo=UTC),
            status=self.status,
            provider_id=model.provider_id,
            credential_ref="credential:local",
            model_id=self.model_id,
            credential_revision=self.credential_revision,
            endpoint_revision="endpoint-a",
        )


def test_text_probe_promotes_only_bound_pass_and_survives_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    service = _service(path)
    service.set_capability_probe_port(_Probe())
    assert service.model_enablement(_identity())[0] is False
    service.probe_capability("local-model", "TEXT")
    assert service.model_profile("local-model").capabilities == (ModelCapability.TEXT,)
    assert service.model_enablement(_identity())[0] is True

    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    assert recovered.restore_from_store() is True
    assert len(recovered.state().capability_probes) == 1
    assert recovered.model_enablement(_identity())[0] is True
    recovered.models.upsert(
        recovered.model_profile("local-model").model_copy(
            update={"credential_revision": "revision-b"}
        )
    )
    assert recovered.model_enablement(_identity())[0] is False


@pytest.mark.parametrize(
    "probe",
    [
        _Probe(status=CapabilityStatus.FAIL),
        _Probe(credential_revision="revision-old"),
        _Probe(model_id="other-model"),
    ],
)
def test_failed_or_wrong_identity_probe_does_not_promote_capability(
    probe: _Probe,
) -> None:
    service = _service()
    service.set_capability_probe_port(probe)
    if probe.model_id != "local-chat" or probe.credential_revision != "revision-a":
        with pytest.raises(ValueError, match="identity"):
            service.probe_capability("local-model", "TEXT")
    else:
        service.probe_capability("local-model", "TEXT")
    assert service.model_enablement(_identity())[0] is False


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    (("endpoint_id", "other-endpoint"), ("model_profile_id", "other-profile")),
)
def test_probe_commit_rejects_mismatched_result_binding(
    field: str, wrong_value: str
) -> None:
    service = _service()

    class _UncheckedProbe:
        def probe(
            self, endpoint: EndpointProfile, model, probe_type: str
        ) -> CapabilityProbeResult:
            return (
                _Probe()
                .probe(endpoint, model, probe_type)
                .model_copy(update={field: wrong_value})
            )

    # Exercise the management commit boundary independently of the adapter's
    # own identity validation.
    service._probes = _UncheckedProbe()
    with pytest.raises(ValueError, match="capability probe identity is invalid"):
        service.probe_capability("local-model", "TEXT")
    assert service.model_profile("local-model").capability_sources == ()
    assert service.state().capability_probes == ()


def test_discovery_refresh_preserves_same_revision_evidence_and_invalidates_changed_revision() -> (
    None
):
    service = _service()
    endpoint = service.endpoint_profile("endpoint-local")
    bare = manual_model_profile(
        model_profile_id="local-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="local-chat",
        display_name="Local chat",
    )

    class _Discovery:
        def discover(self, requested: EndpointProfile):
            return (bare,)

    pool = ProviderModelPool(service, discovery=_Discovery())
    revision = pool.endpoint_revision(endpoint)
    service.models.upsert(
        service.model_profile("local-model").model_copy(
            update={"endpoint_revision": revision}
        )
    )

    class _CurrentProbe:
        def probe(
            self, requested_endpoint: EndpointProfile, model, probe_type: str
        ) -> CapabilityProbeResult:
            return CapabilityProbeResult(
                endpoint_id=model.endpoint_id,
                model_profile_id=model.model_profile_id,
                probe_type=probe_type,
                probe_version="test-v1",
                timestamp=datetime(2026, 9, 28, tzinfo=UTC),
                status=CapabilityStatus.PASS,
                provider_id=model.provider_id,
                credential_ref="credential:local",
                model_id=model.remote_model_id,
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
            )

    service.set_capability_probe_port(_CurrentProbe())
    service.probe_capability("local-model", "TEXT")
    assert service.model_profile("local-model").capabilities == (ModelCapability.TEXT,)

    same = pool.refresh(
        endpoint, credential_revision="revision-a", credential_configured=True
    )
    assert same.models[0].capabilities == (ModelCapability.TEXT,)
    assert same.models[0].capability_sources[0].status is CapabilityStatus.PASS
    changed = pool.refresh(
        endpoint, credential_revision="revision-b", credential_configured=True
    )
    assert changed.models[0].capability_sources == ()
    assert service.model_enablement(_identity())[0] is False


def test_probe_cannot_write_old_revision_after_model_changes() -> None:
    service = _service()

    class _RacingProbe(_Probe):
        def probe(
            self, endpoint: EndpointProfile, model, probe_type: str
        ) -> CapabilityProbeResult:
            service.models.upsert(
                model.model_copy(update={"credential_revision": "revision-b"})
            )
            return super().probe(endpoint, model, probe_type)

    service.set_capability_probe_port(_RacingProbe())
    with pytest.raises(ValueError, match="revision changed"):
        service.probe_capability("local-model", "TEXT")
    assert service.model_profile("local-model").capability_sources == ()
    assert service.model_enablement(_identity())[0] is False


def test_probe_keeps_connection_test_availability_update() -> None:
    service = _service()
    service.models.upsert(
        service.model_profile("local-model").model_copy(
            update={
                "availability": ModelAvailability.UNKNOWN,
                "availability_status": ModelAvailability.UNKNOWN,
            }
        )
    )

    class _ConnectionProbe(_Probe):
        def probe(
            self, endpoint: EndpointProfile, model, probe_type: str
        ) -> CapabilityProbeResult:
            service.models.upsert(
                model.model_copy(
                    update={
                        "availability": ModelAvailability.AVAILABLE,
                        "availability_status": ModelAvailability.AVAILABLE,
                    }
                )
            )
            return super().probe(endpoint, model, probe_type)

    service.set_capability_probe_port(_ConnectionProbe())
    service.probe_capability("local-model", "TEXT")
    assert service.model_enablement(_identity())[0] is True


def test_inflight_discovery_cannot_erase_new_probe_evidence() -> None:
    service = _service()
    endpoint = service.endpoint_profile("endpoint-local")
    entered = Event()
    release = Event()
    bare = manual_model_profile(
        model_profile_id="local-model",
        provider_id="local",
        endpoint_id="endpoint-local",
        remote_model_id="local-chat",
        display_name="Local chat",
    )

    class _SlowDiscovery:
        def discover(self, requested: EndpointProfile):
            entered.set()
            assert release.wait(5)
            return (bare,)

    pool = ProviderModelPool(service, discovery=_SlowDiscovery())
    service.models.upsert(
        service.model_profile("local-model").model_copy(
            update={"endpoint_revision": pool.endpoint_revision(endpoint)}
        )
    )

    class _CurrentProbe:
        def probe(
            self, requested_endpoint: EndpointProfile, model, probe_type: str
        ) -> CapabilityProbeResult:
            return CapabilityProbeResult(
                endpoint_id=model.endpoint_id,
                model_profile_id=model.model_profile_id,
                probe_type=probe_type,
                probe_version="test-v1",
                timestamp=datetime(2026, 9, 28, tzinfo=UTC),
                status=CapabilityStatus.PASS,
                provider_id=model.provider_id,
                credential_ref="credential:local",
                model_id=model.remote_model_id,
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
            )

    service.set_capability_probe_port(_CurrentProbe())
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            pool.refresh,
            endpoint,
            credential_revision="revision-a",
            credential_configured=True,
        )
        assert entered.wait(5)
        service.probe_capability("local-model", "TEXT")
        release.set()
        result = future.result(timeout=5)
    assert result.models[0].capabilities == (ModelCapability.TEXT,)
    assert (
        service.model_profile("local-model").capability_sources[0].status
        is CapabilityStatus.PASS
    )


def test_refresh_save_failure_restores_registry() -> None:
    service = _service()
    endpoint = service.endpoint_profile("endpoint-local")
    before = service.list_models()

    class _Discovery:
        def discover(self, requested: EndpointProfile):
            return (
                manual_model_profile(
                    model_profile_id="local-model",
                    provider_id="local",
                    endpoint_id="endpoint-local",
                    remote_model_id="local-chat",
                    display_name="Local chat",
                ),
            )

    class _FailingStore:
        def save(self, state) -> None:
            raise OSError("test store unavailable")

    service._store = _FailingStore()
    result = ProviderModelPool(service, discovery=_Discovery()).refresh(
        endpoint, credential_revision="revision-a", credential_configured=True
    )
    assert result.status == "UNAVAILABLE"
    assert service.list_models() == before


def test_probe_save_failure_does_not_leave_in_memory_enablement() -> None:
    service = _service()
    service.set_capability_probe_port(_Probe())

    class _FailingStore:
        def save(self, state) -> None:
            raise OSError("test store unavailable")

    service._store = _FailingStore()
    with pytest.raises(OSError, match="store unavailable"):
        service.probe_capability("local-model", "TEXT")
    assert service.model_profile("local-model").capability_sources == ()
    assert service.state().capability_probes == ()
    assert service.model_enablement(_identity())[0] is False
