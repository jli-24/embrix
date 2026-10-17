"""Production Provider configuration, health and transmission approval boundaries."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from embedded_copilot.engineering_context.models import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    BillingClass,
    BoundModelIdentity,
    CapabilityEvidence,
    CapabilityProbeResult,
    CapabilityProvenance,
    CapabilityStatus,
    CredentialProfile,
    DiscoveryMode,
    DiscoveryStatus,
    EndpointProfile,
    EndpointType,
    ExecutionStatus,
    ModelGatewayError,
    IdentityStatus,
    ModelAvailability,
    ModelCapability,
    ModelCatalogSource,
    ModelExecutionRecord,
    ModelGatewayPort,
    ModelGatewayProviderFailure,
    ModelGatewayService,
    ModelPolicy,
    ModelProfile,
    ModelProviderConfigurationError,
    ModelProviderError,
    ModelProviderModelUnavailable,
    ModelProviderNetworkError,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
    ModelRequest,
    ModelResponse,
    ProtocolFamily,
    ProviderKind,
    ProviderProfile,
    QualitySource,
    QualityTier,
    RecordProvenance,
    RoutingStrategy,
    TaskType,
    VerificationStatus,
    merge_model_metadata,
    official_model_catalog,
    provider_presets,
)
from embedded_copilot.model_gateway.transport import (
    GatewayTransportBinding,
    ResolvedTransportEntry,
    TransportKey,
)
from embedded_copilot.model_gateway.adapters.protocol_http import (
    SUPPORTED_PROTOCOL_FAMILIES,
)
from embedded_copilot.model_gateway import (
    ProviderConnection as GatewayProviderConnection,
)
from embedded_copilot.model_gateway import (
    ProviderConnectionStatus as GatewayProviderConnectionStatus,
)
from embedded_copilot.model_runtime.integration import (
    ProductionModelSettings,
    ProductionProvider,
    create_production_model_binding,
    create_production_provider,
)

from .credential_recovery_diagnostic import emit as recovery_emit
from .credential_save_diagnostic import emit, request_scope
from .credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    PROVIDER_BINDING_INTEGRITY_PURPOSE,
    CredentialProviderPort,
    CredentialUnavailable,
    provider_connection_credential_purpose,
    provider_credential_purpose,
)
from .model_discovery import OfficialModelDiscovery
from .model_pool import (
    ModelPoolRefreshResult,
    ProviderModelDiscoveryPort,
    ProviderModelPool,
)
from .provider_verification import (
    ConnectionTestRecord,
    ProviderVerificationReceipt,
    ProviderVerificationReceiptStore,
    ProviderVerificationTarget,
    projection_revision,
)
from .provider_verification import (
    ResponseParseStatus as ReceiptResponseParseStatus,
)
from .provider_verification import (
    VerificationStatus as ReceiptVerificationStatus,
)
from .test_connection_diagnostic import emit as test_connection_emit

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
_AUTO = "sha256:" + "0" * 64
_MODEL_REQUEST_VALIDATION_EVENT = "model_request_validation_boundary_telemetry"
_RUNTIME_DIAGNOSTICS_LOGGER = logging.getLogger("embedded_copilot.runtime_diagnostics")
_REQUEST_VALIDATION_REASON_BY_MESSAGE = {
    "Value error, prompt must match task": "MODEL_REQUEST_PROMPT_ALIAS_MISMATCH",
    "Value error, system_prompt must match system_instruction": "MODEL_REQUEST_SYSTEM_ALIAS_MISMATCH",
    "Value error, context must match engineering_context": "MODEL_REQUEST_CONTEXT_ALIAS_MISMATCH",
    "Value error, temperature must match model policy": "MODEL_REQUEST_TEMPERATURE_MISMATCH",
    "Value error, max_tokens must match model policy": "MODEL_REQUEST_MAX_TOKENS_MISMATCH",
    "Value error, request metadata binding mismatch": "MODEL_REQUEST_METADATA_MISMATCH",
    "Value error, model request fingerprint mismatch": "MODEL_REQUEST_FINGERPRINT_MISMATCH",
}
_RECEIVED_TYPE_BY_TYPE = {
    type(None): "NONE",
    bool: "BOOL",
    int: "INT",
    float: "FLOAT",
    str: "STR",
    bytes: "BYTES",
    tuple: "TUPLE",
    list: "LIST",
    dict: "DICT",
    set: "SET",
    frozenset: "FROZENSET",
}
_MAX_PROVIDER_CREDENTIAL_LENGTH = 4096
_CUSTOM_RELAY_TEMPLATE_ID = "custom-relay"


def _custom_relay_provider_id(connection_name: str, base_url: str) -> str:
    """Build a stable, provider-scoped identity without using the endpoint host as its name."""

    normalized_name = connection_name.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_name.casefold()).strip("-")
    slug = slug[:32] or "relay"
    stable_material = f"{normalized_name}\x00{base_url.rstrip('/')}".encode()
    stable_id = hashlib.sha256(stable_material).hexdigest()[:12]
    return f"custom-{slug}-{stable_id}"


def _connection_name(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 128
        or any(character in "\r\n" or ord(character) < 32 for character in value)
    ):
        raise ValueError("connection_name is invalid")
    return value.strip()


def _request_validation_reason(error: ValidationError) -> str:
    for item in error.errors(include_input=False, include_url=False):
        message = item.get("msg")
        if isinstance(message, str):
            reason = _REQUEST_VALIDATION_REASON_BY_MESSAGE.get(message)
            if reason is not None:
                return reason
    return "MODEL_REQUEST_SCHEMA_MISMATCH"


def _validation_code(value: object, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    checked = value.strip().upper()
    if (
        not checked
        or len(checked) > 64
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in checked
        )
    ):
        return fallback
    return checked


def _canonical_probe_url(endpoint: EndpointProfile | None, model: str) -> str | None:
    """Build the content-free URL used by the canonical protocol adapter."""

    if endpoint is None:
        return None
    protocol = endpoint.protocol_family
    if protocol is ProtocolFamily.CUSTOM:
        protocol = ProtocolFamily.OPENAI_CHAT
    if protocol is ProtocolFamily.OPENAI_RESPONSES:
        path = "responses"
    elif protocol in {
        ProtocolFamily.OPENAI_CHAT,
        ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
    }:
        path = "chat/completions"
    elif protocol is ProtocolFamily.ANTHROPIC_MESSAGES:
        path = "messages"
    else:
        path = f"models/{model}:generateContent"
    return endpoint.base_url.rstrip("/") + "/" + path


def _connection_diagnostic(
    *,
    status: ProviderConnectionStatus,
    provider: str,
    protocol_family: str | None,
    request_url: str | None,
    error: BaseException | None = None,
    stage: ProviderConnectionDiagnosticStage | None = None,
    provider_error_code: str | None = None,
) -> ProviderConnectionDiagnostic:
    safe_http_status = getattr(error, "safe_http_status", None)
    safe_transport_code = getattr(error, "safe_transport_error_code", None)
    safe_transport_stage = getattr(error, "safe_transport_stage", None)
    if stage is None:
        if safe_http_status is not None or (
            isinstance(error, ModelGatewayProviderFailure)
            and error.provider_response_present
        ):
            stage = ProviderConnectionDiagnosticStage.PROVIDER_RESPONSE
        elif safe_transport_stage in {
            item.value
            for item in ProviderConnectionDiagnosticStage
            if item in {
                ProviderConnectionDiagnosticStage.DNS,
                ProviderConnectionDiagnosticStage.TCP,
                ProviderConnectionDiagnosticStage.TLS,
                ProviderConnectionDiagnosticStage.HTTP,
            }
        }:
            stage = ProviderConnectionDiagnosticStage(safe_transport_stage)
        elif isinstance(error, CredentialUnavailable):
            stage = ProviderConnectionDiagnosticStage.CREDENTIAL
        elif isinstance(error, (ModelProviderConfigurationError, TypeError, ValueError, UnicodeError)):
            stage = ProviderConnectionDiagnosticStage.CONFIGURATION
        else:
            stage = ProviderConnectionDiagnosticStage.HTTP
    if provider_error_code is None and error is not None:
        if isinstance(error, ModelGatewayProviderFailure):
            provider_error_code = error.reason_code
        elif isinstance(error, ModelProviderUnauthorized):
            provider_error_code = "AUTHENTICATION_FAILED"
        elif isinstance(error, ModelProviderModelUnavailable):
            provider_error_code = "MODEL_UNAVAILABLE"
        elif isinstance(error, ModelProviderRateLimited):
            provider_error_code = "RATE_LIMITED"
        elif isinstance(error, ModelProviderTimeout):
            provider_error_code = "TIMEOUT"
        elif isinstance(error, ModelProviderNetworkError):
            provider_error_code = safe_transport_code or "NETWORK_FAILED"
        elif isinstance(error, ModelProviderConfigurationError):
            provider_error_code = "CONFIGURATION_INVALID"
        elif safe_http_status is not None:
            provider_error_code = "HTTP_STATUS_ERROR"
        elif isinstance(error, CredentialUnavailable):
            provider_error_code = "CREDENTIAL_STORE_UNAVAILABLE"
        else:
            provider_error_code = "PROVIDER_ERROR"
    error_code = _connection_error_code(
        status=status,
        error=error,
        http_status=safe_http_status,
        transport_stage=safe_transport_stage,
    )
    return ProviderConnectionDiagnostic(
        stage=stage,
        request_url=request_url,
        http_status=safe_http_status if type(safe_http_status) is int else None,
        provider_error_code=provider_error_code,
        error_code=error_code,
        normalized_error=status,
        transport_error_code=(
            safe_transport_code if isinstance(safe_transport_code, str) else None
        ),
        protocol_family=protocol_family,
    )


def _connection_error_code(
    *,
    status: ProviderConnectionStatus,
    error: BaseException | None,
    http_status: object,
    transport_stage: object,
) -> str:
    """Return the stable operator-facing category without exposing provider text."""

    if status is ProviderConnectionStatus.NOT_CONFIGURED:
        return "CREDENTIAL_NOT_CONFIGURED"
    if type(http_status) is int and http_status in {401, 403, 429}:
        return f"HTTP_{http_status}"
    if isinstance(transport_stage, str) and transport_stage in {"DNS", "TCP", "TLS"}:
        return f"{transport_stage}_FAILED"
    if isinstance(error, (ModelProviderConfigurationError, TypeError, ValueError, UnicodeError)):
        return "PROVIDER_ERROR"
    if status is ProviderConnectionStatus.AVAILABLE:
        return "VERIFIED"
    return "PROVIDER_ERROR"


def _validation_location_hash(value: object) -> str:
    location = value if isinstance(value, (tuple, list)) else ()
    normalized = [
        ["INT", item] if type(item) is int else ["STR", str(item)] for item in location
    ]
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _received_type(value: object) -> str:
    return _RECEIVED_TYPE_BY_TYPE.get(
        type(value), "MODEL" if isinstance(value, BaseModel) else "OTHER"
    )


def _validation_error_vector(
    error: ValidationError,
) -> tuple[dict[str, str], ...]:
    vector: list[dict[str, str]] = []
    for item in error.errors(include_input=True, include_url=False):
        error_type = _validation_code(item.get("type"), fallback="PYDANTIC_ERROR")
        message = item.get("msg")
        message_code = (
            _REQUEST_VALIDATION_REASON_BY_MESSAGE.get(message)
            if isinstance(message, str)
            else None
        )
        vector.append(
            {
                "location_hash": _validation_location_hash(item.get("loc")),
                "error_type": error_type,
                "received_type": _received_type(item.get("input")),
                "message_code": message_code or f"PYDANTIC_{error_type}",
            }
        )
    return tuple(vector)


def _record_validation_error_vector(error: ValidationError) -> None:
    """Attach and persist only fixed, content-free Pydantic diagnostics."""

    try:
        vector = _validation_error_vector(error)
        error.validation_error_count = len(vector)
        error.validation_errors = vector
        _RUNTIME_DIAGNOSTICS_LOGGER.info(
            _MODEL_REQUEST_VALIDATION_EVENT,
            extra={
                "event_name": _MODEL_REQUEST_VALIDATION_EVENT,
                "validation_error_count": len(vector),
                "validation_errors": vector,
            },
        )
    except Exception:  # noqa: BLE001  # pragma: no cover - diagnostics must never alter failure behavior
        return


class ProviderConnectionStatus(StrEnum):
    NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    AVAILABLE = "AVAILABLE"
    AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    NETWORK_UNAVAILABLE = "PROVIDER_NETWORK_UNAVAILABLE"
    MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    CONFIG_INVALID = "PROVIDER_CONFIG_INVALID"
    UNVERIFIED = "PROVIDER_CONNECTION_UNVERIFIED"


class ProviderConnectionDiagnosticStage(StrEnum):
    CONFIGURATION = "CONFIGURATION"
    CREDENTIAL = "CREDENTIAL"
    DNS = "DNS"
    TCP = "TCP"
    TLS = "TLS"
    HTTP = "HTTP"
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"


class TransmissionDecision(StrEnum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class ProviderConnectionDiagnostic(BaseModel):
    """Safe, non-secret details for the most recent connection probe."""

    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", hide_input_in_errors=True
    )

    stage: ProviderConnectionDiagnosticStage
    http_method: Literal["POST"] = "POST"
    request_url: str | None = None
    http_status: int | None = None
    provider_error_code: str | None = None
    error_code: str = "PROVIDER_ERROR"
    normalized_error: ProviderConnectionStatus
    transport_error_code: str | None = None
    protocol_family: str | None = None

    @field_validator("request_url")
    @classmethod
    def _request_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or len(value) > 512
        ):
            raise ValueError("request_url is invalid")
        return value

    @field_validator("http_status")
    @classmethod
    def _http_status(cls, value: int | None) -> int | None:
        if value is not None and (type(value) is not int or not 100 <= value <= 599):
            raise ValueError("http_status is invalid")
        return value

    @field_validator("provider_error_code", "error_code", "transport_error_code", "protocol_family")
    @classmethod
    def _safe_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        checked = _validation_code(value, fallback="")
        if not checked:
            raise ValueError("diagnostic identifier is invalid")
        return checked


def _fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _credential_fingerprint(
    credentials: CredentialProviderPort,
    credential: bytes,
    *,
    create_key: bool = True,
    credential_purpose: str = CHAT_PROVIDER_CREDENTIAL_PURPOSE,
) -> str:
    """Return a non-reversible identity for the current provider credential."""

    if not isinstance(credential, bytes) or not credential:
        raise ValueError("credential is invalid")
    key = (
        credentials.get_or_create_provider_binding_key()
        if create_key
        else credentials.get(PROVIDER_BINDING_INTEGRITY_PURPOSE)
    )
    if not isinstance(key, bytes) or len(key) < 32:
        raise CredentialUnavailable("provider binding integrity key is unavailable")
    digest = hmac.new(
        key,
        credential_purpose.encode("utf-8") + b"\x00" + credential,
        hashlib.sha256,
    ).hexdigest()
    return "hmac-sha256:" + digest


def context_fingerprint(reference_fingerprints: tuple[str, ...]) -> str:
    return _fingerprint({"context_references": tuple(sorted(reference_fingerprints))})


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    provider: str = "deepseek"
    connection_name: str | None = None
    model: str | None = DEFAULT_DEEPSEEK_MODEL
    base_url: str = DEEPSEEK_BASE_URL
    last_test_status: ProviderConnectionStatus = ProviderConnectionStatus.UNVERIFIED
    tested_at: datetime | None = None
    tested_configuration_fingerprint: str | None = None
    credential_fingerprint: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    model_profile_id: str | None = None
    protocol_family: str | None = None
    verified_model: str | None = None
    available_models: tuple[str, ...] = ()
    model_available: bool | None = None
    response_model: str | None = None
    response_id: str | None = None
    latency_ms: float | None = None
    http_status: int | None = None
    protocol_adapter: str | None = None
    verification_id: str | None = None
    verification_status: str | None = None
    verification_persisted: bool | None = None
    verification_persistence_error: str | None = None
    configuration_fingerprint: str = _AUTO

    @field_validator("provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(character.isspace() or character in "/\\" for character in value)
        ):
            raise ValueError("provider is invalid")
        return value.strip().lower()

    @field_validator("connection_name")
    @classmethod
    def _connection_name_field(cls, value: object) -> str | None:
        return _connection_name(value)

    @field_validator("model")
    @classmethod
    def _model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(character.isspace() for character in value)
        ):
            raise ValueError("model is invalid")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("base_url is invalid")
        checked = value.strip().rstrip("/")
        if "://" not in checked or "@" in checked or "?" in checked or "#" in checked:
            raise ValueError("base_url is invalid")
        scheme, remainder = checked.split("://", 1)
        if scheme not in {"http", "https"} or not remainder:
            raise ValueError("base_url is invalid")
        authority = remainder.split("/", 1)[0]
        host = (
            authority[1:].split("]", 1)[0]
            if authority.startswith("[") and "]" in authority
            else authority.split(":", 1)[0]
        )
        if not host or (
            scheme == "http"
            and host.casefold() not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("base_url must use HTTPS outside loopback")
        return checked

    @field_validator("credential_fingerprint")
    @classmethod
    def _credential_fingerprint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            len(value) != len("hmac-sha256:") + 64
            or not value.startswith("hmac-sha256:")
            or any(
                character not in "0123456789abcdef"
                for character in value[len("hmac-sha256:") :]
            )
        ):
            raise ValueError("credential fingerprint is invalid")
        return value

    @field_validator(
        "verified_model",
        "response_model",
        "response_id",
        "protocol_adapter",
        "verification_id",
        mode="before",
    )
    @classmethod
    def _optional_identity(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()
        ):
            raise ValueError(f"{info.field_name} is invalid")
        return value.strip()

    @field_validator("verification_status", "verification_persistence_error", mode="before")
    @classmethod
    def _optional_status_code(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()
        ):
            raise ValueError(f"{info.field_name} is invalid")
        return value.strip()

    @field_validator("available_models", mode="before")
    @classmethod
    def _available_models(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError("available_models must be a tuple")
        result = tuple(
            item.strip()
            if isinstance(item, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item.strip())
            else item
            for item in value
        )
        if any(not isinstance(item, str) or not item for item in result):
            raise ValueError("available_models is invalid")
        return result

    @model_validator(mode="after")
    def _verify(self) -> ProviderConfiguration:
        if (
            self.provider == ProductionProvider.DEEPSEEK.value
            and self.base_url != DEEPSEEK_BASE_URL
        ):
            raise ValueError("DeepSeek base URL is fixed")
        expected = _fingerprint(
            {
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url,
                "credential_fingerprint": self.credential_fingerprint,
                "endpoint_id": self.endpoint_id,
                "credential_ref": self.credential_ref,
                "model_profile_id": self.model_profile_id,
            }
        )
        if self.configuration_fingerprint == _AUTO:
            object.__setattr__(self, "configuration_fingerprint", expected)
        elif self.configuration_fingerprint != expected:
            raise ValueError("provider configuration fingerprint mismatch")
        if (
            self.tested_configuration_fingerprint is not None
            and self.tested_configuration_fingerprint != expected
        ):
            object.__setattr__(
                self, "last_test_status", ProviderConnectionStatus.UNVERIFIED
            )
            object.__setattr__(self, "tested_at", None)
            object.__setattr__(self, "tested_configuration_fingerprint", None)
            object.__setattr__(self, "verified_model", None)
            object.__setattr__(self, "available_models", ())
            object.__setattr__(self, "model_available", None)
            object.__setattr__(self, "response_model", None)
            object.__setattr__(self, "response_id", None)
            object.__setattr__(self, "latency_ms", None)
            object.__setattr__(self, "http_status", None)
            object.__setattr__(self, "protocol_adapter", None)
            object.__setattr__(self, "verification_id", None)
            object.__setattr__(self, "verification_status", None)
            object.__setattr__(self, "verification_persisted", None)
            object.__setattr__(self, "verification_persistence_error", None)
        if self.model_available is True and self.verified_model is None:
            raise ValueError("verified model is required when model is available")
        if self.verified_model is not None and self.verified_model not in self.available_models:
            raise ValueError("verified model is not in available models")
        return self

    def with_configuration(
        self,
        *,
        provider: str,
        model: str,
        base_url: str = DEEPSEEK_BASE_URL,
        endpoint_id: str | None = None,
        credential_ref: str | None = None,
        model_profile_id: str | None = None,
    ) -> ProviderConfiguration:
        return ProviderConfiguration(
            provider=provider,
            model=model,
            base_url=base_url,
            credential_fingerprint=self.credential_fingerprint,
            endpoint_id=endpoint_id,
            credential_ref=credential_ref,
            model_profile_id=model_profile_id,
        )


class ProviderModelProjection(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", hide_input_in_errors=True
    )

    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str
    display_name: str
    availability: ModelAvailability
    availability_status: str
    availability_source: ModelCatalogSource
    availability_provenance: ModelCatalogSource
    catalog_source: ModelCatalogSource
    record_provenance: RecordProvenance
    identity_status: IdentityStatus
    verification_status: VerificationStatus
    can_enable: bool
    enable_block_reason: str | None = None
    billing_class: BillingClass
    billing_provenance: str
    billing_source: str | None = None
    billing_label: str = "费用未知"
    lifecycle_status: str = "ACTIVE"
    deprecated: bool = False
    officially_confirmed: bool = False
    metadata_updated_at: datetime | None = None
    metadata_source: str | None = None
    capabilities: tuple[ModelCapability, ...]
    verified_at: datetime | None = None


class ProviderProfileProjection(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", hide_input_in_errors=True
    )

    provider_id: str
    configured: bool
    selected_model: str | None
    credential_status: str
    connection_status: str
    discovered_model_count: int
    usable_model_count: int


class ProviderProjection(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", hide_input_in_errors=True
    )

    provider: str
    model: str
    configuration_state: str = "UNCONFIGURED"
    discovery_state: str = "NOT_REQUESTED"
    discovery_required: bool = False
    connection: str = "unverified"
    base_url: str
    credential_status: str
    connection_status: ProviderConnectionStatus
    last_test_status: ProviderConnectionStatus
    tested_at: datetime | None
    updated_at: datetime | None
    validation_status: str
    configuration_fingerprint: str
    tested_configuration_fingerprint: str | None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    protocol_family: str | None = None
    model_profile_id: str | None = None
    model_id: str | None = None
    verified_model: str | None = None
    available_models: tuple[str, ...] = ()
    model_pool: tuple[ProviderModelProjection, ...] = ()
    provider_profiles: tuple[ProviderProfileProjection, ...] = ()
    active_provider_id: str | None = None
    model_available: bool | None = None
    response_model: str | None = None
    response_id: str | None = None
    latency_ms: float | None = None
    http_status: int | None = None
    protocol_adapter: str | None = None
    verification_id: str | None = None
    verification_status: str | None = None
    verification_persisted: bool | None = None
    verification_persistence_error: str | None = None
    connection_diagnostic: ProviderConnectionDiagnostic | None = None
    transmission_status: str = "NOT_APPROVED"
    restart_required: bool = False
    fingerprint: str = _AUTO

    @model_validator(mode="after")
    def _fingerprint_projection(self) -> ProviderProjection:
        expected = _fingerprint(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("provider projection fingerprint mismatch")
        return self


class ProviderConfigurationStore:
    """Safe JSON store; this class never accepts or writes provider secrets."""

    _KEYS = frozenset(
        {
            "chat_provider",
            "chat_connection_name",
            "chat_model",
            "chat_last_test_status",
            "chat_tested_at",
            "chat_tested_configuration_fingerprint",
            "chat_credential_fingerprint",
            "chat_base_url",
            "chat_endpoint_id",
            "chat_credential_ref",
            "chat_model_profile_id",
            "chat_verified_model",
            "chat_available_models",
            "chat_model_available",
            "chat_response_model",
            "chat_response_id",
            "chat_latency_ms",
            "chat_http_status",
            "chat_protocol_adapter",
            "chat_verification_id",
            "chat_verification_status",
            "chat_verification_persisted",
            "chat_verification_persistence_error",
            "chat_provider_profiles",
        }
    )

    _SAFE_PRESERVED_KEYS = frozenset(
        {
            "project_root",
            "project_location_allow_list",
            "ollama_base_url",
            "embedding_provider",
            "embedding_model",
            "request_timeout_seconds",
            "analysis_timeout_seconds",
            "desktop_session_ttl_seconds",
            *_KEYS,
        }
    )

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> ProviderConfiguration:
        """Load the legacy JSON projection for compatibility/migration only."""
        with self._lock:
            raw = self._read()
            try:
                return ProviderConfiguration(
                    provider=raw.get("chat_provider", "deepseek"),
                    connection_name=raw.get("chat_connection_name"),
                    model=raw.get("chat_model", DEFAULT_DEEPSEEK_MODEL),
                    base_url=raw.get("chat_base_url", DEEPSEEK_BASE_URL),
                    last_test_status=ProviderConnectionStatus(
                        raw.get(
                            "chat_last_test_status",
                            ProviderConnectionStatus.UNVERIFIED.value,
                        )
                    ),
                    tested_at=(
                        datetime.fromisoformat(raw["chat_tested_at"])
                        if isinstance(raw.get("chat_tested_at"), str)
                        else None
                    ),
                    tested_configuration_fingerprint=raw.get(
                        "chat_tested_configuration_fingerprint"
                    ),
                    credential_fingerprint=raw.get("chat_credential_fingerprint"),
                    endpoint_id=raw.get("chat_endpoint_id"),
                    credential_ref=raw.get("chat_credential_ref"),
                    model_profile_id=raw.get("chat_model_profile_id"),
                    verified_model=raw.get("chat_verified_model"),
                    available_models=tuple(raw.get("chat_available_models", ())),
                    model_available=raw.get("chat_model_available"),
                    response_model=raw.get("chat_response_model"),
                    response_id=raw.get("chat_response_id"),
                    latency_ms=raw.get("chat_latency_ms"),
                    http_status=raw.get("chat_http_status"),
                    protocol_adapter=raw.get("chat_protocol_adapter"),
                    verification_id=raw.get("chat_verification_id"),
                    verification_status=raw.get("chat_verification_status"),
                    verification_persisted=raw.get("chat_verification_persisted"),
                    verification_persistence_error=raw.get(
                        "chat_verification_persistence_error"
                    ),
                )
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError("provider configuration is invalid") from error

    def load_profiles(self) -> dict[str, ProviderConfiguration]:
        """Load legacy per-provider profiles for compatibility/migration only."""

        with self._lock:
            raw = self._read()
            encoded = raw.get("chat_provider_profiles", {})
            if encoded is None:
                return {}
            if not isinstance(encoded, dict):
                raise TypeError("provider profiles are invalid")
            result: dict[str, ProviderConfiguration] = {}
            try:
                for provider, value in encoded.items():
                    if not isinstance(provider, str) or not isinstance(value, dict):
                        raise TypeError("provider profiles are invalid")
                    normalized = copy.deepcopy(value)
                    if isinstance(normalized.get("available_models"), list):
                        normalized["available_models"] = tuple(normalized["available_models"])
                    if isinstance(normalized.get("last_test_status"), str):
                        normalized["last_test_status"] = ProviderConnectionStatus(
                            normalized["last_test_status"]
                        )
                    if isinstance(normalized.get("tested_at"), str):
                        normalized["tested_at"] = datetime.fromisoformat(
                            normalized["tested_at"]
                        )
                    profile = ProviderConfiguration.model_validate(normalized)
                    if profile.provider != provider:
                        raise ValueError("provider profile identity is invalid")
                    result[provider] = profile
            except (TypeError, ValueError, KeyError) as error:
                raise ValueError("provider profiles are invalid") from error
            return result

    def save_profiles(self, profiles: Mapping[str, ProviderConfiguration]) -> None:
        """Write the legacy provider-profile projection; canonical paths avoid it."""
        checked: dict[str, dict[str, object]] = {}
        for provider, profile in profiles.items():
            if not isinstance(provider, str) or profile.provider != provider:
                raise ValueError("provider profile identity is invalid")
            checked[provider] = ProviderConfiguration.model_validate(
                copy.deepcopy(profile.model_dump(mode="python"))
            ).model_dump(mode="json")
        with self._lock:
            raw = self._read()
            raw["chat_provider_profiles"] = checked
            self._write(raw)

    @property
    def updated_at(self) -> datetime | None:
        """Return only the safe configuration-file modification time."""

        try:
            return datetime.fromtimestamp(self._path.stat().st_mtime, UTC)
        except OSError:
            return None

    def save(self, configuration: ProviderConfiguration) -> ProviderConfiguration:
        """Write the legacy JSON projection; canonical paths use management state."""
        checked = ProviderConfiguration.model_validate(
            copy.deepcopy(configuration.model_dump(mode="python"))
        )
        with self._lock:
            raw = self._read()
            raw.update(
                {
                    "chat_provider": checked.provider,
                    "chat_connection_name": checked.connection_name,
                    "chat_model": checked.model,
                    "chat_last_test_status": checked.last_test_status.value,
                    "chat_tested_at": checked.tested_at.isoformat()
                    if checked.tested_at
                    else None,
                    "chat_tested_configuration_fingerprint": checked.tested_configuration_fingerprint,
                    "chat_credential_fingerprint": checked.credential_fingerprint,
                    "chat_base_url": checked.base_url,
                    "chat_endpoint_id": checked.endpoint_id,
                    "chat_credential_ref": checked.credential_ref,
                    "chat_model_profile_id": checked.model_profile_id,
                    "chat_verified_model": checked.verified_model,
                    "chat_available_models": list(checked.available_models),
                    "chat_model_available": checked.model_available,
                    "chat_response_model": checked.response_model,
                    "chat_response_id": checked.response_id,
                    "chat_latency_ms": checked.latency_ms,
                    "chat_http_status": checked.http_status,
                    "chat_protocol_adapter": checked.protocol_adapter,
                    "chat_verification_id": checked.verification_id,
                    "chat_verification_status": checked.verification_status,
                    "chat_verification_persisted": checked.verification_persisted,
                    "chat_verification_persistence_error": checked.verification_persistence_error,
                }
            )
            emit("CONFIGURATION_STORE_SAVE_BEGIN", result="BEGIN")
            try:
                self._write(raw)
            except Exception as error:
                emit("CONFIGURATION_STORE_SAVE_FAILURE", result="FAILURE", error=error)
                raise
            emit("CONFIGURATION_STORE_SAVE_SUCCESS", result="SUCCESS")
        return checked

    def record_test(
        self,
        configuration: ProviderConfiguration,
        status: ProviderConnectionStatus,
        tested_at: datetime,
        *,
        verified_model: str | None = None,
        available_models: tuple[str, ...] = (),
        model_available: bool | None = None,
        response_model: str | None = None,
        response_id: str | None = None,
        latency_ms: float | None = None,
        http_status: int | None = None,
        protocol_adapter: str | None = None,
        verification_id: str | None = None,
        verification_status: str | None = None,
        verification_persisted: bool | None = None,
        verification_persistence_error: str | None = None,
    ) -> ProviderConfiguration:
        """Persist legacy test evidence for the old provider selector."""
        model_rate_limited = status is ProviderConnectionStatus.RATE_LIMITED
        recorded_verified_model = (
            configuration.model if model_rate_limited else verified_model
        )
        recorded_available_models = (
            (configuration.model,)
            if model_rate_limited
            else available_models
        )
        return self.save(
            ProviderConfiguration(
                provider=configuration.provider,
                connection_name=configuration.connection_name,
                model=configuration.model,
                base_url=configuration.base_url,
                last_test_status=status,
                tested_at=tested_at,
                tested_configuration_fingerprint=configuration.configuration_fingerprint,
                credential_fingerprint=configuration.credential_fingerprint,
                endpoint_id=configuration.endpoint_id,
                credential_ref=configuration.credential_ref,
                model_profile_id=configuration.model_profile_id,
                verified_model=recorded_verified_model,
                available_models=recorded_available_models,
                model_available=(
                    True
                    if model_rate_limited
                    else model_available
                    if model_available is not None
                    else False
                    if status is not ProviderConnectionStatus.AVAILABLE
                    else None
                ),
                response_model=response_model,
                response_id=response_id,
                latency_ms=latency_ms,
                http_status=http_status,
                protocol_adapter=protocol_adapter,
                verification_id=verification_id,
                verification_status=verification_status,
                verification_persisted=verification_persisted,
                verification_persistence_error=verification_persistence_error,
            )
        )

    def _read(self) -> dict[str, object]:
        if not self._path.is_file():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("provider configuration is unavailable") from error
        if not isinstance(value, dict):
            raise TypeError("provider configuration is invalid")
        return value

    def _write(self, value: Mapping[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(self._path.name + ".tmp")
        sanitized = {
            key: item for key, item in value.items() if key in self._SAFE_PRESERVED_KEYS
        }
        payload = (
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._path)


class _PreparedTransportGatewayPort(Protocol):
    """Private host preparation seam; the legacy gateway port stays unchanged."""

    def prepare_bound_transport(self, identity: BoundModelIdentity, request: ModelRequest) -> tuple: ...


class ReloadableModelGateway(ModelGatewayPort):
    """Stable Application dependency whose provider binding can be swapped atomically."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gateway: ModelGatewayPort | None = None
        self._transport_binding: GatewayTransportBinding | None = None
        self._legacy_policy: ModelPolicy | None = None
        self._legacy_routing_context: tuple[object, Callable[[], tuple[str, ...]]] | None = None
        self._failure_code = "PROVIDER_NOT_CONFIGURED"
        self._endpoint_id: str | None = None
        self._credential_ref: str | None = None
        self._model_profile_id: str | None = None

    @property
    def is_configured(self) -> bool:
        with self._lock:
            return (
                self._transport_binding is not None
                and bool(self._transport_binding.entries)
            )

    @property
    def transport_ready(self) -> bool:
        with self._lock:
            return (
                self._transport_binding is not None
                and bool(self._transport_binding.entries)
            )

    @property
    def transport_binding(self) -> GatewayTransportBinding | None:
        """Return the immutable current snapshot for internal audit/tests."""

        with self._lock:
            return self._transport_binding

    @property
    def model_policy(self) -> ModelPolicy:
        with self._lock:
            policy = self._legacy_policy
        if policy is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            error = ModelGatewayUnavailable("LEGACY_POLICY_UNAVAILABLE")
            error.reason_code = "LEGACY_POLICY_UNAVAILABLE"
            raise error
        return policy

    @property
    def failure_code(self) -> str | None:
        with self._lock:
            return self._failure_code

    def install(
        self,
        gateway: ModelGatewayPort,
        policy: ModelPolicy,
        *,
        endpoint_id: str | None = None,
        credential_ref: str | None = None,
        model_profile_id: str | None = None,
    ) -> None:
        if not isinstance(gateway, ModelGatewayPort):
            raise TypeError("model gateway is invalid")
        checked = ModelPolicy.model_validate(
            copy.deepcopy(policy.model_dump(mode="python"))
        )
        with self._lock:
            self._gateway = gateway
            self._legacy_policy = checked
            self._failure_code = None
            self._legacy_routing_context = None
            self._endpoint_id = endpoint_id
            self._credential_ref = credential_ref
            self._model_profile_id = model_profile_id

    def install_transport(
        self,
        gateway: ModelGatewayPort | GatewayTransportBinding | Sequence[ResolvedTransportEntry],
        entries: Mapping[TransportKey, ResolvedTransportEntry]
        | Sequence[ResolvedTransportEntry]
        | None = None,
        *,
        legacy_policy: ModelPolicy | None = None,
        endpoint_id: str | None = None,
        credential_ref: str | None = None,
        model_profile_id: str | None = None,
    ) -> None:
        """Atomically install transport bindings without requiring a model policy."""

        if isinstance(gateway, GatewayTransportBinding):
            if entries is not None:
                raise TypeError("transport entries must be omitted for a binding snapshot")
            snapshot = gateway
            gateway = snapshot.gateway
        elif isinstance(gateway, ModelGatewayPort):
            if entries is None:
                raise TypeError("transport entries are required")
            snapshot = GatewayTransportBinding.create(gateway, entries)
        else:
            if entries is not None:
                raise TypeError("model gateway is invalid")
            raw_entries = tuple(gateway)
            with self._lock:
                existing = self._gateway
            if existing is None:
                raise TypeError("model gateway is required for transport installation")
            gateway = existing
            snapshot = GatewayTransportBinding.create(gateway, raw_entries)
        if not snapshot.entries:
            raise ValueError("transport binding must contain an entry")
        install_entries = getattr(gateway, "install_transport_entries", None)
        if callable(install_entries):
            install_entries(snapshot.entries)
        checked_policy = None
        if legacy_policy is None:
            with self._lock:
                if self._gateway is gateway:
                    checked_policy = self._legacy_policy
        else:
            checked_policy = ModelPolicy.model_validate(
                copy.deepcopy(legacy_policy.model_dump(mode="python"))
            )
        with self._lock:
            existing_routing_context = (
                self._legacy_routing_context if self._gateway is gateway else None
            )
        with self._lock:
            self._gateway = gateway
            self._transport_binding = snapshot
            self._legacy_policy = checked_policy
            self._legacy_routing_context = existing_routing_context
            self._failure_code = None
            self._endpoint_id = endpoint_id
            self._credential_ref = credential_ref
            self._model_profile_id = model_profile_id

    def rebind_transport(
        self,
        gateway: ModelGatewayPort,
        entries: Mapping[TransportKey, ResolvedTransportEntry]
        | tuple[ResolvedTransportEntry, ...]
        | list[ResolvedTransportEntry],
        **kwargs: object,
    ) -> None:
        """Replace the complete transport snapshot in one operation."""

        self.install_transport(gateway, entries, **kwargs)

    def refresh_transport(
        self,
        gateway: ModelGatewayPort,
        entries: Mapping[TransportKey, ResolvedTransportEntry]
        | Sequence[ResolvedTransportEntry],
        **kwargs: object,
    ) -> None:
        """Compatibility name for a complete validated transport refresh."""

        self.rebind_transport(gateway, entries, **kwargs)

    def invalidate_transport(
        self,
        key: TransportKey | None = None,
        *,
        provider_id: str | None = None,
        endpoint_id: str | None = None,
        credential_ref: str | None = None,
        connection_id: str | None = None,
        reason_code: str = "PROVIDER_CONNECTION_UNVERIFIED",
    ) -> None:
        """Invalidate one connection binding while retaining unrelated entries."""

        with self._lock:
            snapshot = self._transport_binding
            gateway = self._gateway
        if snapshot is None or gateway is None:
            return
        if connection_id is not None:
            matched = any(
                entry.connection_id == connection_id for entry in snapshot.entries
            )
            if not matched:
                return
            remaining = tuple(
                entry
                for entry in snapshot.entries
                if entry.connection_id != connection_id
            )
        else:
            target = key or (provider_id, endpoint_id, credential_ref)
            if any(item is None for item in target):
                raise ValueError("transport invalidation key is incomplete")
            remaining = tuple(entry for entry in snapshot.entries if entry.key != target)
        if remaining:
            install_entries = getattr(gateway, "install_transport_entries", None)
            if callable(install_entries):
                install_entries(remaining)
            with self._lock:
                self._transport_binding = GatewayTransportBinding.create(gateway, remaining)
                self._failure_code = reason_code
            return
        self.invalidate(reason_code)

    def install_routed(
        self,
        gateway: ModelGatewayPort,
        policy: ModelPolicy,
        *,
        management: object,
        available_credential_refs: Callable[[], tuple[str, ...]],
    ) -> None:
        """Install one atomic gateway while keeping routing in the same port."""

        self.install(gateway, policy)
        with self._lock:
            self._legacy_routing_context = (management, available_credential_refs)

    def invalidate(self, reason_code: str = "PROVIDER_NOT_CONFIGURED") -> None:
        with self._lock:
            self._gateway = None
            self._transport_binding = None
            self._legacy_policy = None
            self._failure_code = reason_code
            self._legacy_routing_context = None
            self._endpoint_id = None
            self._credential_ref = None
            self._model_profile_id = None

    def prepare_bound_transport(self, identity: BoundModelIdentity, request: ModelRequest) -> tuple:
        """Hold the same immutable transport binding used for bound execution."""
        with self._lock:
            snapshot = self._transport_binding
        if snapshot is None:
            from embedded_copilot.model_gateway.exceptions import (
                ModelGatewayRejected,
            )
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        result = cast(_PreparedTransportGatewayPort, snapshot.gateway).prepare_bound_transport(identity, request)
        with self._lock:
            if self._transport_binding is not snapshot:
                from embedded_copilot.model_gateway.exceptions import (
                ModelGatewayRejected,
            )
                raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        entry = snapshot.resolve((identity.provider_id, identity.endpoint_id, identity.credential_ref))
        if result[1] is not entry:
            from embedded_copilot.model_gateway.exceptions import (
                ModelGatewayRejected,
            )
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        return result

    def _request(self, request: ModelRequest) -> tuple[ModelGatewayPort, ModelRequest]:
        with self._lock:
            gateway = self._gateway
            policy = self._legacy_policy
            routing_context = self._legacy_routing_context
            endpoint_id = self._endpoint_id
            credential_ref = self._credential_ref
            model_profile_id = self._model_profile_id
        if gateway is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            raise ModelGatewayUnavailable()
        if policy is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            error = ModelGatewayUnavailable("LEGACY_POLICY_UNAVAILABLE")
            error.reason_code = "LEGACY_POLICY_UNAVAILABLE"
            raise error
        selected_model: ModelProfile | None = None
        if routing_context is not None:
            management, available_refs = routing_context
            _, selected_model = management.route_model_request(
                request,
                available_credential_refs=tuple(available_refs()),
            )
            policy = ModelPolicy(
                primary_provider=selected_model.provider_id,
                model_name=selected_model.remote_model_id,
                temperature=policy.temperature,
                max_output_tokens=policy.max_output_tokens,
            )
        material = copy.deepcopy(request.model_dump(mode="python"))
        material["model_policy"] = policy.model_dump(mode="python")
        if selected_model is not None:
            material["endpoint_id"] = selected_model.endpoint_id
            material["credential_ref"] = next(
                (
                    item.credential_ref
                    for item in management.list_endpoints()
                    if item.endpoint_id == selected_model.endpoint_id
                ),
                None,
            )
            material["preferred_model"] = selected_model.model_profile_id
        else:
            if endpoint_id is not None:
                material["endpoint_id"] = endpoint_id
            if credential_ref is not None:
                material["credential_ref"] = credential_ref
            if model_profile_id is not None:
                material["preferred_model"] = model_profile_id
        if material.get("system_prompt") == material.get("system_instruction"):
            material["system_prompt"] = None
        material["temperature"] = None
        material["max_tokens"] = None
        material["fingerprint"] = _AUTO
        try:
            checked = ModelRequest.model_validate(material)
        except ValidationError as error:
            error.reason_code = _request_validation_reason(error)
            _record_validation_error_vector(error)
            raise
        if selected_model is not None:
            object.__setattr__(
                checked, "preferred_model", selected_model.model_profile_id
            )
        return gateway, checked

    def generate(self, request: ModelRequest) -> ModelResponse:
        gateway, checked = self._request(request)
        response = gateway.generate(checked)
        return self._record_routed_execution(checked, response)

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        gateway, checked = self._request(request)
        response = await gateway.agenerate(checked)
        return self._record_routed_execution(checked, response)

    @staticmethod
    def _validate_bound_request_identity(identity, request: ModelRequest) -> None:
        from embedded_copilot.model_gateway import (
            BoundModelIdentity,
            ModelGatewayRejected,
        )

        if not isinstance(identity, BoundModelIdentity) or not isinstance(
            request, ModelRequest
        ):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        if (
            request.model_policy.primary_provider != identity.provider_id
            or request.endpoint_id != identity.endpoint_id
            or request.credential_ref != identity.credential_ref
            or (
                request.preferred_provider is not None
                and request.preferred_provider != identity.provider_id
            )
        ):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")

    def generate_bound(self, identity, request: ModelRequest) -> ModelResponse:
        """Execute a governance-bound identity without invoking the legacy router."""
        from embedded_copilot.model_gateway import (
            BoundModelGatewayPort,
            BoundModelIdentity,
            ModelGatewayRejected,
        )

        if not isinstance(identity, BoundModelIdentity):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        with self._lock:
            gateway = self._gateway
            binding = self._transport_binding
        if gateway is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            raise ModelGatewayUnavailable()
        entry = (
            binding.resolve(
                (identity.provider_id, identity.endpoint_id, identity.credential_ref)
            )
            if binding is not None
            else None
        )
        if binding is not None and entry is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        if binding is not None:
            self._validate_bound_request_identity(identity, request)
        if not isinstance(gateway, BoundModelGatewayPort):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        execute_snapshot = getattr(gateway, "generate_bound_from_transport", None)
        if entry is not None and callable(execute_snapshot):
            return execute_snapshot(entry, identity, request)
        return gateway.generate_bound(identity, request)

    def _counter_snapshot(self, entry):
        from embedded_copilot.model_gateway import ModelGatewayRejected
        with self._lock:
            gateway, binding = self._gateway, self._transport_binding
        if (type(gateway) is not ModelGatewayService or binding is None
                or binding.resolve(entry.key) is not entry):
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        return gateway, binding

    def _count_bound_with_metadata(self, entry, payload, *, dispatch_guard):
        from embedded_copilot.model_gateway import ModelGatewayRejected
        gateway, binding = self._counter_snapshot(entry)

        def check():
            with self._lock:
                if self._gateway is not gateway or self._transport_binding is not binding:
                    raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

        def guard(raw, target):
            check()
            dispatch_guard(raw, target)
            check()

        return gateway._count_bound_with_metadata(entry, payload, dispatch_guard=guard)

    async def _acount_bound_with_metadata(self, entry, payload, *, dispatch_guard):
        from embedded_copilot.model_gateway import ModelGatewayRejected
        gateway, binding = self._counter_snapshot(entry)

        def check():
            with self._lock:
                if self._gateway is not gateway or self._transport_binding is not binding:
                    raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

        async def guard(raw, target):
            check()
            await dispatch_guard(raw, target)
            check()

        return await gateway._acount_bound_with_metadata(entry, payload, dispatch_guard=guard)

    def _generate_bound_with_metadata(self, identity, request: ModelRequest, *, _dispatch_guard=None):
        """Keep provider transport metadata on the canonical bound path."""
        from embedded_copilot.model_gateway import (
            BoundModelGatewayPort,
            BoundModelIdentity,
            ModelGatewayRejected,
        )
        from embedded_copilot.model_gateway._transport_metadata import (
            ProviderExecutionMetadata,
            ProviderExecutionResult,
        )

        if not isinstance(identity, BoundModelIdentity):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        with self._lock:
            gateway = self._gateway
            binding = self._transport_binding
        if gateway is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            raise ModelGatewayUnavailable()
        entry = (
            binding.resolve(
                (identity.provider_id, identity.endpoint_id, identity.credential_ref)
            )
            if binding is not None
            else None
        )
        if binding is not None and entry is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        if binding is not None:
            self._validate_bound_request_identity(identity, request)
        if not isinstance(gateway, BoundModelGatewayPort):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        guard = None
        if _dispatch_guard is not None:
            if binding is None or entry is None:
                raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
            def check_budget_binding():
                with self._lock:
                    if self._gateway is not gateway or self._transport_binding is not binding:
                        raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
            def guard(*args):
                check_budget_binding()
                outcome = _dispatch_guard(*args)
                check_budget_binding()
                return outcome
        if entry is not None:
            execute_snapshot = getattr(
                gateway, "generate_bound_with_transport_metadata", None
            )
            if callable(execute_snapshot):
                try:
                    execution = execute_snapshot(entry, identity, request, **({"_dispatch_guard": guard} if guard is not None else {}))
                except ModelGatewayProviderFailure as error:
                    error.connection_id = entry.connection_id
                    raise
                if isinstance(execution, ProviderExecutionResult):
                    metadata = execution.metadata or ProviderExecutionMetadata()
                    return ProviderExecutionResult(
                        execution.response,
                        replace(metadata, connection_id=entry.connection_id),
                    )
                return execution
        if _dispatch_guard is not None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        execute_with_metadata = getattr(gateway, "_generate_bound_with_metadata", None)
        if callable(execute_with_metadata):
            try:
                execution = execute_with_metadata(identity, request)
            except ModelGatewayProviderFailure as error:
                error.connection_id = None
                raise
            if isinstance(execution, ProviderExecutionResult) and execution.metadata is not None:
                return ProviderExecutionResult(
                    execution.response,
                    replace(execution.metadata, connection_id=None),
                )
            return execution
        return ProviderExecutionResult(gateway.generate_bound(identity, request))

    async def agenerate_bound(self, identity, request: ModelRequest) -> ModelResponse:
        from embedded_copilot.model_gateway import (
            BoundModelGatewayPort,
            BoundModelIdentity,
            ModelGatewayRejected,
        )

        if not isinstance(identity, BoundModelIdentity):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        with self._lock:
            gateway = self._gateway
            binding = self._transport_binding
        if gateway is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            raise ModelGatewayUnavailable()
        entry = (
            binding.resolve(
                (identity.provider_id, identity.endpoint_id, identity.credential_ref)
            )
            if binding is not None
            else None
        )
        if binding is not None and entry is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        if binding is not None:
            self._validate_bound_request_identity(identity, request)
        if not isinstance(gateway, BoundModelGatewayPort):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        execute_snapshot = getattr(gateway, "agenerate_bound_from_transport", None)
        if entry is not None and callable(execute_snapshot):
            return await execute_snapshot(entry, identity, request)
        return await gateway.agenerate_bound(identity, request)

    async def _agenerate_bound_with_metadata(self, identity, request: ModelRequest, *, _dispatch_guard=None):
        from embedded_copilot.model_gateway import (
            BoundModelGatewayPort,
            BoundModelIdentity,
            ModelGatewayRejected,
        )
        from embedded_copilot.model_gateway._transport_metadata import (
            ProviderExecutionMetadata,
            ProviderExecutionResult,
        )

        if not isinstance(identity, BoundModelIdentity):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        with self._lock:
            gateway = self._gateway
            binding = self._transport_binding
        if gateway is None:
            from embedded_copilot.model_gateway import ModelGatewayUnavailable

            raise ModelGatewayUnavailable()
        entry = (
            binding.resolve(
                (identity.provider_id, identity.endpoint_id, identity.credential_ref)
            )
            if binding is not None
            else None
        )
        if binding is not None and entry is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        if binding is not None:
            self._validate_bound_request_identity(identity, request)
        if not isinstance(gateway, BoundModelGatewayPort):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        guard = None
        if _dispatch_guard is not None:
            if binding is None or entry is None:
                raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
            def check_budget_binding():
                with self._lock:
                    if self._gateway is not gateway or self._transport_binding is not binding:
                        raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
            async def guard(*args):
                check_budget_binding()
                outcome = await _dispatch_guard(*args)
                check_budget_binding()
                return outcome
        if entry is not None:
            execute_snapshot = getattr(
                gateway, "agenerate_bound_with_transport_metadata", None
            )
            if callable(execute_snapshot):
                try:
                    execution = await execute_snapshot(entry, identity, request, **({"_dispatch_guard": guard} if guard is not None else {}))
                except ModelGatewayProviderFailure as error:
                    error.connection_id = entry.connection_id
                    raise
                if isinstance(execution, ProviderExecutionResult):
                    metadata = execution.metadata or ProviderExecutionMetadata()
                    return ProviderExecutionResult(
                        execution.response,
                        replace(metadata, connection_id=entry.connection_id),
                    )
                return execution
        if _dispatch_guard is not None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        execute_with_metadata = getattr(gateway, "_agenerate_bound_with_metadata", None)
        if callable(execute_with_metadata):
            try:
                execution = await execute_with_metadata(identity, request)
            except ModelGatewayProviderFailure as error:
                error.connection_id = None
                raise
            if isinstance(execution, ProviderExecutionResult) and execution.metadata is not None:
                return ProviderExecutionResult(
                    execution.response,
                    replace(execution.metadata, connection_id=None),
                )
            return execution
        return ProviderExecutionResult(await gateway.agenerate_bound(identity, request))

    def _record_routed_execution(
        self, request: ModelRequest, response: ModelResponse
    ) -> ModelResponse:
        with self._lock:
            routing_context = self._legacy_routing_context
        management = routing_context[0] if routing_context is not None else None
        if management is None:
            return response
        try:
            task_type = TaskType(
                request.task_type or TaskType.GENERAL_CONVERSATION.value
            )
        except ValueError:
            task_type = TaskType.GENERAL_CONVERSATION
        try:
            strategy = (
                RoutingStrategy(request.routing_strategy)
                if request.routing_strategy
                else None
            )
        except ValueError:
            strategy = None
        # ``preferred_model`` is the canonical model-profile identity selected by
        # the router.  Protocol adapters may populate ``selected_model`` with the
        # provider's remote model name, which is a different identity and must
        # remain in ``actual_model``/``remote_model_id``.
        selected = (
            request.preferred_model
            or response.selected_model
            or request.model_policy.model_name
        )
        actual = (
            response.actual_model or response.remote_model_id or response.model_name
        )
        record = ModelExecutionRecord(
            execution_id="execution-"
            + hashlib.sha256(request.request_id.encode("utf-8")).hexdigest()[:32],
            request_id=request.request_id,
            project_id=request.project_id or request.engineering_context.project_id,
            conversation_id=request.conversation_id,
            task_type=task_type,
            routing_strategy=strategy,
            requested_model=request.preferred_model or request.model_policy.model_name,
            selected_model=selected,
            actual_model=actual,
            provider_id=response.provider_id or response.provider,
            endpoint_id=response.endpoint_id
            or request.endpoint_id
            or "endpoint-unavailable",
            credential_ref=response.credential_ref or request.credential_ref,
            required_capabilities=request.required_capabilities,
            usage=response.usage,
            latency_ms=response.latency_ms,
            status=ExecutionStatus.SUCCEEDED,
        )
        management.record_execution(record)
        if management.persistent:
            management.save()
        return response


