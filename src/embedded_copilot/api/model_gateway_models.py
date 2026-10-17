"""Secret-free API projections for the canonical model gateway."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_serializer,
    model_validator,
)

from embedded_copilot.model_gateway.governance import (
    AllowedModelPolicy,
    BoundModelIdentity,
    EngineeringStage,
    EngineeringStageDefinition,
    PolicyScope,
    ProjectDefaultModelPolicy,
    SelectionMode,
    StageModelPolicy,
)
from embedded_copilot.model_gateway.invocation import ModelInvocationReceipt
from embedded_copilot.model_gateway.invocation.contracts import (
    ModelInvocationAttempt,
    TaskAuthorityReference,
)
from embedded_copilot.model_gateway.metrics import (
    AccountUsageSnapshot,
    ModelReliability,
    ModelTaskMetrics,
)
from embedded_copilot.model_gateway.persistence import IdentityIntegrityIssue
from embedded_copilot.model_gateway.profiles import (
    BillingClass,
    BillingProvenance,
    CapabilityEvidence,
    CredentialProfile,
    DiscoveryStatus,
    EndpointType,
    GlobalModelPolicy,
    IdentityStatus,
    ModelCapability,
    ModelCatalogSource,
    ModelProfile,
    PaidModelPolicy,
    ProjectModelPolicy,
    ProviderConnectionStatus,
    ProviderProfile,
    QualityTier,
    RecordProvenance,
    RoutingStrategy,
    TaskAssignment,
    TaskProfile,
    TaskType,
    VerificationStatus,
)
from embedded_copilot.model_gateway.usage import ModelUsageAggregate


class GatewayDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class InvocationSelectedModelView(GatewayDTO):
    provider_id: str
    endpoint_id: str
    model_profile_id: str
    model_id: str


class InvocationUsageView(GatewayDTO):
    provider_reported_cost: float | None = Field(default=None, exclude_if=lambda v: v is None)
    estimated_cost: float | None = Field(default=None, exclude_if=lambda v: v is None)
    currency: str | None = Field(default=None, exclude_if=lambda v: v is None)
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    usage_source: str


_BUNDLE_PROJECTION_NAMES = ("budget_phase", "counter_admission_fingerprint", "counter_observation_fingerprint", "counter_status", "counter_transport_send_count", "counter_response_observed", "generation_transport_send_count", "total_external_request_count", "counter_http_status", "counter_input_tokens")


class _BundleRoleView(GatewayDTO):
    budget_phase: str | None = None
    counter_admission_fingerprint: str | None = None
    counter_observation_fingerprint: str | None = None
    counter_status: str | None = None
    counter_transport_send_count: int | None = None
    counter_response_observed: bool = False
    generation_transport_send_count: int | None = None
    total_external_request_count: int | None = None
    counter_http_status: int | None = None
    counter_input_tokens: int | None = None

    @model_serializer(mode="wrap")
    def _legacy_roles(self, handler):
        value = handler(self)
        if self.budget_phase is None:
            for name in _BUNDLE_PROJECTION_NAMES:
                value.pop(name, None)
        return value


class InvocationAttemptView(_BundleRoleView):
    budget_envelope_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    budget_evidence_fingerprint: str | None = Field(default=None, exclude_if=lambda v: v is None)
    budget_reservation_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    request_id: str
    project_id: str
    stage_id: str
    policy_source: str
    connection_id: str
    status: str
    invocation_mode: str
    authority_reference: TaskAuthorityReference | None
    confirmed_provider_request_count: int | None
    gateway_call_count: int | None
    gateway_response_observed: bool | None
    transport_send_count: int | None
    receipt_fingerprint: str | None

    @classmethod
    def from_domain(cls, value: ModelInvocationAttempt) -> InvocationAttemptView:
        return cls(
            **{name: getattr(value, name) for name in _BUNDLE_PROJECTION_NAMES},
            request_id=value.request_id,
            project_id=value.project_id,
            stage_id=value.snapshot.stage_id.value,
            policy_source=value.snapshot.policy_source,
            connection_id=value.snapshot.connection_id,
            status=value.status.value,
            invocation_mode=value.invocation_mode.value if value.invocation_mode is not None else "LEGACY_UNBOUND",
            authority_reference=value.authority_reference,
            confirmed_provider_request_count=value.confirmed_provider_request_count,
            gateway_call_count=value.gateway_call_count,
            gateway_response_observed=value.gateway_response_observed if value.attempt_schema_version >= 2 else None,
            budget_envelope_id=value.budget_envelope_id,
            budget_evidence_fingerprint=value.budget_evidence_fingerprint,
            budget_reservation_id=value.budget_reservation_id,
            transport_send_count=value.generation_transport_send_count if value.attempt_schema_version == 4 else value.transport_send_count,
            receipt_fingerprint=value.receipt_fingerprint,
        )


class InvocationReceiptView(_BundleRoleView):
    budget_envelope_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    budget_evidence_fingerprint: str | None = Field(default=None, exclude_if=lambda v: v is None)
    budget_reservation_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    budget_reconciliation_state: str | None = Field(default=None, exclude_if=lambda v: v is None)
    request_id: str
    trace_id: str
    project_id: str
    stage_id: str
    invocation_mode: str
    authority_reference: TaskAuthorityReference | None
    policy_source: str
    selected_model: InvocationSelectedModelView | None
    connection_id: str | None
    usage: InvocationUsageView | None
    gateway_call_count: int | None
    gateway_response_observed: bool | None
    transport_send_count: int | None
    result: str
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    fingerprint: str

    @classmethod
    def from_domain(cls, value: ModelInvocationReceipt) -> InvocationReceiptView:
        selected = value.selected_model
        return cls(
            **{name: getattr(value, name) for name in _BUNDLE_PROJECTION_NAMES},
            request_id=value.request_id,
            trace_id=value.trace_context.trace_id,
            project_id=value.project_id,
            stage_id=value.stage_id.value,
            invocation_mode=value.invocation_mode.value if value.invocation_mode is not None else "LEGACY_UNBOUND",
            authority_reference=value.authority_reference,
            policy_source=value.policy_source,
            selected_model=(
                InvocationSelectedModelView(
                    provider_id=selected.provider_id,
                    endpoint_id=selected.endpoint_id,
                    model_profile_id=selected.model_profile_id,
                    model_id=selected.model_id,
                ) if selected is not None else None
            ),
            connection_id=value.connection_id,
            usage=(InvocationUsageView(
                input_tokens=value.usage.input_tokens,
                output_tokens=value.usage.output_tokens,
                total_tokens=value.usage.total_tokens,
                cached_input_tokens=value.usage.cached_input_tokens,
                cache_write_tokens=value.usage.cache_write_tokens,
                reasoning_tokens=value.usage.reasoning_tokens,
                usage_source=value.usage.usage_source,
                provider_reported_cost=value.usage.provider_reported_cost if value.schema_version >= 6 else None,
                estimated_cost=value.usage.estimated_cost if value.schema_version >= 6 else None,
                currency=value.usage.currency if value.schema_version >= 6 else None,
            ) if value.usage is not None else None),
            gateway_call_count=value.gateway_call_count,
            gateway_response_observed=value.gateway_response_observed if value.schema_version >= 5 else None,
            budget_envelope_id=value.budget_envelope_id,
            budget_evidence_fingerprint=value.budget_evidence_fingerprint,
            budget_reservation_id=value.budget_reservation_id,
            budget_reconciliation_state=value.budget_reconciliation_state,
            transport_send_count=value.transport_send_count,
            result=value.result.value,
            error_code=value.error_code,
            started_at=value.started_at,
            completed_at=value.completed_at,
            fingerprint=value.fingerprint,
        )


class InvocationReceiptPageView(GatewayDTO):
    items: tuple[InvocationReceiptView, ...]
    total: int
    limit: int
    offset: int


class ProviderView(GatewayDTO):
    provider_id: str
    display_name: str
    provider_kind: str
    supported_protocols: tuple[str, ...]
    preset_status: str
    enabled: bool

    @classmethod
    def from_profile(cls, value: ProviderProfile) -> ProviderView:
        return cls(
            provider_id=value.provider_id,
            display_name=value.display_name,
            provider_kind=value.provider_kind.value,
            supported_protocols=tuple(str(item.value if hasattr(item, "value") else item) for item in value.supported_protocols),
            preset_status=value.preset_status,
            enabled=value.enabled,
        )


class ProviderTemplateView(GatewayDTO):
    """A provider template used only by the add-connection flow."""

    provider_id: str
    display_name: str
    provider_kind: str
    supported_protocols: tuple[str, ...]
    preset_status: str
    default_endpoint_template: str | None
    model_discovery_mode: str
    enabled: bool

    @classmethod
    def from_profile(cls, value: ProviderProfile) -> ProviderTemplateView:
        return cls(
            provider_id=value.provider_id,
            display_name=value.display_name,
            provider_kind=value.provider_kind.value,
            supported_protocols=tuple(str(item.value if hasattr(item, "value") else item) for item in value.supported_protocols),
            preset_status=value.preset_status,
            default_endpoint_template=value.default_endpoint_template,
            model_discovery_mode=value.model_discovery_mode.value,
            enabled=value.enabled,
        )


class EndpointView(GatewayDTO):
    endpoint_id: str
    provider_id: str
    display_name: str
    endpoint_type: EndpointType
    protocol_family: str
    base_url: str
    credential_configured: bool
    enabled: bool
    health_status: str


class CredentialView(GatewayDTO):
    """A credential projection that exposes configuration state, never its ref."""

    credential_id: str
    provider_id: str
    display_name: str
    auth_type: str
    enabled: bool
    configured: bool = True

    @classmethod
    def from_profile(cls, value: CredentialProfile) -> CredentialView:
        return cls(
            credential_id=value.credential_id,
            provider_id=value.provider_id,
            display_name=value.display_name,
            auth_type=value.auth_type,
            enabled=value.enabled,
        )


class CredentialReferenceInput(GatewayDTO):
    credential_id: str
    provider_id: str
    display_name: str
    secret_ref: str
    auth_type: str = "API_KEY"
    enabled: bool = True


class ModelView(GatewayDTO):
    model_profile_id: str
    provider_id: str
    endpoint_id: str
    remote_model_id: str
    display_name: str
    advertised_family: str
    capabilities: tuple[ModelCapability, ...]
    quality_tier: QualityTier
    billing_class: BillingClass
    billing_provenance: str = "UNKNOWN"
    billing_source: str | None = None
    catalog_source: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    lifecycle_status: str = "ACTIVE"
    deprecated: bool = False
    metadata_updated_at: datetime | None = None
    metadata_source: str | None = None
    enabled: bool
    verification_status: str

    @classmethod
    def from_profile(cls, value: ModelProfile) -> ModelView:
        return cls(
            model_profile_id=value.model_profile_id,
            provider_id=value.provider_id,
            endpoint_id=value.endpoint_id,
            remote_model_id=value.remote_model_id,
            display_name=value.display_name,
            advertised_family=value.advertised_family,
            capabilities=value.capabilities,
            quality_tier=value.quality_tier,
            billing_class=value.billing_class,
            billing_provenance=value.billing_provenance.value,
            billing_source=value.billing_source,
            catalog_source=value.catalog_source,
            lifecycle_status=value.lifecycle_status,
            deprecated=value.deprecated,
            metadata_updated_at=value.metadata_updated_at,
            metadata_source=value.metadata_source,
            enabled=value.enabled,
            verification_status=value.verification_status.value,
        )


_CONNECTION_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
_CONNECTION_REF = re.compile(r"^(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _validate_connection_identifier(value: str, *, field: str, maximum: int = 128) -> str:
    checked = value.strip().lower()
    if len(checked) > maximum or not _CONNECTION_IDENTIFIER.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def _validate_connection_text(value: str, *, field: str, maximum: int = 128) -> str:
    checked = value.strip()
    if not checked or len(checked) > maximum or any(character in "\r\n" or ord(character) < 32 for character in checked):
        raise ValueError(f"{field} is invalid")
    return checked


def _validate_connection_url(value: str) -> str:
    checked = value.strip().rstrip("/")
    parsed = urlsplit(checked)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("endpoint_url is invalid")
    return checked


class ProviderConnectionCreateInput(GatewayDTO):
    """Canonical connection creation input; it never accepts a credential secret."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    provider_id: str = Field(min_length=1, max_length=128)
    connection_name: str = Field(min_length=1, max_length=128)
    endpoint_url: str = Field(
        min_length=1,
        max_length=512,
        validation_alias=AliasChoices("endpoint_url", "base_url"),
    )
    endpoint_id: str | None = Field(default=None, min_length=1, max_length=128)
    protocol_family: str | None = Field(default=None, min_length=1, max_length=64)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        return _validate_connection_identifier(value, field="provider_id")

    @field_validator("connection_name")
    @classmethod
    def _connection_name(cls, value: str) -> str:
        return _validate_connection_text(value, field="connection_name")

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint_url(cls, value: str) -> str:
        return _validate_connection_url(value)

    @field_validator("endpoint_id")
    @classmethod
    def _endpoint_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_connection_identifier(value, field="endpoint_id")

    @field_validator("protocol_family")
    @classmethod
    def _protocol_family(cls, value: str | None) -> str | None:
        return None if value is None else _validate_connection_text(value, field="protocol_family", maximum=64).upper()

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        checked = value.strip()
        if not _CONNECTION_REF.fullmatch(checked):
            raise ValueError("credential_ref is invalid")
        return checked


