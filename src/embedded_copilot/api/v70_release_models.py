"""Additive v7.0 production-release API DTOs."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class BootstrapExchangeInput(BaseModel):
    nonce: str = Field(min_length=32, max_length=256)


class CredentialUpdateInput(BaseModel):
    model_config = {"hide_input_in_errors": True}

    provider: str = Field(min_length=1, max_length=64)
    secret: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("provider is invalid")
        return value


class ProviderConfigurationInput(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    connection_name: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    endpoint_id: str | None = Field(default=None, max_length=128)
    model_profile_id: str | None = Field(default=None, max_length=128)
    credential_ref: str | None = Field(default=None, max_length=128)
    protocol_family: str | None = Field(default=None, max_length=64)
    configuration_fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value.strip().lower()):
            raise ValueError("provider is invalid")
        return value.strip().lower()

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("model is invalid")
        return value

    @field_validator("connection_name")
    @classmethod
    def validate_connection_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(character in "\r\n" or ord(character) < 32 for character in value):
            raise ValueError("connection_name is invalid")
        return value


class ProviderCredentialInput(BaseModel):
    model_config = {"hide_input_in_errors": True}

    provider: str = Field(min_length=1, max_length=64)
    secret: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value.strip().lower()):
            raise ValueError("provider is invalid")
        return value.strip().lower()


class ProviderConnectionTestInput(BaseModel):
    """The complete safe identity of the model probe.

    Credentials are intentionally absent; the backend resolves the credential
    by provider-scoped reference and binds the receipt to the resolved revision.
    """

    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    provider_id: str = Field(
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("provider_id", "provider"),
    )
    endpoint_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("endpoint_id"),
    )
    credential_ref: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("credential_ref"),
    )
    model_profile_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("model_profile_id"),
    )
    model_id: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("model_id", "model"),
    )
    base_url: str | None = Field(default=None, max_length=512)
    protocol_family: str | None = Field(default=None, max_length=64)
    configuration_fingerprint: str | None = Field(default=None, max_length=128)

    @field_validator("provider_id")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value.strip().lower()):
            raise ValueError("provider is invalid")
        return value.strip().lower()

    @field_validator("model_id")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("model is invalid")
        return value


class FrontendProviderDiagnosticInput(BaseModel):
    """Fixed, content-free payload for the opt-in frontend projection evidence sink."""

    model_config = {"extra": "forbid", "strict": True, "hide_input_in_errors": True}

    build_id: Literal["P0-1G15H2-FE"]
    stage: Literal[
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_REJECTED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_REJECTED",
        "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
        "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_VALIDATE_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_VALIDATE_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
        "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
        "WEBVIEW_FRONTEND_READINESS_PASS",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_BEGIN",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_FAILURE",
    ]
    result: Literal["ENTERED", "RECEIVED", "SUCCESS", "FAILURE", "NOT_APPLICABLE"]
    route: Literal["PROVIDER_PROJECTION", "PROJECT_LOCATIONS"]
    fetch_attempt_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^provider-fetch-[1-9][0-9]{0,8}$",
    )
    event_sequence: int | None = Field(default=None, ge=1, le=1_000_000)
    fetch_attempt_started_at: str | None = Field(default=None, max_length=24, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    fetch_attempt_finished_at: str | None = Field(default=None, max_length=24, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    fetch_attempt_duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    project_locations_attempt_started_at: str | None = Field(default=None, max_length=24, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    project_locations_attempt_finished_at: str | None = Field(default=None, max_length=24, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    project_locations_attempt_duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    frontend_fetch_resolved: bool | None = None
    frontend_http_status: int | Literal["UNKNOWN"] | None = None
    final_result: Literal["SUCCESS", "FAILURE"] | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    projection_state: Literal[
        "NULL",
        "LOADING",
        "RECEIVED",
        "VALIDATED",
        "CONFIGURED",
        "UNCONFIGURED",
        "UNAVAILABLE",
        "ERROR",
    ] | None = None
    credential_state: Literal["CONFIGURED", "UNCONFIGURED", "UNKNOWN"] | None = None
    provider_present: bool | None = None
    fetcher_identity: Literal[
        "GLOBAL_BROWSER_FETCH",
        "GLOBAL_THIS_FETCH",
        "WINDOW_FETCH",
        "CUSTOM_WRAPPER",
        "INJECTED_FETCHER",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    production_fetcher_identity: Literal[
        "GLOBAL_BROWSER_FETCH",
        "GLOBAL_THIS_FETCH",
        "WINDOW_FETCH",
        "CUSTOM_WRAPPER",
        "INJECTED_FETCHER",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    frontend_origin_class: Literal[
        "LOOPBACK_HTTP",
        "LOCALHOST_HTTP",
        "FILE",
        "CUSTOM_SCHEME",
        "HTTPS_REMOTE",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    provider_target_origin_class: Literal[
        "SAME_ORIGIN",
        "LOOPBACK_CROSS_ORIGIN",
        "LOCALHOST_CROSS_ORIGIN",
        "CUSTOM_TO_LOOPBACK",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    frontend_provider_same_origin: bool | None = None
    provider_request_url_strategy: Literal[
        "RELATIVE_SAME_ORIGIN",
        "ABSOLUTE_CURRENT_LOOPBACK",
        "ABSOLUTE_DIFFERENT_LOOPBACK",
        "CUSTOM_BACKEND_ORIGIN",
        "OTHER",
    ] | None = None
    request_credentials_mode: Literal["SAME_ORIGIN", "INCLUDE", "OMIT", "DEFAULT", "UNKNOWN"] | None = None
    document_ready_state_at_fetch: Literal["LOADING", "INTERACTIVE", "COMPLETE", "UNKNOWN"] | None = None
    navigation_state_at_fetch: Literal["STABLE", "NAVIGATING", "UNKNOWN"] | None = None
    document_generation_at_fetch: int | None = Field(default=None, ge=0, le=1_000_000)
    document_generation_at_rejection: int | None = Field(default=None, ge=0, le=1_000_000)
    navigation_started_during_fetch: bool | None = None
    document_context_changed_during_fetch: bool | None = None
    abort_signal_attached_to_provider_fetch: Literal["NONE", "ATTACHED", "UNKNOWN"] | None = None
    abort_trigger_observed: bool | None = None
    abort_trigger_source: Literal[
        "NONE",
        "COMPONENT_CLEANUP",
        "NAVIGATION",
        "SESSION_BOOTSTRAP",
        "TIMEOUT",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    fetch_rejection_error_class: Literal["ABORT_ERROR", "TYPE_ERROR", "DOM_EXCEPTION", "OTHER", "UNKNOWN"] | None = None
    fetch_rejection_error_name: Literal[
        "TypeError",
        "AbortError",
        "NetworkError",
        "SecurityError",
        "InvalidStateError",
        "NotAllowedError",
        "Other",
        "Unknown",
    ] | None = None
    fetch_rejection_message_category: Literal[
        "FAILED_TO_FETCH",
        "LOAD_FAILED",
        "NETWORK_ERROR",
        "ILLEGAL_INVOCATION",
        "BLOCKED_BY_CLIENT_OR_POLICY",
        "CONNECTION_REFUSED",
        "CONNECTION_RESET",
        "OTHER_SANITIZED",
        "EMPTY",
        "UNKNOWN",
    ] | None = None
    fetch_failure_kind: Literal[
        "PROMISE_REJECTION",
        "HTTP_STATUS",
        "JSON_PARSE",
        "RESPONSE_BODY",
        "ABORT",
        "OTHER",
        "UNKNOWN",
    ] | None = None
    fetch_failure_stage: Literal[
        "FETCH_CALL",
        "HTTP_STATUS",
        "RESPONSE_BODY",
        "PROJECTION_PROCESSING",
        "STATE_PROCESSING",
        "UNKNOWN",
    ] | None = None
    safe_error_class: Literal[
        "NONE",
        "FETCH_REJECTED",
        "NETWORK_TRANSPORT_ERROR",
        "UNKNOWN_FETCH_REJECTION",
        "HTTP_STATUS_ERROR",
        "HTTP_ERROR",
        "JSON_PARSE_ERROR",
        "PROJECTION_PROCESSING_ERROR",
        "PROJECTION_VALIDATION_ERROR",
        "REQUEST_ABORTED",
        "NETWORK_ERROR",
        "STATE_PROCESSING_ERROR",
        "DIAGNOSTIC_DELIVERY_ERROR",
        "UNKNOWN",
    ] | None = None
    failure_stage: Literal[
        "FETCH",
        "HTTP_STATUS",
        "JSON_PARSE",
        "PROJECTION_PROCESSING",
        "STATE_PROCESSING",
        "UNKNOWN",
    ] | None = None
    projection_processing_result: Literal["SUCCESS", "FAILURE", "NOT_APPLICABLE"] | None = None
    request_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^(?:webview-session-[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    )
    render_generation: int | None = Field(default=None, ge=0, le=1_000_000)


class TransmissionApprovalInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    context_fingerprints: tuple[str, ...] = ()
    decision: str = Field(default="DENIED", pattern="^(APPROVED|DENIED)$")

    @field_validator("context_fingerprints")
    @classmethod
    def validate_context_fingerprints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not isinstance(item, str)
            or len(item) != 71
            or not item.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in item[7:])
            for item in value
        ):
            raise ValueError("context fingerprints are invalid")
        if len(set(value)) != len(value):
            raise ValueError("context fingerprints are invalid")
        return value


class BackupCreateInput(BaseModel):
    pass


class RestoreAuthorizationInput(BaseModel):
    backup_id: str = Field(pattern=r"^backup-[0-9a-f-]{36}$")


class RestoreInput(RestoreAuthorizationInput):
    authorization: str = Field(min_length=32, max_length=256)
