from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from embedded_copilot.application import FakeApplicationModelGateway
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelGatewayUnavailable,
    ModelPolicy,
    ModelRequest,
    validate_model_request,
)
from embedded_copilot.production_release.credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    PROVIDER_BINDING_INTEGRITY_PURPOSE,
    CredentialUnavailable,
    InMemoryCredentialProvider,
)
from embedded_copilot.production_release.provider import (
    DEEPSEEK_BASE_URL,
    ProviderConfiguration,
    ProviderConfigurationService,
    ProviderConfigurationStore,
    ProviderConnectionStatus,
    ReloadableModelGateway,
    TransmissionApprovalError,
    TransmissionApprovalService,
    TransmissionDecision,
)


def test_reloadable_gateway_recomputes_policy_derived_request_fields() -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="STM32 smart car",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    stale_policy = ModelPolicy(
        primary_provider="unavailable",
        model_name="unavailable",
        temperature=0.0,
        max_output_tokens=1024,
    )
    active_policy = ModelPolicy(
        primary_provider="deepseek",
        model_name="deepseek-chat",
        temperature=0.5,
        max_output_tokens=2048,
    )
    request = ModelRequest(
        request_id="request-1",
        task="STM32 smart car",
        engineering_context=context,
        system_instruction="Use supplied engineering context only.",
        model_policy=stale_policy,
    )
    provider = FakeApplicationModelGateway(content="offline answer")
    gateway = ReloadableModelGateway()
    gateway.install(provider, active_policy)

    response = gateway.generate(request)

    assert provider.call_count == 1
    assert response.provider == "deepseek"
    assert response.model_name == "deepseek-chat"


def test_reloadable_gateway_maps_validation_to_fixed_redacted_reason(caplog) -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="STM32 smart car",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    policy = ModelPolicy(primary_provider="deepseek", model_name="deepseek-chat")
    request = ModelRequest(
        request_id="request-1",
        task="STM32 smart car",
        engineering_context=context,
        system_instruction="Use supplied engineering context only.",
        model_policy=policy,
    )
    object.__setattr__(request, "prompt", "different")
    provider = FakeApplicationModelGateway()
    gateway = ReloadableModelGateway()
    gateway.install(provider, policy)

    with caplog.at_level(
        "INFO", logger="embedded_copilot.runtime_diagnostics"
    ), pytest.raises(ValidationError) as captured:
        gateway.generate(request)

    assert captured.value.reason_code == "MODEL_REQUEST_PROMPT_ALIAS_MISMATCH"
    assert captured.value.validation_error_count == 1
    assert captured.value.validation_errors == (
        {
            "location_hash": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            "error_type": "VALUE_ERROR",
            "received_type": "DICT",
            "message_code": "MODEL_REQUEST_PROMPT_ALIAS_MISMATCH",
        },
    )
    assert provider.call_count == 0
    assert "different" not in caplog.text
    record = next(
        item
        for item in caplog.records
        if getattr(item, "event_name", None)
        == "model_request_validation_boundary_telemetry"
    )
    assert record.validation_error_count == 1
    assert record.validation_errors == captured.value.validation_errors


def test_reloadable_gateway_normal_validation_does_not_emit_vector(caplog) -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="STM32 smart car",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    policy = ModelPolicy(primary_provider="deepseek", model_name="deepseek-chat")
    request = ModelRequest(
        request_id="request-1",
        task="STM32 smart car",
        engineering_context=context,
        system_instruction="Use supplied engineering context only.",
        model_policy=policy,
    )
    provider = FakeApplicationModelGateway()
    gateway = ReloadableModelGateway()
    gateway.install(provider, policy)

    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        bound, checked = gateway._request(request)

    assert bound is provider
    assert checked == request
    assert provider.call_count == 0
    assert not any(
        getattr(item, "event_name", None)
        == "model_request_validation_boundary_telemetry"
        for item in caplog.records
    )


def test_reloadable_gateway_canonical_alias_survives_downstream_revalidation() -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="STM32 smart car",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    policy = ModelPolicy(primary_provider="deepseek", model_name="deepseek-chat")
    request = ModelRequest(
        request_id="request-1",
        task="STM32 smart car",
        engineering_context=context,
        system_instruction="Use workspace://C:/redacted only.",
        model_policy=policy,
    )
    provider = FakeApplicationModelGateway(content="offline answer")
    gateway = ReloadableModelGateway()
    gateway.install(provider, policy)

    bound, first_validation = gateway._request(request)
    gateway_revalidation = validate_model_request(first_validation)

    assert bound is provider
    assert first_validation.system_instruction == first_validation.system_prompt
    assert gateway_revalidation == first_validation
    assert provider.call_count == 0

    object.__setattr__(first_validation, "system_prompt", "different")
    with pytest.raises(ValidationError, match="system_prompt must match"):
        validate_model_request(first_validation)
    assert provider.call_count == 0