class ProviderConnectionPatchInput(GatewayDTO):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    connection_name: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        validation_alias=AliasChoices("endpoint_url", "base_url"),
    )
    protocol_family: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None

    @field_validator("connection_name")
    @classmethod
    def _connection_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_connection_text(value, field="connection_name")

    @field_validator("endpoint_url")
    @classmethod
    def _endpoint_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_connection_url(value)

    @field_validator("protocol_family")
    @classmethod
    def _protocol_family(cls, value: str | None) -> str | None:
        return None if value is None else _validate_connection_text(value, field="protocol_family", maximum=64).upper()

    @model_validator(mode="after")
    def _has_change(self) -> ProviderConnectionPatchInput:
        if all(value is None for value in (self.connection_name, self.endpoint_url, self.protocol_family, self.enabled)):
            raise ValueError("connection patch is empty")
        return self


class ProviderConnectionCredentialInput(GatewayDTO):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    # Length and content are validated inside the credential service so a
    # FastAPI validation error cannot serialize the submitted secret.
    secret: SecretStr = Field(json_schema_extra={"minLength": 1, "maxLength": 4096})


class ProviderConnectionModelTestInput(GatewayDTO):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    model_profile_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=128)

    @field_validator("model_profile_id", "model_id")
    @classmethod
    def _model_identity(cls, value: str, info) -> str:
        return _validate_connection_text(value, field=info.field_name, maximum=128)


