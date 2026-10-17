from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.v70_release_routes import router as release_router
from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRuntime,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationSupervisor,
    FixedApplicationClock,
)
from embedded_copilot.core.config import Settings
from embedded_copilot.debug_agent_adapter import DebugPromptAdapter
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.firmware_agent_adapter import FirmwarePromptAdapter
from embedded_copilot.model_gateway import (
    AccountUsageSnapshot,
    BoundModelIdentity,
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    EngineeringStage,
    ModelCapability,
    ModelGatewayManagementService,
    ModelGatewayUnavailable,
    ModelInvocationRequest,
    ModelInvocationService,
    ModelPolicy,
    ModelRequest,
    PaidModelPolicy,
    PolicyScope,
    ProjectModelPolicy,
    SelectionMode,
    SQLiteModelGatewayStore,
    StageModelPolicy,
    TaskAssignment,
    TaskType,
    VerificationStatus,
    provider_presets,
)
from embedded_copilot.model_gateway.invocation import ManagedModelSelectionResolver
from embedded_copilot.production_release import (
    ProviderModelPool,
    test_connection_diagnostic as connection_diagnostic,
)
from embedded_copilot.production_release.credentials import (
    CredentialUnavailable,
    InMemoryCredentialProvider,
    provider_credential_purpose,
)
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ProviderConnectionStatus,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _transport(calls: list[tuple[str, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append((str(request.url), str(payload["model"])))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "offline answer"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
        )

    return httpx.MockTransport(handler)


class _UsagePort:
    def read(
        self,
        endpoint,
        credential_ref,
        *,
        connection_id=None,
        credential_revision=None,
    ):
        return AccountUsageSnapshot(
            provider_id=endpoint.provider_id,
            endpoint_id=endpoint.endpoint_id,
            connection_id=connection_id,
            credential_ref=credential_ref,
            credential_revision=credential_revision,
            quota_remaining=1,
            status="AVAILABLE",
        )


def _context(project_id: str = "project-1") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        tenant_id="tenant-1",
        task_description="offline switch",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )


def _application_context(project_id: str = "project-1") -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="application-memory-evidence",
        project_id=project_id,
        tenant_id="tenant-1",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="offline application route",
        summary="approved application context",
        fact_key="route",
        fact_value="offline",
        source_reference="memory:application-route",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=1.0,
        verification_status="APPROVED",
        provenance=("memory:application-route",),
    )
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        tenant_id="tenant-1",
        task_description="offline switch",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(evidence,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=1.0,
        provenance=(evidence.source(),),
        verification_status="APPROVED",
    )


def _request(
    *,
    task_type: str = "GENERAL_CONVERSATION",
    project_id: str = "project-1",
    request_id: str | None = None,
) -> ModelRequest:
    return ModelRequest(
        request_id=request_id or "request-" + task_type.lower(),
        task="Answer offline.",
        engineering_context=_context(project_id),
        system_instruction="Answer offline only.",
        model_policy=ModelPolicy(
            primary_provider="unavailable", model_name="unavailable"
        ),
        project_id=project_id,
        task_type=task_type,
    )


def _service(
    tmp_path: Path,
    calls: list[tuple[str, str]],
    *,
    manager: ModelGatewayManagementService | None = None,
    http_transport: object | None = None,
    credentials: InMemoryCredentialProvider | None = None,
):
    credentials = credentials or InMemoryCredentialProvider()
    management = manager or ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    if not management.list_providers():
        from embedded_copilot.model_gateway import provider_presets

        for preset in provider_presets():
            management.add_provider(preset)
    management.set_account_usage_port(_UsagePort())
    # These tests exercise provider switching and transport identity.  The
    # selected provider/model is an explicit test fixture choice, so allow the
    # request to reach the transport while the router's ASK/DENY guard remains
    # covered by the model-gateway routing tests.
    management.set_global_policy(
        management.global_policy().model_copy(
            update={"paid_model_policy": PaidModelPolicy.ALLOW}
        )
    )
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=http_transport or _transport(calls),
        model_gateway_management=management,
    )
    return service, credentials, management