class TransmissionApprovalError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransmissionApprovalReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", hide_input_in_errors=True
    )

    receipt_id: str
    tenant_id: str
    requester: str
    session_id: str
    project_id: str
    request_id: str
    context_fingerprint: str
    provider_configuration_fingerprint: str
    decision: TransmissionDecision
    expires_at: datetime
    consumed: bool = False
    fingerprint: str = _AUTO

    @model_validator(mode="after")
    def _verify(self) -> TransmissionApprovalReceipt:
        if self.expires_at.tzinfo is None:
            raise ValueError("approval expiry is invalid")
        expected = _fingerprint(
            self.model_dump(mode="json", exclude={"fingerprint", "consumed"})
        )
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("approval receipt fingerprint mismatch")
        return self


class TransmissionApprovalService:
    def __init__(
        self, provider: ProviderConfigurationService, *, ttl_seconds: int = 300
    ) -> None:
        self._provider = provider
        self._ttl = timedelta(seconds=ttl_seconds)
        self._lock = threading.RLock()
        self._receipts: dict[str, TransmissionApprovalReceipt] = {}

    def issue(
        self,
        *,
        tenant_id: str,
        requester: str,
        session_id: str,
        project_id: str,
        request_id: str,
        reference_fingerprints: tuple[str, ...],
        decision: TransmissionDecision,
        now: datetime | None = None,
    ) -> TransmissionApprovalReceipt:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        receipt = TransmissionApprovalReceipt(
            receipt_id="approval-" + secrets.token_hex(16),
            tenant_id=tenant_id,
            requester=requester,
            session_id=session_id,
            project_id=project_id,
            request_id=request_id,
            context_fingerprint=context_fingerprint(reference_fingerprints),
            provider_configuration_fingerprint=self._provider.configuration_fingerprint,
            decision=decision,
            expires_at=current + self._ttl,
        )
        with self._lock:
            self._receipts[receipt.receipt_id] = receipt
        return receipt

    def consume(
        self,
        *,
        tenant_id: str,
        requester: str,
        session_id: str,
        project_id: str,
        request_id: str,
        reference_fingerprints: tuple[str, ...],
        now: datetime | None = None,
    ) -> TransmissionApprovalReceipt:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        expected_context = context_fingerprint(reference_fingerprints)
        expected_provider = self._provider.configuration_fingerprint
        with self._lock:
            candidates = [
                receipt
                for receipt in self._receipts.values()
                if receipt.request_id == request_id
            ]
            if not candidates:
                raise TransmissionApprovalError(
                    "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED"
                )
            receipt = candidates[-1]
            if receipt.consumed:
                raise TransmissionApprovalError(
                    "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH"
                )
            if receipt.expires_at <= current:
                raise TransmissionApprovalError(
                    "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED"
                )
            if (
                receipt.tenant_id != tenant_id
                or receipt.requester != requester
                or receipt.session_id != session_id
                or receipt.project_id != project_id
                or receipt.context_fingerprint != expected_context
                or receipt.provider_configuration_fingerprint != expected_provider
            ):
                raise TransmissionApprovalError(
                    "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH"
                )
            object.__setattr__(receipt, "consumed", True)
            if receipt.decision is TransmissionDecision.DENIED:
                raise TransmissionApprovalError("EXTERNAL_TRANSMISSION_DENIED")
            return receipt


