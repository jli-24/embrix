from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelPolicy,
    ModelRequest,
    ModelResponse,
    ProviderCapability,
)
from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.metrics import (
    AccountUsageSnapshot,
    ModelExecutionRecord,
    ModelReliability,
    ModelTaskMetrics,
    cache_hit_rate,
)
from embedded_copilot.model_gateway.persistence import (
    ModelGatewayState,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.profiles import (
    BillingClass,
    CapabilityEvidence,
    CapabilityProvenance,
    EndpointProfile,
    EndpointType,
    GlobalModelPolicy,
    ModelCapability,
    ModelProfile,
    PaidModelPolicy,
    PricingMetadata,
    ProjectModelPolicy,
    ProviderProfile,
    QualitySource,
    QualityTier,
    RoutingRequest,
    RoutingStrategy,
    TaskAssignment,
    TaskProfile,
    TaskType,
    UsageSnapshotStatus,
)
from embedded_copilot.model_gateway.router import (
    ModelRouter,
    ModelRoutingError,
)
from embedded_copilot.model_gateway.usage import ModelUsageAggregator


def _model(
    model_id: str,
    *,
    billing: BillingClass = BillingClass.FREE,
    quality: QualityTier = QualityTier.STANDARD,
    capabilities: tuple[ModelCapability, ...] = (ModelCapability.TEXT,),
    endpoint_id: str = "endpoint-official",
    reliability: float | None = None,
) -> ModelProfile:
    return ModelProfile(
        model_profile_id=model_id,
        provider_id="relay" if endpoint_id == "endpoint-relay" else "openai",
        endpoint_id=endpoint_id,
        remote_model_id=model_id,
        display_name=model_id,
        advertised_family="GPT" if model_id.startswith("gpt") else "custom",
        input_modalities=capabilities,
        output_modalities=(ModelCapability.TEXT,),
        capabilities=capabilities,
        capability_sources=tuple(
            CapabilityEvidence(
                capability=value,
                provenance=CapabilityProvenance.PROVIDER_METADATA,
                status="PASS",
                source="fixture",
                provider_id="relay" if endpoint_id == "endpoint-relay" else "openai",
                endpoint_id=endpoint_id,
                credential_ref="credential:relay" if endpoint_id == "endpoint-relay" else "credential:openai",
                model_profile_id=model_id,
                model_id=model_id,
            )
            for value in capabilities
        ),
        quality_tier=quality,
        quality_source=QualitySource.USER_DEFINED,
        billing_class=billing,
        pricing=(
            PricingMetadata(
                input_token_price=0.001,
                output_token_price=0.002,
                currency="USD",
                pricing_unit="1K_TOKENS",
                source="fixture",
            )
            if billing is BillingClass.METERED
            else None
        ),
        reliability_score=reliability,
    )


def _request(
    *,
    strategy: RoutingStrategy = RoutingStrategy.BEST_VALUE,
    minimum_quality: QualityTier = QualityTier.STANDARD,
    capabilities: tuple[ModelCapability, ...] = (ModelCapability.TEXT,),
    override: str | None = None,
    assignment: str | None = None,
    paid: PaidModelPolicy = PaidModelPolicy.ALLOW,
) -> RoutingRequest:
    task = TaskProfile(
        task_type=TaskType.FIRMWARE,
        required_capabilities=capabilities,
        minimum_quality_tier=minimum_quality,
        default_routing_strategy=strategy,
    )
    project = ProjectModelPolicy(
        project_id="project-a",
        default_strategy=strategy,
        task_assignments=(
            TaskAssignment(task_type=TaskType.FIRMWARE, model_profile_id=assignment),
        )
        if assignment
        else (),
    )
    return RoutingRequest(
        request_id="request-1",
        project_id="project-a",
        task_profile=task,
        request_model_override=override,
        project_policy=project,
        global_policy=GlobalModelPolicy(
            default_strategy=strategy,
            paid_model_policy=paid,
        ),
        available_credential_refs=("credential:openai",),
        endpoint_health=(
            ("endpoint-official", "AVAILABLE"),
            ("endpoint-relay", "AVAILABLE"),
        ),
    )


def test_profiles_keep_provider_endpoint_credential_and_model_distinct() -> None:
    provider = ProviderProfile(
        provider_id="openai",
        display_name="OpenAI",
        supported_protocols=("OPENAI_RESPONSES",),
    )
    endpoint = EndpointProfile(
        endpoint_id="endpoint-official",
        provider_id=provider.provider_id,
        display_name="OpenAI Official",
        endpoint_type=EndpointType.OFFICIAL,
        protocol_family="OPENAI_RESPONSES",
        base_url="https://api.openai.com/v1",
        credential_ref="credential:openai",
    )
    from embedded_copilot.model_gateway.profiles import CredentialProfile

    credential = CredentialProfile(
        credential_id="credential-openai",
        provider_id=provider.provider_id,
        display_name="Personal",
        secret_ref="credential:openai",
    )
    assert provider.provider_id == endpoint.provider_id == credential.provider_id
    assert endpoint.credential_ref == credential.secret_ref
    assert provider.model_dump(mode="json").get("api_key") is None


def test_relay_model_keeps_relay_identity_even_when_advertised_as_gpt() -> None:
    relay_model = _model("gpt-relay", endpoint_id="endpoint-relay")
    assert relay_model.provider_id == "relay"
    assert relay_model.advertised_family == "GPT"
    assert relay_model.remote_model_id == "gpt-relay"


def test_router_applies_capability_and_quality_gates_before_cost() -> None:
    models = (
        _model("cheap-text", billing=BillingClass.FREE),
        _model(
            "paid-vision",
            billing=BillingClass.METERED,
            quality=QualityTier.HIGH,
            capabilities=(ModelCapability.TEXT, ModelCapability.VISION),
        ),
    )
    result = ModelRouter().select(
        _request(
            capabilities=(ModelCapability.VISION,),
            minimum_quality=QualityTier.HIGH,
        ),
        models,
    )
    assert result.model_profile_id == "paid-vision"


def test_capability_without_evidence_is_unknown_and_not_eligible() -> None:
    model = ModelProfile(
        model_profile_id="unverified-vision",
        provider_id="openai",
        endpoint_id="endpoint-official",
        remote_model_id="vision-model",
        display_name="Unverified Vision",
        advertised_family="vision",
        capabilities=(ModelCapability.VISION,),
    )
    with pytest.raises(ModelRoutingError, match="CAPABILITY_UNAVAILABLE"):
        ModelRouter().select(
            _request(capabilities=(ModelCapability.VISION,)),
            (model,),
        )


def test_best_value_prefers_free_after_hard_gates() -> None:
    result = ModelRouter().select(
        _request(),
        (_model("paid", billing=BillingClass.METERED), _model("free")),
    )
    assert result.model_profile_id == "free"


def test_lowest_cost_does_not_treat_unknown_cost_as_free() -> None:
    unknown = _model("unknown", billing=BillingClass.UNKNOWN)
    free = _model("free", billing=BillingClass.FREE)
    result = ModelRouter().select(
        _request(strategy=RoutingStrategy.LOWEST_COST),
        (unknown, free),
    )
    assert result.model_profile_id == "free"


def test_strategy_precedence_is_override_then_assignment_then_project_then_global() -> None:
    result = ModelRouter().select(
        _request(override="override-model", assignment="assigned-model"),
        (
            _model("override-model"),
            _model("assigned-model"),
            _model("automatic-model"),
        ),
    )
    assert result.model_profile_id == "override-model"


def test_manual_vision_selection_rejects_text_only_model() -> None:
    with pytest.raises(ModelRoutingError, match="CAPABILITY_UNAVAILABLE"):
        ModelRouter().select(
            _request(
                strategy=RoutingStrategy.MANUAL,
                capabilities=(ModelCapability.VISION,),
                override="text-only",
            ),
            (_model("text-only"),),
        )


def test_router_requires_the_credential_bound_to_the_selected_endpoint() -> None:
    request = RoutingRequest(
        **{
            **_request().model_dump(mode="python"),
            "credential_ref_by_endpoint": (("endpoint-official", "credential:other"),),
        }
    )
    with pytest.raises(ModelRoutingError, match="CREDENTIAL_UNAVAILABLE"):
        ModelRouter().select(request, (_model("bound-model"),))


def test_paid_guard_asks_and_denies_without_hidden_execution() -> None:
    paid = _model("paid", billing=BillingClass.METERED)
    with pytest.raises(ModelRoutingError, match="PAID_MODEL_APPROVAL_REQUIRED"):
        ModelRouter().select(
            _request(paid=PaidModelPolicy.ASK),
            (paid,),
        )
    with pytest.raises(ModelRoutingError, match="PAID_MODEL_DENIED"):
        ModelRouter().select(
            _request(paid=PaidModelPolicy.DENY),
            (paid,),
        )


def test_usage_metrics_return_unknown_when_provider_does_not_report_cache() -> None:
    assert cache_hit_rate(1000, 700) == pytest.approx(0.7)
    assert cache_hit_rate(0, 0) is None
    assert cache_hit_rate(1000, None) is None


def test_metrics_distinguish_request_success_from_task_acceptance() -> None:
    reliability = ModelReliability(
        model_profile_id="model-a",
        request_attempts=1,
        request_successes=1,
    )
    task_metrics = ModelTaskMetrics(
        model_profile_id="model-a",
        task_type=TaskType.FIRMWARE,
        attempts=1,
        model_response_successes=1,
        task_accepted=0,
        task_failed=1,
    )
    assert reliability.request_success_rate == 1.0
    assert task_metrics.task_acceptance_rate == 0.0


def test_usage_snapshot_and_execution_record_are_secret_free() -> None:
    snapshot = AccountUsageSnapshot(
        provider_id="openai",
        endpoint_id="endpoint-official",
        credential_ref="credential:openai",
        billing_class=BillingClass.METERED,
        status=UsageSnapshotStatus.AVAILABLE,
    )
    record = ModelExecutionRecord(
        execution_id="execution-1",
        request_id="request-1",
        project_id="project-a",
        task_type=TaskType.FIRMWARE,
        selected_model="model-a",
        provider_id="openai",
        endpoint_id="endpoint-official",
        credential_ref="credential:openai",
        status="SUCCEEDED",
    )
    serialized = str(snapshot.model_dump(mode="json")) + str(record.model_dump(mode="json"))
    assert "api_key" not in serialized
    assert "Authorization" not in serialized
    assert "secret-value" not in serialized


def test_sqlite_state_store_round_trips_without_secrets(tmp_path: Path) -> None:
    state = ModelGatewayState(
        providers=(
            ProviderProfile(
                provider_id="openai",
                display_name="OpenAI",
                supported_protocols=("OPENAI_RESPONSES",),
            ),
        ),
        endpoints=(
            EndpointProfile(
                endpoint_id="endpoint-official",
                provider_id="openai",
                display_name="Official",
                endpoint_type=EndpointType.OFFICIAL,
                protocol_family="OPENAI_RESPONSES",
                base_url="https://api.openai.com/v1",
                credential_ref="credential:openai",
            ),
        ),
    )
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    store.save(state)
    restored = store.load()
    assert restored == state
    raw = (tmp_path / "gateway.sqlite3").read_bytes()
    assert b"secret-value" not in raw


def test_state_rejects_cross_identity_bindings() -> None:
    with pytest.raises(ValueError, match="endpoint identity"):
        ModelGatewayState(
            providers=(ProviderProfile(provider_id="openai", display_name="OpenAI"),),
            endpoints=(
                EndpointProfile(
                    endpoint_id="endpoint-relay",
                    provider_id="relay",
                    display_name="Relay",
                    endpoint_type=EndpointType.RELAY,
                    protocol_family="OPENAI_CHAT",
                    base_url="https://relay.example/v1",
                ),
            ),
        )


class _Provider:
    capabilities = (ProviderCapability.TEXT,)

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="routed",
            model_name=request.model_policy.model_name,
            provider=request.model_policy.primary_provider,
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        return self.generate(request)


def test_routed_execution_uses_the_existing_gateway_service() -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-a",
        task_description="firmware task",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    request = ModelRequest(
        request_id="request-1",
        task="firmware task",
        engineering_context=context,
        system_instruction="Use evidence only.",
        model_policy=ModelPolicy(primary_provider="openai", model_name="placeholder"),
    )
    response = ModelGatewayService(providers={"relay": _Provider()}).generate_routed(
        request,
        _request(override="selected-model"),
        (_model("selected-model", endpoint_id="endpoint-relay"),),
    )
    assert response.content == "routed"
    assert response.model_name == "selected-model"
    assert response.provider_id == "relay"
    assert response.endpoint_id == "endpoint-relay"
    assert response.routing_record is not None
    assert response.routing_record.endpoint_id == "endpoint-relay"


def test_usage_aggregator_tracks_tokens_routes_and_task_acceptance_separately() -> None:
    aggregator = ModelUsageAggregator()
    aggregator.record(
        ModelExecutionRecord(
            execution_id="execution-1",
            request_id="request-1",
            project_id="project-a",
            task_type=TaskType.FIRMWARE,
            selected_model="model-a",
            provider_id="openai",
            endpoint_id="endpoint-official",
            status="SUCCEEDED",
            usage={
                "input_tokens": 1000,
                "output_tokens": 100,
                "cached_input_tokens": 700,
                "total_tokens": 1100,
            },
        ),
        task_accepted=False,
    )
    usage = aggregator.usage()[0]
    reliability = aggregator.reliability()[0]
    task = aggregator.task_metrics()[0]
    assert usage.total_tokens == 1100
    assert usage.cache_hit_rate == pytest.approx(0.7)
    assert reliability.request_success_rate == 1.0
    assert reliability.primary_route_success_rate == 1.0
    assert task.task_acceptance_rate == 0.0


def test_management_estimates_cost_only_from_explicit_model_pricing() -> None:
    service = ModelGatewayManagementService()
    service.add_provider(ProviderProfile(provider_id="openai", display_name="OpenAI"))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-official", provider_id="openai", display_name="Official", endpoint_type=EndpointType.OFFICIAL, protocol_family="OPENAI_CHAT", base_url="https://api.openai.com/v1"))
    service.add_model(_model("priced-model", billing=BillingClass.METERED))
    service.record_execution(ModelExecutionRecord(execution_id="execution-priced", request_id="request-priced", project_id="project-a", task_type=TaskType.FIRMWARE, selected_model="priced-model", provider_id="openai", endpoint_id="endpoint-official", status="SUCCEEDED", usage={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100}))
    aggregate = service.usage()[0]
    assert aggregate.estimated_cost == pytest.approx(0.0012)
    assert aggregate.usage_source == "ESTIMATED"
