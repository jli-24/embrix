"""Typed, secret-free profiles used by the single Embrix model gateway.

The module deliberately models provider, endpoint, credential reference and
model as separate identities.  None of these contracts contains a secret or a
provider client; credentials are resolved only at the injected adapter boundary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import canonical_fingerprint, fingerprint, identifier, safe_text


class _Profile(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


class ProviderKind(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    XAI = "xai"
    DEEPSEEK = "deepseek"
    ZHIPU = "zhipu"
    ALIBABA = "alibaba"
    MOONSHOT = "moonshot"
    MINIMAX = "minimax"
    CUSTOM = "custom"
    LOCAL = "local"


class EndpointType(StrEnum):
    OFFICIAL = "OFFICIAL"
    RELAY = "RELAY"
    LOCAL = "LOCAL"
    CUSTOM = "CUSTOM"


class ProtocolFamily(StrEnum):
    OPENAI_RESPONSES = "OPENAI_RESPONSES"
    OPENAI_CHAT = "OPENAI_CHAT"
    ANTHROPIC_MESSAGES = "ANTHROPIC_MESSAGES"
    GOOGLE_GENAI = "GOOGLE_GENAI"
    LOCAL_OPENAI_COMPATIBLE = "LOCAL_OPENAI_COMPATIBLE"
    CUSTOM = "CUSTOM"


class ModelCapability(StrEnum):
    TEXT = "TEXT"
    CODING = "CODING"
    VISION = "VISION"
    AUDIO_INPUT = "AUDIO_INPUT"
    REASONING = "REASONING"
    TOOL_CALLING = "TOOL_CALLING"
    PARALLEL_TOOL_CALLING = "PARALLEL_TOOL_CALLING"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    STREAMING = "STREAMING"
    FILE_INPUT = "FILE_INPUT"
    EMBEDDING = "EMBEDDING"
    WEB_SEARCH = "WEB_SEARCH"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    COMPUTER_USE = "COMPUTER_USE"


class CapabilityProvenance(StrEnum):
    DECLARED = "DECLARED"
    PROVIDER_METADATA = "PROVIDER_METADATA"
    PROBED = "PROBED"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"


class QualityTier(StrEnum):
    STANDARD = "STANDARD"
    HIGH = "HIGH"
    PREMIUM = "PREMIUM"
    UNKNOWN = "UNKNOWN"


class QualitySource(StrEnum):
    USER_DEFINED = "USER_DEFINED"
    PROVIDER_PRESET = "PROVIDER_PRESET"
    VERIFIED_BENCHMARK = "VERIFIED_BENCHMARK"
    OBSERVED_TASK_METRICS = "OBSERVED_TASK_METRICS"
    UNKNOWN = "UNKNOWN"


class BillingClass(StrEnum):
    FREE = "FREE"
    PAID = "PAID"
    MIXED = "MIXED"
    # Legacy values remain readable for persisted projects and existing
    # routing policies.  New provider metadata should use PAID/MIXED.
    METERED = "METERED"
    SUBSCRIPTION = "SUBSCRIPTION"
    LOCAL = "LOCAL"
    UNKNOWN = "UNKNOWN"


class BillingProvenance(StrEnum):
    """Evidence level for a model's billing classification."""

    VERIFIED = "VERIFIED"
    PROBED = "PROBED"
    PROVIDER_METADATA = "PROVIDER_METADATA"
    DECLARED = "DECLARED"
    UNKNOWN = "UNKNOWN"