class ProviderConnectionVerificationSummaryView(GatewayDTO):
    total: int = Field(ge=0)
    current: int = Field(ge=0)
    historical: int = Field(ge=0)
    ambiguous_legacy: int = Field(ge=0)
    latest_status: str | None
    latest_at: datetime | None


class ModelRuntimeIdentityView(GatewayDTO):
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str


class ModelRuntimeHealthView(GatewayDTO):
    identity: ModelRuntimeIdentityView
    endpoint_status: str
    last_success_at: datetime | None
    last_failure_at: datetime | None
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: float | None = Field(default=None, ge=0.0)
    quota_status: str
    observed_at: datetime | None
    sample_count: int = Field(ge=0)
    evidence_freshness: str = "UNKNOWN"


class ProviderConnectionUsageView(GatewayDTO):
    status: str
    current_balance: float | None
    last_known_balance: float | None
    queried_at: datetime | None


class ProviderConnectionView(GatewayDTO):
    connection_id: str
    connection_name: str
    legacy_source: str | None = None
    provider_id: str
    provider_display_name: str
    status: ProviderConnectionStatus
    health_status: str
    block_reason: str | None
    endpoint_status: str
    endpoint_observed_at: datetime | None
    endpoint_freshness: str
    credential_status: str
    credential_freshness: str
    discovery_observed_at: datetime | None
    discovery_freshness: str
    endpoint_id: str
    endpoint_url: str
    protocol_family: str
    credential_ref: str | None
    credential_configured: bool
    credential_revision: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    discovery_state: str
    discovery_required: bool
    current_model_count: int = Field(ge=0)
    verification: ProviderConnectionVerificationSummaryView
    usage: ProviderConnectionUsageView