@pytest.mark.parametrize(
    ("provider", "model", "protocol", "request_suffix"),
    [
        ("deepseek", "deepseek-chat", "OPENAI_CHAT", "/chat/completions"),
        ("zhipu", "glm-4.7-flash", "OPENAI_CHAT", "/chat/completions"),
        ("openai", "gpt-4o-mini", "OPENAI_RESPONSES", "/responses"),
        ("google", "gemini-2.5-flash", "GOOGLE_GENAI", "/models/gemini-2.5-flash:generateContent"),
        ("anthropic", "claude-3-5-haiku-latest", "ANTHROPIC_MESSAGES", "/messages"),
        ("alibaba", "qwen-plus", "OPENAI_CHAT", "/chat/completions"),
    ],
)
def test_production_provider_connection_uses_shared_endpoint_credential_and_protocol_path(
    tmp_path: Path,
    provider: str,
    model: str,
    protocol: str,
    request_suffix: str,
) -> None:
    calls: list[tuple[str, str]] = []

    def unauthorized_transport(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), str(request.headers.get("authorization", ""))))
        return httpx.Response(401, json={"error": "redacted"})

    service, credentials, _ = _service(
        tmp_path,
        calls=[],
        http_transport=httpx.MockTransport(unauthorized_transport),
    )
    service.save_configuration(provider=provider, model=model)
    service.save_credential(SecretStr("test-only-secret"), provider=provider)

    result = service.test_connection()

    assert result.connection_status is ProviderConnectionStatus.AUTH_FAILED
    assert result.provider == provider
    assert result.model == model
    assert result.endpoint_id == f"endpoint-{provider}"
    assert result.credential_ref == f"credential:{provider}"
    assert result.connection_diagnostic is not None
    assert result.connection_diagnostic.protocol_family == protocol
    assert result.connection_diagnostic.request_url == result.base_url + request_suffix
    assert result.connection_diagnostic.stage.value == "PROVIDER_RESPONSE"
    assert result.connection_diagnostic.http_status == 401
    assert calls and calls[-1][0] == result.connection_diagnostic.request_url
    assert credentials.get(provider_credential_purpose(provider)) == b"test-only-secret"
    assert "test-only-secret" not in result.model_dump_json()


def _application_request(request_id: str, question: str) -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id=request_id,
        trace_id=f"trace-{request_id}",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="engineer-1",
        question=question,
        requested_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
    )


def _application_runtime(
    *,
    request: ApplicationQuestionRequest,
    route: ApplicationRoute,
    gateway: object,
    model_policy: ModelPolicy,
    context: EngineeringContextSnapshot | None,
    model_invocation_service: ModelInvocationService | None = None,
    firmware_agent: object | None = None,
    debug_agent: object | None = None,
) -> ApplicationRuntime:
    return ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(request, route=route)
            ),
            context_provider=FakeApplicationContext(context),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(datetime(2026, 9, 10, 12, 0, tzinfo=UTC)),
            model_policy=model_policy,
            mode="GOVERNED" if model_invocation_service is not None else "LEGACY",
            model_invocation_service=model_invocation_service,
            firmware_agent=firmware_agent,
            firmware_prompt=FirmwarePromptAdapter()
            if firmware_agent is not None
            else None,
            debug_agent=debug_agent,
            debug_prompt=DebugPromptAdapter() if debug_agent is not None else None,
        )
    )


def _bound_identity(management: ModelGatewayManagementService, model_profile_id: str) -> BoundModelIdentity:
    model = management.model_profile(model_profile_id)
    assert model is not None
    endpoint = management.endpoint_profile(model.endpoint_id)
    assert endpoint is not None
    return BoundModelIdentity.from_profile(model, endpoint.credential_ref or "credential:local")


def _invocation_service(
    service: ProviderConfigurationService,
    management: ModelGatewayManagementService,
) -> ModelInvocationService:
    for connection in management.list_connections():
        endpoint = management.endpoint_profile(connection.endpoint_id)
        assert endpoint is not None
        management.read_account_usage(
            endpoint.endpoint_id,
            credential_ref=connection.credential_ref,
            connection_id=connection.connection_id,
            credential_revision=service.credential_revision_for_connection(
                connection.connection_id
            ),
            force=True,
        )
    return ModelInvocationService(
        selection_resolver=ManagedModelSelectionResolver(
            management,
            service.available_credential_refs,
            service.credential_revision_for_connection,
            ProviderModelPool.endpoint_revision,
        ),
        gateway=service.binding,
    )