class ProviderConfigurationService:
    """Owns the safe provider projection and the single production binding."""

    def __init__(
        self,
        *,
        configuration_path: Path,
        credentials: CredentialProviderPort,
        http_transport: object | None = None,
        async_transport: object | None = None,
        now: Callable[[], datetime] | None = None,
        model_gateway_management: object | None = None,
        verification_store: ProviderVerificationReceiptStore | None = None,
        model_discovery: ProviderModelDiscoveryPort | None = None,
        model_catalog_ttl_seconds: float = 300.0,
    ) -> None:
        self._store = ProviderConfigurationStore(configuration_path)
        self._verification_store = verification_store or ProviderVerificationReceiptStore(
            Path(configuration_path).with_name("provider-verification.sqlite3")
        )
        self._credentials = credentials
        self._http_transport = http_transport
        self._async_transport = async_transport
        self._now = now or (lambda: datetime.now(UTC))
        self._management = model_gateway_management
        self._discovery_states: dict[str, str] = {}
        self._lock = threading.RLock()
        self.binding = ReloadableModelGateway()
        self._last_connection_diagnostic: ProviderConnectionDiagnostic | None = None
        self._last_probe_result: dict[str, object] | None = None
        self._legacy_compatibility_policy: ModelPolicy | None = None
        self._configuration = self._store.load()
        self._provider_profiles = self._store.load_profiles()
        self._connection_configurations: dict[str, ProviderConfiguration] = {}
        self._credential_cleanup_pending: set[str] = set()
        self._provider_profiles.setdefault(self._configuration.provider, self._configuration)
        self._model_pool = (
            ProviderModelPool(
                model_gateway_management,
                discovery=model_discovery or OfficialModelDiscovery(credentials, transport=http_transport),
                now=self._now,
                ttl_seconds=model_catalog_ttl_seconds,
            )
            if model_gateway_management is not None
            else None
        )
        if self._management is not None:
            self._initialize_canonical_configuration()
            self._initialize_provider_endpoints()
            self._initialize_saved_provider_connections()
            self._ensure_connection_for_configuration(self._configuration)
        self._apply_persisted_binding()

    @property
    def configuration_fingerprint(self) -> str:
        with self._lock:
            # A fingerprint query may observe current credential identity, but
            # it must not persist recovery metadata or create a credential.
            return self.projection().configuration_fingerprint

    @property
    def model_policy(self) -> ModelPolicy:
        with self._lock:
            return self.binding.model_policy

    def available_credential_refs(self) -> tuple[str, ...]:
        """Return credential references only; never expose credential values."""
        with self._lock:
            if self._management is None:
                return ()
            return self._canonical_available_credential_refs()

    def _active_connections_for_provider(
        self, provider_id: str
    ) -> tuple[GatewayProviderConnection, ...]:
        if self._management is None:
            return ()
        return tuple(
            item
            for item in self._management.list_connections()
            if item.provider_id == provider_id
            and item.status is GatewayProviderConnectionStatus.ACTIVE
        )

    def _resolve_connection(
        self,
        *,
        provider_id: str | None = None,
        connection_id: str | None = None,
        require_active: bool = True,
    ) -> GatewayProviderConnection:
        if self._management is None:
            raise ValueError("PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE")
        if connection_id is not None:
            connection = self._management.connection(connection_id.strip())
            if connection is None:
                raise ValueError("PROVIDER_CONNECTION_NOT_FOUND")
            if provider_id is not None and connection.provider_id != provider_id:
                raise ValueError("PROVIDER_CONNECTION_PROVIDER_MISMATCH")
            if require_active and connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                raise ValueError("PROVIDER_CONNECTION_DISABLED")
            return connection
        if provider_id is None:
            provider_id = self._configuration.provider
        candidates = self._active_connections_for_provider(provider_id)
        if not candidates:
            raise ValueError("PROVIDER_NOT_CONFIGURED")
        if len(candidates) != 1:
            raise ValueError("PROVIDER_CONNECTION_AMBIGUOUS")
        return candidates[0]

    def resolve_unique_active_connection(
        self, provider_id: str
    ) -> GatewayProviderConnection:
        """Resolve the legacy provider selector without choosing a connection implicitly."""

        with self._lock:
            return self._resolve_connection(provider_id=provider_id)

    def create_connection(
        self,
        *,
        provider_id: str,
        connection_name: str,
        endpoint_url: str,
        endpoint_id: str | None = None,
        protocol_family: str | None = None,
        credential_ref: str | None = None,
    ) -> GatewayProviderConnection:
        """Create one connection and its owned endpoint through the canonical service."""

        with self._lock:
            if self._management is None:
                raise ValueError("PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE")
            provider = provider_id.strip().lower()
            if provider == "custom-relay":
                provider = _custom_relay_provider_id(connection_name, endpoint_url)
            provider_profile = self._ensure_provider_profile(
                provider, display_name=connection_name
            )
            if endpoint_id is not None and self._management.endpoint_profile(endpoint_id) is not None:
                raise ValueError("ENDPOINT_ALREADY_REGISTERED")
            resolved_endpoint_id = endpoint_id or f"endpoint-{provider}-{uuid4().hex}"
            supported = tuple(provider_profile.supported_protocols)
            adapter_values = {item.value for item in SUPPORTED_PROTOCOL_FAMILIES}
            supported_values = tuple(
                getattr(item, "value", item)
                for item in supported
                if getattr(item, "value", item) in adapter_values
            )
            if not supported_values:
                if not supported:
                    supported_values = (ProtocolFamily.OPENAI_CHAT.value,)
                else:
                    raise ValueError("PROVIDER_PROTOCOL_UNSUPPORTED")
            if protocol_family is None:
                if len(supported_values) > 1:
                    raise ValueError("PROVIDER_PROTOCOL_REQUIRED")
                resolved_protocol = supported_values[0]
            else:
                resolved_protocol = protocol_family.strip().upper()
                if resolved_protocol not in supported_values:
                    raise ValueError("PROVIDER_PROTOCOL_UNSUPPORTED")
            resolved_credential_ref = credential_ref or (
                None if provider == "local" else f"credential:{provider}"
            )
            endpoint_type = (
                EndpointType.LOCAL
                if provider == "local"
                else EndpointType.RELAY
                if provider_profile.provider_kind is ProviderKind.CUSTOM
                else EndpointType.OFFICIAL
                if endpoint_url.rstrip("/")
                == (provider_profile.default_endpoint_template or "").rstrip("/")
                else EndpointType.RELAY
            )
            endpoint = EndpointProfile(
                endpoint_id=resolved_endpoint_id,
                provider_id=provider,
                display_name=f"{connection_name} endpoint",
                endpoint_type=endpoint_type,
                protocol_family=resolved_protocol,
                base_url=endpoint_url,
                credential_ref=resolved_credential_ref,
            )
            self._management.add_endpoint(endpoint)
            connection = self._management.create_connection(
                provider_id=provider,
                endpoint_id=resolved_endpoint_id,
                connection_name=connection_name,
                credential_ref=resolved_credential_ref,
                status=GatewayProviderConnectionStatus.ACTIVE,
            )
            configuration = ProviderConfiguration(
                provider=provider,
                connection_name=connection_name,
                model=None,
                base_url=endpoint.base_url,
                endpoint_id=endpoint.endpoint_id,
                credential_ref=resolved_credential_ref,
                protocol_family=resolved_protocol,
            )
            self._connection_configurations[connection.connection_id] = configuration
            self._ensure_canonical_profiles(configuration, persist_legacy=False)
            if self._management.persistent:
                self._management.save()
            return connection

    def _connection_for_endpoint(self, endpoint_id: str) -> GatewayProviderConnection | None:
        if self._management is None:
            return None
        return next(
            (
                item
                for item in self._management.list_connections()
                if item.endpoint_id == endpoint_id
            ),
            None,
        )

    def _ensure_connection_for_configuration(
        self, configuration: ProviderConfiguration
    ) -> GatewayProviderConnection | None:
        if self._management is None or configuration.endpoint_id is None:
            return None
        existing = self._connection_for_endpoint(configuration.endpoint_id)
        if existing is not None:
            return existing
        endpoint = self._management.endpoint_profile(configuration.endpoint_id)
        if endpoint is None or endpoint.provider_id != configuration.provider:
            return None
        provider = self._management.providers.get(configuration.provider)
        if provider is None:
            return None
        connection = self._management.create_connection(
            provider_id=configuration.provider,
            endpoint_id=endpoint.endpoint_id,
            connection_name=configuration.connection_name or provider.display_name,
            credential_ref=configuration.credential_ref or endpoint.credential_ref,
            status=GatewayProviderConnectionStatus.ACTIVE,
            legacy_source="provider_configuration_migration",
        )
        if self._management.persistent:
            self._management.save()
        return connection

    def _connection_credential_observation(
        self,
        connection: GatewayProviderConnection,
        *,
        create_integrity_key: bool,
    ) -> tuple[bytes | None, bool, str | None]:
        scoped_purpose = provider_connection_credential_purpose(connection.connection_id)
        try:
            credential = self._credentials.get(scoped_purpose)
        except CredentialUnavailable:
            return None, True, None
        purpose = scoped_purpose
        if credential is None:
            # The old provider purpose is readable only while the provider has
            # one active connection.  This prevents a legacy key from being
            # silently shared by a newly multi-connection provider.
            if len(self._active_connections_for_provider(connection.provider_id)) != 1:
                return None, False, None
            purpose = provider_credential_purpose(connection.provider_id)
            try:
                credential = self._credentials.get(purpose)
            except CredentialUnavailable:
                return None, True, None
        if credential is None:
            return None, False, None
        if not isinstance(credential, bytes) or not credential:
            return None, True, None
        try:
            revision = _credential_fingerprint(
                self._credentials,
                credential,
                create_key=create_integrity_key,
                credential_purpose=purpose,
            )
        except CredentialUnavailable:
            return credential, True, None
        return credential, False, revision

    def configured_provider_ids(self) -> tuple[str, ...]:
        """Return providers with a real saved connection, never preset templates."""

        with self._lock:
            if self._management is None:
                return (self._configuration.provider,)
            configured: set[str] = set()
            for connection in self._management.list_connections():
                if connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                    continue
                endpoint = self._management.endpoint_profile(connection.endpoint_id)
                if endpoint is None or not endpoint.enabled:
                    continue
                if connection.provider_id == "local":
                    configured.add(connection.provider_id)
                    continue
                try:
                    credential, unavailable, _ = self._connection_credential_observation(
                        connection, create_integrity_key=False
                    )
                except (CredentialUnavailable, RuntimeError, TypeError, ValueError):
                    continue
                if not unavailable and credential is not None:
                    configured.add(connection.provider_id)
            return tuple(sorted(configured))

    @property
    def current_provider(self) -> str:
        with self._lock:
            return self._configuration.provider

    @property
    def verification_store(self) -> ProviderVerificationReceiptStore:
        return self._verification_store

    def latest_verification(
        self,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        connection_id: str | None = None,
        include_legacy: bool = False,
    ) -> ProviderVerificationReceipt | None:
        if connection_id is not None and self._management is not None:
            connection = self._management.connection(connection_id)
            if connection is None or connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                return None
            values = self._verification_store.list_recent(
                1000,
                provider_id=connection.provider_id,
                model_id=model_id,
                connection_id=connection_id,
            )
            if values:
                return values[0]
            if include_legacy:
                return next(
                    (
                        item
                        for item in self._verification_store.list_recent(
                            1000,
                            provider_id=connection.provider_id,
                            model_id=model_id,
                        )
                        if item.connection_id is None
                        and item.endpoint_id == connection.endpoint_id
                    ),
                    None,
                )
            return None
        provider = provider_id or self._configuration.provider
        if self._management is not None and len(
            self._active_connections_for_provider(provider)
        ) > 1:
            raise ValueError("PROVIDER_CONNECTION_AMBIGUOUS")
        model = model_id
        if model is None and provider == self._configuration.provider:
            model = self._configuration.model
        if model is None:
            return self._verification_store.latest_for_provider(provider)
        return self._verification_store.latest_for_provider_model(provider, model)

    def model_pool(
        self,
        *,
        provider_id: str | None = None,
        connection_id: str | None = None,
        refresh: bool = False,
    ) -> tuple[ModelProfile, ...]:
        """Return the usable models for one exact provider/endpoint scope."""

        with self._lock:
            if self._management is None or self._model_pool is None:
                return ()
            connection, configuration = self._scoped_configuration(
                provider_id=provider_id,
                connection_id=connection_id,
            )
            endpoint = (
                self._management.endpoint_profile(configuration.endpoint_id)
                if configuration.endpoint_id
                else None
            )
            if endpoint is None:
                return ()
            if connection is not None:
                _, unavailable, credential_revision = self._connection_credential_observation(
                    connection, create_integrity_key=False
                )
            else:
                _, unavailable, credential_revision = self._canonical_credential_observation(
                    configuration.provider, create_integrity_key=False
                )
            configured = not unavailable and (
                credential_revision is not None or configuration.provider == "local"
            )
            if refresh:
                self._model_pool.refresh(
                    endpoint,
                    credential_revision=credential_revision,
                    credential_configured=configured,
                    connection_id=connection.connection_id if connection is not None else None,
                )
            return self._model_pool.usable(
                endpoint,
                credential_revision=credential_revision if configured else None,
                connection_id=connection.connection_id if connection is not None else None,
            )

    def refresh_model_pool(
        self,
        *,
        provider_id: str | None = None,
        connection_id: str | None = None,
    ) -> ModelPoolRefreshResult:
        """Force discovery for one provider and keep stale cache on failure."""

        with self._lock:
            if self._management is None or self._model_pool is None:
                raise RuntimeError("model discovery is unavailable")
            connection, configuration = self._scoped_configuration(
                provider_id=provider_id,
                connection_id=connection_id,
            )
            endpoint = (
                self._management.endpoint_profile(configuration.endpoint_id)
                if configuration.endpoint_id
                else None
            )
            if endpoint is None:
                raise ValueError("provider endpoint is not registered")
            if connection is not None:
                _, unavailable, credential_revision = self._connection_credential_observation(
                    connection, create_integrity_key=False
                )
            else:
                _, unavailable, credential_revision = self._canonical_credential_observation(
                    configuration.provider, create_integrity_key=False
                )
            result = self._model_pool.refresh(
                endpoint,
                credential_revision=credential_revision,
                credential_configured=(not unavailable and credential_revision is not None),
                connection_id=connection.connection_id if connection is not None else None,
            )
            state_key = connection.connection_id if connection is not None else configuration.provider
            self._discovery_states[state_key] = (
                "READY" if result.status == "UPDATED" else
                "STALE_WITH_CACHE" if result.status == "STALE" else "FAILED"
            )
            return result

    def credential_revision_for_provider(self, provider_id: str) -> str | None:
        """Return the current non-reversible credential identity for diagnostics."""

        with self._lock:
            _, unavailable, revision = self._canonical_credential_observation(
                provider_id.strip().lower(),
                create_integrity_key=False,
            )
            return None if unavailable else revision

    def credential_revision_for_connection(self, connection_id: str) -> str | None:
        with self._lock:
            connection = self._resolve_connection(
                connection_id=connection_id,
                require_active=False,
            )
            if connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                return None
            _, unavailable, revision = self._connection_credential_observation(
                connection, create_integrity_key=False
            )
            return None if unavailable else revision

    def _configuration_for_provider(self, provider_id: str | None) -> ProviderConfiguration:
        provider = (provider_id or self._configuration.provider).strip().lower()
        if provider_id is None:
            return self._configuration
        value = self._provider_profiles.get(provider)
        if value is not None:
            return copy.deepcopy(value)
        preset = self._provider_preset(provider)
        if preset is None:
            raise ValueError("provider profile is not configured")
        model = next(iter(official_model_catalog(provider, f"endpoint-{provider}")), None)
        if model is None:
            return ProviderConfiguration(
                provider=provider,
                model="custom-model",
                base_url="https://example.invalid/v1",
                endpoint_id=f"endpoint-{provider}",
                credential_ref=None if provider == "local" else f"credential:{provider}",
            )
        return ProviderConfiguration(
            provider=provider,
            model=None,
            base_url=preset.default_endpoint_template,
            endpoint_id=f"endpoint-{provider}",
            credential_ref=f"credential:{provider}",
        )

    def _configuration_for_connection(
        self,
        connection: GatewayProviderConnection,
        *,
        include_legacy_model: bool = False,
    ) -> ProviderConfiguration:
        """Project one connection without making legacy model state authoritative.

        Canonical connection, discovery, transport and projection paths only need
        connection-scoped endpoint and credential identity.  The optional legacy
        view is retained for the old provider selector and generic test route.
        """
        endpoint = self._management.endpoint_profile(connection.endpoint_id)
        if endpoint is None:
            raise ValueError("PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND")
        base = None
        selected = None
        if include_legacy_model:
            base = self._connection_configurations.get(connection.connection_id)
            if base is None:
                base = self._provider_profiles.get(connection.provider_id)
            if base is None and connection.provider_id == self._configuration.provider:
                base = self._configuration
            models = tuple(
                item
                for item in self._management.list_models()
                if item.provider_id == connection.provider_id
                and item.endpoint_id == connection.endpoint_id
                and item.enabled
            )
            selected = next(
                (
                    item
                    for item in models
                    if base is not None
                    and base.model is not None
                    and item.remote_model_id == base.model
                ),
                None,
            )
        return ProviderConfiguration(
            provider=connection.provider_id,
            connection_name=connection.connection_name,
            model=(
                selected.remote_model_id
                if selected is not None
                else base.model
                if base is not None
                and base.endpoint_id == endpoint.endpoint_id
                else None
            )
            if include_legacy_model
            else None,
            base_url=endpoint.base_url,
            endpoint_id=endpoint.endpoint_id,
            credential_ref=connection.credential_ref or endpoint.credential_ref,
            model_profile_id=(
                selected.model_profile_id
                if include_legacy_model and selected is not None
                else None
            ),
            protocol_family=getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
        )

    def _scoped_configuration(
        self,
        *,
        provider_id: str | None,
        connection_id: str | None,
    ) -> tuple[GatewayProviderConnection | None, ProviderConfiguration]:
        if self._management is None:
            return None, self._configuration_for_provider(provider_id)
        provider = provider_id.strip().lower() if provider_id is not None else None
        connection = self._resolve_connection(
            provider_id=provider,
            connection_id=connection_id,
        )
        return connection, self._configuration_for_connection(connection)

    def recent_verifications(
        self,
        *,
        limit: int = 100,
        provider_id: str | None = None,
        model_id: str | None = None,
        connection_id: str | None = None,
        include_legacy: bool = False,
    ) -> tuple[ProviderVerificationReceipt, ...]:
        if connection_id is not None and self._management is not None:
            connection = self._resolve_connection(
                connection_id=connection_id,
                require_active=False,
            )
            provider_id = connection.provider_id
            if include_legacy:
                values = self._verification_store.list_recent(
                    max(limit, 1000),
                    provider_id=provider_id,
                    model_id=model_id,
                )
                return tuple(
                    item
                    for item in values
                    if item.connection_id == connection_id
                    or (
                        item.connection_id is None
                        and item.endpoint_id == connection.endpoint_id
                    )
                )[:limit]
        return self._verification_store.list_recent(
            limit,
            provider_id=provider_id,
            model_id=model_id,
            connection_id=connection_id,
        )

    def recent_connection_test_history(
        self, *, connection_id: str, limit: int = 100
    ) -> tuple[ConnectionTestRecord, ...]:
        """Project append-only provider evidence into test-history records."""

        if self._management is not None:
            self._resolve_connection(connection_id=connection_id, require_active=False)
        return self._verification_store.list_test_history(connection_id, limit=limit)

    def _initialize_canonical_configuration(self) -> None:
        """Migrate the legacy projection into the existing catalog once."""

        management = self._management
        raw_configuration = self._store._read()
        legacy_model_present = isinstance(raw_configuration.get("chat_model"), str) and bool(
            str(raw_configuration.get("chat_model")).strip()
        )
        has_existing_provider_connection = any(
            item.provider_id == self._configuration.provider
            for item in management.list_connections()
        )
        preset = self._provider_preset(self._configuration.provider)
        if (
            legacy_model_present
            and not has_existing_provider_connection
            and preset is not None
            and preset.provider_kind not in {ProviderKind.CUSTOM, ProviderKind.LOCAL}
        ):
            canonical_models = official_model_catalog(
                self._configuration.provider,
                f"endpoint-{self._configuration.provider}",
            )
            registered = management.model_profile(self._configuration.model_profile_id) if self._configuration.model_profile_id else None
            if canonical_models and not (registered is not None and registered.provider_id == self._configuration.provider and registered.remote_model_id == self._configuration.model) and (
                self._configuration.model not in {item.remote_model_id for item in canonical_models}
                or self._configuration.base_url != preset.default_endpoint_template
                or self._configuration.endpoint_id not in {None, f"endpoint-{self._configuration.provider}"}
            ):
                replacement = canonical_models[0]
                self._configuration = ProviderConfiguration(
                    provider=self._configuration.provider,
                    model=replacement.remote_model_id,
                    base_url=preset.default_endpoint_template or self._configuration.base_url,
                    endpoint_id=f"endpoint-{self._configuration.provider}",
                    credential_ref=f"credential:{self._configuration.provider}",
                    model_profile_id=replacement.model_profile_id,
                    credential_fingerprint=self._configuration.credential_fingerprint,
                )
                self._store.save(self._configuration)
        # The legacy global policy is a runtime-governance input, never the
        # source of truth for the provider connection editor.  In particular,
        # a stage-fixed model must not be replaced while loading a provider.
        self._ensure_canonical_profiles(
            self._configuration,
            seed_model=legacy_model_present or not has_existing_provider_connection,
        )
        if legacy_model_present and isinstance(self._configuration.model, str):
            # Keep the old generic provider port recoverable after restart, but
            # keep this policy outside the canonical transport/bootstrap truth.
            self._legacy_compatibility_policy = ModelPolicy(
                primary_provider=self._configuration.provider,
                model_name=self._configuration.model,
            )

    def _initialize_provider_endpoints(self) -> None:
        """Create safe endpoint identities for official providers once."""

        for preset in provider_presets():
            if preset.provider_kind in {ProviderKind.CUSTOM, ProviderKind.LOCAL}:
                continue
            if preset.default_endpoint_template is None:
                continue
            if self._management.providers.get(preset.provider_id) is None:
                continue
            endpoint_id = f"endpoint-{preset.provider_id}"
            if self._management.endpoint_profile(endpoint_id) is not None:
                continue
            protocol = next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT)
            self._management.add_endpoint(
                EndpointProfile(
                    endpoint_id=endpoint_id,
                    provider_id=preset.provider_id,
                    display_name=f"{preset.display_name} endpoint",
                    endpoint_type=EndpointType.OFFICIAL,
                    protocol_family=protocol,
                    base_url=preset.default_endpoint_template,
                    credential_ref=f"credential:{preset.provider_id}",
                    health_status="UNKNOWN",
                )
            )
        if self._management.persistent:
            self._management.save()

    def _initialize_saved_provider_connections(self) -> None:
        """Restore saved custom connection identities without promoting templates."""

        for configuration in tuple(self._provider_profiles.values()):
            if configuration.provider == self._configuration.provider:
                continue
            if configuration.provider.startswith("custom-"):
                self._ensure_canonical_profiles(configuration)
        if self._management.persistent:
            self._management.save()

    @staticmethod
    def _canonical_model_id(provider: str, model: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", model.strip()).strip("-") or "model"
        return f"model-{provider}-{slug}"[:128]

    def _provider_preset(self, provider: str) -> ProviderProfile | None:
        preset = next(
            (item for item in provider_presets() if item.provider_id == provider), None
        )
        if preset is not None:
            return preset
        if provider.startswith("custom-"):
            configuration = self._provider_profiles.get(provider)
            return ProviderProfile(
                provider_id=provider,
                display_name=(configuration.connection_name if configuration else None) or provider,
                provider_kind=ProviderKind.CUSTOM,
                supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
                default_endpoint_template=None,
                model_discovery_mode=DiscoveryMode.AUTO_DISCOVERY,
            )
        return None

    def _ensure_provider_profile(
        self, provider: str, *, display_name: str | None = None
    ) -> ProviderProfile:
        management = self._management
        existing = management.providers.get(provider)
        if existing is not None:
            return existing
        preset = self._provider_preset(provider)
        if preset is None:
            preset = ProviderProfile(
                provider_id=provider,
                display_name=display_name or provider,
                provider_kind=ProviderKind.CUSTOM,
                supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
                default_endpoint_template=None,
            )
        management.add_provider(preset)
        return preset

    def _ensure_canonical_profiles(
        self,
        configuration: ProviderConfiguration,
        *,
        seed_model: bool = True,
        persist_legacy: bool = True,
    ) -> ModelProfile | None:
        """Ensure management identities, optionally preserving legacy storage.

        ``persist_legacy`` is false for canonical connection and probe paths.
        Those paths may update the management projection, but they must not call
        the JSON store writers that carry the historical selected-model fields.
        Startup migration and the legacy provider API retain the default behavior.
        """
        management = self._management
        provider = self._ensure_provider_profile(
            configuration.provider,
            display_name=configuration.connection_name,
        )
        endpoint_id = configuration.endpoint_id or f"endpoint-{configuration.provider}"
        endpoint = management.endpoint_profile(endpoint_id)
        base_url = configuration.base_url
        if endpoint is None:
            if base_url == DEEPSEEK_BASE_URL and provider.default_endpoint_template:
                base_url = provider.default_endpoint_template
            if not base_url:
                raise ValueError("provider endpoint is required")
            protocols = tuple(provider.supported_protocols)
            configured_protocol = configuration.protocol_family
            protocol = (
                ProtocolFamily(configured_protocol)
                if configured_protocol in ProtocolFamily._value2member_map_
                else protocols[0]
                if protocols
                else ProtocolFamily.OPENAI_CHAT
            )
            if protocol is ProtocolFamily.CUSTOM:
                protocol = ProtocolFamily.OPENAI_CHAT
            endpoint = EndpointProfile(
                endpoint_id=endpoint_id,
                provider_id=configuration.provider,
                display_name=f"{provider.display_name} endpoint",
                endpoint_type=(
                    EndpointType.LOCAL
                    if configuration.provider == "local"
                    else EndpointType.RELAY
                    if provider.provider_kind is ProviderKind.CUSTOM
                    else EndpointType.OFFICIAL
                ),
                protocol_family=protocol,
                base_url=base_url,
                credential_ref=(
                    None
                    if configuration.provider == "local"
                    else f"credential:{configuration.provider}"
                ),
                health_status=configuration.last_test_status.value
                if configuration.last_test_status is ProviderConnectionStatus.AVAILABLE
                else "UNKNOWN",
            )
            management.add_endpoint(endpoint)
        elif (
            (
                endpoint.base_url != base_url
                or endpoint.provider_id != configuration.provider
            )
            and self._connection_for_endpoint(endpoint.endpoint_id) is None
        ):
            endpoint = endpoint.model_copy(
                update={
                    "provider_id": configuration.provider,
                    "base_url": base_url,
                    "updated_at": self._now(),
                    "health_status": "STALE",
                }
            )
            management.upsert_endpoint(endpoint)
        self._ensure_connection_for_configuration(
            ProviderConfiguration(
                **{
                    **configuration.model_dump(mode="python"),
                    "endpoint_id": endpoint.endpoint_id,
                    "base_url": endpoint.base_url,
                    "credential_ref": endpoint.credential_ref,
                    "configuration_fingerprint": _AUTO,
                }
            )
        )
        credential_ref = endpoint.credential_ref
        if not configuration.model or not seed_model:
            if credential_ref is not None:
                management.upsert_credential(
                    CredentialProfile(
                        credential_id=f"credential-profile-{configuration.provider}",
                        provider_id=configuration.provider,
                        display_name=f"{provider.display_name} credential",
                        secret_ref=credential_ref,
                    )
                )
            if persist_legacy and (
                configuration.endpoint_id != endpoint.endpoint_id
                or configuration.credential_ref != credential_ref
                or configuration.base_url != base_url
            ):
                normalized = ProviderConfiguration(
                    **{
                        **configuration.model_dump(mode="python"),
                        "base_url": base_url,
                        "endpoint_id": endpoint.endpoint_id,
                        "credential_ref": credential_ref,
                        "configuration_fingerprint": _AUTO,
                    }
                )
                self._provider_profiles[normalized.provider] = normalized
                self._store.save_profiles(self._provider_profiles)
                if configuration.provider == self._configuration.provider:
                    self._configuration = self._store.save(normalized)
            if management.persistent:
                management.save()
            return None
        model_id = configuration.model_profile_id or self._canonical_model_id(
            configuration.provider, configuration.model
        )
        existing_model = management.model_profile(model_id)
        catalog_model = next(
            (
                item
                for item in official_model_catalog(configuration.provider, endpoint.endpoint_id)
                if item.remote_model_id == configuration.model
            ),
            None,
        )
        model = (
            existing_model
            if existing_model is not None
            and existing_model.provider_id == configuration.provider
            and existing_model.endpoint_id == endpoint.endpoint_id
            and existing_model.remote_model_id == configuration.model
            else None
        )
        if model is None:
            model = ModelProfile(
                model_profile_id=model_id,
                provider_id=configuration.provider,
                endpoint_id=endpoint.endpoint_id,
                remote_model_id=configuration.model,
                display_name=configuration.model,
                advertised_family=configuration.provider,
                capabilities=(),
                capability_sources=(),
                quality_tier=QualityTier.STANDARD,
                quality_source=QualitySource.PROVIDER_PRESET,
                billing_class=(
                    BillingClass.LOCAL
                    if configuration.provider == "local"
                    else BillingClass.UNKNOWN
                ),
                max_output=32768,
                enabled=True,
                discovery_status=DiscoveryStatus.MANUAL,
                verification_status=VerificationStatus.UNVERIFIED,
                record_provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
            )
        if catalog_model is not None:
            # The official catalog enriches metadata only.  It never supplies
            # endpoint availability or a successful capability assertion.
            model = merge_model_metadata(model, catalog_model).model_copy(
                update={"catalog_source": ModelCatalogSource.EMBRIX_OFFICIAL_CATALOG}
            )
        record_provenance = (
            model.record_provenance
            if model.record_provenance is not RecordProvenance.UNKNOWN
            else RecordProvenance.EXPLICIT_CONFIGURATION
        )
        availability = (
            ModelAvailability.AVAILABLE
            if configuration.last_test_status is ProviderConnectionStatus.AVAILABLE
            else ModelAvailability.RATE_LIMITED
            if configuration.last_test_status is ProviderConnectionStatus.RATE_LIMITED
            else model.availability_status
            if model.availability_status in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
            and model.credential_revision == configuration.credential_fingerprint
            else ModelAvailability.UNVERIFIED
        )
        model = model.model_copy(
            update={
                "model_profile_id": model_id,
                "provider_id": configuration.provider,
                "endpoint_id": endpoint.endpoint_id,
                "remote_model_id": configuration.model,
                "display_name": model.display_name or configuration.model,
                "record_provenance": record_provenance,
                # Billing comes from the canonical catalog/provider metadata;
                # configuration or a model name must never imply FREE.
                "billing_class": (
                    BillingClass.LOCAL
                    if configuration.provider == "local"
                    else model.billing_class
                ),
                "enabled": True,
                "discovery_status": model.discovery_status,
                "availability": availability,
                "availability_status": availability,
                "availability_source": (
                    ModelCatalogSource.PROVIDER_API
                    if availability in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
                    else model.availability_provenance
                ),
                "availability_provenance": (
                    ModelCatalogSource.RUNTIME_VERIFICATION
                    if availability in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
                    else model.availability_provenance
                ),
                "credential_revision": configuration.credential_fingerprint,
                "endpoint_revision": ProviderModelPool.endpoint_revision(endpoint),
                "verified_at": configuration.tested_at
                if availability in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
                else model.verified_at,
                "verification_status": (
                    VerificationStatus.VERIFIED
                    if configuration.tested_configuration_fingerprint == configuration.configuration_fingerprint
                    and configuration.last_test_status in {ProviderConnectionStatus.AVAILABLE, ProviderConnectionStatus.RATE_LIMITED}
                    else VerificationStatus.UNVERIFIED
                ),
                "metadata": (("health_status", endpoint.health_status),),
            }
        )
        management.models.upsert(model)
        if credential_ref is not None:
            management.upsert_credential(
                CredentialProfile(
                    credential_id=f"credential-profile-{configuration.provider}",
                    provider_id=configuration.provider,
                    display_name=f"{provider.display_name} credential",
                    secret_ref=credential_ref,
                )
            )
        if persist_legacy and (
            configuration.endpoint_id != endpoint.endpoint_id
            or configuration.model_profile_id != model_id
            or configuration.base_url != base_url
        ):
            normalized = ProviderConfiguration(
                provider=configuration.provider,
                connection_name=configuration.connection_name,
                model=configuration.model,
                base_url=base_url,
                last_test_status=configuration.last_test_status,
                tested_at=configuration.tested_at,
                tested_configuration_fingerprint=configuration.tested_configuration_fingerprint,
                credential_fingerprint=configuration.credential_fingerprint,
                endpoint_id=endpoint.endpoint_id,
                credential_ref=credential_ref,
                model_profile_id=model_id,
                protocol_family=configuration.protocol_family,
            )
            self._provider_profiles[normalized.provider] = normalized
            self._store.save_profiles(self._provider_profiles)
            if configuration.provider == self._configuration.provider:
                self._configuration = self._store.save(normalized)
        if management.persistent:
            management.save()
        return model

    def _canonical_credential_observation(
        self, provider: str, *, create_integrity_key: bool
    ) -> tuple[bytes | None, bool, str | None]:
        if self._management is not None:
            connections = self._active_connections_for_provider(provider)
            if len(connections) == 1:
                return self._connection_credential_observation(
                    connections[0], create_integrity_key=create_integrity_key
                )
            if len(connections) > 1:
                return None, False, None
        purpose = provider_credential_purpose(provider)
        try:
            credential = self._credentials.get(purpose)
        except CredentialUnavailable:
            return None, True, None
        if credential is None:
            return None, False, None
        if not isinstance(credential, bytes) or not credential:
            return None, True, None
        try:
            fingerprint = _credential_fingerprint(
                self._credentials,
                credential,
                create_key=create_integrity_key,
                credential_purpose=purpose,
            )
        except CredentialUnavailable:
            return credential, True, None
        return credential, False, fingerprint

    def _canonical_available_credential_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for endpoint in self._management.list_endpoints():
            if endpoint.credential_ref is None:
                continue
            connection = self._connection_for_endpoint(endpoint.endpoint_id)
            if connection is not None:
                try:
                    if self._connection_credential_observation(
                        connection, create_integrity_key=False
                    )[0] is not None:
                        refs.append(endpoint.credential_ref)
                except CredentialUnavailable:
                    return ()
                continue
            try:
                if (
                    self._credentials.get(
                        provider_credential_purpose(endpoint.provider_id)
                    )
                    is not None
                ):
                    refs.append(endpoint.credential_ref)
            except CredentialUnavailable:
                return ()
        return tuple(sorted(set(refs)))

    def _canonical_transport_binding(self) -> GatewayTransportBinding | None:
        """Build the canonical transport snapshot from active connections only.

        Connection, endpoint and credential state are the source of truth for
        this path.  Model inventory, governance and legacy configuration are
        intentionally absent from the construction loop so a transport can be
        restored before model selection has any result.
        """

        management = self._management
        if management is None:
            return None
        providers: dict[str, object] = {}
        transport_entries: list[ResolvedTransportEntry] = []
        for connection in management.list_connections():
            if connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                continue
            endpoint = management.endpoint_profile(connection.endpoint_id)
            provider = management.providers.get(connection.provider_id)
            if endpoint is None or provider is None:
                continue
            if not endpoint.enabled or not provider.enabled:
                continue
            if endpoint.provider_id != connection.provider_id:
                continue
            endpoint_protocol = endpoint.protocol_family
            protocol = endpoint_protocol
            if protocol is ProtocolFamily.CUSTOM:
                protocol = ProtocolFamily.OPENAI_CHAT
            supported = tuple(provider.supported_protocols)
            if (
                supported
                and endpoint_protocol not in supported
                and protocol not in supported
                and ProtocolFamily.CUSTOM not in supported
            ):
                continue
            credential: bytes | None = None
            unavailable = False
            credential_revision: str | None = None
            if protocol is not ProtocolFamily.LOCAL_OPENAI_COMPATIBLE:
                credential, unavailable, credential_revision = (
                    self._connection_credential_observation(
                        connection, create_integrity_key=False
                    )
                )
                # One bad connection is excluded from the snapshot; it must
                # not make a sibling connection for the same provider unusable.
                if unavailable or credential is None:
                    continue
            from embedded_copilot.model_gateway import ProtocolHTTPProviderAdapter

            try:
                api_key = (
                    None
                    if protocol is ProtocolFamily.LOCAL_OPENAI_COMPATIBLE
                    else SecretStr(credential.decode("utf-8"))
                )
                adapter = ProtocolHTTPProviderAdapter(
                    provider=connection.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    base_url=endpoint.base_url,
                    protocol_family=protocol,
                    api_key=api_key,
                    timeout_seconds=30.0,
                    http_transport=self._http_transport,
                    async_transport=self._async_transport,
                )
            except (TypeError, ValueError, UnicodeDecodeError):
                # A malformed connection is unavailable on its own; a sibling
                # connection may still form the next atomic snapshot.
                continue
            # Provider aliases remain available for legacy generic callers;
            # governed bound execution resolves the exact transport key.
            providers.setdefault(connection.provider_id, adapter)
            providers[endpoint.endpoint_id] = adapter
            credential_ref = connection.credential_ref or endpoint.credential_ref
            if credential_ref is None:
                # Local endpoints have no secret reference by design.  The
                # stable sentinel is only a transport identity, never a
                # CredentialProfile or a fabricated secret.
                if protocol is ProtocolFamily.LOCAL_OPENAI_COMPATIBLE:
                    credential_ref = "credential:local"
                else:
                    continue
            transport_entries.append(
                ResolvedTransportEntry(
                    provider_id=connection.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    credential_ref=credential_ref,
                    adapter=adapter,
                    endpoint_revision=ProviderModelPool.endpoint_revision(endpoint),
                    credential_revision=credential_revision,
                    connection_id=connection.connection_id,
                    protocol_metadata=(
                        (
                            "protocol_family",
                            str(
                                protocol.value
                                if isinstance(protocol, ProtocolFamily)
                                else protocol
                            ),
                        ),
                    ),
                )
            )
        if not transport_entries:
            return None
        gateway = ModelGatewayService(
            providers=providers,
            transport_entries=transport_entries,
        )
        return GatewayTransportBinding.create(gateway, transport_entries)

    def _canonical_gateway(self) -> tuple[ModelGatewayService, ModelPolicy] | None:
        """Return the legacy policy view over the canonical transport snapshot."""

        snapshot = self._canonical_transport_binding()
        if snapshot is None:
            return None
        gateway = snapshot.gateway
        active_id = self._management.global_policy().active_model_profile_id
        model = self._management.model_profile(active_id) if active_id else None
        if model is None and self._configuration.model_profile_id:
            # Compatibility binding for callers that still use the legacy
            # provider port directly.  Production invocation is governed by
            # ModelInvocationService and does not derive selection here.
            model = self._management.model_profile(self._configuration.model_profile_id)
        if model is None or model.provider_id not in {
            entry.provider_id for entry in snapshot.entries
        }:
            return None
        return (
            gateway,
            ModelPolicy(
                primary_provider=model.provider_id, model_name=model.remote_model_id
            ),
        )

    def _refresh_canonical_binding(self, *, transport_only: bool = True) -> None:
        if transport_only:
            snapshot = self._canonical_transport_binding()
            if snapshot is None:
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
                return
            # Canonical bootstrap restores only connection-scoped transport.
            # Preserve an already-installed legacy policy for old generic
            # callers, but never reconstruct it from selected/default model
            # fields while restoring the canonical transport snapshot.
            legacy_policy: ModelPolicy | None = None
            try:
                existing_policy = self.binding.model_policy
            except ModelGatewayError:
                existing_policy = None
            if existing_policy is None:
                existing_policy = self._legacy_compatibility_policy
            if existing_policy is not None and any(
                item.provider_id == existing_policy.primary_provider
                for item in snapshot.entries
            ):
                legacy_policy = existing_policy
            compatibility_entry = (
                next(
                    (
                        item
                        for item in snapshot.entries
                        if legacy_policy is not None
                        and item.provider_id == legacy_policy.primary_provider
                    ),
                    None,
                )
                if legacy_policy is not None
                else None
            )
            self.binding.install_transport(
                snapshot,
                legacy_policy=legacy_policy,
                endpoint_id=(
                    compatibility_entry.endpoint_id
                    if compatibility_entry is not None
                    else None
                ),
                credential_ref=(
                    compatibility_entry.credential_ref
                    if compatibility_entry is not None
                    else None
                ),
                model_profile_id=None,
            )
            return
        endpoint = (
            self._management.endpoint_profile(self._configuration.endpoint_id)
            if self._configuration.endpoint_id
            else None
        )
        if (
            self._configuration.last_test_status
            is not ProviderConnectionStatus.AVAILABLE
            or endpoint is None
            or endpoint.health_status != "AVAILABLE"
            or self._configuration.tested_configuration_fingerprint
            != self._configuration.configuration_fingerprint
        ):
            self.binding.invalidate(self._configuration.last_test_status.value)
            return
        result = self._canonical_gateway()
        if result is None:
            self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            return
        gateway, policy = result
        # The provider port is a bound compatibility transport only.  Runtime
        # selection is performed by ModelInvocationService before it reaches
        # generate_bound(); keeping management out of this binding prevents a
        # legacy router from overriding a stage-fixed identity.
        self.binding.install_transport(
            gateway,
            gateway.transport_entries,
            legacy_policy=policy,
            endpoint_id=self._configuration.endpoint_id,
            credential_ref=self._configuration.credential_ref,
            model_profile_id=self._configuration.model_profile_id,
        )

    def projection(
        self,
        *,
        provider_id: str | None = None,
        connection_id: str | None = None,
        include_legacy_model: bool = False,
    ) -> ProviderProjection:
        with self._lock:
            if self._management is not None:
                if connection_id is not None:
                    connection = self._resolve_connection(
                        connection_id=connection_id,
                        require_active=False,
                    )
                    configuration = self._configuration_for_connection(
                        connection,
                        include_legacy_model=include_legacy_model,
                    )
                    return self._canonical_projection(configuration, connection=connection)
                if provider_id is not None:
                    connection = self._resolve_connection(provider_id=provider_id)
                    return self._canonical_projection(
                        self._configuration_for_connection(
                            connection,
                            include_legacy_model=include_legacy_model,
                        ),
                        connection=connection,
                    )
                configuration = self._configuration_for_provider(provider_id)
                return self._canonical_projection(configuration)
            credential, unavailable, current_credential_fingerprint = (
                self._credential_observation(create_integrity_key=False)
            )
            configuration = self._configuration
            credential_status = (
                "CONFIGURED" if credential is not None else "NOT_CONFIGURED"
            )
            effective = configuration
            if unavailable:
                credential_status = "UNAVAILABLE"
                effective = self._candidate(
                    status=ProviderConnectionStatus.UNVERIFIED,
                    credential_fingerprint=configuration.credential_fingerprint,
                )
            elif credential is None:
                effective = self._candidate(
                    status=ProviderConnectionStatus.NOT_CONFIGURED,
                    credential_fingerprint=None,
                )
            elif current_credential_fingerprint is None:
                effective = self._candidate(
                    status=ProviderConnectionStatus.UNVERIFIED,
                    credential_fingerprint=configuration.credential_fingerprint,
                )
            elif current_credential_fingerprint != configuration.credential_fingerprint:
                recovery_emit("CREDENTIAL_RECOVERY_BINDING_MISMATCH", result="MISMATCH")
                effective = self._candidate(
                    status=ProviderConnectionStatus.UNVERIFIED,
                    credential_fingerprint=current_credential_fingerprint,
                )
            if unavailable:
                recovery_emit(
                    "CREDENTIAL_RECOVERY_PROJECTION_UNAVAILABLE", result="UNAVAILABLE"
                )
            elif credential is None:
                recovery_emit(
                    "CREDENTIAL_RECOVERY_PROJECTION_UNCONFIGURED", result="UNCONFIGURED"
                )
            else:
                recovery_emit(
                    "CREDENTIAL_RECOVERY_PROJECTION_CONFIGURED", result="CONFIGURED"
                )
            model_available = (
                effective.model_available is True
                and effective.last_test_status
                in {
                    ProviderConnectionStatus.AVAILABLE,
                    ProviderConnectionStatus.RATE_LIMITED,
                }
                and effective.tested_configuration_fingerprint
                == effective.configuration_fingerprint
            )
            return ProviderProjection(
                provider=effective.provider,
                model=effective.model,
                connection=(
                    "verified"
                    if effective.last_test_status is ProviderConnectionStatus.AVAILABLE
                    and model_available
                    else "failed"
                    if effective.last_test_status
                    not in {
                        ProviderConnectionStatus.UNVERIFIED,
                        ProviderConnectionStatus.NOT_CONFIGURED,
                    }
                    else "not_configured"
                    if effective.last_test_status is ProviderConnectionStatus.NOT_CONFIGURED
                    else "unverified"
                ),
                base_url=effective.base_url,
                credential_status=credential_status,
                connection_status=effective.last_test_status,
                last_test_status=effective.last_test_status,
                tested_at=effective.tested_at,
                updated_at=self._store.updated_at,
                validation_status=effective.last_test_status.value,
                configuration_fingerprint=effective.configuration_fingerprint,
                tested_configuration_fingerprint=effective.tested_configuration_fingerprint,
                protocol_family=None,
                model_id=effective.model,
                verified_model=effective.verified_model,
                available_models=effective.available_models,
                model_available=model_available,
                response_model=effective.response_model,
                response_id=effective.response_id,
                latency_ms=effective.latency_ms,
                http_status=effective.http_status,
                protocol_adapter=effective.protocol_adapter,
                verification_id=effective.verification_id,
                verification_status=effective.verification_status,
                verification_persisted=effective.verification_persisted,
                verification_persistence_error=effective.verification_persistence_error,
                connection_diagnostic=self._last_connection_diagnostic,
            )

    def _canonical_projection(
        self,
        configuration: ProviderConfiguration | None = None,
        *,
        connection: GatewayProviderConnection | None = None,
    ) -> ProviderProjection:
        configuration = configuration or self._configuration
        if connection is not None:
            credential, unavailable, current_fingerprint = self._connection_credential_observation(
                connection, create_integrity_key=False
            )
        else:
            credential, unavailable, current_fingerprint = self._canonical_credential_observation(
                configuration.provider, create_integrity_key=False
            )
        credential_status = (
            "CONFIGURED"
            if credential is not None or configuration.provider == "local"
            else "NOT_CONFIGURED"
        )
        status = configuration.last_test_status
        endpoint = (
            self._management.endpoint_profile(configuration.endpoint_id)
            if configuration.endpoint_id
            else None
        )
        probe = (
            self._last_probe_result
            if self._last_probe_result is not None
            and self._last_probe_result.get("provider_id") == configuration.provider
            and self._last_probe_result.get("endpoint_id") == configuration.endpoint_id
            else None
        )
        projected_model = (
            probe.get("model_id") if probe is not None else configuration.model
        )
        projected_model_profile_id = (
            probe.get("model_profile_id")
            if probe is not None
            else configuration.model_profile_id
        )
        projected_configuration_fingerprint = (
            probe.get("configuration_fingerprint")
            if probe is not None and probe.get("configuration_fingerprint") is not None
            else configuration.configuration_fingerprint
        )
        if unavailable:
            credential_status = "UNAVAILABLE"
            status = ProviderConnectionStatus.UNVERIFIED
        elif configuration.provider != "local" and credential is None:
            status = ProviderConnectionStatus.NOT_CONFIGURED
        elif status is ProviderConnectionStatus.AVAILABLE and (
            current_fingerprint is None
            and configuration.provider != "local"
            or configuration.provider != "local"
            and current_fingerprint != configuration.credential_fingerprint
            or (
                endpoint is None
                or endpoint.health_status != "AVAILABLE"
                or configuration.tested_configuration_fingerprint
                != configuration.configuration_fingerprint
            )
        ):
            status = ProviderConnectionStatus.UNVERIFIED
        if probe is not None and not unavailable and (
            configuration.provider == "local" or credential is not None
        ):
            status = probe["status"]
        model_available = (
            (
                probe.get("model_available") is True
                if probe is not None
                else configuration.model_available is True
            )
            and status
            in {
                ProviderConnectionStatus.AVAILABLE,
                ProviderConnectionStatus.RATE_LIMITED,
            }
            and configuration.tested_configuration_fingerprint
            == configuration.configuration_fingerprint
        )
        return ProviderProjection(
            provider=configuration.provider,
            model=projected_model or "",
            configuration_state=(
                "UNCONFIGURED" if credential_status != "CONFIGURED" else
                "FULLY_CONFIGURED"
            ),
            discovery_state=self._discovery_states.get(
                connection.connection_id if connection is not None else configuration.provider,
                "NOT_REQUESTED",
            ),
            discovery_required=self._discovery_states.get(
                connection.connection_id if connection is not None else configuration.provider
            ) == "STALE",
            connection=(
                "verified"
                if status is ProviderConnectionStatus.AVAILABLE and model_available
                else "failed"
                if status
                not in {
                    ProviderConnectionStatus.UNVERIFIED,
                    ProviderConnectionStatus.NOT_CONFIGURED,
                }
                else "not_configured"
                if status is ProviderConnectionStatus.NOT_CONFIGURED
                else "unverified"
            ),
            base_url=configuration.base_url,
            credential_status=credential_status,
            connection_status=status,
            last_test_status=status,
            tested_at=configuration.tested_at,
            updated_at=self._store.updated_at,
            validation_status=status.value,
            configuration_fingerprint=projected_configuration_fingerprint,
            tested_configuration_fingerprint=(
                projected_configuration_fingerprint
                if probe is not None
                and status in {
                    ProviderConnectionStatus.AVAILABLE,
                    ProviderConnectionStatus.RATE_LIMITED,
                }
                else configuration.tested_configuration_fingerprint
            ),
            endpoint_id=configuration.endpoint_id,
            credential_ref=configuration.credential_ref,
            protocol_family=(
                endpoint.protocol_family.value
                if endpoint is not None and isinstance(endpoint.protocol_family, ProtocolFamily)
                else endpoint.protocol_family
                if endpoint is not None
                else None
            ),
            model_profile_id=projected_model_profile_id,
            model_id=projected_model,
            verified_model=(
                probe.get("verified_model") if probe is not None else configuration.verified_model
            ),
            available_models=(
                probe.get("available_models", ())
                if probe is not None
                else configuration.available_models
            ),
            model_pool=self._model_pool_projection(configuration, connection=connection),
            provider_profiles=self._provider_profile_projections(),
            active_provider_id=self._configuration.provider,
            model_available=model_available,
            response_model=(
                probe.get("response_model") if probe is not None else configuration.response_model
            ),
            response_id=(
                probe.get("response_id") if probe is not None else configuration.response_id
            ),
            latency_ms=(probe.get("latency_ms") if probe is not None else configuration.latency_ms),
            http_status=(probe.get("http_status") if probe is not None else configuration.http_status),
            protocol_adapter=(
                probe.get("protocol_adapter") if probe is not None else configuration.protocol_adapter
            ),
            verification_id=(
                receipt.verification_id
                if probe is not None and (receipt := self.latest_verification(
                    provider_id=configuration.provider,
                    model_id=projected_model,
                    connection_id=connection.connection_id if connection is not None else None,
                )) is not None
                else configuration.verification_id
            ),
            verification_status=(
                "PASS" if probe is not None and status in {
                    ProviderConnectionStatus.AVAILABLE,
                    ProviderConnectionStatus.RATE_LIMITED,
                } else configuration.verification_status
            ),
            verification_persisted=(
                probe.get("verification_persisted")
                if probe is not None
                else configuration.verification_persisted
            ),
            verification_persistence_error=(
                probe.get("verification_persistence_error")
                if probe is not None
                else configuration.verification_persistence_error
            ),
            connection_diagnostic=(
                self._last_connection_diagnostic
                if configuration.provider == self._configuration.provider
                else None
            ),
        )

    def _model_pool_projection(
        self,
        configuration: ProviderConfiguration,
        *,
        connection: GatewayProviderConnection | None = None,
    ) -> tuple[ProviderModelProjection, ...]:
        if self._model_pool is None:
            return ()
        try:
            models = self.model_pool(
                provider_id=configuration.provider,
                connection_id=connection.connection_id if connection is not None else None,
            )
        except (TypeError, ValueError, RuntimeError):
            return ()
        credential_ref = configuration.credential_ref or "credential:local"

        def project(item: ModelProfile) -> ProviderModelProjection:
            can_enable, reason, identity_status = self._management.model_enablement(
                BoundModelIdentity(
                    provider_id=item.provider_id,
                    endpoint_id=item.endpoint_id,
                    credential_ref=credential_ref,
                    model_profile_id=item.model_profile_id,
                    model_id=item.remote_model_id,
                )
            )
            return ProviderModelProjection(
                provider_id=item.provider_id,
                endpoint_id=item.endpoint_id,
                credential_ref=credential_ref,
                model_profile_id=item.model_profile_id,
                model_id=item.remote_model_id,
                display_name=item.display_name,
                availability=item.availability_status,
                availability_status=item.availability_status.value,
                availability_source=item.availability_source,
                availability_provenance=item.availability_provenance,
                catalog_source=item.catalog_source,
                record_provenance=item.record_provenance,
                identity_status=identity_status,
                verification_status=(
                    item.verification_status
                    if isinstance(item.verification_status, VerificationStatus)
                    else VerificationStatus(item.verification_status)
                ),
                can_enable=can_enable,
                enable_block_reason=reason,
                billing_class=item.billing_class,
                billing_provenance=item.billing_provenance.value,
                billing_source=item.billing_source,
                billing_label=(
                    "免费" if item.billing_class is BillingClass.FREE
                    else "付费" if item.billing_class in {BillingClass.PAID, BillingClass.METERED, BillingClass.SUBSCRIPTION}
                    else "费用未知"
                ),
                lifecycle_status=item.lifecycle_status,
                deprecated=item.deprecated,
                officially_confirmed=(
                    item.catalog_source is ModelCatalogSource.PROVIDER_API
                    and item.availability_provenance is ModelCatalogSource.PROVIDER_API
                ),
                metadata_updated_at=item.metadata_updated_at,
                metadata_source=item.metadata_source,
                capabilities=item.capabilities,
                verified_at=item.verified_at,
            )
        return tuple(project(item) for item in models)

    def _provider_profile_projections(self) -> tuple[ProviderProfileProjection, ...]:
        if self._management is None:
            return ()
        result: list[ProviderProfileProjection] = []
        for provider in self._management.list_providers():
            configuration = self._provider_profiles.get(provider.provider_id)
            if configuration is None and provider.provider_id == self._configuration.provider:
                configuration = self._configuration
            credential_status = "NOT_CONFIGURED"
            selected_model: str | None = None
            connection_status = ProviderConnectionStatus.NOT_CONFIGURED.value
            usable_count = 0
            discovered_count = 0
            if configuration is not None:
                # The provider profile projection is intentionally connection
                # scoped.  A tested model is probe evidence, not a runtime
                # selection or provider default.
                selected_model = None
                connection_status = configuration.last_test_status.value
                try:
                    credential, unavailable, credential_fingerprint = self._canonical_credential_observation(
                        provider.provider_id, create_integrity_key=False
                    )
                    if unavailable:
                        credential_status = "UNAVAILABLE"
                    elif credential is not None or provider.provider_id == "local":
                        credential_status = "CONFIGURED"
                    endpoint = (
                        self._management.endpoint_profile(configuration.endpoint_id)
                        if configuration.endpoint_id
                        else None
                    )
                    if endpoint is not None:
                        endpoint_revision = ProviderModelPool.endpoint_revision(endpoint)
                        discovered_count = sum(
                            1
                            for item in self._management.list_models()
                            if item.provider_id == provider.provider_id
                            and item.endpoint_id == endpoint.endpoint_id
                            and item.record_provenance is RecordProvenance.ENDPOINT_DISCOVERY
                            and item.endpoint_revision == endpoint_revision
                            and item.credential_revision == credential_fingerprint
                        )
                        usable_count = sum(
                            1
                            for item in self.model_pool(provider_id=provider.provider_id)
                            if self._management.model_enablement(
                                BoundModelIdentity(
                                    provider_id=item.provider_id,
                                    endpoint_id=item.endpoint_id,
                                    credential_ref=endpoint.credential_ref or "credential:local",
                                    model_profile_id=item.model_profile_id,
                                    model_id=item.remote_model_id,
                                )
                            )[0]
                        )
                except (TypeError, ValueError, RuntimeError):
                    credential_status = "UNAVAILABLE"
            result.append(
                ProviderProfileProjection(
                    provider_id=provider.provider_id,
                    configured=(
                        configuration is not None
                        and credential_status == "CONFIGURED"
                    ),
                    selected_model=selected_model,
                    credential_status=credential_status,
                    connection_status=connection_status,
                    discovered_model_count=discovered_count,
                    usable_model_count=usable_count,
                )
            )
        return tuple(result)

    def save_configuration(
        self,
        *,
        provider: str,
        connection_name: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        endpoint_id: str | None = None,
        model_profile_id: str | None = None,
        credential_ref: str | None = None,
        protocol_family: str | None = None,
        configuration_fingerprint: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        with self._lock:
            if model is not None and (not isinstance(model, str) or not model.strip()):
                raise ValueError("MODEL_REQUIRED")
            if self._management is not None:
                return self._canonical_save_configuration(
                    provider=provider,
                    connection_name=connection_name,
                    model=model,
                    base_url=base_url,
                    endpoint_id=endpoint_id,
                    model_profile_id=model_profile_id,
                    credential_ref=credential_ref,
                    protocol_family=protocol_family,
                    configuration_fingerprint=configuration_fingerprint,
                    connection_id=connection_id,
                )
            if provider.strip().lower() != ProductionProvider.DEEPSEEK.value:
                raise ValueError(
                    "provider is unsupported without canonical model gateway management"
                )
            emit("CONFIGURATION_SERVICE_ENTERED", result="ENTERED")
            credential, unavailable, credential_fingerprint = (
                self._credential_observation(create_integrity_key=True)
            )
            if unavailable:
                self._append_verification_receipt(
                    ProviderConnectionStatus.UNVERIFIED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.UNVERIFIED,
                        provider=self._configuration.provider,
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                        provider_error_code="CREDENTIAL_STORE_UNAVAILABLE",
                    ),
                    endpoint=None,
                    requested_model=self._configuration.model,
                )
                raise CredentialUnavailable("provider credential store is unavailable")
            candidate = ProviderConfiguration(
                provider=provider,
                model=model,
                base_url=DEEPSEEK_BASE_URL,
                credential_fingerprint=credential_fingerprint,
            )
            if (
                candidate.configuration_fingerprint
                == self._configuration.configuration_fingerprint
            ):
                # Preserve the existing verification evidence on an idempotent
                # save; the explicit save may still repair a missing file.
                self._configuration = self._store.save(self._configuration)
                return self.projection()
            persisted = self._store.save(candidate)
            self._configuration = persisted
            self.binding.invalidate(
                "PROVIDER_CONNECTION_UNVERIFIED"
                if credential is not None
                else "PROVIDER_NOT_CONFIGURED"
            )
            return self.projection()

    def _commit_connection_configuration(
        self,
        configuration: ProviderConfiguration,
        connection: GatewayProviderConnection,
        *,
        persist_legacy: bool = False,
    ) -> ProviderProjection:
        """Commit a connection edit with an explicit legacy-writer boundary.

        The v70 provider selector may still persist its historical selected
        model.  Canonical connection management passes ``persist_legacy=False``
        (the canonical routes currently use the dedicated connection methods).
        """

        previous_configuration = self._configuration
        previous_profiles = copy.deepcopy(self._provider_profiles)
        self._connection_configurations[connection.connection_id] = configuration
        try:
            self._ensure_canonical_profiles(
                configuration,
                persist_legacy=persist_legacy,
            )
        finally:
            if not persist_legacy:
                self._configuration = previous_configuration
                self._provider_profiles = previous_profiles
        if self._management is not None and self._management.persistent:
            self._management.save()
        if self._model_pool is not None:
            self._model_pool.invalidate(
                configuration.provider,
                connection_id=connection.connection_id,
                endpoint_id=connection.endpoint_id,
            )
        self.binding.invalidate_transport(
            connection_id=connection.connection_id,
            reason_code="PROVIDER_CONNECTION_UNVERIFIED",
        )
        return self._canonical_projection(configuration, connection=connection)

    def _canonical_save_configuration(
        self,
        *,
        provider: str,
        connection_name: str | None,
        model: str | None,
        base_url: str | None,
        endpoint_id: str | None,
        model_profile_id: str | None,
        credential_ref: str | None,
        protocol_family: str | None,
        configuration_fingerprint: str | None,
        connection_id: str | None,
    ) -> ProviderProjection:
        provider = provider.strip().lower()
        normalized_connection_name = _connection_name(connection_name)
        if provider == _CUSTOM_RELAY_TEMPLATE_ID:
            if normalized_connection_name is None:
                raise ValueError("CUSTOM_CONNECTION_NAME_REQUIRED")
            if base_url is None:
                raise ValueError("provider endpoint is required")
            provider = _custom_relay_provider_id(normalized_connection_name, base_url)
        elif provider.startswith("custom-"):
            normalized_connection_name = normalized_connection_name or (
                self._provider_profiles.get(provider).connection_name
                if self._provider_profiles.get(provider) is not None
                else None
            )
            if normalized_connection_name is None:
                raise ValueError("CUSTOM_CONNECTION_NAME_REQUIRED")
        selected_connection = None
        replaced_legacy_connection = None
        if connection_id is not None:
            selected_connection = self._resolve_connection(
                provider_id=provider,
                connection_id=connection_id,
            )
        elif self._active_connections_for_provider(provider):
            selected_connection = self._resolve_connection(provider_id=provider)
            if (
                connection_id is None
                and endpoint_id is not None
                and selected_connection.endpoint_id != endpoint_id
            ):
                replaced_legacy_connection = selected_connection
                selected_connection = None
        preset = self._provider_preset(provider)
        if preset is None:
            raise ValueError("provider is unsupported")
        is_official = preset.provider_kind not in {ProviderKind.CUSTOM, ProviderKind.LOCAL}
        preset_url = preset.default_endpoint_template
        if selected_connection is not None:
            _, unavailable, credential_fingerprint = self._connection_credential_observation(
                selected_connection, create_integrity_key=True
            )
        else:
            _, unavailable, credential_fingerprint = self._canonical_credential_observation(
                provider, create_integrity_key=True
            )
        if unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        resolved_endpoint = endpoint_id or (
            selected_connection.endpoint_id
            if selected_connection is not None
            else f"endpoint-{provider}"
        )
        if selected_connection is not None and resolved_endpoint != selected_connection.endpoint_id:
            raise ValueError("PROVIDER_CONNECTION_ENDPOINT_MISMATCH")
        if is_official and resolved_endpoint != f"endpoint-{provider}" and base_url is None:
            raise ValueError("provider endpoint is required")
        if is_official and resolved_endpoint == f"endpoint-{provider}":
            if base_url is not None and base_url.rstrip("/") != (preset_url or "").rstrip("/"):
                raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
            resolved_url = preset_url
        else:
            resolved_url = base_url or preset_url
        if resolved_url is None:
            raise ValueError("provider endpoint is required")
        existing_endpoint = self._management.endpoint_profile(resolved_endpoint)
        if existing_endpoint is not None and existing_endpoint.provider_id != provider:
            raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
        existing_profile = self._provider_profiles.get(provider)
        normalized_model = model.strip() if isinstance(model, str) else None
        if (
            is_official
            and existing_profile is not None
            and existing_profile.model is None
            and normalized_model is not None
            and (
                credential_fingerprint is None
                or not any(
                    item.remote_model_id == normalized_model
                    for item in (
                        self.model_pool(
                            provider_id=provider,
                            connection_id=selected_connection.connection_id,
                        )
                        if selected_connection is not None
                        else ()
                    )
                )
            )
        ):
            raise ValueError("PROVIDER_MODEL_NOT_USABLE")
        if normalized_model is None:
            expected_credential_ref = (
                selected_connection.credential_ref
                if selected_connection is not None
                else None if provider == "local" else f"credential:{provider}"
            )
            if credential_ref is not None and credential_ref != expected_credential_ref:
                raise ValueError("PROVIDER_CREDENTIAL_REF_MISMATCH")
            if protocol_family is not None:
                expected_protocol = next(
                    iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT
                )
                if protocol_family != getattr(expected_protocol, "value", expected_protocol):
                    raise ValueError("PROVIDER_PROTOCOL_MISMATCH")
            candidate = ProviderConfiguration(
                provider=provider,
                connection_name=normalized_connection_name,
                model=None,
                base_url=resolved_url,
                endpoint_id=resolved_endpoint,
                model_profile_id=None,
                credential_ref=expected_credential_ref,
                last_test_status=(
                    existing_profile.last_test_status
                    if existing_profile is not None
                    and existing_profile.model is None
                    and existing_profile.base_url == resolved_url
                    and existing_profile.credential_fingerprint == credential_fingerprint
                    else ProviderConnectionStatus.UNVERIFIED
                ),
                tested_at=(
                    existing_profile.tested_at
                    if existing_profile is not None
                    and existing_profile.model is None
                    and existing_profile.base_url == resolved_url
                    and existing_profile.credential_fingerprint == credential_fingerprint
                    else None
                ),
                tested_configuration_fingerprint=(
                    existing_profile.tested_configuration_fingerprint
                    if existing_profile is not None
                    and existing_profile.model is None
                    and existing_profile.base_url == resolved_url
                    and existing_profile.credential_fingerprint == credential_fingerprint
                    else None
                ),
                credential_fingerprint=credential_fingerprint,
                protocol_family=(
                    protocol_family
                    if protocol_family is not None
                    else getattr(next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT), "value", next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT))
                ),
            )
            if configuration_fingerprint is not None and candidate.configuration_fingerprint != configuration_fingerprint:
                raise ValueError("PROVIDER_CONFIG_FINGERPRINT_MISMATCH")
            if selected_connection is not None and connection_id is not None:
                return self._commit_connection_configuration(
                    candidate,
                    selected_connection,
                    persist_legacy=True,
                )
            self._configuration = self._store.save(candidate)
            self._provider_profiles[provider] = self._configuration
            self._store.save_profiles(self._provider_profiles)
            self._ensure_canonical_profiles(self._configuration)
            if replaced_legacy_connection is not None:
                self._management.tombstone_connection(replaced_legacy_connection.connection_id)
            self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            return self.projection()
        model = normalized_model
        same_profile = (
            existing_profile is not None
            and existing_profile.provider == provider
            and existing_profile.model == model
            and existing_profile.base_url == resolved_url
            and existing_profile.credential_fingerprint == credential_fingerprint
        )
        current_model = next(
            (
                item
                for item in (
                    self.model_pool(
                        provider_id=provider,
                        connection_id=selected_connection.connection_id,
                    )
                    if selected_connection is not None
                    else ()
                )
                if item.remote_model_id == model
            ),
            None,
        )
        catalog_model = next(
            (item for item in official_model_catalog(provider, resolved_endpoint) if item.remote_model_id == model),
            None,
        )
        if is_official and catalog_model is None:
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        expected_model_profile_id = (
            current_model.model_profile_id
            if current_model is not None
            else self._canonical_model_id(provider, model)
        )
        if model_profile_id is not None and model_profile_id != expected_model_profile_id:
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        expected_credential_ref = (
            selected_connection.credential_ref
            if selected_connection is not None
            else None if provider == "local" else f"credential:{provider}"
        )
        if credential_ref is not None and credential_ref != expected_credential_ref:
            raise ValueError("PROVIDER_CREDENTIAL_REF_MISMATCH")
        if protocol_family is not None:
            expected_protocol = next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT)
            if protocol_family != getattr(expected_protocol, "value", expected_protocol):
                raise ValueError("PROVIDER_PROTOCOL_MISMATCH")
            registered_model = self._management.model_profile(model_profile_id)
            if registered_model is not None and (
                registered_model.provider_id != provider
                or registered_model.endpoint_id != resolved_endpoint
                or registered_model.remote_model_id != model
            ):
                raise ValueError("PROVIDER_MODEL_MISMATCH")
        resolved_model_profile_id = model_profile_id or expected_model_profile_id
        if same_profile:
            resolved_model_profile_id = existing_profile.model_profile_id or resolved_model_profile_id
        candidate = ProviderConfiguration(
            provider=provider,
            connection_name=normalized_connection_name,
            model=model,
            base_url=resolved_url,
            endpoint_id=resolved_endpoint,
            model_profile_id=resolved_model_profile_id,
            credential_ref=expected_credential_ref,
            last_test_status=(
                existing_profile.last_test_status
                if same_profile and existing_profile is not None
                else ProviderConnectionStatus.UNVERIFIED
            ),
            tested_at=(
                existing_profile.tested_at
                if same_profile and existing_profile is not None
                else None
            ),
            tested_configuration_fingerprint=(
                existing_profile.tested_configuration_fingerprint
                if same_profile and existing_profile is not None
                else None
            ),
            credential_fingerprint=credential_fingerprint,
            protocol_family=(
                protocol_family
                if protocol_family is not None
                else getattr(next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT), "value", next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT))
            ),
        )
        if configuration_fingerprint is not None and candidate.configuration_fingerprint != configuration_fingerprint:
            raise ValueError("PROVIDER_CONFIG_FINGERPRINT_MISMATCH")
        if selected_connection is not None and connection_id is not None:
            return self._commit_connection_configuration(
                candidate,
                selected_connection,
                persist_legacy=True,
            )
        self._configuration = self._store.save(candidate)
        self._provider_profiles[provider] = self._configuration
        self._store.save_profiles(self._provider_profiles)
        model_profile = self._ensure_canonical_profiles(self._configuration)
        if replaced_legacy_connection is not None:
            self._management.tombstone_connection(replaced_legacy_connection.connection_id)
        if (
            self._configuration.last_test_status
            is not ProviderConnectionStatus.AVAILABLE
        ):
            if model_profile is None:
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
                return self.projection()
            endpoint = self._management.endpoint_profile(model_profile.endpoint_id)
            if endpoint is not None and endpoint.health_status == "AVAILABLE":
                self._management.upsert_endpoint(
                    endpoint.model_copy(
                        update={"health_status": "STALE", "updated_at": self._now()}
                    )
                )
        self._refresh_canonical_binding(transport_only=False)
        return self.projection()

    def save_credential(
        self,
        secret: SecretStr,
        *,
        diagnostic_request_id: str | None = None,
        provider: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        with request_scope(diagnostic_request_id), self._lock:
            if self._management is not None:
                return self._canonical_save_credential(
                    secret,
                    provider=provider,
                    connection_id=connection_id,
                )
            if (
                provider is not None
                and provider.strip().lower() != ProductionProvider.DEEPSEEK.value
            ):
                raise ValueError(
                    "provider is unsupported without canonical model gateway management"
                )
            emit("PROVIDER_SERVICE_ENTERED", result="ENTERED")
            credential = self._validate_credential(secret)
            emit("SECRET_VALIDATION_PASSED", result="PASSED")
            previous, unavailable, _ = self._credential_observation(
                create_integrity_key=False
            )
            if unavailable:
                raise CredentialUnavailable("provider credential store is unavailable")
            emit("INTEGRITY_IDENTITY_BEGIN", result="BEGIN")
            credential_fingerprint = _credential_fingerprint(
                self._credentials, credential
            )
            emit("CREDENTIAL_FINGERPRINT_READY", result="READY")
            if (
                previous is not None
                and credential_fingerprint == self._configuration.credential_fingerprint
            ):
                return self.projection()
            previous_configuration = self._configuration
            emit("BINDING_INVALIDATE_BEGIN", result="BEGIN")
            emit("PRIMARY_CREDENTIAL_WRITE_BEGIN", result="BEGIN")
            try:
                self._credentials.set(CHAT_PROVIDER_CREDENTIAL_PURPOSE, credential)
                self._verify_credential_write(
                    self._credentials,
                    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
                    credential,
                )
            except Exception as error:
                emit("PRIMARY_CREDENTIAL_WRITE_FAILURE", result="FAILURE", error=error)
                raise
            emit("PRIMARY_CREDENTIAL_WRITE_SUCCESS", result="SUCCESS")
            emit("SAFE_CONFIG_PERSIST_BEGIN", result="BEGIN")
            try:
                persisted = self._store.save(
                    self._candidate(
                        status=ProviderConnectionStatus.UNVERIFIED,
                        credential_fingerprint=credential_fingerprint,
                    )
                )
            except Exception as error:
                emit("SAFE_CONFIG_PERSIST_FAILURE", result="FAILURE", error=error)
                self._restore_credential(previous)
                self._configuration = previous_configuration
                raise
            emit("SAFE_CONFIG_PERSIST_SUCCESS", result="SUCCESS")
            self._configuration = persisted
            self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            emit("BINDING_INVALIDATE_SUCCESS", result="SUCCESS")
            projection = self.projection()
            emit("PROVIDER_PROJECTION_RETURNED", result="RETURNED")
            return projection

    def _canonical_save_credential(
        self,
        secret: SecretStr,
        *,
        provider: str | None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        target_provider = (provider or self._configuration.provider).strip().lower()
        connection = (
            self._resolve_connection(
                provider_id=target_provider if provider is not None else None,
                connection_id=connection_id,
            )
            if connection_id is not None
            else self._resolve_connection(provider_id=target_provider)
            if self._active_connections_for_provider(target_provider)
            else None
        )
        if connection is None and connection_id is None:
            legacy_target = self._configuration_for_provider(target_provider)
            connection = self._ensure_connection_for_configuration(legacy_target)
        if connection is not None:
            # The provider selector without a connection id is the legacy
            # compatibility writer; only that path may carry its historical
            # selected-model fields forward.  Canonical connection-scoped
            # credential writes stay model-free.
            target = self._configuration_for_connection(
                connection,
                include_legacy_model=connection_id is None,
            )
            target_provider = connection.provider_id
        else:
            target = self._configuration_for_provider(target_provider)
        preset = self._provider_preset(target_provider)
        staged = preset is not None and preset.provider_kind not in {ProviderKind.CUSTOM, ProviderKind.LOCAL}
        credential = self._validate_credential(secret)
        if connection is not None:
            previous, unavailable, _ = self._connection_credential_observation(
                connection, create_integrity_key=False
            )
        else:
            previous, unavailable, _ = self._canonical_credential_observation(
                target_provider, create_integrity_key=False
            )
        if unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        connection_scoped_write = connection is not None and connection_id is not None
        purpose = (
            provider_connection_credential_purpose(connection.connection_id)
            if connection_scoped_write
            else provider_credential_purpose(target_provider)
        )
        fingerprint = _credential_fingerprint(self._credentials, credential, credential_purpose=purpose)
        if previous is not None and fingerprint == target.credential_fingerprint:
            return self._canonical_projection(target, connection=connection)
        previous_profiles = dict(self._provider_profiles)
        try:
            self._credentials.set(purpose, credential)
            self._verify_credential_write(self._credentials, purpose, credential)
            # Credential writes persist only the editing profile. Runtime identity
            # and global model policy are owned exclusively by full configuration save.
            target = ProviderConfiguration(
                provider=target_provider,
                connection_name=target.connection_name,
                # Credential onboarding through the legacy provider selector
                # keeps its historical staged-model behavior.  A canonical
                # connection-scoped secret update, however, invalidates
                # discovery/verification without clearing that connection's
                # model identity for the next explicit test.
                model=target.model if connection_scoped_write else None if staged else target.model,
                base_url=target.base_url,
                endpoint_id=target.endpoint_id,
                credential_ref=target.credential_ref or f"credential:{target_provider}",
                credential_fingerprint=fingerprint,
                protocol_family=target.protocol_family,
                model_profile_id=(
                    target.model_profile_id
                    if connection_scoped_write
                    else None
                    if staged
                    else target.model_profile_id
                ),
            )
            if connection is not None and connection_id is not None:
                self._connection_configurations[connection.connection_id] = target
            else:
                profiles = {**previous_profiles, target_provider: target}
                self._store.save_profiles(profiles)
                self._provider_profiles = profiles
        except Exception:
            self._provider_profiles = previous_profiles
            try:
                if previous is None:
                    self._credentials.delete(purpose)
                else:
                    self._credentials.set(purpose, previous)
            except Exception:  # noqa: BLE001 - rollback failure must fail closed
                self.binding.invalidate("PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
            raise
        state_key = connection.connection_id if connection is not None else target_provider
        self._discovery_states[state_key] = "STALE"
        if self._model_pool is not None:
            self._model_pool.invalidate(
                target_provider,
                connection_id=connection.connection_id if connection is not None else None,
                endpoint_id=connection.endpoint_id if connection is not None else None,
            )
        if connection is not None and connection_id is not None:
            self.binding.invalidate_transport(
                connection_id=connection.connection_id,
                reason_code="PROVIDER_CONNECTION_UNVERIFIED",
            )
            return self._canonical_projection(target, connection=connection)
        if not staged and target_provider == self._configuration.provider:
            self._configuration = self._store.save(target)
            self._ensure_canonical_profiles(target)
            self._refresh_canonical_binding(transport_only=False)
            return self._canonical_projection(target, connection=connection)
        if target_provider == self._configuration.provider:
            # Invalidate authorization after key replacement without activating
            # the incomplete editing profile or rewriting the active model.
            self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
        return self._canonical_projection(target, connection=connection)

    def remove_credential(
        self,
        *,
        provider: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        with self._lock:
            if self._management is not None:
                return self._canonical_remove_credential(
                    provider=provider,
                    connection_id=connection_id,
                )
            if provider is not None and provider.strip().lower() != ProductionProvider.DEEPSEEK.value:
                raise ValueError("provider is unsupported without canonical model gateway management")
            previous, unavailable, _ = self._credential_observation(
                create_integrity_key=False
            )
            if unavailable:
                raise CredentialUnavailable("provider credential store is unavailable")
            previous_configuration = self._configuration
            if previous is not None:
                self._credentials.delete(CHAT_PROVIDER_CREDENTIAL_PURPOSE)
            try:
                persisted = self._store.save(
                    self._candidate(
                        status=ProviderConnectionStatus.NOT_CONFIGURED,
                        credential_fingerprint=None,
                    )
                )
            except Exception:
                if previous is not None:
                    self._restore_credential(previous)
                self._configuration = previous_configuration
                raise
            self._configuration = persisted
            self.binding.invalidate("PROVIDER_NOT_CONFIGURED")
            return self.projection()

    def remove_connection(
        self,
        *,
        provider: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        """Deactivate one saved connection while retaining auditable model history."""

        with self._lock:
            if self._management is None:
                raise ValueError("provider connection management is unavailable")
            target_provider = (provider or self._configuration.provider).strip().lower()
            connection = self._resolve_connection(
                provider_id=target_provider if provider is not None else None,
                connection_id=connection_id,
            )
            scoped_purpose = provider_connection_credential_purpose(connection.connection_id)
            try:
                scoped_credential = self._credentials.get(scoped_purpose)
            except CredentialUnavailable:
                raise CredentialUnavailable("provider credential store is unavailable") from None
            if scoped_credential is not None:
                self._credentials.delete(scoped_purpose)
            else:
                # A provider-scoped legacy secret may be shared or may be
                # referenced by old receipts; never delete it implicitly.
                self._credential_cleanup_pending.add(connection.connection_id)
            self._management.tombstone_connection(connection.connection_id)
            self._provider_profiles.pop(target_provider, None)
            if target_provider == self._configuration.provider:
                fallback = ProviderConfiguration(
                    provider=ProductionProvider.DEEPSEEK.value,
                    model=DEFAULT_DEEPSEEK_MODEL,
                    base_url=DEEPSEEK_BASE_URL,
                    last_test_status=ProviderConnectionStatus.NOT_CONFIGURED,
                )
                self._configuration = self._store.save(fallback)
                self._provider_profiles[self._configuration.provider] = self._configuration
            if self.binding.transport_ready:
                self.binding.invalidate_transport(
                    connection_id=connection.connection_id,
                    reason_code="PROVIDER_NOT_CONFIGURED",
                )
            elif target_provider == self._configuration.provider:
                self.binding.invalidate("PROVIDER_NOT_CONFIGURED")
            self._store.save_profiles(self._provider_profiles)
            if self._model_pool is not None:
                self._model_pool.invalidate(
                    target_provider,
                    connection_id=connection.connection_id,
                    endpoint_id=connection.endpoint_id,
                )
            if self._management.persistent:
                self._management.save()
            return self.projection()

    def disable_connection(self, connection_id: str) -> ProviderProjection:
        with self._lock:
            if self._management is None:
                raise ValueError("provider connection management is unavailable")
            connection = self._management.disable_connection(connection_id)
            self._connection_configurations.pop(connection_id, None)
            if self._model_pool is not None:
                self._model_pool.invalidate(
                    connection.provider_id,
                    connection_id=connection_id,
                    endpoint_id=connection.endpoint_id,
                )
            if self.binding.transport_ready:
                self.binding.invalidate_transport(
                    connection_id=connection.connection_id,
                    reason_code="PROVIDER_CONNECTION_DISABLED",
                )
            elif connection.provider_id == self._configuration.provider:
                self.binding.invalidate("PROVIDER_CONNECTION_DISABLED")
            return self._canonical_projection(
                self._configuration_for_connection(connection),
                connection=connection,
            )

    def update_connection(
        self,
        connection_id: str,
        *,
        connection_name: str | None = None,
        endpoint_url: str | None = None,
        protocol_family: str | None = None,
        enabled: bool | None = None,
    ) -> ProviderProjection:
        """Apply a safe connection edit and invalidate its owned runtime state."""

        with self._lock:
            if self._management is None:
                raise ValueError("PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE")
            current = self._management.connection(connection_id)
            if current is None:
                raise ValueError("PROVIDER_CONNECTION_NOT_FOUND")
            if current.status is GatewayProviderConnectionStatus.DELETED:
                raise ValueError("PROVIDER_CONNECTION_DELETED")
            endpoint = self._management.endpoint_profile(current.endpoint_id)
            if endpoint is None:
                raise ValueError("PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND")
            if endpoint_url is not None or protocol_family is not None:
                resolved_protocol = protocol_family or getattr(
                    endpoint.protocol_family, "value", endpoint.protocol_family
                )
                if resolved_protocol == ProtocolFamily.CUSTOM.value:
                    resolved_protocol = ProtocolFamily.OPENAI_CHAT.value
                provider_profile = self._management.providers.get(current.provider_id)
                supported_protocols = (
                    tuple(provider_profile.supported_protocols)
                    if provider_profile is not None
                    else ()
                )
                if supported_protocols and resolved_protocol not in {
                    getattr(item, "value", item) for item in supported_protocols
                }:
                    raise ValueError("PROVIDER_PROTOCOL_MISMATCH")
                updated_endpoint = endpoint.model_copy(
                    update={
                        "base_url": endpoint_url or endpoint.base_url,
                        "protocol_family": resolved_protocol,
                        "health_status": "UNKNOWN",
                        "updated_at": self._now(),
                    }
                )
                self._management.upsert_endpoint(updated_endpoint)
                if self._model_pool is not None:
                    self._model_pool.invalidate(
                        current.provider_id,
                        connection_id=current.connection_id,
                        endpoint_id=current.endpoint_id,
                    )
            next_connection = current
            if connection_name is not None:
                next_connection = self._management.update_connection(
                    current, connection_name=connection_name
                )
            if enabled is False and next_connection.status is GatewayProviderConnectionStatus.ACTIVE:
                next_connection = self._management.disable_connection(connection_id)
            elif enabled is True and next_connection.status is not GatewayProviderConnectionStatus.ACTIVE:
                if next_connection.status is GatewayProviderConnectionStatus.DELETED:
                    raise ValueError("PROVIDER_CONNECTION_DELETED")
                next_connection = self._management.update_connection(
                    next_connection, status=GatewayProviderConnectionStatus.ACTIVE
                )
                restored_endpoint = self._management.endpoint_profile(next_connection.endpoint_id)
                if restored_endpoint is not None and not restored_endpoint.enabled:
                    self._management.upsert_endpoint(
                        restored_endpoint.model_copy(
                            update={"enabled": True, "health_status": "UNKNOWN", "updated_at": self._now()}
                        )
                    )
            if self._management.persistent:
                self._management.save()
            self._connection_configurations.pop(connection_id, None)
            if self._model_pool is not None:
                self._model_pool.invalidate(
                    next_connection.provider_id,
                    connection_id=connection_id,
                    endpoint_id=next_connection.endpoint_id,
                )
            if self.binding.transport_ready:
                self.binding.invalidate_transport(
                    connection_id=next_connection.connection_id,
                    reason_code="PROVIDER_CONNECTION_UNVERIFIED",
                )
            else:
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            return self._canonical_projection(
                self._configuration_for_connection(next_connection),
                connection=next_connection,
            )

    def delete_connection(self, connection_id: str) -> ProviderProjection:
        with self._lock:
            if self._management is None:
                raise ValueError("provider connection management is unavailable")
            connection = self._management.tombstone_connection(connection_id)
            self._connection_configurations.pop(connection_id, None)
            if self._model_pool is not None:
                self._model_pool.invalidate(
                    connection.provider_id,
                    connection_id=connection_id,
                    endpoint_id=connection.endpoint_id,
                )
            if self.binding.transport_ready:
                self.binding.invalidate_transport(
                    connection_id=connection.connection_id,
                    reason_code="PROVIDER_CONNECTION_DELETED",
                )
            elif connection.provider_id == self._configuration.provider:
                self.binding.invalidate("PROVIDER_CONNECTION_DELETED")
            return self._canonical_projection(
                self._configuration_for_connection(connection),
                connection=connection,
            )

    def _canonical_remove_credential(
        self,
        *,
        provider: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        provider = (provider or self._configuration.provider).strip().lower()
        connection = (
            self._resolve_connection(
                provider_id=provider if provider is not None else None,
                connection_id=connection_id,
                require_active=False,
            )
            if connection_id is not None
            else None
        )
        if connection is not None:
            purpose = provider_connection_credential_purpose(connection.connection_id)
            previous, unavailable, _ = self._connection_credential_observation(
                connection, create_integrity_key=False
            )
            if unavailable:
                raise CredentialUnavailable("provider credential store is unavailable")
            if previous is not None:
                self._credentials.delete(purpose)
            self._credential_cleanup_pending.discard(connection.connection_id)
            self._connection_configurations.pop(connection.connection_id, None)
            self._model_pool.invalidate(
                provider,
                connection_id=connection.connection_id,
                endpoint_id=connection.endpoint_id,
            ) if self._model_pool is not None else None
            if self.binding.transport_ready:
                self.binding.invalidate_transport(
                    connection_id=connection.connection_id,
                    reason_code="PROVIDER_NOT_CONFIGURED",
                )
            return self._canonical_projection(
                self._configuration_for_connection(connection),
                connection=connection,
            )
        configuration = (
            self._configuration
            if provider == self._configuration.provider
            else self._provider_profiles.get(provider) or self._configuration_for_provider(provider)
        )
        purpose = provider_credential_purpose(provider)
        previous, unavailable, _ = self._canonical_credential_observation(
            provider, create_integrity_key=False
        )
        if unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        if previous is not None:
            self._credentials.delete(purpose)
        updated = self._store.save(
            ProviderConfiguration(
                **{
                    **configuration.model_dump(mode="python"),
                    "credential_fingerprint": None,
                    "last_test_status": ProviderConnectionStatus.NOT_CONFIGURED,
                    "tested_at": None,
                    "tested_configuration_fingerprint": None,
                    "configuration_fingerprint": _AUTO,
                }
            )
        )
        self._provider_profiles[provider] = updated
        self._store.save_profiles(self._provider_profiles)
        endpoint = (
            self._management.endpoint_profile(configuration.endpoint_id)
            if configuration.endpoint_id
            else None
        )
        if endpoint is not None:
            self._management.upsert_endpoint(
                endpoint.model_copy(
                    update={"health_status": "UNKNOWN", "updated_at": self._now()}
                )
            )
        if provider == self._configuration.provider:
            self._configuration = updated
            self.binding.invalidate("PROVIDER_NOT_CONFIGURED")
        if self._management.persistent:
            self._management.save()
        return self.projection(provider_id=provider)

    def test_connection(
        self,
        *,
        draft: Mapping[str, object] | None = None,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        with self._lock:
            if self._management is not None:
                if draft is not None:
                    return self._canonical_test_draft(draft)
                return self._canonical_test_connection(connection_id=connection_id)
            test_connection_emit(
                "TEST_CONNECTION_SERVICE_ENTERED",
                result="ENTERED",
                provider="deepseek",
            )
            credential, unavailable, credential_fingerprint = (
                self._credential_observation(create_integrity_key=True)
            )
            if unavailable:
                self._append_verification_receipt(
                    ProviderConnectionStatus.UNVERIFIED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.UNVERIFIED,
                        provider=self._configuration.provider,
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                        provider_error_code="CREDENTIAL_STORE_UNAVAILABLE",
                    ),
                    endpoint=None,
                    requested_model=self._configuration.model,
                )
                raise CredentialUnavailable("provider credential store is unavailable")
            if credential is None:
                if self._configuration.credential_fingerprint is not None:
                    self._configuration = self._store.save(
                        self._candidate(
                            status=ProviderConnectionStatus.NOT_CONFIGURED,
                            credential_fingerprint=None,
                        )
                    )
                return self._record(
                    ProviderConnectionStatus.NOT_CONFIGURED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.NOT_CONFIGURED,
                        provider=self._configuration.provider,
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                        provider_error_code="CREDENTIAL_NOT_CONFIGURED",
                    ),
                )
            if credential_fingerprint != self._configuration.credential_fingerprint:
                self._configuration = self._store.save(
                    self._candidate(
                        status=ProviderConnectionStatus.UNVERIFIED,
                        credential_fingerprint=credential_fingerprint,
                    )
                )
            try:
                settings = ProductionModelSettings(
                    provider=ProductionProvider.DEEPSEEK,
                    model_name=self._configuration.model,
                    api_key=SecretStr(credential.decode("utf-8")),
                    base_url=None,
                    timeout_seconds=30.0,
                    max_retries=0,
                    retry_backoff_seconds=0.0,
                )
                provider = create_production_provider(
                    settings,
                    http_transport=self._http_transport,
                    async_transport=self._async_transport,
                )
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
                    result="BEGIN",
                    provider="deepseek",
                )
                started_at = time.perf_counter()
                response = provider.generate(self._health_request(settings.to_model_policy()))
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                self._install_binding(credential)
            except ModelProviderUnauthorized as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="AUTH_FAILED",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.AUTH_FAILED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.AUTH_FAILED,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            except ModelProviderModelUnavailable as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="MODEL_UNAVAILABLE",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.MODEL_UNAVAILABLE,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.MODEL_UNAVAILABLE,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            except ModelProviderRateLimited as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="RATE_LIMITED",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.RATE_LIMITED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.RATE_LIMITED,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            except ModelProviderConfigurationError as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="CONFIG_INVALID",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.CONFIG_INVALID,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.CONFIG_INVALID,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            except (ModelProviderTimeout, ModelProviderError, OSError) as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="NETWORK_UNAVAILABLE",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.NETWORK_UNAVAILABLE,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.NETWORK_UNAVAILABLE,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            except (TypeError, ValueError, UnicodeError) as error:
                test_connection_emit(
                    "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                    result="CONFIG_INVALID",
                    error=error,
                    provider="deepseek",
                )
                return self._record(
                    ProviderConnectionStatus.CONFIG_INVALID,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.CONFIG_INVALID,
                        provider="deepseek",
                        protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                        request_url=self._configuration.base_url.rstrip("/")
                        + "/chat/completions",
                        error=error,
                    ),
                )
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_SUCCESS",
                result="SUCCESS",
                provider="deepseek",
            )
            return self._record(
                ProviderConnectionStatus.AVAILABLE,
                diagnostic=_connection_diagnostic(
                    status=ProviderConnectionStatus.AVAILABLE,
                    provider="deepseek",
                    protocol_family=ProtocolFamily.OPENAI_CHAT.value,
                    request_url=self._configuration.base_url.rstrip("/")
                    + "/chat/completions",
                ),
                verification=self._verification_from_response(
                    response,
                    requested_model=self._configuration.model,
                    protocol_adapter=ProtocolFamily.OPENAI_CHAT.value,
                    latency_ms=latency_ms,
                ),
                response=response,
                latency_ms=latency_ms,
            )

    def _canonical_test_connection(
        self, *, connection_id: str | None = None
    ) -> ProviderProjection:
        connection = (
            self._resolve_connection(connection_id=connection_id)
            if connection_id is not None
            else None
        )
        if connection is None and self._management is not None:
            self._resolve_connection(provider_id=self._configuration.provider)
        scoped_configuration = (
            self._configuration_for_connection(
                connection,
                include_legacy_model=True,
            )
            if connection is not None
            else self._configuration
        )
        provider_id = scoped_configuration.provider
        test_connection_emit(
            "TEST_CONNECTION_SERVICE_ENTERED", result="ENTERED", provider=provider_id
        )
        if connection is not None:
            credential, unavailable, credential_fingerprint = self._connection_credential_observation(
                connection, create_integrity_key=True
            )
        else:
            credential, unavailable, credential_fingerprint = self._canonical_credential_observation(
                provider_id, create_integrity_key=True
            )
        if unavailable:
            endpoint = (
                self._management.endpoint_profile(
                    connection.endpoint_id if connection is not None else self._configuration.endpoint_id
                )
                if connection is not None or self._configuration.endpoint_id
                else None
            )
            self._append_verification_receipt(
                ProviderConnectionStatus.UNVERIFIED,
                diagnostic=_connection_diagnostic(
                    status=ProviderConnectionStatus.UNVERIFIED,
                    provider=provider_id,
                    protocol_family=(
                        endpoint.protocol_family.value
                        if endpoint is not None
                        and isinstance(endpoint.protocol_family, ProtocolFamily)
                        else None
                    ),
                    request_url=_canonical_probe_url(endpoint, scoped_configuration.model or "unknown-model"),
                    stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                    provider_error_code="CREDENTIAL_STORE_UNAVAILABLE",
                ),
                endpoint=endpoint,
                requested_model=scoped_configuration.model or "unknown-model",
                connection_id=connection.connection_id if connection is not None else None,
            )
            raise CredentialUnavailable("provider credential store is unavailable")
        if credential is None and provider_id != "local":
            if connection is not None:
                endpoint = self._management.endpoint_profile(connection.endpoint_id)
                self._append_verification_receipt(
                    ProviderConnectionStatus.NOT_CONFIGURED,
                    diagnostic=_connection_diagnostic(
                        status=ProviderConnectionStatus.NOT_CONFIGURED,
                        provider=provider_id,
                        protocol_family=(
                            endpoint.protocol_family.value
                            if endpoint is not None and isinstance(endpoint.protocol_family, ProtocolFamily)
                            else None
                        ),
                        request_url=_canonical_probe_url(endpoint, scoped_configuration.model),
                        stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                        provider_error_code="CREDENTIAL_NOT_CONFIGURED",
                    ),
                    endpoint=endpoint,
                    requested_model=scoped_configuration.model or "unknown-model",
                    connection_id=connection.connection_id,
                    credential_revision=credential_fingerprint,
                    configuration_fingerprint=scoped_configuration.configuration_fingerprint,
                )
                return self._canonical_projection(scoped_configuration, connection=connection)
            return self._canonical_record(
                ProviderConnectionStatus.NOT_CONFIGURED,
                diagnostic=_connection_diagnostic(
                    status=ProviderConnectionStatus.NOT_CONFIGURED,
                    provider=provider_id,
                    protocol_family=None,
                    request_url=None,
                    stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                    provider_error_code="CREDENTIAL_NOT_CONFIGURED",
                ),
            )

        if connection is not None:
            endpoint = self._management.endpoint_profile(connection.endpoint_id)
            model = self._management.model_profile(scoped_configuration.model_profile_id) if scoped_configuration.model_profile_id else None
            if endpoint is None or model is None:
                return self._canonical_projection(scoped_configuration, connection=connection)
            identity = BoundModelIdentity(
                provider_id=connection.provider_id,
                endpoint_id=endpoint.endpoint_id,
                credential_ref=connection.credential_ref or endpoint.credential_ref or "credential:local",
                model_profile_id=model.model_profile_id,
                model_id=model.remote_model_id,
            )
            return self._execute_explicit_probe(
                identity,
                endpoint=endpoint,
                credential_revision=credential_fingerprint,
                configuration_fingerprint=scoped_configuration.configuration_fingerprint,
                connection_id=connection.connection_id,
            )

        result = self._canonical_gateway()
        if result is None:
            status = (
                ProviderConnectionStatus.NOT_CONFIGURED
                if credential is None
                else ProviderConnectionStatus.CONFIG_INVALID
            )
            return self._canonical_record(
                status,
                diagnostic=_connection_diagnostic(
                    status=status,
                    provider=provider_id,
                    protocol_family=None,
                    request_url=None,
                    stage=(
                        ProviderConnectionDiagnosticStage.CREDENTIAL
                        if credential is None
                        else ProviderConnectionDiagnosticStage.CONFIGURATION
                    ),
                    provider_error_code=(
                        "CREDENTIAL_NOT_CONFIGURED"
                        if credential is None
                        else "CONFIGURATION_INVALID"
                    ),
                ),
            )
        gateway, policy = result
        endpoint = (
            self._management.endpoint_profile(self._configuration.endpoint_id)
            if self._configuration.endpoint_id
            else None
        )
        diagnostic_fields = {
            "provider": provider_id,
            "endpoint_id": endpoint.endpoint_id if endpoint is not None else None,
            "credential_ref": endpoint.credential_ref if endpoint is not None else None,
            "protocol_family": (
                endpoint.protocol_family.value if endpoint is not None else None
            ),
            "request_url": _canonical_probe_url(endpoint, policy.model_name),
            "proxy_mode": "DIRECT_NO_ENV_PROXY",
        }
        if (
            credential_fingerprint is not None
            and credential_fingerprint != self._configuration.credential_fingerprint
        ):
            self._configuration = self._store.save(
                ProviderConfiguration(
                    **{
                        **self._configuration.model_dump(mode="python"),
                        "credential_fingerprint": credential_fingerprint,
                        "configuration_fingerprint": _AUTO,
                    }
                )
            )
        def diagnostic(
            status: ProviderConnectionStatus,
            *,
            error: BaseException | None = None,
            stage: ProviderConnectionDiagnosticStage | None = None,
            provider_error_code: str | None = None,
        ) -> ProviderConnectionDiagnostic:
            return _connection_diagnostic(
                status=status,
                provider=provider_id,
                protocol_family=diagnostic_fields["protocol_family"],
                request_url=diagnostic_fields["request_url"],
                error=error,
                stage=stage,
                provider_error_code=provider_error_code,
            )
        try:
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
                result="BEGIN",
                **diagnostic_fields,
            )
            started_at = time.perf_counter()
            response = gateway.generate(self._health_request(policy))
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        except ModelProviderUnauthorized as error:
            status = ProviderConnectionStatus.AUTH_FAILED
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="AUTH_FAILED",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except ModelProviderModelUnavailable as error:
            status = ProviderConnectionStatus.MODEL_UNAVAILABLE
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="MODEL_UNAVAILABLE",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except ModelProviderRateLimited as error:
            status = ProviderConnectionStatus.RATE_LIMITED
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="RATE_LIMITED",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except ModelProviderConfigurationError as error:
            status = ProviderConnectionStatus.CONFIG_INVALID
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="CONFIG_INVALID",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except (ModelProviderTimeout, ModelProviderError, OSError) as error:
            status = ProviderConnectionStatus.NETWORK_UNAVAILABLE
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="NETWORK_UNAVAILABLE",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except (TypeError, ValueError, UnicodeError) as error:
            status = ProviderConnectionStatus.CONFIG_INVALID
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result="CONFIG_INVALID",
                error=error,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        except ModelGatewayProviderFailure as error:
            status = {
                "PROVIDER_AUTH_FAILED": ProviderConnectionStatus.AUTH_FAILED,
                "PROVIDER_MODEL_UNAVAILABLE": ProviderConnectionStatus.MODEL_UNAVAILABLE,
                "PROVIDER_RATE_LIMITED": ProviderConnectionStatus.RATE_LIMITED,
                "PROVIDER_CONFIG_INVALID": ProviderConnectionStatus.CONFIG_INVALID,
            }.get(error.reason_code)
            if status is None and error.reason_code == "PROVIDER_HTTP_ERROR":
                status = (
                    ProviderConnectionStatus.CONFIG_INVALID
                    if error.safe_http_status in {400, 422}
                    else ProviderConnectionStatus.MODEL_UNAVAILABLE
                    if error.safe_http_status == 404
                    else ProviderConnectionStatus.NETWORK_UNAVAILABLE
                )
            status = status or ProviderConnectionStatus.NETWORK_UNAVAILABLE
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
                result={
                    ProviderConnectionStatus.AUTH_FAILED: "AUTH_FAILED",
                    ProviderConnectionStatus.MODEL_UNAVAILABLE: "MODEL_UNAVAILABLE",
                    ProviderConnectionStatus.RATE_LIMITED: "RATE_LIMITED",
                    ProviderConnectionStatus.CONFIG_INVALID: "CONFIG_INVALID",
                    ProviderConnectionStatus.NETWORK_UNAVAILABLE: "NETWORK_UNAVAILABLE",
                }[status],
                error=error,
                safe_http_status=error.safe_http_status,
                **diagnostic_fields,
            )
            return self._canonical_record(status, diagnostic=diagnostic(status, error=error))
        test_connection_emit(
            "TEST_CONNECTION_PROVIDER_CALL_SUCCESS",
            result="SUCCESS",
            **diagnostic_fields,
        )
        if credential_fingerprint != self._configuration.credential_fingerprint:
            self._configuration = self._store.save(
                ProviderConfiguration(
                    **{
                        **self._configuration.model_dump(mode="python"),
                        "credential_fingerprint": credential_fingerprint,
                        "configuration_fingerprint": _AUTO,
                    }
                )
            )
        return self._canonical_record(
            ProviderConnectionStatus.AVAILABLE,
            diagnostic=diagnostic(ProviderConnectionStatus.AVAILABLE),
            verification=self._verification_from_response(
                response,
                requested_model=policy.model_name,
                protocol_adapter=diagnostic_fields["protocol_family"] or ProtocolFamily.OPENAI_CHAT.value,
                latency_ms=latency_ms,
            ),
            response=response,
            latency_ms=latency_ms,
        )

    def _canonical_test_draft(
        self, draft: Mapping[str, object]
    ) -> ProviderProjection:
        """Probe an explicit identity without changing runtime governance."""

        required = {
            "provider_id",
            "endpoint_id",
            "credential_ref",
            "model_profile_id",
            "model_id",
        }
        optional = {"base_url", "protocol_family", "configuration_fingerprint", "connection_id"}
        if not isinstance(draft, Mapping):
            raise TypeError("provider probe identity is invalid")
        legacy_draft = "provider" in draft or "model" in draft
        if legacy_draft:
            draft = {
                **draft,
                "provider_id": draft.get("provider_id", draft.get("provider")),
                "model_id": draft.get("model_id", draft.get("model")),
            }
            draft.pop("provider", None)
            draft.pop("model", None)
        if set(draft) - required - optional:
            raise ValueError("provider probe identity is invalid")
        if not required.issubset(draft):
            raise ValueError("provider probe identity is incomplete")
        values = {key: draft[key] for key in required | optional if key in draft}
        provider_id = values["provider_id"]
        endpoint_id = values["endpoint_id"]
        credential_ref = values["credential_ref"]
        model_profile_id = values["model_profile_id"]
        model_id = values["model_id"]
        if not all(isinstance(item, str) and item.strip() for item in (
            provider_id, endpoint_id, credential_ref, model_profile_id, model_id
        )):
            raise TypeError("provider probe identity is invalid")
        provider_id = provider_id.strip().lower()
        endpoint_id = endpoint_id.strip()
        credential_ref = credential_ref.strip()
        model_profile_id = model_profile_id.strip()
        model_id = model_id.strip()
        endpoint = self._management.endpoint_profile(endpoint_id)
        if endpoint is None or endpoint.provider_id != provider_id:
            raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
        endpoint_credential_ref = endpoint.credential_ref or (
            "credential:local" if provider_id == "local" else None
        )
        if endpoint_credential_ref != credential_ref:
            raise ValueError("PROVIDER_CREDENTIAL_REF_MISMATCH")
        connection = None
        if values.get("connection_id") is not None:
            connection = self._resolve_connection(
                provider_id=provider_id,
                connection_id=values["connection_id"],
            )
            if connection.endpoint_id != endpoint_id:
                raise ValueError("PROVIDER_CONNECTION_ENDPOINT_MISMATCH")
            # Canonical connection probes may only target a model admitted to
            # this connection's revision-qualified current pool.  The model
            # registry intentionally retains historical and quarantined
            # profiles, so the identity lookup below is not sufficient by
            # itself.  Reject before constructing an adapter or issuing a
            # provider request; legacy drafts without a connection id retain
            # their compatibility behavior.
            current_pool = self.model_pool(connection_id=connection.connection_id)
            if not any(
                item.model_profile_id == model_profile_id
                and item.remote_model_id == model_id
                for item in current_pool
            ):
                raise ValueError("PROVIDER_MODEL_MISMATCH")
        expected_protocol = (
            endpoint.protocol_family.value
            if isinstance(endpoint.protocol_family, ProtocolFamily)
            else endpoint.protocol_family
        )
        if values.get("protocol_family") not in {None, expected_protocol}:
            raise ValueError("PROVIDER_PROTOCOL_MISMATCH")
        if values.get("base_url") not in {None, endpoint.base_url}:
            raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
        model = self._management.model_profile(model_profile_id)
        temporary_state = None
        if model is None and (
            legacy_draft
            or model_profile_id == self._canonical_model_id(provider_id, model_id)
        ):
            candidate = self._draft_configuration(
                provider=provider_id,
                model=model_id,
                base_url=values.get("base_url"),
                endpoint_id=endpoint_id,
                credential_ref=credential_ref,
                protocol_family=values.get("protocol_family"),
                model_profile_id=model_profile_id,
                configuration_fingerprint=values.get("configuration_fingerprint"),
            )
            temporary_state = (
                self._management.state(),
                self._configuration,
                copy.deepcopy(self._provider_profiles),
                self._last_connection_diagnostic,
                self._last_probe_result,
            )
            self._ensure_canonical_profiles(
                candidate,
                persist_legacy=legacy_draft,
            )
            model = self._management.model_profile(candidate.model_profile_id)
        if (
            model is None
            or model.provider_id != provider_id
            or model.endpoint_id != endpoint_id
            or model.remote_model_id != model_id
        ):
            if temporary_state is not None:
                self._management.restore(temporary_state[0])
                self._configuration = temporary_state[1]
                self._provider_profiles = temporary_state[2]
                self._store.save(temporary_state[1])
                self._store.save_profiles(temporary_state[2])
                self._last_connection_diagnostic = temporary_state[3]
                self._last_probe_result = temporary_state[4]
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        identity = BoundModelIdentity(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            credential_ref=credential_ref,
            model_profile_id=model_profile_id,
            model_id=model_id,
        )
        if connection is not None:
            _, unavailable, credential_revision = self._connection_credential_observation(
                connection, create_integrity_key=True
            )
        else:
            _, unavailable, credential_revision = self._canonical_credential_observation(
                provider_id, create_integrity_key=True
            )
        candidate = ProviderConfiguration(
            provider=provider_id,
            model=model_id,
            base_url=endpoint.base_url,
            endpoint_id=endpoint_id,
            credential_ref=credential_ref,
            model_profile_id=model_profile_id,
            credential_fingerprint=credential_revision,
        )
        if (
            values.get("configuration_fingerprint") is not None
            and candidate.configuration_fingerprint != values["configuration_fingerprint"]
        ):
            raise ValueError("PROVIDER_CONFIG_FINGERPRINT_MISMATCH")
        if unavailable:
            diagnostic = _connection_diagnostic(
                status=ProviderConnectionStatus.UNVERIFIED,
                provider=provider_id,
                protocol_family=expected_protocol,
                request_url=_canonical_probe_url(endpoint, model_id),
                stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                provider_error_code="CREDENTIAL_STORE_UNAVAILABLE",
            )
            self._record_explicit_probe(
                identity,
                endpoint=endpoint,
                status=ProviderConnectionStatus.UNVERIFIED,
                diagnostic=diagnostic,
                configuration_fingerprint=candidate.configuration_fingerprint,
                connection_id=connection.connection_id if connection is not None else None,
            )
            raise CredentialUnavailable("provider credential store is unavailable")
        if credential_revision is None and provider_id != "local":
            return self._record_explicit_probe(
                identity,
                endpoint=endpoint,
                status=ProviderConnectionStatus.NOT_CONFIGURED,
                diagnostic=_connection_diagnostic(
                    status=ProviderConnectionStatus.NOT_CONFIGURED,
                    provider=provider_id,
                    protocol_family=expected_protocol,
                    request_url=_canonical_probe_url(endpoint, model_id),
                    stage=ProviderConnectionDiagnosticStage.CREDENTIAL,
                    provider_error_code="CREDENTIAL_NOT_CONFIGURED",
                ),
                configuration_fingerprint=candidate.configuration_fingerprint,
                connection_id=connection.connection_id if connection is not None else None,
                credential_revision=credential_revision,
            )
        try:
            return self._execute_explicit_probe(
                identity,
                endpoint=endpoint,
                credential_revision=credential_revision,
                configuration_fingerprint=candidate.configuration_fingerprint,
                connection_id=connection.connection_id if connection is not None else None,
            )
        finally:
            if temporary_state is not None:
                self._management.restore(temporary_state[0])
                self._configuration = temporary_state[1]
                self._provider_profiles = temporary_state[2]
                self._store.save(temporary_state[1])
                self._store.save_profiles(temporary_state[2])
                self._last_connection_diagnostic = temporary_state[3]
                self._last_probe_result = temporary_state[4]

    def _execute_explicit_probe(
        self,
        identity: BoundModelIdentity,
        *,
        endpoint: EndpointProfile,
        credential_revision: str | None,
        configuration_fingerprint: str,
        connection_id: str | None = None,
    ) -> ProviderProjection:
        if connection_id is not None:
            connection = self._resolve_connection(
                provider_id=identity.provider_id,
                connection_id=connection_id,
            )
            credential, unavailable, _ = self._connection_credential_observation(
                connection, create_integrity_key=False
            )
        else:
            credential, unavailable, _ = self._canonical_credential_observation(
                identity.provider_id, create_integrity_key=False
            )
        protocol = endpoint.protocol_family
        if protocol is ProtocolFamily.CUSTOM:
            protocol = ProtocolFamily.OPENAI_CHAT
        if unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        if protocol is ProtocolFamily.LOCAL_OPENAI_COMPATIBLE:
            api_key = None
        elif credential is None:
            raise CredentialUnavailable("provider credential is not configured")
        else:
            api_key = SecretStr(credential.decode("utf-8"))
        from embedded_copilot.model_gateway import ProtocolHTTPProviderAdapter

        adapter = ProtocolHTTPProviderAdapter(
            provider=identity.provider_id,
            endpoint_id=identity.endpoint_id,
            base_url=endpoint.base_url,
            protocol_family=protocol,
            api_key=api_key,
            timeout_seconds=30.0,
            http_transport=self._http_transport,
            async_transport=self._async_transport,
        )
        gateway = ModelGatewayService(
            providers={identity.provider_id: adapter, identity.endpoint_id: adapter}
        )
        policy = ModelPolicy(
            primary_provider=identity.provider_id,
            model_name=identity.model_id,
        )
        diagnostic_fields = {
            "provider": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "credential_ref": identity.credential_ref,
            "protocol_family": getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
            "request_url": _canonical_probe_url(endpoint, identity.model_id),
            "proxy_mode": "DIRECT_NO_ENV_PROXY",
        }
        diagnostic = lambda status, **kwargs: _connection_diagnostic(
            status=status,
            provider=identity.provider_id,
            protocol_family=diagnostic_fields["protocol_family"],
            request_url=diagnostic_fields["request_url"],
            **kwargs,
        )
        try:
            test_connection_emit(
                "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
                result="BEGIN",
                **diagnostic_fields,
            )
            started_at = time.perf_counter()
            response = gateway.generate_bound(identity, self._health_request(policy))
            latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
        except ModelGatewayProviderFailure as error:
            status = {
                "PROVIDER_AUTH_FAILED": ProviderConnectionStatus.AUTH_FAILED,
                "PROVIDER_MODEL_UNAVAILABLE": ProviderConnectionStatus.MODEL_UNAVAILABLE,
                "PROVIDER_RATE_LIMITED": ProviderConnectionStatus.RATE_LIMITED,
                "PROVIDER_CONFIG_INVALID": ProviderConnectionStatus.CONFIG_INVALID,
            }.get(error.reason_code)
            if status is None and error.reason_code == "PROVIDER_HTTP_ERROR":
                status = (
                    ProviderConnectionStatus.CONFIG_INVALID
                    if error.safe_http_status in {400, 422}
                    else ProviderConnectionStatus.MODEL_UNAVAILABLE
                    if error.safe_http_status == 404
                    else ProviderConnectionStatus.NETWORK_UNAVAILABLE
                )
            status = status or ProviderConnectionStatus.NETWORK_UNAVAILABLE
            return self._record_explicit_probe(
                identity,
                endpoint=endpoint,
                status=status,
                diagnostic=diagnostic(status, error=error),
                configuration_fingerprint=configuration_fingerprint,
                credential_revision=credential_revision,
                connection_id=connection_id,
            )
        except (ModelProviderTimeout, ModelProviderError, OSError) as error:
            status = ProviderConnectionStatus.NETWORK_UNAVAILABLE
            return self._record_explicit_probe(
                identity,
                endpoint=endpoint,
                status=status,
                diagnostic=diagnostic(status, error=error),
                configuration_fingerprint=configuration_fingerprint,
                credential_revision=credential_revision,
                connection_id=connection_id,
            )
        except (TypeError, ValueError, UnicodeError) as error:
            status = ProviderConnectionStatus.CONFIG_INVALID
            return self._record_explicit_probe(
                identity,
                endpoint=endpoint,
                status=status,
                diagnostic=diagnostic(status, error=error),
                configuration_fingerprint=configuration_fingerprint,
                credential_revision=credential_revision,
                connection_id=connection_id,
            )
        return self._record_explicit_probe(
            identity,
            endpoint=endpoint,
            status=ProviderConnectionStatus.AVAILABLE,
            diagnostic=diagnostic(ProviderConnectionStatus.AVAILABLE),
            verification=self._verification_from_response(
                response,
                requested_model=identity.model_id,
                protocol_adapter=diagnostic_fields["protocol_family"]
                or ProtocolFamily.OPENAI_CHAT.value,
                latency_ms=latency_ms,
            ),
            response=response,
            latency_ms=latency_ms,
            configuration_fingerprint=configuration_fingerprint,
            credential_revision=credential_revision,
            connection_id=connection_id,
        )

    def _record_explicit_probe(
        self,
        identity: BoundModelIdentity,
        *,
        endpoint: EndpointProfile,
        status: ProviderConnectionStatus,
        diagnostic: ProviderConnectionDiagnostic,
        configuration_fingerprint: str | None,
        credential_revision: str | None = None,
        connection_id: str | None = None,
        verification: Mapping[str, object] | None = None,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
    ) -> ProviderProjection:
        self._last_connection_diagnostic = diagnostic
        receipt, persisted, receipt_error = self._append_verification_receipt(
            status,
            diagnostic=diagnostic,
            endpoint=endpoint,
            requested_model=identity.model_id,
            response=response,
            latency_ms=latency_ms,
            probe_identity=identity,
            configuration_fingerprint=configuration_fingerprint,
            credential_revision=credential_revision,
            connection_id=connection_id,
        )
        projection_fields = self._receipt_projection_fields(receipt)
        projection_fields.update(verification or {})
        model = self._management.model_profile(identity.model_profile_id)
        if model is not None:
            availability = (
                ModelAvailability.AVAILABLE
                if status is ProviderConnectionStatus.AVAILABLE
                else ModelAvailability.RATE_LIMITED
                if status is ProviderConnectionStatus.RATE_LIMITED
                else ModelAvailability.PERMISSION_DENIED
                if status is ProviderConnectionStatus.AUTH_FAILED
                else ModelAvailability.MODEL_UNAVAILABLE
                if status is ProviderConnectionStatus.MODEL_UNAVAILABLE
                else ModelAvailability.UNKNOWN
            )
            verified_at = self._now() if status in {
                ProviderConnectionStatus.AVAILABLE,
                ProviderConnectionStatus.RATE_LIMITED,
            } else model.verified_at
            self._management.models.upsert(
                model.model_copy(
                    update={
                        "verification_status": VerificationStatus.VERIFIED
                        if status in {
                            ProviderConnectionStatus.AVAILABLE,
                            ProviderConnectionStatus.RATE_LIMITED,
                        }
                        else VerificationStatus.UNVERIFIED,
                        "availability": availability,
                        "availability_status": availability,
                        "availability_source": ModelCatalogSource.PROVIDER_API
                        if status in {
                            ProviderConnectionStatus.AVAILABLE,
                            ProviderConnectionStatus.RATE_LIMITED,
                        }
                        else model.availability_provenance,
                        "availability_provenance": ModelCatalogSource.RUNTIME_VERIFICATION
                        if status in {
                            ProviderConnectionStatus.AVAILABLE,
                            ProviderConnectionStatus.RATE_LIMITED,
                        }
                        else model.availability_provenance,
                        "credential_revision": credential_revision,
                        "endpoint_revision": ProviderModelPool.endpoint_revision(endpoint),
                        "verified_at": verified_at,
                        "metadata": (("health_status", endpoint.health_status),),
                    }
                )
            )
            if self._model_pool is not None:
                self._model_pool.record_verification(
                    endpoint,
                    model_id=identity.model_id,
                    credential_revision=credential_revision,
                    availability=availability,
                    verified_at=verified_at,
                    connection_id=connection_id,
                )
        health = (
            "AVAILABLE"
            if status is ProviderConnectionStatus.AVAILABLE
            else "QUOTA_EXHAUSTED"
            if status is ProviderConnectionStatus.RATE_LIMITED
            else "AUTH_FAILED"
            if status is ProviderConnectionStatus.AUTH_FAILED
            else "UNAVAILABLE"
            if status is not ProviderConnectionStatus.NOT_CONFIGURED
            else "UNKNOWN"
        )
        self._management.upsert_endpoint(
            endpoint.model_copy(update={"health_status": health, "updated_at": self._now()})
        )
        if self._management.persistent:
            self._management.save()
        if status is ProviderConnectionStatus.AVAILABLE and connection_id is not None:
            self._refresh_canonical_binding(transport_only=True)
        self._last_probe_result = {
            "provider_id": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "model_profile_id": identity.model_profile_id,
            "model_id": identity.model_id,
            "connection_id": connection_id,
            "status": status,
            "configuration_fingerprint": configuration_fingerprint,
            **projection_fields,
            "verification_persisted": persisted,
            "verification_persistence_error": receipt_error,
        }
        if connection_id is not None:
            connection = self._resolve_connection(
                connection_id=connection_id,
                require_active=False,
            )
            return self._canonical_projection(
                self._configuration_for_connection(connection),
                connection=connection,
            )
        return self.projection(provider_id=identity.provider_id)

    def _draft_configuration(
        self,
        *,
        provider: object,
        model: object,
        base_url: object,
        endpoint_id: object,
        credential_ref: object,
        protocol_family: object,
        model_profile_id: object,
        configuration_fingerprint: object,
    ) -> ProviderConfiguration:
        if not isinstance(provider, str) or not isinstance(model, str):
            raise TypeError("provider draft is invalid")
        provider = provider.strip().lower()
        model = model.strip()
        preset = self._provider_preset(provider)
        if preset is None:
            raise ValueError("provider is unsupported")
        resolved_endpoint = (
            endpoint_id.strip()
            if isinstance(endpoint_id, str) and endpoint_id.strip()
            else f"endpoint-{provider}"
        )
        endpoint = self._management.endpoint_profile(resolved_endpoint)
        if endpoint is not None and endpoint.provider_id != provider:
            raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
        is_official = preset.provider_kind not in {ProviderKind.CUSTOM, ProviderKind.LOCAL}
        preset_url = preset.default_endpoint_template
        if is_official and resolved_endpoint == f"endpoint-{provider}":
            if isinstance(base_url, str) and base_url.rstrip("/") != (preset_url or "").rstrip("/"):
                raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
            resolved_url = preset_url
        else:
            resolved_url = (
                base_url.strip().rstrip("/")
                if isinstance(base_url, str) and base_url.strip()
                else endpoint.base_url
                if endpoint is not None
                else preset_url
            )
        if resolved_url is None:
            raise ValueError("provider endpoint is required")
        if endpoint is not None and resolved_url != endpoint.base_url:
            raise ValueError("PROVIDER_ENDPOINT_MISMATCH")
        expected_protocol = (
            endpoint.protocol_family
            if endpoint is not None
            else next(iter(preset.supported_protocols), ProtocolFamily.OPENAI_CHAT)
        )
        expected_protocol_value = getattr(expected_protocol, "value", expected_protocol)
        if protocol_family is not None and protocol_family != expected_protocol_value:
            raise ValueError("PROVIDER_PROTOCOL_MISMATCH")
        expected_credential_ref = endpoint.credential_ref if endpoint is not None else (
            None if provider == "local" else f"credential:{provider}"
        )
        if credential_ref is not None and credential_ref != expected_credential_ref:
            raise ValueError("PROVIDER_CREDENTIAL_REF_MISMATCH")
        current_model = next(
            (
                item
                for item in (
                    self.model_pool(provider_id=provider)
                    if self._active_connections_for_provider(provider)
                    else ()
                )
                if item.remote_model_id == model
            ),
            None,
        )
        catalog_model = next(
            (item for item in official_model_catalog(provider, resolved_endpoint) if item.remote_model_id == model),
            None,
        )
        if is_official and catalog_model is None:
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        expected_model_profile_id = current_model.model_profile_id if current_model is not None else self._canonical_model_id(provider, model)
        if model_profile_id is not None and model_profile_id != expected_model_profile_id:
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        _, unavailable, credential_fingerprint = self._canonical_credential_observation(
            provider, create_integrity_key=True
        )
        if unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        candidate = ProviderConfiguration(
            provider=provider,
            model=model,
            base_url=resolved_url,
            endpoint_id=resolved_endpoint,
            credential_ref=expected_credential_ref,
            model_profile_id=expected_model_profile_id,
            credential_fingerprint=credential_fingerprint,
        )
        if configuration_fingerprint is not None and (
            not isinstance(configuration_fingerprint, str)
            or candidate.configuration_fingerprint != configuration_fingerprint
        ):
            raise ValueError("PROVIDER_CONFIG_FINGERPRINT_MISMATCH")
        return candidate

    @staticmethod
    def _verification_from_response(
        response: ModelResponse,
        *,
        requested_model: str,
        protocol_adapter: str,
        latency_ms: float,
    ) -> dict[str, object]:
        verified_model = response.actual_model or response.remote_model_id or response.model_name
        return {
            "verified_model": verified_model,
            "available_models": (verified_model,),
            "model_available": True,
            "response_model": verified_model,
            "response_id": response.provider_response_id,
            "latency_ms": latency_ms,
            "http_status": 200,
            "protocol_adapter": protocol_adapter,
        }

    def _verification_target(
        self,
        *,
        endpoint: EndpointProfile | None,
        requested_model: str,
        probe_identity: BoundModelIdentity | None = None,
        configuration_fingerprint: str | None = None,
        credential_revision: str | None = None,
        model_revision: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderVerificationTarget:
        endpoint_id = (
            endpoint.endpoint_id
            if endpoint is not None
            else self._configuration.endpoint_id or "unknown-endpoint"
        )
        if endpoint is not None:
            # Health and timestamps are mutable probe output, not configuration
            # identity.  Excluding them keeps a receipt current after the probe
            # updates the endpoint health projection.
            endpoint_revision = projection_revision(
                {
                    "endpoint_id": endpoint.endpoint_id,
                    "provider_id": endpoint.provider_id,
                    "protocol_family": (
                        endpoint.protocol_family.value
                        if isinstance(endpoint.protocol_family, ProtocolFamily)
                        else endpoint.protocol_family
                    ),
                    "base_url": endpoint.base_url,
                    "credential_ref": endpoint.credential_ref,
                    "enabled": endpoint.enabled,
                }
            )
        else:
            endpoint_revision = projection_revision(
                {
                    "endpoint_id": endpoint_id,
                    "provider_id": self._configuration.provider,
                    "base_url": self._configuration.base_url,
                }
            )
        model = (
            self._management.model_profile(
                probe_identity.model_profile_id
                if probe_identity is not None
                else self._configuration.model_profile_id
            )
            if self._management is not None
            and (
                probe_identity is not None and probe_identity.model_profile_id
                or probe_identity is None and self._configuration.model_profile_id
            )
            else None
        )
        resolved_model_revision = model_revision or (
            projection_revision(
                {
                    "model_profile_id": model.model_profile_id,
                    "provider_id": model.provider_id,
                    "endpoint_id": model.endpoint_id,
                    "remote_model_id": model.remote_model_id,
                }
            )
            if model is not None
            else projection_revision({"model_id": requested_model})
        )
        return ProviderVerificationTarget(
            provider_id=(
                probe_identity.provider_id
                if probe_identity is not None
                else self._configuration.provider
            ),
            connection_id=connection_id,
            endpoint_id=endpoint_id,
            model_id=requested_model,
            credential_revision=(
                credential_revision
                or (
                    self._configuration.credential_fingerprint
                    or "UNKNOWN"
                )
            ),
            endpoint_revision=endpoint_revision,
            model_revision=resolved_model_revision,
            configuration_fingerprint=(
                configuration_fingerprint
                or self._configuration.configuration_fingerprint
            ),
        )

    def _build_verification_receipt(
        self,
        status: ProviderConnectionStatus,
        *,
        diagnostic: ProviderConnectionDiagnostic | None,
        endpoint: EndpointProfile | None,
        requested_model: str,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
        probe_identity: BoundModelIdentity | None = None,
        configuration_fingerprint: str | None = None,
        credential_revision: str | None = None,
        model_revision: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderVerificationReceipt:
        completed_at = self._now()
        requested_at = (
            completed_at - timedelta(milliseconds=latency_ms)
            if latency_ms is not None
            else completed_at
        )
        target = self._verification_target(
            endpoint=endpoint,
            requested_model=requested_model,
            probe_identity=probe_identity,
            configuration_fingerprint=configuration_fingerprint,
            credential_revision=credential_revision,
            model_revision=model_revision,
            connection_id=connection_id,
        )
        verified_model = (
            response.actual_model or response.remote_model_id or response.model_name
            if response is not None
            else None
        )
        response_parse_status = (
            ReceiptResponseParseStatus.PASS
            if response is not None
            else ReceiptResponseParseStatus.FAIL
            if diagnostic is not None
            and diagnostic.provider_error_code
            in {
                "PROVIDER_PARSE_FAILED",
                "ANSWER_EMPTY",
                "ANSWER_SCHEMA_INVALID",
                "ANSWER_CONTRACT_MISMATCH",
            }
            else ReceiptResponseParseStatus.NOT_APPLICABLE
        )
        if response is not None:
            receipt_status = (
                ReceiptVerificationStatus.PASS
                if verified_model == requested_model
                else ReceiptVerificationStatus.FAIL
            )
            model_available: bool | str = True
        elif status is ProviderConnectionStatus.RATE_LIMITED:
            receipt_status = ReceiptVerificationStatus.PARTIAL
            model_available = True
        elif status is ProviderConnectionStatus.NOT_CONFIGURED or (
            diagnostic is not None
            and diagnostic.stage is ProviderConnectionDiagnosticStage.CREDENTIAL
        ):
            receipt_status = ReceiptVerificationStatus.BLOCKED
            model_available = "UNKNOWN"
        else:
            receipt_status = ReceiptVerificationStatus.FAIL
            model_available = False if status is ProviderConnectionStatus.MODEL_UNAVAILABLE else "UNKNOWN"
        usage = response.usage if response is not None else None
        request_was_sent = response is not None or (
            diagnostic is not None
            and diagnostic.stage
            not in {
                ProviderConnectionDiagnosticStage.CREDENTIAL,
                ProviderConnectionDiagnosticStage.CONFIGURATION,
            }
        )
        provider_cost = usage.provider_reported_cost if usage is not None else None
        estimated_cost = usage.estimated_cost if usage is not None else None
        cost = provider_cost if provider_cost is not None else estimated_cost if estimated_cost is not None else "UNKNOWN"
        cost_source = (
            "PROVIDER_REPORTED"
            if provider_cost is not None
            else "ESTIMATED"
            if estimated_cost is not None
            else "UNKNOWN"
        )
        return ProviderVerificationReceipt.create(
            provider_id=target.provider_id,
            connection_id=target.connection_id,
            endpoint_id=target.endpoint_id,
            model_id=target.model_id,
            credential_ref=(
                endpoint.credential_ref
                if endpoint is not None
                else probe_identity.credential_ref
                if probe_identity is not None
                else self._configuration.credential_ref
            ),
            credential_revision=target.credential_revision,
            endpoint_revision=target.endpoint_revision,
            model_revision=target.model_revision,
            configuration_fingerprint=target.configuration_fingerprint or "UNKNOWN",
            protocol_family=(
                endpoint.protocol_family.value
                if endpoint is not None and isinstance(endpoint.protocol_family, ProtocolFamily)
                else diagnostic.protocol_family
                if diagnostic is not None and diagnostic.protocol_family
                else "UNKNOWN"
            ),
            requested_at=requested_at,
            completed_at=completed_at,
            latency_ms=latency_ms,
            request_url_safe=diagnostic.request_url if diagnostic is not None else None,
            http_status=(
                diagnostic.http_status
                if diagnostic is not None and diagnostic.http_status is not None
                else 200
                if response is not None
                else "NOT_OBTAINED"
            ),
            provider_request_id=response.provider_response_id if response is not None else None,
            requested_model=requested_model,
            verified_model=verified_model,
            response_parse_status=response_parse_status,
            model_available=model_available,
            connection_status=status.value,
            provider_error_code=diagnostic.provider_error_code if diagnostic is not None else None,
            normalized_error_code=status.value,
            input_tokens=usage.input_tokens if usage is not None else "UNKNOWN",
            output_tokens=usage.output_tokens if usage is not None else "UNKNOWN",
            cached_input_tokens=usage.cached_input_tokens if usage is not None and usage.cached_input_tokens is not None else "UNKNOWN",
            reasoning_tokens=usage.reasoning_tokens if usage is not None and usage.reasoning_tokens is not None else "UNKNOWN",
            total_tokens=usage.total_tokens if usage is not None else "UNKNOWN",
            usage_source=usage.usage_source if usage is not None else "UNKNOWN",
            cost=cost,
            currency=usage.currency if usage is not None and usage.currency is not None else "UNKNOWN",
            cost_source=cost_source,
            cache_status="PROVIDER_REPORTED" if usage is not None and usage.cached_input_tokens is not None else "UNKNOWN",
            message_count=1 if request_was_sent else 0,
            request_shape=(
                "single_user_message" if request_was_sent else "not_sent"
            ),
            verification_status=receipt_status,
        )

    def _append_verification_receipt(
        self,
        status: ProviderConnectionStatus,
        *,
        diagnostic: ProviderConnectionDiagnostic | None,
        endpoint: EndpointProfile | None,
        requested_model: str,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
        probe_identity: BoundModelIdentity | None = None,
        configuration_fingerprint: str | None = None,
        credential_revision: str | None = None,
        model_revision: str | None = None,
        connection_id: str | None = None,
    ) -> tuple[ProviderVerificationReceipt | None, bool, str | None]:
        receipt: ProviderVerificationReceipt | None = None
        try:
            receipt = self._build_verification_receipt(
                status,
                diagnostic=diagnostic,
                endpoint=endpoint,
                requested_model=requested_model,
                response=response,
                latency_ms=latency_ms,
                probe_identity=probe_identity,
                configuration_fingerprint=configuration_fingerprint,
                credential_revision=credential_revision,
                model_revision=model_revision,
                connection_id=connection_id,
            )
            self._verification_store.append(receipt)
        except Exception as error:  # noqa: BLE001 - projection remains safe if evidence storage fails
            test_connection_emit(
                "TEST_CONNECTION_VERIFICATION_RECEIPT_FAILURE",
                result="PERSISTENCE_FAILURE",
                error=error,
                provider=(
                    probe_identity.provider_id
                    if probe_identity is not None
                    else self._configuration.provider
                ),
            )
            return receipt, False, "RECEIPT_PERSISTENCE_FAILED"
        return receipt, True, None

    @staticmethod
    def _receipt_projection_fields(
        receipt: ProviderVerificationReceipt | None,
    ) -> dict[str, object]:
        if receipt is None:
            return {}
        return {
            "verified_model": receipt.verified_model,
            "available_models": (
                (receipt.verified_model,) if receipt.verified_model is not None else ()
            ),
            "model_available": (
                receipt.model_available
                if isinstance(receipt.model_available, bool)
                else None
            ),
            "response_model": receipt.verified_model,
            "response_id": receipt.provider_request_id,
            "latency_ms": receipt.latency_ms,
            "http_status": (
                receipt.http_status if isinstance(receipt.http_status, int) else None
            ),
            "protocol_adapter": (
                receipt.protocol_family
                if receipt.protocol_family != "UNKNOWN"
                else None
            ),
        }

    def _canonical_record(
        self,
        status: ProviderConnectionStatus,
        *,
        diagnostic: ProviderConnectionDiagnostic | None = None,
        verification: Mapping[str, object] | None = None,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
    ) -> ProviderProjection:
        self._last_connection_diagnostic = diagnostic
        endpoint = (
            self._management.endpoint_profile(self._configuration.endpoint_id)
            if self._configuration.endpoint_id
            else None
        )
        requested_model = self._configuration.model
        receipt, receipt_persisted, receipt_error = self._append_verification_receipt(
            status,
            diagnostic=diagnostic,
            endpoint=endpoint,
            requested_model=requested_model,
            response=response,
            latency_ms=latency_ms,
        )
        projection_fields = self._receipt_projection_fields(receipt)
        projection_fields.update(verification or {})
        recorded_verification_status = (
            ReceiptVerificationStatus.PARTIAL.value
            if receipt is not None
            and not receipt_persisted
            and status is ProviderConnectionStatus.AVAILABLE
            else receipt.verification_status.value
            if receipt is not None
            else None
        )
        if endpoint is not None:
            health = (
                "AVAILABLE"
                if status is ProviderConnectionStatus.AVAILABLE
                else {
                    ProviderConnectionStatus.AUTH_FAILED: "AUTH_FAILED",
                    ProviderConnectionStatus.MODEL_UNAVAILABLE: "UNAVAILABLE",
                    ProviderConnectionStatus.RATE_LIMITED: "QUOTA_EXHAUSTED",
                    ProviderConnectionStatus.NETWORK_UNAVAILABLE: "UNAVAILABLE",
                    ProviderConnectionStatus.CONFIG_INVALID: "UNAVAILABLE",
                    ProviderConnectionStatus.NOT_CONFIGURED: "UNKNOWN",
                }.get(status, "UNKNOWN")
            )
            self._management.upsert_endpoint(
                endpoint.model_copy(
                    update={"health_status": health, "updated_at": self._now()}
                )
            )
        self._configuration = self._store.record_test(
            self._configuration,
            status,
            self._now(),
            verification_id=receipt.verification_id if receipt is not None else None,
            verification_status=recorded_verification_status,
            verification_persisted=receipt_persisted,
            verification_persistence_error=receipt_error,
            **projection_fields,
        )
        self._provider_profiles[self._configuration.provider] = self._configuration
        self._store.save_profiles(self._provider_profiles)
        model_id = self._configuration.model_profile_id
        if model_id:
            model = self._management.model_profile(model_id)
            if model is not None:
                availability = (
                    ModelAvailability.AVAILABLE
                    if status is ProviderConnectionStatus.AVAILABLE
                    else ModelAvailability.RATE_LIMITED
                    if status is ProviderConnectionStatus.RATE_LIMITED
                    else ModelAvailability.PERMISSION_DENIED
                    if status is ProviderConnectionStatus.AUTH_FAILED
                    else ModelAvailability.MODEL_UNAVAILABLE
                    if status is ProviderConnectionStatus.MODEL_UNAVAILABLE
                    else ModelAvailability.UNKNOWN
                )
                self._management.models.upsert(
                    model.model_copy(
                        update={
                            "record_provenance": (
                                model.record_provenance
                                if model.record_provenance
                                in {
                                    RecordProvenance.ENDPOINT_DISCOVERY,
                                    RecordProvenance.EXPLICIT_CONFIGURATION,
                                }
                                else RecordProvenance.EXPLICIT_CONFIGURATION
                            ),
                            "verification_status": VerificationStatus.VERIFIED
                            if status
                            in {
                                ProviderConnectionStatus.AVAILABLE,
                                ProviderConnectionStatus.RATE_LIMITED,
                            }
                            else VerificationStatus.UNVERIFIED,
                            "metadata": (
                                (
                                    "health_status",
                                    "AVAILABLE"
                                    if status is ProviderConnectionStatus.AVAILABLE
                                    else "RATE_LIMITED"
                                    if status is ProviderConnectionStatus.RATE_LIMITED
                                    else "UNAVAILABLE",
                                ),
                            ),
                            "availability": availability,
                            "availability_status": availability,
                            "availability_source": ModelCatalogSource.PROVIDER_API
                            if status in {
                                ProviderConnectionStatus.AVAILABLE,
                                ProviderConnectionStatus.RATE_LIMITED,
                            }
                            else model.availability_provenance,
                            "availability_provenance": ModelCatalogSource.RUNTIME_VERIFICATION
                            if status in {
                                ProviderConnectionStatus.AVAILABLE,
                                ProviderConnectionStatus.RATE_LIMITED,
                            }
                            else model.availability_provenance,
                            "credential_revision": self._configuration.credential_fingerprint,
                            "endpoint_revision": ProviderModelPool.endpoint_revision(endpoint)
                            if endpoint is not None
                            else model.endpoint_revision,
                            "verified_at": self._configuration.tested_at
                            if status in {
                                ProviderConnectionStatus.AVAILABLE,
                                ProviderConnectionStatus.RATE_LIMITED,
                            }
                            else model.verified_at,
                            "capability_sources": (
                                tuple(
                                    item
                                    for item in model.capability_sources
                                    if item.capability is not ModelCapability.TEXT
                                )
                                + (
                                    (
                                        CapabilityEvidence(
                                            capability=ModelCapability.TEXT,
                                            provenance=CapabilityProvenance.PROBED,
                                            status=CapabilityStatus.PASS,
                                            source="runtime_verification",
                                            observed_at=self._configuration.tested_at,
                                            provider_id=model.provider_id,
                                            endpoint_id=model.endpoint_id,
                                            credential_ref=(
                                                endpoint.credential_ref
                                                if endpoint is not None
                                                else None
                                            ),
                                            model_profile_id=model.model_profile_id,
                                            model_id=model.remote_model_id,
                                            credential_revision=self._configuration.credential_fingerprint,
                                            endpoint_revision=(
                                                ProviderModelPool.endpoint_revision(endpoint)
                                                if endpoint is not None
                                                else model.endpoint_revision
                                            ),
                                        ),
                                    )
                                    if status is ProviderConnectionStatus.AVAILABLE
                                    else ()
                                )
                            ),
                            "capabilities": (
                                model.capabilities
                                if status is not ProviderConnectionStatus.AVAILABLE
                                or ModelCapability.TEXT in model.capabilities
                                else model.capabilities + (ModelCapability.TEXT,)
                            ),
                            "input_modalities": (
                                model.input_modalities
                                if status is not ProviderConnectionStatus.AVAILABLE
                                or ModelCapability.TEXT in model.input_modalities
                                else model.input_modalities + (ModelCapability.TEXT,)
                            ),
                            "output_modalities": (
                                model.output_modalities
                                if status is not ProviderConnectionStatus.AVAILABLE
                                or ModelCapability.TEXT in model.output_modalities
                                else model.output_modalities + (ModelCapability.TEXT,)
                            ),
                        }
                    )
                )
                if self._model_pool is not None and endpoint is not None:
                    self._model_pool.record_verification(
                        endpoint,
                        model_id=self._configuration.model,
                        credential_revision=self._configuration.credential_fingerprint,
                        availability=availability,
                        verified_at=self._configuration.tested_at,
                    )
        if self._management.persistent:
            self._management.save()
        if status is ProviderConnectionStatus.AVAILABLE:
            self._refresh_canonical_binding(transport_only=False)
        else:
            self.binding.invalidate(status.value)
        return self.projection()

    def _record(
        self,
        status: ProviderConnectionStatus,
        *,
        diagnostic: ProviderConnectionDiagnostic | None = None,
        verification: Mapping[str, object] | None = None,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
    ) -> ProviderProjection:
        self._last_connection_diagnostic = diagnostic
        endpoint = None
        requested_model = self._configuration.model
        receipt, receipt_persisted, receipt_error = self._append_verification_receipt(
            status,
            diagnostic=diagnostic,
            endpoint=endpoint,
            requested_model=requested_model,
            response=response,
            latency_ms=latency_ms,
        )
        projection_fields = self._receipt_projection_fields(receipt)
        projection_fields.update(verification or {})
        recorded_verification_status = (
            ReceiptVerificationStatus.PARTIAL.value
            if receipt is not None
            and not receipt_persisted
            and status is ProviderConnectionStatus.AVAILABLE
            else receipt.verification_status.value
            if receipt is not None
            else None
        )
        test_connection_emit(
            "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN",
            result="BEGIN",
            provider="deepseek",
        )
        try:
            self._configuration = self._store.record_test(
                self._configuration,
                status,
                self._now(),
                verification_id=receipt.verification_id if receipt is not None else None,
                verification_status=recorded_verification_status,
                verification_persisted=receipt_persisted,
                verification_persistence_error=receipt_error,
                **projection_fields,
            )
        except Exception as error:
            if status is ProviderConnectionStatus.AVAILABLE:
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            test_connection_emit(
                "TEST_CONNECTION_RECEIPT_PERSIST_FAILURE",
                result="FAILURE",
                error=error,
                provider="deepseek",
            )
            raise
        test_connection_emit(
            "TEST_CONNECTION_RECEIPT_PERSIST_SUCCESS",
            result="SUCCESS",
            provider="deepseek",
        )
        if status is not ProviderConnectionStatus.AVAILABLE:
            self.binding.invalidate(status.value)
        return self.projection()

    def _apply_persisted_binding(self) -> None:
        with self._lock:
            if self._management is not None:
                self._apply_persisted_canonical_binding()
                return
            credential, unavailable, credential_fingerprint = (
                self._credential_observation(create_integrity_key=False)
            )
            if unavailable:
                self.binding.invalidate("PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
            elif credential is None:
                self.binding.invalidate("PROVIDER_NOT_CONFIGURED")
                recovery_emit(
                    "CREDENTIAL_RECOVERY_BINDING_INVALIDATED", result="INVALIDATED"
                )
                if self._configuration.credential_fingerprint is not None:
                    self._configuration = self._candidate(
                        status=ProviderConnectionStatus.NOT_CONFIGURED,
                        credential_fingerprint=None,
                    )
            elif credential_fingerprint is None:
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            elif credential_fingerprint != self._configuration.credential_fingerprint:
                recovery_emit("CREDENTIAL_RECOVERY_BINDING_MISMATCH", result="MISMATCH")
                self._configuration = self._candidate(
                    status=ProviderConnectionStatus.UNVERIFIED,
                    credential_fingerprint=credential_fingerprint,
                )
                self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
            elif (
                self._configuration.last_test_status
                is ProviderConnectionStatus.AVAILABLE
                and self._configuration.tested_configuration_fingerprint
                == self._configuration.configuration_fingerprint
            ):
                try:
                    self._install_binding(credential)
                except (TypeError, ValueError, ModelProviderError) as error:
                    self._configuration = self._candidate(
                        status=ProviderConnectionStatus.UNVERIFIED,
                        credential_fingerprint=credential_fingerprint,
                    )
                    self.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
                    recovery_emit(
                        "CREDENTIAL_RECOVERY_BINDING_MISMATCH",
                        result="MISMATCH",
                        error=error,
                    )
                else:
                    recovery_emit("CREDENTIAL_RECOVERY_BINDING_MATCH", result="MATCH")
            else:
                self.binding.invalidate(self._configuration.last_test_status.value)

    def _apply_persisted_canonical_binding(self) -> None:
        # Restore only the connection-scoped transport snapshot.  Probe and
        # selected-model evidence are intentionally not prerequisites here;
        # a later governed request performs selection and exact binding.
        self._refresh_canonical_binding(transport_only=True)

    def _install_binding(self, credential: bytes) -> None:
        settings = ProductionModelSettings(
            provider=ProductionProvider.DEEPSEEK,
            model_name=self._configuration.model,
            api_key=SecretStr(credential.decode("utf-8")),
            base_url=None,
        )
        binding = create_production_model_binding(
            settings,
            http_transport=self._http_transport,
            async_transport=self._async_transport,
        )
        adapter = getattr(binding.gateway, "_providers", {}).get(
            self._configuration.provider
        )
        if adapter is not None and isinstance(binding.gateway, ModelGatewayService):
            entry = ResolvedTransportEntry(
                provider_id=self._configuration.provider,
                endpoint_id=self._configuration.endpoint_id
                or f"endpoint-{self._configuration.provider}",
                credential_ref=self._configuration.credential_ref
                or f"credential:{self._configuration.provider}",
                adapter=adapter,
                endpoint_revision=self._configuration.configuration_fingerprint,
                credential_revision=self._configuration.credential_fingerprint,
            )
            self.binding.install_transport(
                binding.gateway,
                (entry,),
                legacy_policy=binding.model_policy,
                endpoint_id=self._configuration.endpoint_id,
                credential_ref=self._configuration.credential_ref,
                model_profile_id=self._configuration.model_profile_id,
            )
        else:
            self.binding.install(binding.gateway, binding.model_policy)

    def _read_credential(self, *, raise_on_unavailable: bool = False) -> bytes | None:
        credential, unavailable, _ = self._credential_observation(
            create_integrity_key=False
        )
        if unavailable and raise_on_unavailable:
            raise CredentialUnavailable("provider credential store is unavailable")
        return credential

    @staticmethod
    def _validate_credential(secret: SecretStr) -> bytes:
        if not isinstance(secret, SecretStr):
            raise TypeError("credential is invalid")
        value = secret.get_secret_value()
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.strip()) > _MAX_PROVIDER_CREDENTIAL_LENGTH
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in value.strip()
            )
        ):
            raise ValueError("credential is invalid")
        return value.strip().encode("utf-8")

    @staticmethod
    def _verify_credential_write(
        credentials: CredentialProviderPort,
        purpose: str,
        expected: bytes,
    ) -> None:
        emit("PRIMARY_CREDENTIAL_READBACK_BEGIN", result="BEGIN")
        try:
            resolved = credentials.get(purpose)
        except Exception as error:
            emit(
                "PRIMARY_CREDENTIAL_READBACK_FAILURE",
                result="FAILURE",
                error=error,
            )
            raise
        if resolved != expected:
            error = CredentialUnavailable(
                "provider credential write could not be verified"
            )
            emit(
                "PRIMARY_CREDENTIAL_READBACK_FAILURE",
                result="FAILURE",
                error=error,
            )
            raise error
        emit("PRIMARY_CREDENTIAL_READBACK_SUCCESS", result="SUCCESS")

    def _credential_observation(
        self,
        *,
        create_integrity_key: bool,
    ) -> tuple[bytes | None, bool, str | None]:
        """Read credential state without mutation unless explicitly requested."""

        recovery_emit("CREDENTIAL_RECOVERY_BEGIN", result="BEGIN")
        try:
            credential = self._credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE)
        except CredentialUnavailable as error:
            recovery_emit(
                "CREDENTIAL_RECOVERY_PRIMARY_ERROR", result="ERROR", error=error
            )
            return None, True, None
        if credential is None:
            recovery_emit("CREDENTIAL_RECOVERY_PRIMARY_ABSENT", result="ABSENT")
            return None, False, None
        recovery_emit("CREDENTIAL_RECOVERY_PRIMARY_PRESENT", result="PRESENT")
        if not isinstance(credential, bytes) or not credential:
            return None, True, None
        try:
            fingerprint = _credential_fingerprint(
                self._credentials,
                credential,
                create_key=create_integrity_key,
            )
        except CredentialUnavailable:
            return credential, True, None
        return credential, False, fingerprint

    def _candidate(
        self,
        *,
        status: ProviderConnectionStatus,
        credential_fingerprint: str | None,
        provider: str | None = None,
        model: str | None = None,
    ) -> ProviderConfiguration:
        return ProviderConfiguration(
            provider=self._configuration.provider if provider is None else provider,
            connection_name=self._configuration.connection_name,
            model=self._configuration.model if model is None else model,
            base_url=DEEPSEEK_BASE_URL,
            last_test_status=status,
            credential_fingerprint=credential_fingerprint,
        )

    def _restore_credential(self, previous: bytes | None) -> None:
        try:
            if previous is None:
                self._credentials.delete(CHAT_PROVIDER_CREDENTIAL_PURPOSE)
            else:
                self._credentials.set(CHAT_PROVIDER_CREDENTIAL_PURPOSE, previous)
        except Exception:
            self.binding.invalidate("PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
            raise

    @staticmethod
    def _health_request(policy: ModelPolicy) -> ModelRequest:
        context = EngineeringContextSnapshot.create(
            project_id="health-check",
            tenant_id="health-check",
            task_description="connection health check",
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=0.0,
            provenance=(),
            verification_status="VERIFIED",
        )
        return ModelRequest(
            request_id="health-check",
            task="Respond with OK.",
            engineering_context=context,
            system_instruction="Respond with OK only.",
            model_policy=policy,
            task_type="connection_probe",
            constraints=(),
        )


class ProviderConfigurationCapabilityProbeAdapter:
    """Run revision-bound TEXT capability probes through the canonical provider service."""

    def __init__(self, service: ProviderConfigurationService) -> None:
        self._service = service

    def probe(
        self,
        endpoint: EndpointProfile,
        model: ModelProfile,
        probe_type: str,
    ) -> CapabilityProbeResult:
        if (
            model.provider_id != endpoint.provider_id
            or model.endpoint_id != endpoint.endpoint_id
        ):
            raise ValueError("PROVIDER_MODEL_MISMATCH")
        credential_ref = endpoint.credential_ref or (
            "credential:local" if endpoint.provider_id == "local" else None
        )
        if credential_ref is None:
            raise ValueError("PROVIDER_CREDENTIAL_REF_MISMATCH")
        connection = self._service._connection_for_endpoint(endpoint.endpoint_id)
        if connection is None or connection.provider_id != endpoint.provider_id:
            raise ValueError("PROVIDER_CONNECTION_NOT_FOUND")
        credential_revision = self._service.credential_revision_for_connection(
            connection.connection_id
        )
        endpoint_revision = ProviderModelPool.endpoint_revision(endpoint)
        if (
            (credential_revision is None and endpoint.provider_id != "local")
            or model.credential_revision != credential_revision
            or model.endpoint_revision != endpoint_revision
            or not any(
                current.model_profile_id == model.model_profile_id
                and current.remote_model_id == model.remote_model_id
                for current in self._service.model_pool(
                    connection_id=connection.connection_id
                )
            )
        ):
            raise ValueError("PROVIDER_MODEL_MISMATCH")

        status = CapabilityStatus.UNAVAILABLE
        timestamp = datetime.now(UTC)
        if probe_type == ModelCapability.TEXT.value:
            projection = self._service.test_connection(
                draft={
                    "provider_id": model.provider_id,
                    "endpoint_id": endpoint.endpoint_id,
                    "credential_ref": credential_ref,
                    "model_profile_id": model.model_profile_id,
                    "model_id": model.remote_model_id,
                    "connection_id": connection.connection_id,
                }
            )
            timestamp = projection.tested_at or timestamp
            if projection.connection_status is ProviderConnectionStatus.AVAILABLE:
                status = CapabilityStatus.PASS
            elif projection.connection_status not in {
                ProviderConnectionStatus.NOT_CONFIGURED,
                ProviderConnectionStatus.UNVERIFIED,
            }:
                status = CapabilityStatus.FAIL

        return CapabilityProbeResult(
            endpoint_id=endpoint.endpoint_id,
            model_profile_id=model.model_profile_id,
            probe_type=probe_type,
            probe_version="provider-connection-v1",
            timestamp=timestamp,
            status=status,
            provider_id=model.provider_id,
            credential_ref=credential_ref,
            model_id=model.remote_model_id,
            credential_revision=credential_revision,
            endpoint_revision=endpoint_revision,
        )


__all__ = (
    "DEEPSEEK_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
    "ProviderConfiguration",
    "ProviderConfigurationCapabilityProbeAdapter",
    "ProviderConfigurationService",
    "ProviderConfigurationStore",
    "ProviderConnectionDiagnostic",
    "ProviderConnectionDiagnosticStage",
    "ProviderConnectionStatus",
    "ProviderProjection",
    "ReloadableModelGateway",
    "TransmissionApprovalError",
    "TransmissionApprovalReceipt",
    "TransmissionApprovalService",
    "TransmissionDecision",
    "context_fingerprint",
)