class ProviderConnectionTestResultView(GatewayDTO):
    status: str
    reason_code: str | None
    tested_at: datetime | None


class ProviderConnectionTestView(ProviderConnectionView):
    test_result: ProviderConnectionTestResultView


class ProviderConnectionCredentialView(GatewayDTO):
    connection_id: str
    credential_ref: str | None
    configured: bool
    credential_revision: str | None


class ProviderConnectionRefreshView(GatewayDTO):
    connection_id: str
    provider_id: str
    endpoint_id: str
    status: str
    raw_model_count: int | None = Field(default=None, ge=0)
    normalized_model_count: int = Field(ge=0)
    models: tuple[ModelView, ...]
    diagnostic_summary: dict[str, object] | None = None
    error_code: str | None = None


class ProviderConnectionDeleteView(GatewayDTO):
    connection_id: str
    status: ProviderConnectionStatus


class GlobalPolicyInput(GatewayDTO):
    default_strategy: RoutingStrategy = RoutingStrategy.BEST_VALUE
    paid_model_policy: str = "ASK"
    preferred_providers: tuple[str, ...] = ()
    disabled_models: tuple[str, ...] = ()
    disabled_endpoints: tuple[str, ...] = ()
    fallback_allowed: bool = True
    minimum_sample_size: int = Field(default=5, ge=1, le=1000)

    def to_domain(self) -> GlobalModelPolicy:
        return GlobalModelPolicy(
            default_strategy=self.default_strategy,
            paid_model_policy=PaidModelPolicy(self.paid_model_policy),
            preferred_providers=self.preferred_providers,
            disabled_models=self.disabled_models,
            disabled_endpoints=self.disabled_endpoints,
            fallback_allowed=self.fallback_allowed,
            minimum_sample_size=self.minimum_sample_size,
        )