class _SpecialistResult:
    def __init__(self, evidence: tuple[EngineeringContextEvidence, ...]) -> None:
        self.facts = ("specialist evidence",)
        self.evidence = tuple(item.evidence_id for item in evidence)
        self.analysis = ("specialist route selected",)
        self.confidence = 1.0
        self.limitations = ()
        self.context_evidence = evidence


class _SpecialistAgent:
    def __init__(self, result: _SpecialistResult) -> None:
        self._result = result

    def analyze(self, _request: object, _context: object) -> _SpecialistResult:
        return self._result

    async def aanalyze(self, request: object, context: object) -> _SpecialistResult:
        return self.analyze(request, context)


def _specialist_evidence(
    source_type: ContextSourceType, evidence_id: str
) -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        project_id="project-1",
        tenant_id="tenant-1",
        source_type=source_type,
        subject="offline specialist route",
        summary="offline specialist evidence",
        fact_key="route",
        fact_value=source_type.value,
        source_reference=f"artifact:{evidence_id}",
        source_fingerprint="sha256:" + "e" * 64,
        confidence=1.0,
        verification_status="VERIFIED",
        provenance=(f"artifact:{evidence_id}",),
    )


def _model_variant(active: object, model_profile_id: str, remote_model_id: str):
    variant = active.model_copy(
        update={
            "model_profile_id": model_profile_id,
            "remote_model_id": remote_model_id,
            "display_name": remote_model_id,
        }
    )
    return variant.model_copy(
        update={
            "capability_sources": tuple(
                evidence.model_copy(
                    update={
                        "provider_id": variant.provider_id,
                        "endpoint_id": variant.endpoint_id,
                        "credential_ref": "credential:zhipu",
                        "model_profile_id": variant.model_profile_id,
                        "model_id": variant.remote_model_id,
                        "credential_revision": variant.credential_revision,
                        "endpoint_revision": variant.endpoint_revision,
                    }
                )
                for evidence in variant.capability_sources
            )
        }
    )


def test_production_switches_deepseek_to_zhipu_without_deepseek_fallback(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, credentials, management = _service(tmp_path, calls)

    service.save_credential(SecretStr("deepseek-secret"))
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    calls.clear()

    service.save_configuration(provider="zhipu", model="glm-4")
    assert (
        service.save_credential(
            SecretStr("zhipu-secret"), provider="zhipu"
        ).connection_status
        is ProviderConnectionStatus.UNVERIFIED
    )
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    calls.clear()
    response = service.binding.generate(_request())

    assert response.provider == "zhipu"
    assert response.endpoint_id == "endpoint-zhipu"
    assert response.model_name == "glm-4"
    assert response.remote_model_id == "glm-4"
    assert calls == [("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4")]
    assert response.credential_ref == "credential:zhipu"
    assert response.routing_record is not None
    assert response.routing_record.fallback_used is False
    assert management.global_policy().active_model_profile_id is None
    assert management.global_policy().active_model_profile_id is None
    assert credentials.get("CHAT_PROVIDER_API_KEY:deepseek") == b"deepseek-secret"


def test_application_runtime_conversation_and_requirement_use_production_router(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, management = _service(tmp_path, calls)
    service.save_credential(SecretStr("deepseek-secret"))
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    deepseek_identity = _bound_identity(
        management, service.projection().model_profile_id or ""
    )
    management.create_allowed_model_policy(
        scope=PolicyScope.GLOBAL,
        project_id=None,
        stage_id=None,
        identity=deepseek_identity,
    )
    now = datetime.now(UTC)
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-general-runtime",
            project_id="project-1",
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=deepseek_identity,
            created_at=now,
            updated_at=now,
        )
    )
    invocation = _invocation_service(service, management)

    general_request = _application_request("application-general", "offline switch")
    general_result = _application_runtime(
        request=general_request,
        route=ApplicationRoute.KNOWLEDGE,
        gateway=service.binding,
        model_policy=service.model_policy,
        context=_application_context(),
        model_invocation_service=invocation,
    ).answer(general_request)
    assert general_result.status.value == "COMPLETED"
    assert general_result.answer is not None
    assert general_result.answer.model_info.provider == "deepseek"
    assert general_result.answer.model_info.model_name == "deepseek-chat"

    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )

    requirement_request = _application_request(
        "application-requirement", "Design a new embedded sensor node"
    )
    requirement_model = _model_variant(
        management.model_profile(service.projection().model_profile_id or ""),
        "model-zhipu-requirement-runtime",
        "glm-4-requirement",
    )
    assert requirement_model is not None
    management.models.upsert(requirement_model)
    requirement_identity = BoundModelIdentity.from_profile(
        requirement_model, "credential:zhipu"
    )
    management.create_allowed_model_policy(
        scope=PolicyScope.GLOBAL,
        project_id=None,
        stage_id=None,
        identity=requirement_identity,
    )
    now = datetime.now(UTC)
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-requirement-runtime",
            project_id="project-1",
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=requirement_identity,
            created_at=now,
            updated_at=now,
        )
    )
    calls.clear()
    requirement_result = _application_runtime(
        request=requirement_request,
        route=ApplicationRoute.GREENFIELD_DISCOVERY,
        gateway=service.binding,
        model_policy=service.model_policy,
        context=None,
        model_invocation_service=invocation,
    ).answer(requirement_request)

    assert requirement_result.status.value == "COMPLETED"
    assert requirement_result.answer is not None
    assert requirement_result.answer.model_info.provider == "zhipu"
    assert requirement_result.answer.model_info.model_name == "glm-4-requirement"
    assert calls == [
        ("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-requirement")
    ]