def _transport(status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{DEEPSEEK_BASE_URL}/chat/completions"
        body = json.loads(request.content.decode("utf-8"))
        content = body["messages"][1]["content"]
        assert "Respond with OK." in content
        assert "qdwq" not in content
        assert "offline-secret" not in content
        if status != 200:
            return httpx.Response(status, json={"error": "redacted"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            },
        )

    return httpx.MockTransport(handler)


def _service(tmp_path: Path, *, status: int = 200) -> tuple[ProviderConfigurationService, InMemoryCredentialProvider]:
    credentials = InMemoryCredentialProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(status),
        now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    return service, credentials


def test_provider_secret_lifecycle_isolated_and_connection_fingerprint_invalidates(tmp_path: Path) -> None:
    service, credentials = _service(tmp_path)

    initial = service.projection()
    assert initial.credential_status == "NOT_CONFIGURED"
    assert initial.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert initial.updated_at is None
    assert initial.validation_status == ProviderConnectionStatus.NOT_CONFIGURED.value

    saved = service.save_credential(SecretStr("offline-secret"))
    assert saved.credential_status == "CONFIGURED"
    assert saved.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert saved.updated_at is not None
    assert saved.validation_status == ProviderConnectionStatus.UNVERIFIED.value
    assert "offline-secret" not in json.dumps(saved.model_dump(mode="json"))
    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) == b"offline-secret"
    assert "offline-secret" not in (tmp_path / "production.json").read_text(encoding="utf-8")

    available = service.test_connection()
    assert available.connection_status is ProviderConnectionStatus.AVAILABLE
    old_fingerprint = available.configuration_fingerprint
    assert available.tested_configuration_fingerprint == old_fingerprint
    assert service.binding.is_configured is True

    changed = service.save_configuration(provider="deepseek", model="deepseek-reasoner")
    assert changed.configuration_fingerprint != old_fingerprint
    assert changed.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert service.binding.is_configured is False

    service.save_credential(SecretStr("replacement-secret"))
    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) == b"replacement-secret"
    removed = service.remove_credential()
    assert removed.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) is None
    with pytest.raises(ModelGatewayUnavailable):
        service.binding.generate(object())  # type: ignore[arg-type]


def test_configuration_fingerprint_binds_credential_identity_and_external_replacement(
    tmp_path: Path,
) -> None:
    service, credentials = _service(tmp_path)
    saved = service.save_credential(SecretStr("first-secret"))
    first_fingerprint = saved.configuration_fingerprint
    assert saved.connection_status is ProviderConnectionStatus.UNVERIFIED

    configuration_text = (tmp_path / "production.json").read_text(encoding="utf-8")
    assert "first-secret" not in configuration_text
    assert "chat_credential_fingerprint" in configuration_text
    assert "hmac-sha256:" in configuration_text
    assert credentials.get_or_create_provider_binding_key() != credentials.get(
        CHAT_PROVIDER_CREDENTIAL_PURPOSE
    )
    assert PROVIDER_BINDING_INTEGRITY_PURPOSE not in configuration_text

    assert service.test_connection().connection_status is ProviderConnectionStatus.AVAILABLE
    credentials.set(CHAT_PROVIDER_CREDENTIAL_PURPOSE, b"external-replacement")

    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(),
    )
    projection = restarted.projection()
    assert projection.configuration_fingerprint != first_fingerprint
    assert projection.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert restarted.binding.is_configured is False

    replacement_fingerprint = projection.configuration_fingerprint
    credentials.delete(CHAT_PROVIDER_CREDENTIAL_PURPOSE)
    deleted = restarted.projection()
    assert deleted.configuration_fingerprint != replacement_fingerprint
    assert deleted.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert restarted.binding.is_configured is False


def test_provider_configuration_store_removes_unknown_secret_shaped_fields(tmp_path: Path) -> None:
    path = tmp_path / "production.json"
    path.write_text(
        json.dumps(
            {
                "chat_model": "deepseek-chat",
                "secret_canary": "EMBRIX_SECRET_CANARY_store",
                "api_key": "EMBRIX_SECRET_CANARY_key",
            }
        ),
        encoding="utf-8",
    )
    store = ProviderConfigurationStore(path)

    store.save(store.load())

    persisted = path.read_text(encoding="utf-8")
    assert "EMBRIX_SECRET_CANARY" not in persisted
    assert "api_key" not in persisted
    assert "chat_model" in persisted