class ProjectPolicyInput(GatewayDTO):
    project_id: str
    default_strategy: RoutingStrategy | None = None
    task_assignments: tuple[TaskAssignment, ...] = ()
    disabled_models: tuple[str, ...] = ()
    disabled_endpoints: tuple[str, ...] = ()

    def to_domain(self) -> ProjectModelPolicy:
        return ProjectModelPolicy(
            project_id=self.project_id,
            default_strategy=self.default_strategy,
            task_assignments=self.task_assignments,
            disabled_models=self.disabled_models,
            disabled_endpoints=self.disabled_endpoints,
        )


class TaskAssignmentInput(GatewayDTO):
    task_type: TaskType
    model_profile_id: str
    enabled: bool = True

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value: object) -> TaskType:
        return value if isinstance(value, TaskType) else TaskType(value)

    def to_domain(self) -> TaskAssignment:
        return TaskAssignment(
            task_type=self.task_type,
            model_profile_id=self.model_profile_id,
            enabled=self.enabled,
        )


class TaskProfileView(GatewayDTO):
    task_type: TaskType
    required_capabilities: tuple[ModelCapability, ...]
    minimum_quality_tier: QualityTier
    quality_priority: float
    cost_sensitivity: float
    latency_sensitivity: float
    reliability_requirement: float
    fallback_allowed: bool
    default_routing_strategy: RoutingStrategy

    @classmethod
    def from_profile(cls, value: TaskProfile) -> TaskProfileView:
        return cls(**value.model_dump(mode="python"))