def test_application_runtime_task_assignments_route_firmware_and_debug(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, management = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    active = management.model_profile(service.projection().model_profile_id or "")
    assert active is not None
    firmware_model = _model_variant(
        active, "model-zhipu-firmware-runtime", "glm-4-firmware"
    ).model_copy(
        update={
            "capabilities": (
                ModelCapability.TEXT,
                ModelCapability.CODING,
                ModelCapability.TOOL_CALLING,
            ),
            "capability_sources": tuple(
                CapabilityEvidence(
                    capability=capability,
                    provenance=CapabilityProvenance.PROVIDER_METADATA,
                    status=CapabilityStatus.PASS,
                    provider_id="zhipu",
                    endpoint_id="endpoint-zhipu",
                    credential_ref="credential:zhipu",
                    model_profile_id="model-zhipu-firmware-runtime",
                    model_id="glm-4-firmware",
                    credential_revision=active.credential_revision,
                    endpoint_revision=active.endpoint_revision,
                )
                for capability in (
                    ModelCapability.TEXT,
                    ModelCapability.CODING,
                    ModelCapability.TOOL_CALLING,
                )
            ),
        }
    )
    debug_model = _model_variant(active, "model-zhipu-debug-runtime", "glm-4-debug").model_copy(
        update={
            "capabilities": (ModelCapability.TEXT,),
            "capability_sources": (
                CapabilityEvidence(
                    capability=ModelCapability.TEXT,
                    provenance=CapabilityProvenance.PROVIDER_METADATA,
                    status=CapabilityStatus.PASS,
                    provider_id="zhipu",
                    endpoint_id="endpoint-zhipu",
                    credential_ref="credential:zhipu",
                    model_profile_id="model-zhipu-debug-runtime",
                    model_id="glm-4-debug",
                    credential_revision=active.credential_revision,
                    endpoint_revision=active.endpoint_revision,
                ),
            ),
        }
    )
    management.models.upsert(firmware_model)
    management.models.upsert(debug_model)
    firmware_identity = BoundModelIdentity.from_profile(
        firmware_model, "credential:zhipu"
    )
    debug_identity = BoundModelIdentity.from_profile(debug_model, "credential:zhipu")
    for identity in (firmware_identity, debug_identity):
        management.create_allowed_model_policy(
            scope=PolicyScope.GLOBAL,
            project_id=None,
            stage_id=None,
            identity=identity,
        )
    now = datetime.now(UTC)
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-firmware-runtime",
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=firmware_identity,
            created_at=now,
            updated_at=now,
        )
    )
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-debug-runtime",
            project_id="project-1",
            stage_id=EngineeringStage.DEBUGGING,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=debug_identity,
            created_at=now,
            updated_at=now,
        )
    )
    invocation = _invocation_service(service, management)
    firmware_request = _application_request(
        "application-firmware", "Locate the I2C init"
    )
    firmware_agent = _SpecialistAgent(
        _SpecialistResult(
            (_specialist_evidence(ContextSourceType.FIRMWARE_SOURCE, "firmware-route"),)
        )
    )
    calls.clear()
    firmware_result = _application_runtime(
        request=firmware_request,
        route=ApplicationRoute.FIRMWARE,
        gateway=service.binding,
        model_policy=service.model_policy,
        context=None,
        firmware_agent=firmware_agent,
        model_invocation_service=invocation,
    ).answer(firmware_request)
    assert firmware_result.status.value == "COMPLETED"
    assert firmware_result.answer is not None
    assert firmware_result.answer.model_info.model_name == "glm-4-firmware"

    debug_request = _application_request(
        "application-debug", "Analyze this hardfault log"
    )
    debug_agent = _SpecialistAgent(
        _SpecialistResult(
            (_specialist_evidence(ContextSourceType.DEBUG, "debug-route"),)
        )
    )
    calls.clear()
    debug_result = _application_runtime(
        request=debug_request,
        route=ApplicationRoute.DEBUG,
        gateway=service.binding,
        model_policy=service.model_policy,
        context=None,
        debug_agent=debug_agent,
        model_invocation_service=invocation,
    ).answer(debug_request)
    assert debug_result.status.value == "COMPLETED"
    assert debug_result.answer is not None
    assert debug_result.answer.model_info.model_name == "glm-4-debug"
    assert calls == [
        ("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-debug")
    ]