def test_provider_status_read_does_not_mutate_persisted_state_or_create_integrity_credential(
    tmp_path: Path,
) -> None:
    service, credentials = _service(tmp_path)
    service.save_credential(SecretStr("test-only-secret"))
    service.test_connection()
    persisted_before = (tmp_path / "production.json").read_bytes()
    configuration_before = service._configuration  # type: ignore[attr-defined]
    credentials.delete(PROVIDER_BINDING_INTEGRITY_PURPOSE)
    persisted_after_integrity_delete = (tmp_path / "production.json").read_bytes()

    projection = service.projection()

    assert projection.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert (tmp_path / "production.json").read_bytes() == persisted_after_integrity_delete == persisted_before
    assert service._configuration == configuration_before  # type: ignore[attr-defined]
    assert credentials.get(PROVIDER_BINDING_INTEGRITY_PURPOSE) is None


def test_failed_credential_save_preserves_previous_credential_and_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, credentials = _service(tmp_path)
    service.save_credential(SecretStr("previous-secret"))
    service.test_connection()
    persisted_before = (tmp_path / "production.json").read_bytes()

    def fail_save(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("store unavailable")

    monkeypatch.setattr(ProviderConfigurationStore, "save", fail_save)

    with pytest.raises(OSError):
        service.save_credential(SecretStr("replacement-secret"))

    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) == b"previous-secret"
    assert (tmp_path / "production.json").read_bytes() == persisted_before
    assert service.binding.is_configured is True


@pytest.mark.parametrize(
    "secret",
    [SecretStr(""), SecretStr("   "), SecretStr("a" * 4097), SecretStr("valid\tkey"), SecretStr("valid\x00key")],
)
def test_provider_credential_validation_rejects_obviously_invalid_values(
    tmp_path: Path,
    secret: SecretStr,
) -> None:
    service, credentials = _service(tmp_path)

    with pytest.raises((TypeError, ValueError)):
        service.save_credential(secret)

    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) is None
    assert not (tmp_path / "production.json").exists()


def test_same_configuration_save_preserves_successful_verification(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    service.save_credential(SecretStr("test-only-secret"))
    available = service.test_connection()

    repeated = service.save_configuration(provider="deepseek", model="deepseek-chat")

    assert repeated.connection_status is ProviderConnectionStatus.AVAILABLE
    assert repeated.tested_at == available.tested_at
    assert repeated.tested_configuration_fingerprint == available.tested_configuration_fingerprint
    assert service.binding.is_configured is True


def test_failed_configuration_save_preserves_previous_configuration_and_gateway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(tmp_path)
    service.save_credential(SecretStr("test-only-secret"))
    available = service.test_connection()
    persisted_before = (tmp_path / "production.json").read_bytes()

    def fail_save(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("store unavailable")

    monkeypatch.setattr(ProviderConfigurationStore, "save", fail_save)

    with pytest.raises(OSError):
        service.save_configuration(provider="deepseek", model="deepseek-reasoner")

    current = service.projection()
    assert current.configuration_fingerprint == available.configuration_fingerprint
    assert current.connection_status is ProviderConnectionStatus.AVAILABLE
    assert (tmp_path / "production.json").read_bytes() == persisted_before
    assert service.binding.is_configured is True


def test_credential_manager_failure_fails_closed_without_fake_fallback(tmp_path: Path) -> None:
    class UnavailableCredentials:
        def get(self, purpose: str) -> bytes | None:
            del purpose
            raise CredentialUnavailable("credential manager unavailable")

        def set(self, purpose: str, value: bytes) -> None:
            del purpose, value
            raise CredentialUnavailable("credential manager unavailable")

        def delete(self, purpose: str) -> None:
            del purpose
            raise CredentialUnavailable("credential manager unavailable")

        def get_or_create_provider_binding_key(self) -> bytes:
            raise CredentialUnavailable("credential manager unavailable")

    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=UnavailableCredentials(),
        http_transport=_transport(),
    )

    projection = service.projection()
    assert projection.credential_status == "UNAVAILABLE"
    assert projection.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert service.binding.is_configured is False
    with pytest.raises(CredentialUnavailable):
        service.test_connection()


def test_connection_errors_are_safe_and_do_not_claim_available(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, status=401)
    service.save_credential(SecretStr("test-only-secret"))
    result = service.test_connection()
    assert result.connection_status is ProviderConnectionStatus.AUTH_FAILED
    assert result.last_test_status is ProviderConnectionStatus.AUTH_FAILED
    assert result.connection_diagnostic is not None
    assert result.connection_diagnostic.stage.value == "PROVIDER_RESPONSE"
    assert result.connection_diagnostic.http_status == 401
    assert result.connection_diagnostic.provider_error_code == "AUTHENTICATION_FAILED"
    assert result.connection_diagnostic.request_url == f"{DEEPSEEK_BASE_URL}/chat/completions"
    assert service.binding.is_configured is False


def test_connection_model_and_network_failures_have_frozen_statuses(tmp_path: Path) -> None:
    model_service, _ = _service(tmp_path / "model", status=404)
    model_service.save_credential(SecretStr("test-only-secret"))
    assert model_service.test_connection().connection_status is ProviderConnectionStatus.MODEL_UNAVAILABLE

    network_service, _ = _service(tmp_path / "network", status=503)
    network_service.save_credential(SecretStr("test-only-secret"))
    network_result = network_service.test_connection()
    assert network_result.connection_status is ProviderConnectionStatus.NETWORK_UNAVAILABLE
    assert network_result.connection_diagnostic is not None
    assert network_result.connection_diagnostic.stage.value == "PROVIDER_RESPONSE"
    assert network_result.connection_diagnostic.http_status == 503
    assert network_result.connection_diagnostic.provider_error_code == "HTTP_STATUS_ERROR"


def test_connection_rate_limit_is_projected_as_provider_response_with_http_status(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path, status=429)
    service.save_credential(SecretStr("test-only-secret"))

    result = service.test_connection()

    assert result.connection_status is ProviderConnectionStatus.RATE_LIMITED
    assert result.connection_diagnostic is not None
    assert result.connection_diagnostic.stage.value == "PROVIDER_RESPONSE"
    assert result.connection_diagnostic.http_status == 429
    assert result.connection_diagnostic.provider_error_code == "RATE_LIMITED"


def test_restart_rehydrates_only_a_matching_available_binding(tmp_path: Path) -> None:
    service, credentials = _service(tmp_path)
    service.save_credential(SecretStr("test-only-secret"))
    available = service.test_connection()
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(),
    )
    assert restarted.projection().configuration_fingerprint == available.configuration_fingerprint
    assert restarted.projection().connection_status is ProviderConnectionStatus.AVAILABLE
    assert restarted.binding.is_configured is True