class RequestOverrideInput(GatewayDTO):
    request_id: str
    model_profile_id: str


class UsageView(GatewayDTO):
    model_profile_id: str
    provider_id: str
    endpoint_id: str
    project_id: str | None
    task_type: TaskType | None
    request_count: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    provider_reported_cost: float | None
    estimated_cost: float | None
    cache_hit_rate: float | None
    currency: str | None
    billing_class: str | None
    usage_source: str

    @classmethod
    def from_aggregate(cls, value: ModelUsageAggregate) -> UsageView:
        return cls(
            model_profile_id=value.model_profile_id,
            provider_id=value.provider_id,
            endpoint_id=value.endpoint_id,
            project_id=value.project_id,
            task_type=value.task_type,
            request_count=value.request_count,
            input_tokens=value.input_tokens,
            output_tokens=value.output_tokens,
            cached_input_tokens=value.cached_input_tokens,
            cache_write_tokens=value.cache_write_tokens,
            reasoning_tokens=value.reasoning_tokens,
            total_tokens=value.total_tokens,
            provider_reported_cost=value.provider_reported_cost,
            estimated_cost=value.estimated_cost,
            cache_hit_rate=value.cache_hit_rate,
            currency=value.currency,
            billing_class=value.billing_class,
            usage_source=value.usage_source,
        )


class AccountUsageView(GatewayDTO):
    connection_id: str | None
    provider_id: str
    endpoint_id: str
    credential_ref: str | None
    billing_class: BillingClass
    current_balance: float | None
    balance_status: str
    balance_source: str | None
    queried_at: str | None
    last_known_balance: float | None
    last_success_at: str | None
    # Legacy aliases remain response-only for existing desktop clients.
    balance: float | None
    currency: str | None
    usage_today: int | None
    usage_month: int | None
    budget_remaining: float | None
    quota_remaining: int | None
    reset_at: str | None
    status: str
    source: str | None

    @classmethod
    def from_snapshot(cls, value: AccountUsageSnapshot) -> AccountUsageView:
        return cls(
            connection_id=value.connection_id,
            provider_id=value.provider_id,
            endpoint_id=value.endpoint_id,
            credential_ref=value.credential_ref,
            billing_class=value.billing_class,
            current_balance=value.current_balance,
            balance_status=value.status.value,
            balance_source=value.source,
            queried_at=(
                value.updated_at.isoformat()
                if value.status.value in {"AVAILABLE", "STALE", "AUTH_FAILED"}
                else None
            ),
            last_known_balance=value.last_known_balance,
            last_success_at=value.last_success_at.isoformat() if value.last_success_at else None,
            balance=value.current_balance,
            currency=value.currency,
            usage_today=value.usage_today,
            usage_month=value.usage_month,
            budget_remaining=value.budget_remaining,
            quota_remaining=value.quota_remaining,
            reset_at=value.reset_at.isoformat() if value.reset_at else None,
            status=value.status.value,
            source=value.source,
        )


class ModelMetricsView(GatewayDTO):
    model_profile_id: str
    request_attempts: int
    request_success_rate: float | None
    primary_route_success_rate: float | None
    retry_rate: float | None
    fallback_rate: float | None
    task_acceptance_rate: float | None
    task_sample_status: str

    @classmethod
    def from_metrics(cls, reliability: ModelReliability | None, task: ModelTaskMetrics | None, model_profile_id: str) -> ModelMetricsView:
        return cls(
            model_profile_id=model_profile_id,
            request_attempts=reliability.request_attempts if reliability else 0,
            request_success_rate=reliability.request_success_rate if reliability else None,
            primary_route_success_rate=reliability.primary_route_success_rate if reliability else None,
            retry_rate=reliability.retry_rate if reliability else None,
            fallback_rate=reliability.fallback_rate if reliability else None,
            task_acceptance_rate=task.task_acceptance_rate if task else None,
            task_sample_status=task.sample_status if task else "INSUFFICIENT_DATA",
        )