def test_custom_relay_persists_and_recovers_after_restart(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    service, credentials, management = _service(tmp_path, calls)
    service.save_configuration(
        provider="custom",
        model="relay-model",
        base_url="https://relay.example/v1",
    )
    service.save_credential(SecretStr("relay-secret"), provider="custom")
    available = service.test_connection()
    assert available.connection_status is ProviderConnectionStatus.AVAILABLE
    calls.clear()

    restarted_manager = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    assert restarted_manager.restore_from_store() is True
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(calls),
        model_gateway_management=restarted_manager,
    )
    projection = restarted.projection()
    assert projection.provider == "custom"
    assert projection.connection_status is ProviderConnectionStatus.AVAILABLE
    response = restarted.binding.generate(_request())
    assert response.provider == "custom"
    assert response.endpoint_id == "endpoint-custom"
    assert response.model_name == "relay-model"
    assert response.credential_ref == "credential:custom"
    assert calls == [("https://relay.example/v1/chat/completions", "relay-model")]
    assert "relay-secret" not in (tmp_path / "production.json").read_text(
        encoding="utf-8"
    )
    assert "relay-secret" not in json.dumps(
        restarted_manager.state().model_dump(mode="json")
    )
    assert (
        management.global_policy().active_model_profile_id
        == restarted_manager.global_policy().active_model_profile_id
    )


def test_zhipu_to_custom_relay_switch_uses_relay_endpoint_and_credential(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, _ = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )

    service.save_configuration(
        provider="custom",
        model="relay-model",
        base_url="https://relay-x.example/v1",
        endpoint_id="endpoint-relay-x",
    )
    service.save_credential(SecretStr("relay-secret"), provider="custom")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    calls.clear()

    response = service.binding.generate(_request())

    assert response.provider == "custom"
    assert response.endpoint_id == "endpoint-relay-x"
    assert response.model_name == "relay-model"
    assert response.credential_ref == "credential:custom"
    assert calls == [("https://relay-x.example/v1/chat/completions", "relay-model")]


def test_legacy_deepseek_projection_migrates_idempotently_into_canonical_catalog(
    tmp_path: Path,
) -> None:
    (tmp_path / "production.json").write_text(
        json.dumps({"chat_provider": "deepseek", "chat_model": "deepseek-chat"}),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []
    service, credentials, management = _service(tmp_path, calls)
    first_counts = (
        len(management.list_endpoints()),
        len(management.list_models()),
        len(management.list_credentials()),
    )
    assert service.projection().provider == "deepseek"
    service2 = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(calls),
        model_gateway_management=management,
    )
    assert service2.projection().provider == "deepseek"
    assert (
        len(management.list_endpoints()),
        len(management.list_models()),
        len(management.list_credentials()),
    ) == first_counts