class PaidModelPolicy(StrEnum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class RoutingStrategy(StrEnum):
    BEST_VALUE = "BEST_VALUE"
    LOWEST_COST = "LOWEST_COST"
    QUALITY_FIRST = "QUALITY_FIRST"
    MANUAL = "MANUAL"


class TaskType(StrEnum):
    GENERAL_CONVERSATION = "GENERAL_CONVERSATION"
    REQUIREMENT_CONVERSATION = "REQUIREMENT_CONVERSATION"
    RESEARCH = "RESEARCH"
    ENGINEERING_DECISION = "ENGINEERING_DECISION"
    SYSTEM_ARCHITECTURE = "SYSTEM_ARCHITECTURE"
    FIRMWARE = "FIRMWARE"
    DEBUG = "DEBUG"
    VISION = "VISION"
    OCR = "OCR"
    REPORT = "REPORT"
    EMBEDDING = "EMBEDDING"


class DiscoveryMode(StrEnum):
    AUTO_DISCOVERY = "AUTO_DISCOVERY"
    MANUAL_MODEL_ID = "MANUAL_MODEL_ID"


class DiscoveryStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class ModelAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    DEPRECATED = "DEPRECATED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class ModelCatalogSource(StrEnum):
    PROVIDER_API = "PROVIDER_API"
    RUNTIME_VERIFICATION = "RUNTIME_VERIFICATION"
    PROVIDER_METADATA = "PROVIDER_METADATA"
    EMBRIX_OFFICIAL_CATALOG = "EMBRIX_OFFICIAL_CATALOG"
    CACHE = "CACHE"
    DECLARED = "DECLARED"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class RecordProvenance(StrEnum):
    """Source of a model binding record, separate from field-level evidence."""

    OFFICIAL_CATALOG = "OFFICIAL_CATALOG"
    ENDPOINT_DISCOVERY = "ENDPOINT_DISCOVERY"
    EXPLICIT_CONFIGURATION = "EXPLICIT_CONFIGURATION"
    PROVIDER_METADATA = "PROVIDER_METADATA"
    UNKNOWN = "UNKNOWN"


class IdentityStatus(StrEnum):
    """Integrity of a bound Provider/Endpoint/Credential/Model identity."""

    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class ProviderUsageCapability(StrEnum):
    USAGE_QUERY = "USAGE_QUERY"
    QUOTA_QUERY = "QUOTA_QUERY"
    BALANCE_QUERY = "BALANCE_QUERY"
    BILLING_QUERY = "BILLING_QUERY"


class UsageSnapshotStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class RouteStatus(StrEnum):
    SELECTED = "SELECTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED = "BLOCKED"


class ProviderConnectionStatus(StrEnum):
    """Lifecycle of a persisted provider connection aggregate."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    DELETED = "DELETED"
    QUARANTINED = "QUARANTINED"


# Keep the shorter names available to callers without introducing a second
# status enum.  The model-gateway contract remains the single source of truth.
ConnectionStatus = ProviderConnectionStatus
ConnectionLifecycleStatus = ProviderConnectionStatus


_REF = re.compile(r"^(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?:api[_ -]?key|authorization|bearer|password|secret|token)\s*[:=]",
    re.IGNORECASE,
)


def _ref(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value.strip()) or _SENSITIVE.search(value):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _identifier(value: object, *, field: str) -> str:
    return identifier(value, field=field)


def _connection_id(value: object) -> str:
    # The ``conn-<uuid>`` shape is used by the generator, while persisted or
    # imported opaque IDs remain readable as long as they are valid IDs.
    return _identifier(value, field="connection_id")


def _text(value: object, *, field: str, maximum: int = 256) -> str:
    return safe_text(value, field=field, maximum=maximum)


def _pairs(value: object, *, field: str) -> tuple[tuple[str, str], ...]:
    if isinstance(value, Mapping):
        value = tuple(sorted(value.items()))
    if isinstance(value, list):
        value = tuple(value)
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple or mapping")
    result: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, list):
            item = tuple(item)
        if type(item) is not tuple or len(item) != 2:
            raise TypeError(f"{field} contains an invalid entry")
        result.append((_text(item[0], field=f"{field}.key"), _text(item[1], field=f"{field}.value")))
    return tuple(result)


def _timestamp(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{field} is invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


class ProviderProfile(_Profile):
    provider_id: str
    display_name: str
    provider_kind: ProviderKind = ProviderKind.CUSTOM
    supported_protocols: tuple[ProtocolFamily | str, ...] = ()
    usage_capabilities: tuple[ProviderUsageCapability, ...] = ()
    preset_status: str = "PRESET_PARTIAL"
    default_endpoint_template: str | None = None
    model_discovery_mode: DiscoveryMode = DiscoveryMode.MANUAL_MODEL_ID
    enabled: bool = True
    metadata: tuple[tuple[str, str], ...] = ()

    @field_validator("provider_kind", mode="before")
    @classmethod
    def _provider_kind(cls, value: object) -> ProviderKind:
        return value if isinstance(value, ProviderKind) else ProviderKind(value)

    @field_validator("model_discovery_mode", mode="before")
    @classmethod
    def _discovery_mode(cls, value: object) -> DiscoveryMode:
        return value if isinstance(value, DiscoveryMode) else DiscoveryMode(value)

    @field_validator("usage_capabilities", mode="before")
    @classmethod
    def _usage_capabilities(cls, value: object) -> tuple[ProviderUsageCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("usage_capabilities must be a tuple")
        return tuple(item if isinstance(item, ProviderUsageCapability) else ProviderUsageCapability(item) for item in value)

    @field_validator("provider_id", mode="before")
    @classmethod
    def _provider_id(cls, value: object) -> str:
        return _identifier(value, field="provider_id")

    @field_validator("display_name", "preset_status", mode="before")
    @classmethod
    def _display_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("supported_protocols", mode="before")
    @classmethod
    def _protocols(cls, value: object) -> tuple[ProtocolFamily | str, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("supported_protocols must be a tuple")
        return tuple(ProtocolFamily(item) if isinstance(item, str) and item in ProtocolFamily._value2member_map_ else _text(item, field="protocol") for item in value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> tuple[tuple[str, str], ...]:
        return _pairs(value, field="metadata")

    @field_validator("default_endpoint_template", mode="before")
    @classmethod
    def _endpoint_template(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("default_endpoint_template is invalid")
        checked = value.strip()
        if not checked or len(checked) > 512 or "\n" in checked or "\r" in checked or _SENSITIVE.search(checked):
            raise ValueError("default_endpoint_template is invalid")
        return checked


class EndpointProfile(_Profile):
    endpoint_id: str
    provider_id: str
    display_name: str
    endpoint_type: EndpointType
    protocol_family: ProtocolFamily | str
    base_url: str
    credential_ref: str | None = None
    enabled: bool = True
    health_status: str = "UNKNOWN"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("endpoint_type", mode="before")
    @classmethod
    def _endpoint_type(cls, value: object) -> EndpointType:
        return value if isinstance(value, EndpointType) else EndpointType(value)

    @field_validator("endpoint_id", "provider_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("display_name", "health_status", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("protocol_family", mode="before")
    @classmethod
    def _protocol(cls, value: object) -> ProtocolFamily | str:
        if isinstance(value, ProtocolFamily):
            return value
        if isinstance(value, str) and value in ProtocolFamily._value2member_map_:
            return ProtocolFamily(value)
        return _text(value, field="protocol_family")

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str | None:
        return None if value is None else _ref(value, field="credential_ref")

    @field_validator("base_url", mode="before")
    @classmethod
    def _url(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("base_url is invalid")
        checked = value.strip().rstrip("/")
        if "://" not in checked or "@" in checked or "?" in checked or "#" in checked:
            raise ValueError("base_url is invalid")
        scheme, remainder = checked.split("://", 1)
        if scheme not in {"http", "https"} or not remainder:
            raise ValueError("base_url is invalid")
        authority = remainder.split("/", 1)[0]
        host = authority[1:].split("]", 1)[0] if authority.startswith("[") and "]" in authority else authority.split(":", 1)[0]
        if not host or (scheme == "http" and host.casefold() not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("base_url must use HTTPS outside loopback")
        return checked

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _dates(cls, value: object, info) -> datetime:
        return _timestamp(value, field=info.field_name)


class ProviderConnection(_Profile):
    """Stable, secret-free aggregate above one provider endpoint binding.

    ``connection_name`` and lifecycle timestamps are mutable presentation and
    audit data.  They are intentionally excluded from the fingerprint so a
    rename or tombstone update cannot create a new connection identity.
    """

    connection_id: str
    provider_id: str
    connection_name: str = "Default connection"
    status: ProviderConnectionStatus = ProviderConnectionStatus.ACTIVE
    endpoint_id: str
    credential_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None
    legacy_source: str | None = None
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("connection_id", mode="before")
    @classmethod
    def _connection_id_field(cls, value: object) -> str:
        return _connection_id(value)

    @field_validator("provider_id", "endpoint_id", mode="before")
    @classmethod
    def _identity_fields(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("connection_name", mode="before")
    @classmethod
    def _connection_name(cls, value: object) -> str:
        return _text(value, field="connection_name", maximum=128)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ProviderConnectionStatus:
        return value if isinstance(value, ProviderConnectionStatus) else ProviderConnectionStatus(value)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str | None:
        return None if value is None else _ref(value, field="credential_ref")

    @field_validator("legacy_source", mode="before")
    @classmethod
    def _legacy_source(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="legacy_source", maximum=512)

    @field_validator("created_at", "updated_at", "deleted_at", mode="before")
    @classmethod
    def _dates(cls, value: object, info) -> datetime | None:
        return None if value is None else _timestamp(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="fingerprint")

    @model_validator(mode="after")
    def _verify(self) -> ProviderConnection:
        if self.updated_at < self.created_at:
            raise ValueError("connection timestamps are invalid")
        if self.status is ProviderConnectionStatus.DELETED:
            if self.deleted_at is None:
                object.__setattr__(self, "deleted_at", self.updated_at)
        elif self.deleted_at is not None:
            raise ValueError("only deleted connection may have deleted_at")
        expected = canonical_fingerprint(
            self,
            exclude={"connection_name", "created_at", "updated_at", "deleted_at", "fingerprint"},
        )
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("provider connection fingerprint mismatch")
        return self


class CredentialProfile(_Profile):
    credential_id: str
    provider_id: str
    display_name: str
    secret_ref: str
    auth_type: str = "API_KEY"
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("credential_id", "provider_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("display_name", "auth_type", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("secret_ref", mode="before")
    @classmethod
    def _secret_ref(cls, value: object) -> str:
        return _ref(value, field="secret_ref")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _dates(cls, value: object, info) -> datetime:
        return _timestamp(value, field=info.field_name)


class CapabilityEvidence(_Profile):
    capability: ModelCapability
    provenance: CapabilityProvenance
    status: CapabilityStatus
    source: str | None = None
    observed_at: datetime | None = None
    provider_id: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    model_profile_id: str | None = None
    model_id: str | None = None
    credential_revision: str | None = None
    endpoint_revision: str | None = None

    @field_validator("capability", mode="before")
    @classmethod
    def _capability(cls, value: object) -> ModelCapability:
        return value if isinstance(value, ModelCapability) else ModelCapability(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value: object) -> CapabilityProvenance:
        return value if isinstance(value, CapabilityProvenance) else CapabilityProvenance(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> CapabilityStatus:
        return value if isinstance(value, CapabilityStatus) else CapabilityStatus(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="source", maximum=512)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _observed_at(cls, value: object) -> datetime | None:
        return None if value is None else _timestamp(value, field="observed_at")

    @field_validator("provider_id", "endpoint_id", "model_profile_id", "model_id", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential_ref(cls, value: object) -> str | None:
        return None if value is None else _ref(value, field="credential_ref")

    @field_validator("credential_revision", "endpoint_revision", mode="before")
    @classmethod
    def _revisions(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    def matches_model_identity(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        credential_ref: str,
        model_profile_id: str,
        model_id: str,
        credential_revision: str | None,
        endpoint_revision: str | None,
    ) -> bool:
        """Return whether this evidence is bound to the current model identity."""

        return (
            self.provider_id == provider_id
            and self.endpoint_id == endpoint_id
            and self.credential_ref == credential_ref
            and self.model_profile_id == model_profile_id
            and self.model_id == model_id
            and self.credential_revision == credential_revision
            and self.endpoint_revision == endpoint_revision
        )


class PricingMetadata(_Profile):
    input_token_price: float | None = Field(default=None, ge=0.0)
    output_token_price: float | None = Field(default=None, ge=0.0)
    cached_input_price: float | None = Field(default=None, ge=0.0)
    currency: str | None = None
    pricing_unit: str | None = None
    source: str | None = None
    updated_at: datetime | None = None

    @field_validator("currency", "pricing_unit", "source", mode="before")
    @classmethod
    def _optional_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name)

    @field_validator("updated_at", mode="before")
    @classmethod
    def _updated_at(cls, value: object) -> datetime | None:
        return None if value is None else _timestamp(value, field="updated_at")


class ModelMetadata(_Profile):
    """Provider-neutral metadata merged into a discovered model profile.

    This is part of the existing model catalog contract.  It carries no
    credentials and never asserts account availability; that comes from the
    provider-scoped discovery/verification path.
    """

    provider_id: str
    model_id: str
    display_name: str
    billing_class: BillingClass = BillingClass.UNKNOWN
    billing_provenance: BillingProvenance = BillingProvenance.UNKNOWN
    billing_source: str | None = None
    lifecycle_status: str = "ACTIVE"
    deprecated: bool = False
    capabilities: tuple[ModelCapability, ...] = ()
    metadata_updated_at: datetime | None = None
    metadata_source: str | None = None

    @field_validator("provider_id", "model_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("display_name", "lifecycle_status", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("billing_class", mode="before")
    @classmethod
    def _billing_class(cls, value: object) -> BillingClass:
        return value if isinstance(value, BillingClass) else BillingClass(value)

    @field_validator("billing_provenance", mode="before")
    @classmethod
    def _billing_provenance(cls, value: object) -> BillingProvenance:
        return value if isinstance(value, BillingProvenance) else BillingProvenance(value)

    @field_validator("billing_source", "metadata_source", mode="before")
    @classmethod
    def _optional_sources(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=1024)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)

    @field_validator("metadata_updated_at", mode="before")
    @classmethod
    def _metadata_updated_at(cls, value: object) -> datetime | None:
        return None if value is None else _timestamp(value, field="metadata_updated_at")


class ModelProfile(_Profile):
    model_profile_id: str
    provider_id: str
    endpoint_id: str
    remote_model_id: str
    display_name: str
    advertised_family: str
    input_modalities: tuple[ModelCapability, ...] = ()
    output_modalities: tuple[ModelCapability, ...] = ()
    capabilities: tuple[ModelCapability, ...] = ()
    capability_sources: tuple[CapabilityEvidence, ...] = ()
    quality_tier: QualityTier = QualityTier.UNKNOWN
    quality_source: QualitySource = QualitySource.UNKNOWN
    billing_class: BillingClass = BillingClass.UNKNOWN
    billing_provenance: BillingProvenance = BillingProvenance.UNKNOWN
    billing_source: str | None = None
    pricing: PricingMetadata | None = None
    latency_class: str = "UNKNOWN"
    context_window: int | None = Field(default=None, ge=1)
    max_output: int | None = Field(default=None, ge=1)
    enabled: bool = True
    discovery_status: DiscoveryStatus = DiscoveryStatus.UNKNOWN
    availability: ModelAvailability = ModelAvailability.UNKNOWN
    availability_status: ModelAvailability = ModelAvailability.UNKNOWN
    availability_source: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    availability_provenance: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    catalog_source: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    lifecycle_status: str = "ACTIVE"
    deprecated: bool = False
    metadata_updated_at: datetime | None = None
    metadata_source: str | None = None
    discovered_at: datetime | None = None
    verified_at: datetime | None = None
    credential_revision: str | None = None
    endpoint_revision: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNKNOWN
    record_provenance: RecordProvenance = RecordProvenance.UNKNOWN
    reliability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: tuple[tuple[str, str], ...] = ()

    @field_validator("quality_tier", mode="before")
    @classmethod
    def _quality_tier(cls, value: object) -> QualityTier:
        return value if isinstance(value, QualityTier) else QualityTier(value)

    @field_validator("quality_source", mode="before")
    @classmethod
    def _quality_source(cls, value: object) -> QualitySource:
        return value if isinstance(value, QualitySource) else QualitySource(value)

    @field_validator("billing_class", mode="before")
    @classmethod
    def _billing_class(cls, value: object) -> BillingClass:
        return value if isinstance(value, BillingClass) else BillingClass(value)

    @field_validator("billing_provenance", mode="before")
    @classmethod
    def _billing_provenance(cls, value: object) -> BillingProvenance:
        return value if isinstance(value, BillingProvenance) else BillingProvenance(value)

    @field_validator("discovery_status", mode="before")
    @classmethod
    def _discovery_status(cls, value: object) -> DiscoveryStatus:
        return value if isinstance(value, DiscoveryStatus) else DiscoveryStatus(value)

    @field_validator("verification_status", mode="before")
    @classmethod
    def _verification_status(cls, value: object) -> VerificationStatus:
        return value if isinstance(value, VerificationStatus) else VerificationStatus(value)

    @field_validator("record_provenance", mode="before")
    @classmethod
    def _record_provenance(cls, value: object) -> RecordProvenance:
        return value if isinstance(value, RecordProvenance) else RecordProvenance(value)

    @field_validator("availability", mode="before")
    @classmethod
    def _availability(cls, value: object) -> ModelAvailability:
        return value if isinstance(value, ModelAvailability) else ModelAvailability(value)

    @field_validator("availability_status", mode="before")
    @classmethod
    def _availability_status(cls, value: object) -> ModelAvailability:
        return value if isinstance(value, ModelAvailability) else ModelAvailability(value)

    @field_validator("availability_source", mode="before")
    @classmethod
    def _availability_source(cls, value: object) -> ModelCatalogSource:
        return value if isinstance(value, ModelCatalogSource) else ModelCatalogSource(value)

    @field_validator("availability_provenance", mode="before")
    @classmethod
    def _availability_provenance(cls, value: object) -> ModelCatalogSource:
        return value if isinstance(value, ModelCatalogSource) else ModelCatalogSource(value)

    @field_validator("catalog_source", mode="before")
    @classmethod
    def _catalog_source(cls, value: object) -> ModelCatalogSource:
        return value if isinstance(value, ModelCatalogSource) else ModelCatalogSource(value)

    @field_validator("capability_sources", mode="before")
    @classmethod
    def _sources(cls, value: object) -> tuple[CapabilityEvidence, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("capability_sources must be a tuple")
        return tuple(item if isinstance(item, CapabilityEvidence) else CapabilityEvidence.model_validate(item) for item in value)

    @field_validator("model_profile_id", "provider_id", "endpoint_id", "remote_model_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("display_name", "advertised_family", "latency_class", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("input_modalities", "output_modalities", "capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object, info) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> tuple[tuple[str, str], ...]:
        return _pairs(value, field="metadata")

    @field_validator("discovered_at", "verified_at", mode="before")
    @classmethod
    def _timestamps(cls, value: object, info) -> datetime | None:
        return None if value is None else _timestamp(value, field=info.field_name)

    @field_validator("lifecycle_status", mode="before")
    @classmethod
    def _lifecycle_status(cls, value: object) -> str:
        return _text(value, field="lifecycle_status")

    @field_validator("billing_source", "metadata_source", mode="before")
    @classmethod
    def _metadata_sources(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=1024)

    @field_validator("credential_revision", "endpoint_revision", mode="before")
    @classmethod
    def _revisions(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    @model_validator(mode="before")
    @classmethod
    def _migrate_availability_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if "availability_status" not in migrated and "availability" in migrated:
            migrated["availability_status"] = migrated["availability"]
        if "availability_provenance" not in migrated and "availability_source" in migrated:
            migrated["availability_provenance"] = migrated["availability_source"]
        return migrated

    @model_validator(mode="after")
    def _complete_capability_provenance(self) -> ModelProfile:
        # ``availability`` and ``availability_source`` are compatibility
        # aliases.  New callers must provide/read the split authoritative
        # fields above; aliases are synchronized only after validation.
        object.__setattr__(self, "availability", self.availability_status)
        object.__setattr__(self, "availability_source", self.availability_provenance)
        known = {item.capability for item in self.capability_sources}
        missing = tuple(item for item in self.capabilities if item not in known)
        if missing:
            object.__setattr__(
                self,
                "capability_sources",
                self.capability_sources
                + tuple(
                    CapabilityEvidence(
                        capability=item,
                        provenance=CapabilityProvenance.DECLARED,
                        status=CapabilityStatus.UNKNOWN,
                    )
                    for item in missing
                ),
            )
        return self


class TaskProfile(_Profile):
    task_type: TaskType
    required_capabilities: tuple[ModelCapability, ...] = (ModelCapability.TEXT,)
    minimum_quality_tier: QualityTier = QualityTier.UNKNOWN
    quality_priority: float = Field(default=1.0, ge=0.0, le=1.0)
    cost_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    latency_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    reliability_requirement: float = Field(default=0.0, ge=0.0, le=1.0)
    fallback_allowed: bool = True
    default_routing_strategy: RoutingStrategy = RoutingStrategy.BEST_VALUE

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value: object) -> TaskType:
        return value if isinstance(value, TaskType) else TaskType(value)

    @field_validator("minimum_quality_tier", mode="before")
    @classmethod
    def _minimum_quality(cls, value: object) -> QualityTier:
        return value if isinstance(value, QualityTier) else QualityTier(value)

    @field_validator("default_routing_strategy", mode="before")
    @classmethod
    def _routing_strategy(cls, value: object) -> RoutingStrategy:
        return value if isinstance(value, RoutingStrategy) else RoutingStrategy(value)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _required(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)


class TaskAssignment(_Profile):
    task_type: TaskType
    model_profile_id: str
    enabled: bool = True

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value: object) -> TaskType:
        return value if isinstance(value, TaskType) else TaskType(value)

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _model_id(cls, value: object) -> str:
        return _identifier(value, field="model_profile_id")


class GlobalModelPolicy(_Profile):
    default_strategy: RoutingStrategy = RoutingStrategy.BEST_VALUE
    paid_model_policy: PaidModelPolicy = PaidModelPolicy.ASK
    active_model_profile_id: str | None = None
    preferred_providers: tuple[str, ...] = ()
    disabled_models: tuple[str, ...] = ()
    disabled_endpoints: tuple[str, ...] = ()
    fallback_allowed: bool = True
    minimum_sample_size: int = Field(default=5, ge=1, le=1000)

    @field_validator("default_strategy", mode="before")
    @classmethod
    def _strategy(cls, value: object) -> RoutingStrategy:
        return value if isinstance(value, RoutingStrategy) else RoutingStrategy(value)

    @field_validator("paid_model_policy", mode="before")
    @classmethod
    def _paid_policy(cls, value: object) -> PaidModelPolicy:
        return value if isinstance(value, PaidModelPolicy) else PaidModelPolicy(value)

    @field_validator("preferred_providers", "disabled_models", "disabled_endpoints", mode="before")
    @classmethod
    def _id_lists(cls, value: object, info) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError(f"{info.field_name} must be a tuple")
        return tuple(_identifier(item, field=info.field_name) for item in value)

    @field_validator("active_model_profile_id", mode="before")
    @classmethod
    def _active_model(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="active_model_profile_id")


class ProjectModelPolicy(_Profile):
    project_id: str
    default_strategy: RoutingStrategy | None = None
    task_assignments: tuple[TaskAssignment, ...] = ()
    disabled_models: tuple[str, ...] = ()
    disabled_endpoints: tuple[str, ...] = ()

    @field_validator("default_strategy", mode="before")
    @classmethod
    def _strategy(cls, value: object) -> RoutingStrategy | None:
        return None if value is None else value if isinstance(value, RoutingStrategy) else RoutingStrategy(value)

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return _identifier(value, field="project_id")

    @field_validator("task_assignments", mode="before")
    @classmethod
    def _assignments(cls, value: object) -> tuple[TaskAssignment, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("task_assignments must be a tuple")
        return tuple(TaskAssignment.model_validate(item) for item in value)

    @field_validator("disabled_models", "disabled_endpoints", mode="before")
    @classmethod
    def _disabled(cls, value: object, info) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        return tuple(_identifier(item, field=info.field_name) for item in value)


class RequestModelOverride(_Profile):
    request_id: str
    model_profile_id: str
    enabled: bool = True

    @field_validator("request_id", "model_profile_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)


class RoutingRequest(_Profile):
    request_id: str
    project_id: str
    task_profile: TaskProfile
    routing_strategy: RoutingStrategy | None = None
    request_model_override: RequestModelOverride | str | None = None
    project_policy: ProjectModelPolicy | None = None
    global_policy: GlobalModelPolicy = Field(default_factory=GlobalModelPolicy)
    available_credential_refs: tuple[str, ...] = ()
    credential_ref_by_endpoint: tuple[tuple[str, str], ...] = ()
    endpoint_health: tuple[tuple[str, str], ...] = ()
    preferred_model: str | None = None
    preferred_provider: str | None = None

    @field_validator("request_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("request_model_override", mode="before")
    @classmethod
    def _override(cls, value: object) -> RequestModelOverride | str | None:
        if value is None or isinstance(value, RequestModelOverride):
            return value
        if isinstance(value, str):
            return _identifier(value, field="request_model_override")
        return RequestModelOverride.model_validate(value)

    @field_validator("available_credential_refs", mode="before")
    @classmethod
    def _credentials(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError("available_credential_refs must be a tuple")
        return tuple(_ref(item, field="available_credential_ref") for item in value)

    @field_validator("credential_ref_by_endpoint", mode="before")
    @classmethod
    def _credential_bindings(cls, value: object) -> tuple[tuple[str, str], ...]:
        if isinstance(value, Mapping):
            value = tuple(sorted(value.items()))
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("credential_ref_by_endpoint must be a tuple or mapping")
        result: list[tuple[str, str]] = []
        for item in value:
            if isinstance(item, list):
                item = tuple(item)
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("credential_ref_by_endpoint contains an invalid entry")
            result.append((_identifier(item[0], field="endpoint_id"), _ref(item[1], field="credential_ref")))
        return tuple(result)

    @field_validator("endpoint_health", mode="before")
    @classmethod
    def _health(cls, value: object) -> tuple[tuple[str, str], ...]:
        return tuple((_identifier(item[0], field="endpoint_id"), _text(item[1], field="health_status")) for item in _pairs(value, field="endpoint_health"))

    @field_validator("preferred_model", "preferred_provider", mode="before")
    @classmethod
    def _preferred(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    @model_validator(mode="after")
    def _project_binding(self) -> RoutingRequest:
        if self.project_policy is not None and self.project_policy.project_id != self.project_id:
            raise ValueError("project policy binding mismatch")
        if self.request_model_override is not None and isinstance(self.request_model_override, RequestModelOverride) and self.request_model_override.request_id != self.request_id:
            raise ValueError("request override binding mismatch")
        return self


class ModelSelection(_Profile):
    model_profile_id: str
    strategy: RoutingStrategy
    status: RouteStatus = RouteStatus.SELECTED
    reason: str
    fallback_allowed: bool = True

    @field_validator("model_profile_id", mode="before")
    @classmethod
    def _model_id(cls, value: object) -> str:
        return _identifier(value, field="model_profile_id")

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return _text(value, field="reason", maximum=1024)


def default_task_profiles() -> tuple[TaskProfile, ...]:
    quality_first = {
        TaskType.ENGINEERING_DECISION,
        TaskType.SYSTEM_ARCHITECTURE,
        TaskType.FIRMWARE,
        TaskType.DEBUG,
    }
    lowest_cost = {TaskType.OCR, TaskType.EMBEDDING}
    result: list[TaskProfile] = []
    for task_type in TaskType:
        strategy = RoutingStrategy.QUALITY_FIRST if task_type in quality_first else RoutingStrategy.LOWEST_COST if task_type in lowest_cost else RoutingStrategy.BEST_VALUE
        capabilities = (ModelCapability.EMBEDDING,) if task_type is TaskType.EMBEDDING else (ModelCapability.VISION,) if task_type in {TaskType.VISION, TaskType.OCR} else (ModelCapability.TEXT,)
        result.append(TaskProfile(task_type=task_type, required_capabilities=capabilities, default_routing_strategy=strategy))
    return tuple(result)


__all__ = (
    "BillingClass",
    "BillingProvenance",
    "CapabilityEvidence",
    "CapabilityProvenance",
    "CapabilityStatus",
    "ConnectionLifecycleStatus",
    "ConnectionStatus",
    "CredentialProfile",
    "DiscoveryMode",
    "DiscoveryStatus",
    "EndpointProfile",
    "EndpointType",
    "GlobalModelPolicy",
    "IdentityStatus",
    "ModelAvailability",
    "ModelCapability",
    "ModelCatalogSource",
    "ModelMetadata",
    "ModelProfile",
    "ModelSelection",
    "PaidModelPolicy",
    "PricingMetadata",
    "ProjectModelPolicy",
    "ProtocolFamily",
    "ProviderConnection",
    "ProviderConnectionStatus",
    "ProviderKind",
    "ProviderProfile",
    "ProviderUsageCapability",
    "QualitySource",
    "QualityTier",
    "RecordProvenance",
    "RequestModelOverride",
    "RouteStatus",
    "RoutingRequest",
    "RoutingStrategy",
    "TaskAssignment",
    "TaskProfile",
    "TaskType",
    "UsageSnapshotStatus",
    "VerificationStatus",
    "default_task_profiles",
)