def test_denied_transmission_does_not_trigger_an_extra_external_call(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
        )

    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=InMemoryCredentialProvider(),
        http_transport=httpx.MockTransport(handler),
    )
    service.save_credential(SecretStr("test-only-secret"))
    assert service.test_connection().connection_status is ProviderConnectionStatus.AVAILABLE
    approvals = TransmissionApprovalService(service)
    receipt = approvals.issue(
        tenant_id="tenant",
        requester="requester",
        session_id="session",
        project_id="project",
        request_id="request-denied",
        reference_fingerprints=(),
        decision=TransmissionDecision.DENIED,
    )
    with pytest.raises(TransmissionApprovalError, match="DENIED"):
        approvals.consume(
            tenant_id="tenant",
            requester="requester",
            session_id="session",
            project_id="project",
            request_id=receipt.request_id,
            reference_fingerprints=(),
        )
    assert calls == 1


def test_transmission_approval_is_bound_single_use_and_provider_fingerprint(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    approvals = TransmissionApprovalService(service, ttl_seconds=300)
    receipt = approvals.issue(
        tenant_id="tenant",
        requester="requester",
        session_id="session",
        project_id="project",
        request_id="request-1",
        reference_fingerprints=("sha256:" + "1" * 64,),
        decision=TransmissionDecision.APPROVED,
        now=datetime(2026, 8, 17, tzinfo=UTC),
    )
    assert "secret" not in receipt.model_dump_json()
    consumed = approvals.consume(
        tenant_id="tenant",
        requester="requester",
        session_id="session",
        project_id="project",
        request_id="request-1",
        reference_fingerprints=("sha256:" + "1" * 64,),
        now=datetime(2026, 8, 17, tzinfo=UTC),
    )
    assert consumed.receipt_id == receipt.receipt_id
    with pytest.raises(TransmissionApprovalError, match="MISMATCH"):
        approvals.consume(
            tenant_id="tenant",
            requester="requester",
            session_id="session",
            project_id="project",
            request_id="request-1",
            reference_fingerprints=("sha256:" + "1" * 64,),
            now=datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_deepseek_base_url_is_fixed(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        ProviderConfiguration(
            provider="deepseek", model="deepseek-chat", base_url="https://evil.example/v1"
        )
    assert service.projection().base_url == DEEPSEEK_BASE_URL