def test_task_assignment_changes_the_production_request_model(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    service, _, management = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    active = management.model_profile(service.projection().model_profile_id or "")
    assert active is not None
    assigned = active.model_copy(
        update={
            "model_profile_id": "model-zhipu-requirement",
            "remote_model_id": "glm-4-air",
            "display_name": "glm-4-air",
            "capability_sources": (
                CapabilityEvidence(
                    capability=ModelCapability.TEXT,
                    provenance=CapabilityProvenance.PROVIDER_METADATA,
                    status=CapabilityStatus.PASS,
                    provider_id="zhipu",
                    endpoint_id="endpoint-zhipu",
                    credential_ref="credential:zhipu",
                    model_profile_id="model-zhipu-requirement",
                    model_id="glm-4-air",
                    credential_revision=active.credential_revision,
                    endpoint_revision=active.endpoint_revision,
                ),
            ),
            "verification_status": VerificationStatus.VERIFIED,
        }
    )
    management.models.upsert(assigned)
    assigned_identity = BoundModelIdentity.from_profile(
        assigned, "credential:zhipu"
    )
    management.create_allowed_model_policy(
        scope=PolicyScope.GLOBAL,
        project_id=None,
        stage_id=None,
        identity=assigned_identity,
    )
    now = datetime.now(UTC)
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-requirement-assignment",
            project_id="project-1",
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=assigned_identity,
            created_at=now,
            updated_at=now,
        )
    )
    calls.clear()

    invocation = _invocation_service(service, management)
    result = invocation.invoke(
        ModelInvocationRequest.from_model_request(
            _request(task_type=TaskType.REQUIREMENT_CONVERSATION.value),
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            task_type=TaskType.REQUIREMENT_CONVERSATION.value,
        )
    )

    assert result.response is not None
    assert result.response.provider == "zhipu"
    assert result.response.model_name == "glm-4-air"
    assert calls == [
        ("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-air")
    ]


def test_provider_or_model_change_stales_verification_and_invalidates_binding(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, credentials, _ = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    assert service.binding.is_configured

    changed = service.save_configuration(provider="zhipu", model="glm-4-plus")

    assert changed.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert changed.tested_configuration_fingerprint is None
    assert not service.binding.is_configured
    assert credentials.get("CHAT_PROVIDER_API_KEY:zhipu") == b"zhipu-secret"


def test_credential_and_endpoint_changes_stale_verification_and_binding(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, credentials, management = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )

    credential_changed = service.save_credential(
        SecretStr("zhipu-replacement"), provider="zhipu"
    )
    assert credential_changed.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert credential_changed.tested_configuration_fingerprint is None
    assert not service.binding.is_configured
    assert credentials.get("CHAT_PROVIDER_API_KEY:zhipu") == b"zhipu-replacement"
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )

    endpoint_changed = service.save_configuration(
        provider="zhipu",
        model="glm-4",
        base_url="https://relay.example/v1",
        endpoint_id="endpoint-zhipu-revision",
    )
    assert endpoint_changed.connection_status is ProviderConnectionStatus.UNVERIFIED
    assert endpoint_changed.tested_configuration_fingerprint is None
    assert endpoint_changed.endpoint_id == "endpoint-zhipu-revision"
    assert not service.binding.is_configured
    endpoint = management.endpoint_profile("endpoint-zhipu-revision")
    assert endpoint is not None
    assert endpoint.health_status in {"UNKNOWN", "STALE"}


def test_explicit_endpoint_switch_uses_the_selected_endpoint_for_execution(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, management = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )

    service.save_configuration(
        provider="zhipu",
        model="glm-4",
        base_url="https://relay.example/v1",
        endpoint_id="endpoint-zhipu-relay",
    )
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    calls.clear()

    response = service.binding.generate(_request())

    assert response.provider == "zhipu"
    assert response.endpoint_id == "endpoint-zhipu-relay"
    assert calls == [("https://relay.example/v1/chat/completions", "glm-4")]
    active = management.model_profile(service.projection().model_profile_id or "")
    assert active is not None
    assert active.endpoint_id == "endpoint-zhipu-relay"