class BoundModelInput(GatewayDTO):
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str

    def to_domain(self) -> BoundModelIdentity:
        return BoundModelIdentity(**self.model_dump())


class EngineeringStageView(GatewayDTO):
    stage_id: EngineeringStage
    display_name: str
    required_capabilities: tuple[ModelCapability, ...]

    @classmethod
    def from_domain(cls, value: EngineeringStageDefinition) -> EngineeringStageView:
        return cls(**value.model_dump(mode="python"))


class AllowedModelPolicyInput(GatewayDTO):
    scope: PolicyScope
    project_id: str | None = None
    stage_id: EngineeringStage | None = None
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1000)

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, value: object) -> PolicyScope:
        return value if isinstance(value, PolicyScope) else PolicyScope(value)

    @field_validator("stage_id", mode="before")
    @classmethod
    def _stage(cls, value: object) -> EngineeringStage | None:
        return None if value is None else value if isinstance(value, EngineeringStage) else EngineeringStage(value)

    def identity(self) -> BoundModelIdentity:
        return BoundModelIdentity(
            provider_id=self.provider_id,
            endpoint_id=self.endpoint_id,
            credential_ref=self.credential_ref,
            model_profile_id=self.model_profile_id,
            model_id=self.model_id,
        )


class AllowedModelPolicyView(GatewayDTO):
    id: str
    scope: PolicyScope
    project_id: str | None
    stage_id: EngineeringStage | None
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str
    enabled: bool
    priority: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: AllowedModelPolicy) -> AllowedModelPolicyView:
        return cls(**value.model_dump(mode="python"))


class GovernanceModelView(GatewayDTO):
    model_profile_id: str
    provider_id: str
    provider_display_name: str
    connection_id: str | None
    connection_name: str | None
    endpoint_id: str
    protocol_family: str
    credential_ref: str
    model_id: str
    display_name: str
    billing_class: BillingClass
    billing_provenance: BillingProvenance
    billing_source: str | None
    availability: str
    availability_status: str
    availability_provenance: str
    identity_status: IdentityStatus
    identity_status_reason: str | None
    record_provenance: RecordProvenance
    verification_status: VerificationStatus
    discovery_status: DiscoveryStatus
    metadata_source: str | None
    metadata_updated_at: str | None
    discovered_at: str | None
    verified_at: str | None
    capabilities: tuple[ModelCapability, ...]
    capability_provenance: tuple[CapabilityEvidence, ...]
    can_enable: bool
    enable_block_reason: str | None
    allowed: bool
    policy_status: str
    policy_block_reason: str | None
    credential_revision: str | None = None
    endpoint_revision: str | None = None
    policy_id: str | None = None
    policy_scope: PolicyScope | None = None
    policy_project_id: str | None = None
    policy_stage_id: EngineeringStage | None = None
    capability_warning: str | None = None


class OfficialCatalogModelView(GatewayDTO):
    """Metadata-only model row; it has no current endpoint binding."""

    provider_id: str
    model_id: str
    display_name: str
    billing_class: BillingClass
    billing_provenance: BillingProvenance
    billing_source: str | None
    capabilities: tuple[ModelCapability, ...]
    metadata_source: str | None
    metadata_updated_at: str | None
    deprecated: bool
    record_type: str = "OFFICIAL_CATALOG"
    availability_status: str = "UNVERIFIED"
    verification_status: str = "UNVERIFIED"
    can_enable: bool = False
    enable_block_reason: str = "OFFICIAL_CATALOG_NOT_ENDPOINT_DISCOVERY"