def test_missing_active_provider_credential_fails_closed_without_deepseek_fallback(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, _ = _service(tmp_path, calls)
    projection = service.save_configuration(provider="zhipu", model="glm-4")

    assert projection.credential_status == "NOT_CONFIGURED"
    assert projection.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert not service.binding.is_configured
    with pytest.raises(ModelGatewayUnavailable):
        service.binding.generate(_request())
    assert calls == []


def test_credential_save_fails_closed_when_written_secret_cannot_be_read_back(
    tmp_path: Path,
) -> None:
    class WriteOnlyZhipuCredentialProvider(InMemoryCredentialProvider):
        def get(self, purpose: str) -> bytes | None:
            if purpose == provider_credential_purpose("zhipu"):
                return None
            return super().get(purpose)

    calls: list[tuple[str, str]] = []
    credentials = WriteOnlyZhipuCredentialProvider()
    service, _, _ = _service(tmp_path, calls, credentials=credentials)
    service.save_configuration(provider="zhipu", model="glm-4")

    with pytest.raises(CredentialUnavailable):
        service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    assert credentials.get(provider_credential_purpose("zhipu")) is None
    projection = service.projection()
    assert projection.credential_status == "NOT_CONFIGURED"
    assert projection.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert "zhipu-secret" not in (tmp_path / "production.json").read_text(
        encoding="utf-8"
    )


def test_restart_missing_zhipu_secret_downgrades_stale_metadata_without_http(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    service, _, _ = _service(tmp_path, calls)
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    class MissingZhipuCredentialProvider(InMemoryCredentialProvider):
        def get(self, purpose: str) -> bytes | None:
            if purpose == provider_credential_purpose("zhipu"):
                return None
            return super().get(purpose)

    restarted_management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    assert restarted_management.restore_from_store() is True
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=MissingZhipuCredentialProvider(),
        http_transport=_transport(calls),
        model_gateway_management=restarted_management,
    )

    projection = restarted.projection()
    assert projection.provider == "zhipu"
    assert projection.model == "glm-4.7-flash"
    assert projection.credential_status == "NOT_CONFIGURED"
    assert projection.connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert restarted.binding.is_configured is False
    assert restarted.test_connection().connection_status is ProviderConnectionStatus.NOT_CONFIGURED
    assert calls == []


def test_settings_http_save_get_and_test_connection_use_canonical_persisted_state(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    credentials = InMemoryCredentialProvider()
    release_runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            _env_file=None,
        ),
        credentials=credentials,
    )
    release_runtime.start()
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    for preset in provider_presets():
        management.add_provider(preset)
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(calls),
        model_gateway_management=management,
    )
    app = FastAPI()
    app.include_router(release_router)
    app.state.production_release_runtime = release_runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(
        release_runtime.sessions
    )
    app.state.provider_configuration_service = service

    with TestClient(app) as client:
        nonce = release_runtime.sessions.issue_bootstrap(
            requester="requester", tenant_id="tenant"
        )
        exchange = client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        )
        assert exchange.status_code == 200
        saved = client.put(
            "/api/v1/copilot/desktop/system/provider/configuration",
            json={"provider": "zhipu", "model": "glm-4"},
        )
        assert saved.status_code == 200
        assert saved.json()["provider"] == "zhipu"
        assert saved.json()["connection_status"] == "PROVIDER_NOT_CONFIGURED"
        credential = client.put(
            "/api/v1/copilot/desktop/system/provider/credential",
            json={"provider": "zhipu", "secret": "zhipu-secret"},
        )
        assert credential.status_code == 200
        tested = client.post("/api/v1/copilot/desktop/system/provider/test-connection")
        assert tested.status_code == 200
        assert tested.json()["connection_status"] == "AVAILABLE"

        restarted_manager = ModelGatewayManagementService(
            store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
        )
        assert restarted_manager.restore_from_store() is True
        app.state.provider_configuration_service = ProviderConfigurationService(
            configuration_path=tmp_path / "production.json",
            credentials=credentials,
            http_transport=_transport(calls),
            model_gateway_management=restarted_manager,
        )
        persisted = client.get("/api/v1/copilot/desktop/system/provider")

    assert persisted.status_code == 200
    assert persisted.json()["provider"] == "zhipu"
    assert persisted.json()["model"] == "glm-4"
    assert persisted.json()["credential_status"] == "CONFIGURED"


def test_canonical_zhipu_http_400_is_not_reported_as_network_unavailable(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append((str(request.url), str(payload["model"])))
        return httpx.Response(400, json={"error": "redacted"})

    service, _, _ = _service(
        tmp_path,
        calls,
        http_transport=httpx.MockTransport(handler),
    )
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.CONFIG_INVALID
    assert calls == [
        ("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4.7-flash")
    ]


def test_canonical_zhipu_http_429_is_reported_as_rate_limited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "test-connection.jsonl"
    monkeypatch.setenv(connection_diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(connection_diagnostic, "diagnostic_output_path", lambda: output)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "redacted"})

    service, _, _ = _service(
        tmp_path,
        [],
        http_transport=httpx.MockTransport(handler),
    )
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    with connection_diagnostic.request_scope("test-connection-" + "b" * 32):
        projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.RATE_LIMITED
    assert projection.connection == "failed"
    assert projection.model_available is True
    assert projection.verified_model == "glm-4.7-flash"
    assert projection.available_models == ("glm-4.7-flash",)
    failure = next(
        row
        for row in (json.loads(line) for line in output.read_text(encoding="utf-8").splitlines())
        if row["stage"] == "TEST_CONNECTION_PROVIDER_CALL_FAILURE"
    )
    assert failure["result"] == "RATE_LIMITED"
    assert failure["safe_http_status"] == 429
    assert failure["safe_error_class"] == "PROVIDER_RATE_LIMITED"


def test_canonical_zhipu_transport_failure_records_safe_chain_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "test-connection.jsonl"
    monkeypatch.setenv(connection_diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(connection_diagnostic, "diagnostic_output_path", lambda: output)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("redacted", request=request)

    service, _, _ = _service(
        tmp_path,
        [],
        http_transport=httpx.MockTransport(handler),
    )
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    with connection_diagnostic.request_scope("test-connection-" + "a" * 32):
        projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.NETWORK_UNAVAILABLE
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    failure = next(
        row
        for row in rows
        if row["stage"] == "TEST_CONNECTION_PROVIDER_CALL_FAILURE"
    )
    assert failure["provider"] == "zhipu"
    assert failure["endpoint_id"] == "endpoint-zhipu"
    assert failure["credential_ref"] == "credential:zhipu"
    assert failure["protocol_family"] == "OPENAI_CHAT"
    assert failure["request_url"] == (
        "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    )
    assert failure["proxy_mode"] == "DIRECT_NO_ENV_PROXY"
    assert failure["safe_error_class"] == "CONNECT_ERROR"
    assert failure["safe_transport_error_code"] == "CONNECT_ERROR"
    assert "zhipu-secret" not in output.read_text(encoding="utf-8")


def test_inflight_request_keeps_old_model_while_next_request_uses_saved_model(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    started = threading.Event()
    release = threading.Event()
    block_next = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append((str(request.url), str(payload["model"])))
        if block_next["value"]:
            block_next["value"] = False
            started.set()
            assert release.wait(timeout=5)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "offline answer"}}]},
        )

    service, credentials, management = _service(
        tmp_path,
        calls,
        http_transport=httpx.MockTransport(handler),
    )
    service.save_credential(SecretStr("deepseek-secret"))
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    block_next["value"] = True
    calls.clear()
    result: dict[str, object] = {}

    def run_first_request() -> None:
        result["response"] = service.binding.generate(
            _request(request_id="request-inflight")
        )

    worker = threading.Thread(target=run_first_request)
    worker.start()
    assert started.wait(timeout=5)
    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert (
        service.test_connection().connection_status
        is ProviderConnectionStatus.AVAILABLE
    )
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    first = result["response"]
    assert first.provider == "deepseek"
    assert first.model_name == "deepseek-chat"
    calls.clear()
    second = service.binding.generate(_request(request_id="request-next"))

    assert second.provider == "zhipu"
    assert second.model_name == "glm-4"
    assert calls == [("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4")]
    assert credentials.get("CHAT_PROVIDER_API_KEY:zhipu") == b"zhipu-secret"
    assert management.global_policy().active_model_profile_id is None