class IdentityIntegrityIssueView(GatewayDTO):
    record_type: str
    record_id: str
    provider_id: str | None
    endpoint_id: str | None
    credential_ref: str | None
    model_profile_id: str | None
    model_id: str | None
    identity_status: IdentityStatus
    reason: str
    detected_at: datetime

    @classmethod
    def from_domain(cls, value: IdentityIntegrityIssue) -> IdentityIntegrityIssueView:
        return cls(**value.model_dump(mode="python"))


class StageModelPolicyInput(GatewayDTO):
    selection_mode: SelectionMode = Field(validation_alias=AliasChoices("selection_mode", "mode"))
    fixed_model: BoundModelInput | None = None
    candidate_models: tuple[BoundModelInput, ...] = ()
    candidate_group_id: str | None = None
    candidate_group_revision: int | None = Field(default=None, ge=1)

    @field_validator("selection_mode", mode="before")
    @classmethod
    def _mode(cls, value: object) -> SelectionMode:
        return value if isinstance(value, SelectionMode) else SelectionMode(value)

    @field_validator("candidate_models", mode="before")
    @classmethod
    def _candidate_tuple(cls, value: object) -> tuple[BoundModelInput, ...]:
        if isinstance(value, list):
            return tuple(value)
        return value

    def to_values(self) -> tuple[BoundModelIdentity | None, tuple[BoundModelIdentity, ...]]:
        return (
            self.fixed_model.to_domain() if self.fixed_model is not None else None,
            tuple(item.to_domain() for item in self.candidate_models),
        )


class StageModelPolicyView(GatewayDTO):
    id: str
    project_id: str
    stage_id: EngineeringStage
    selection_mode: SelectionMode
    fixed_model: BoundModelIdentity | None
    candidate_models: tuple[BoundModelIdentity, ...]
    candidate_group_id: str | None
    candidate_group_revision: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: StageModelPolicy) -> StageModelPolicyView:
        return cls(**value.model_dump(mode="python"))


class ProjectDefaultModelPolicyInput(GatewayDTO):
    selection_mode: SelectionMode
    fixed_model: BoundModelInput | None = None
    candidate_models: tuple[BoundModelInput, ...] = ()

    @field_validator("selection_mode", mode="before")
    @classmethod
    def _mode(cls, value: object) -> SelectionMode:
        return value if isinstance(value, SelectionMode) else SelectionMode(value)

    @field_validator("candidate_models", mode="before")
    @classmethod
    def _candidate_tuple(cls, value: object) -> tuple[BoundModelInput, ...]:
        if isinstance(value, list):
            return tuple(value)
        return value

    def to_values(self) -> tuple[BoundModelIdentity | None, tuple[BoundModelIdentity, ...]]:
        return (
            self.fixed_model.to_domain() if self.fixed_model is not None else None,
            tuple(item.to_domain() for item in self.candidate_models),
        )


class ProjectDefaultModelPolicyView(GatewayDTO):
    id: str
    project_id: str
    selection_mode: SelectionMode
    fixed_model: BoundModelIdentity | None
    candidate_models: tuple[BoundModelIdentity, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: ProjectDefaultModelPolicy) -> ProjectDefaultModelPolicyView:
        return cls(**value.model_dump(mode="python"))


__all__ = (
    "AccountUsageView",
    "AllowedModelPolicyInput",
    "AllowedModelPolicyView",
    "BoundModelInput",
    "CredentialReferenceInput",
    "CredentialView",
    "EndpointView",
    "EngineeringStageView",
    "GlobalPolicyInput",
    "GovernanceModelView",
    "IdentityIntegrityIssueView",
    "ModelMetricsView",
    "ModelRuntimeHealthView",
    "ModelRuntimeIdentityView",
    "ModelView",
    "ProjectDefaultModelPolicyInput",
    "ProjectDefaultModelPolicyView",
    "ProjectPolicyInput",
    "ProviderTemplateView",
    "ProviderView",
    "RequestOverrideInput",
    "StageModelPolicyInput",
    "StageModelPolicyView",
    "TaskAssignmentInput",
    "TaskProfileView",
    "UsageView",
)
