"""Read-only official model listing behind the existing discovery port."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from embedded_copilot.model_gateway import (
    BillingClass,
    BillingProvenance,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelProfile,
    descriptor_to_model_profile,
    official_model_catalog,
    provider_presets,
)
from embedded_copilot.model_gateway.exceptions import ModelProviderError

from .credentials import (
    CredentialProviderPort,
    provider_connection_credential_purpose,
    provider_credential_purpose,
)

logger = logging.getLogger(__name__)
_MAX_SAFE_TEXT = 512
_MAX_SAFE_KEYS = 32
_SAFE_ID = re.compile(r"[^A-Za-z0-9._:/-]+")
_FAILURE_STAGES = frozenset(
    {
        "CREDENTIAL_RESOLUTION",
        "DNS",
        "TCP_CONNECT",
        "TLS",
        "HTTP_REQUEST",
        "HTTP_RESPONSE",
        "HTTP_STATUS",
        "RESPONSE_BODY",
        "JSON_PARSE",
        "MODEL_NORMALIZATION",
        "MODEL_SCHEMA_VALIDATION",
        "PERSISTENCE",
    }
)
_CLASSIFICATIONS = frozenset(
    {
        "DNS_FAILED",
        "TCP_CONNECT_FAILED",
        "TLS_FAILED",
        "NETWORK_TIMEOUT",
        "HTTP_401_AUTH_FAILED",
        "HTTP_403_PERMISSION_DENIED",
        "HTTP_404_ENDPOINT_NOT_FOUND",
        "HTTP_429_RATE_LIMITED",
        "QUOTA_EXHAUSTED",
        "PROVIDER_5XX",
        "RESPONSE_PARSE_FAILED",
        "MODEL_DISCOVERY_SCHEMA_INVALID",
        "PROVIDER_RESPONSE_EMPTY",
        "UNKNOWN_PROVIDER_ERROR",
    }
)


def _safe_text(value: object, *, maximum: int = _MAX_SAFE_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(
        r"(?i)(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|token|secret|password)\s*[:=]\s*[^\s,;}]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s,;}]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9._~-]+", "[REDACTED]", text)
    return text.replace("\x00", "")[:maximum]


def _safe_identifier(value: object) -> str | None:
    text = _safe_text(value, maximum=128)
    return _SAFE_ID.sub("_", text) if text else None


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if not parsed.scheme or not hostname:
            return None
        netloc = hostname
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))[:512]
    except (TypeError, ValueError):
        return None


def _safe_header(headers: httpx.Headers, *names: str) -> str | None:
    for name in names:
        value = headers.get(name)
        if value:
            return _safe_text(value, maximum=128)
    return None


def _safe_keys(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, Mapping):
        return ()
    keys: list[str] = []
    for key in payload:
        if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key):
            keys.append(key)
        if len(keys) >= _MAX_SAFE_KEYS:
            break
    return tuple(keys)


def _response_body(response: httpx.Response) -> tuple[bytes, str | None]:
    try:
        raw = response.content
        return raw, raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics must remain safe
        return b"", None


def _provider_fields(payload: object) -> tuple[str | None, str | None, str | None, str | None]:
    if not isinstance(payload, Mapping):
        return None, None, None, None
    error = payload.get("error")
    candidate: Mapping[str, object] = error if isinstance(error, Mapping) else payload
    request_id = payload.get("request_id", payload.get("requestId"))
    if request_id is None and isinstance(payload.get("id"), str):
        request_id = payload.get("id")
    return (
        _safe_text(candidate.get("code"), maximum=128),
        _safe_text(candidate.get("type"), maximum=128),
        _safe_text(candidate.get("message", candidate.get("detail"))),
        _safe_text(request_id, maximum=128),
    )


def _network_details(error: BaseException) -> tuple[str, str, str]:
    lowered = str(error).lower()
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "nodename" in lowered:
        return "DNS", "DNS_FAILED", "CONNECT_ERROR"
    if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
        return "TLS", "TLS_FAILED", "CONNECT_ERROR"
    if isinstance(error, httpx.TimeoutException):
        return "TCP_CONNECT", "NETWORK_TIMEOUT", "CONNECT_TIMEOUT"
    if isinstance(error, httpx.ConnectError):
        return "TCP_CONNECT", "TCP_CONNECT_FAILED", "CONNECT_ERROR"
    if isinstance(error, httpx.NetworkError):
        return "HTTP_REQUEST", "UNKNOWN_PROVIDER_ERROR", "HTTP_TRANSPORT_ERROR"
    if isinstance(error, httpx.HTTPError):
        return "HTTP_REQUEST", "UNKNOWN_PROVIDER_ERROR", "HTTP_TRANSPORT_ERROR"
    return "HTTP_REQUEST", "UNKNOWN_PROVIDER_ERROR", "HTTP_TRANSPORT_ERROR"


@dataclass(frozen=True, slots=True)
class DiscoveryFailureEvidence:
    """Secret-free evidence for one failed provider model-list request."""

    provider_id: str
    connection_id: str | None
    endpoint_id: str
    credential_ref: str | None
    credential_revision: str | None
    request_method: str
    sanitized_request_url: str | None
    failure_stage: str
    exception_type: str | None
    network_error_class: str | None
    provider_http_status: int | None
    provider_error_code: str | None
    provider_error_type: str | None
    provider_error_message_sanitized: str | None
    provider_request_id: str | None
    response_content_type: str | None
    response_body_present: bool
    response_body_length: int
    safe_excerpt: str | None
    safe_top_level_keys: tuple[str, ...]
    classification: str
    latency_ms: float | None
    timestamp: str

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "connection_id": self.connection_id,
            "endpoint_id": self.endpoint_id,
            "credential_ref": self.credential_ref,
            "credential_revision": self.credential_revision,
            "method": self.request_method,
            "sanitized_url": self.sanitized_request_url,
            "failure_stage": self.failure_stage,
            "exception_type": self.exception_type,
            "network_error_class": self.network_error_class,
            "provider_http_status": self.provider_http_status,
            "provider_error_code": self.provider_error_code,
            "provider_error_type": self.provider_error_type,
            "provider_error_message": self.provider_error_message_sanitized,
            "provider_request_id": self.provider_request_id,
            "response_content_type": self.response_content_type,
            "response_body_present": self.response_body_present,
            "response_body_length": self.response_body_length,
            "safe_excerpt": self.safe_excerpt,
            "safe_top_level_keys": self.safe_top_level_keys,
            "reason_code": self.classification,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp,
        }

    def as_api_detail(self) -> dict[str, object]:
        return self.as_dict()


class ModelDiscoveryFailure(ModelProviderError):
    """Structured, sanitized failure from the official model discovery port."""

    def __init__(self, evidence: DiscoveryFailureEvidence) -> None:
        if evidence.failure_stage not in _FAILURE_STAGES or evidence.classification not in _CLASSIFICATIONS:
            raise ValueError("discovery failure evidence is invalid")
        super().__init__(
            "model discovery failed",
            safe_http_status=evidence.provider_http_status,
            safe_transport_error_code=evidence.network_error_class,
            safe_transport_stage=("TCP" if evidence.failure_stage == "TCP_CONNECT" else evidence.failure_stage),
        )
        self.evidence = evidence


class OfficialModelDiscovery:
    def __init__(
        self,
        credentials: CredentialProviderPort,
        *,
        transport: object | None = None,
        credential_revision_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._credentials = credentials
        self._transport = transport
        self._credential_revision_resolver = credential_revision_resolver

    def discover(
        self,
        endpoint: EndpointProfile,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> tuple[ModelDescriptor, ...]:
        preset = next((p for p in provider_presets() if p.provider_id == endpoint.provider_id), None)
        is_custom_relay = (
            endpoint.endpoint_type is EndpointType.RELAY
            and endpoint.provider_id.startswith("custom-")
            and getattr(endpoint.protocol_family, "value", endpoint.protocol_family) == "OPENAI_CHAT"
        )
        if (
            not is_custom_relay
            and (
                preset is None
                or not preset.default_endpoint_template
                or endpoint.base_url.rstrip("/") != preset.default_endpoint_template.rstrip("/")
            )
        ):
            raise ValueError("MODEL_DISCOVERY_ENDPOINT_INVALID")

        connection_id = _safe_identifier(connection_id)
        resolved_credential_revision = credential_revision or self._credential_revision(
            endpoint.provider_id,
            connection_id=connection_id,
        )
        purposes = (
            (provider_connection_credential_purpose(connection_id), provider_credential_purpose(endpoint.provider_id))
            if connection_id is not None
            else (provider_credential_purpose(endpoint.provider_id),)
        )
        try:
            secret = None
            for purpose in purposes:
                secret = self._credentials.get(purpose)
                if secret is not None:
                    break
        except Exception as error:  # noqa: BLE001 - never expose credential backend details
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="CREDENTIAL_RESOLUTION",
                classification="UNKNOWN_PROVIDER_ERROR",
                exception=error,
                credential_revision=resolved_credential_revision,
            ) from None
        if not secret:
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="CREDENTIAL_RESOLUTION",
                classification="UNKNOWN_PROVIDER_ERROR",
                exception_type="CredentialUnavailable",
                credential_revision=resolved_credential_revision,
            ) from None
        try:
            decoded_secret = secret.decode("utf-8")
        except UnicodeError as error:
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="CREDENTIAL_RESOLUTION",
                classification="UNKNOWN_PROVIDER_ERROR",
                exception=error,
                credential_revision=resolved_credential_revision,
            ) from None

        headers = {"Authorization": "Bearer " + decoded_secret}
        if endpoint.provider_id == "anthropic":
            headers = {"x-api-key": decoded_secret, "anthropic-version": "2023-06-01"}
        elif endpoint.provider_id == "google":
            headers = {"x-goog-api-key": decoded_secret}

        request_url = endpoint.base_url.rstrip("/") + "/models"
        started = time.monotonic()
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=15.0,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.get(request_url, headers=headers)
        except Exception as error:  # noqa: BLE001 - convert all provider transport errors to safe evidence
            stage, classification, network_class = _network_details(error)
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage=stage,
                classification=classification,
                exception=error,
                network_error_class=network_class,
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                latency_ms=self._latency(started),
            ) from None

        body, body_text = _response_body(response)
        payload: object | None = None
        try:
            payload = response.json()
        except Exception as error:  # noqa: BLE001 - preserve HTTP status and safe parse evidence
            if 200 <= response.status_code < 300:
                raise self._failure(
                    endpoint,
                    connection_id=connection_id,
                    failure_stage="JSON_PARSE",
                    classification="RESPONSE_PARSE_FAILED",
                    exception=error,
                    credential_revision=resolved_credential_revision,
                    request_url=request_url,
                    response=response,
                    body=body,
                    body_text=body_text,
                    latency_ms=self._latency(started),
                ) from None

        if response.status_code < 200 or response.status_code >= 300:
            code, error_type, message, request_id = _provider_fields(payload)
            classification = self._http_classification(response.status_code, code, message)
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="HTTP_STATUS",
                classification=classification,
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                response=response,
                body=body,
                body_text=body_text,
                payload=payload,
                provider_error_code=code,
                provider_error_type=error_type,
                provider_error_message=message,
                provider_request_id=request_id,
                latency_ms=self._latency(started),
            ) from None

        if not isinstance(payload, Mapping):
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="MODEL_SCHEMA_VALIDATION",
                classification="MODEL_DISCOVERY_SCHEMA_INVALID",
                exception_type="SchemaValidationError",
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                response=response,
                body=body,
                body_text=body_text,
                payload=payload,
                latency_ms=self._latency(started),
            ) from None
        rows = payload.get("data", payload.get("models"))
        if not isinstance(rows, list):
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="MODEL_SCHEMA_VALIDATION",
                classification="MODEL_DISCOVERY_SCHEMA_INVALID",
                exception_type="SchemaValidationError",
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                response=response,
                body=body,
                body_text=body_text,
                payload=payload,
                latency_ms=self._latency(started),
            ) from None

        try:
            result = self._normalize_rows(endpoint, rows, is_custom_relay)
        except ModelDiscoveryFailure:
            raise
        except Exception as error:  # noqa: BLE001 - normalization must not leak provider data
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="MODEL_NORMALIZATION",
                classification="MODEL_DISCOVERY_SCHEMA_INVALID",
                exception=error,
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                response=response,
                body=body,
                body_text=body_text,
                payload=payload,
                latency_ms=self._latency(started),
            ) from None
        if not result:
            raise self._failure(
                endpoint,
                connection_id=connection_id,
                failure_stage="MODEL_NORMALIZATION",
                classification="PROVIDER_RESPONSE_EMPTY",
                exception_type="NoUsableModels",
                credential_revision=resolved_credential_revision,
                request_url=request_url,
                response=response,
                body=body,
                body_text=body_text,
                payload=payload,
                latency_ms=self._latency(started),
            ) from None
        return result

    def _normalize_rows(
        self,
        endpoint: EndpointProfile,
        rows: list[object],
        is_custom_relay: bool,
    ) -> tuple[ModelDescriptor, ...]:
        known = {m.remote_model_id for m in official_model_catalog(endpoint.provider_id, endpoint.endpoint_id)}
        result: list[ModelDescriptor] = []
        seen: set[str] = set()
        families = {
            "openai": ("gpt-", "o1", "o3", "o4", "chatgpt-"),
            "deepseek": ("deepseek-",),
            "zhipu": ("glm-",),
            "anthropic": ("claude-",),
            "google": ("gemini-",),
            "xai": ("grok-",),
            "alibaba": ("qwen-",),
            "moonshot": ("moonshot-",),
            "minimax": ("minimax-", "MiniMax-"),
        }
        for row in rows:
            model = row.get("id", row.get("name", "")) if isinstance(row, dict) else ""
            if endpoint.provider_id == "google":
                model = model.removeprefix("models/") if isinstance(model, str) else model
                if not isinstance(row, dict) or "generateContent" not in row.get("supportedGenerationMethods", []):
                    continue
            if not isinstance(model, str):
                continue
            model = model.strip()
            if not model or model in seen:
                continue
            seen.add(model)
            if (
                not is_custom_relay
                and model not in known
                and not model.startswith(families.get(endpoint.provider_id, ()))
            ):
                continue
            if any(part in model.lower() for part in ("image", "audio", "realtime", "embedding", "tts", "transcribe")):
                continue
            billing_class = BillingClass.UNKNOWN
            billing_provenance = BillingProvenance.UNKNOWN
            billing_source = None
            if isinstance(row, dict):
                declared = row.get("billing_class", row.get("billing"))
                if isinstance(declared, str):
                    try:
                        billing_class = BillingClass(declared.strip().upper())
                    except ValueError:
                        billing_class = BillingClass.UNKNOWN
                    else:
                        billing_provenance = BillingProvenance.PROVIDER_METADATA
                        billing_source = "provider_models_api"
            result.append(
                ModelDescriptor(
                    provider_id=endpoint.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    model_id=model,
                    display_name=model,
                    protocol_family=endpoint.protocol_family,
                    availability=ModelAvailability.AVAILABLE,
                    availability_source=ModelCatalogSource.PROVIDER_API,
                    catalog_source=ModelCatalogSource.PROVIDER_API,
                    billing_class=billing_class,
                    billing_provenance=billing_provenance,
                    billing_source=billing_source,
                )
            )
        return tuple(result)

    def _credential_revision(
        self, provider_id: str, *, connection_id: str | None = None
    ) -> str | None:
        if self._credential_revision_resolver is None:
            return None
        try:
            try:
                value = self._credential_revision_resolver(provider_id, connection_id)  # type: ignore[misc]
            except TypeError:
                value = self._credential_revision_resolver(provider_id)
        except Exception:  # noqa: BLE001 - resolver is diagnostic metadata only
            return None
        return _safe_identifier(value)

    @staticmethod
    def _latency(started: float) -> float:
        return round(max(0.0, time.monotonic() - started) * 1000, 3)

    @staticmethod
    def _http_classification(status_code: int, code: str | None, message: str | None) -> str:
        marker = f"{code or ''} {message or ''}".lower()
        if "quota" in marker or "insufficient" in marker or "billing" in marker:
            return "QUOTA_EXHAUSTED"
        if status_code == 401:
            return "HTTP_401_AUTH_FAILED"
        if status_code == 403:
            return "HTTP_403_PERMISSION_DENIED"
        if status_code == 404:
            return "HTTP_404_ENDPOINT_NOT_FOUND"
        if status_code == 429:
            return "HTTP_429_RATE_LIMITED"
        if 500 <= status_code <= 599:
            return "PROVIDER_5XX"
        return "UNKNOWN_PROVIDER_ERROR"

    def _failure(
        self,
        endpoint: EndpointProfile,
        *,
        connection_id: str | None = None,
        failure_stage: str,
        classification: str,
        exception: BaseException | None = None,
        exception_type: str | None = None,
        network_error_class: str | None = None,
        credential_revision: str | None = None,
        request_url: str | None = None,
        response: httpx.Response | None = None,
        body: bytes = b"",
        body_text: str | None = None,
        payload: object | None = None,
        provider_error_code: str | None = None,
        provider_error_type: str | None = None,
        provider_error_message: str | None = None,
        provider_request_id: str | None = None,
        latency_ms: float | None = None,
    ) -> ModelDiscoveryFailure:
        if response is not None and payload is not None:
            body_code, body_type, body_message, body_request_id = _provider_fields(payload)
            provider_error_code = provider_error_code or body_code
            provider_error_type = provider_error_type or body_type
            provider_error_message = provider_error_message or body_message
            provider_request_id = provider_request_id or body_request_id
        evidence = DiscoveryFailureEvidence(
            provider_id=_safe_identifier(endpoint.provider_id) or "unknown",
            connection_id=_safe_identifier(connection_id or getattr(endpoint, "connection_id", None)),
            endpoint_id=_safe_identifier(endpoint.endpoint_id) or "unknown",
            credential_ref=_safe_identifier(getattr(endpoint, "credential_ref", None)),
            credential_revision=_safe_identifier(credential_revision),
            request_method="GET",
            sanitized_request_url=_safe_url(request_url or getattr(endpoint, "base_url", None)),
            failure_stage=failure_stage,
            exception_type=_safe_identifier(exception_type or (type(exception).__name__ if exception else None)),
            network_error_class=_safe_identifier(network_error_class),
            provider_http_status=response.status_code if response is not None else None,
            provider_error_code=_safe_text(provider_error_code, maximum=128),
            provider_error_type=_safe_text(provider_error_type, maximum=128),
            provider_error_message_sanitized=_safe_text(provider_error_message),
            provider_request_id=_safe_text(
                provider_request_id
                or (_safe_header(response.headers, "x-request-id", "x-ds-trace-id", "request-id") if response else None),
                maximum=128,
            ),
            response_content_type=_safe_header(response.headers, "content-type") if response is not None else None,
            response_body_present=bool(body),
            response_body_length=len(body),
            safe_excerpt=_safe_text(body_text),
            safe_top_level_keys=_safe_keys(payload),
            classification=classification,
            latency_ms=latency_ms,
            timestamp=datetime.now(UTC).isoformat(),
        )
        logger.warning(
            "model discovery failed provider_id=%s endpoint_id=%s stage=%s reason_code=%s "
            "http_status=%s provider_error_code=%s provider_request_id=%s latency_ms=%s",
            evidence.provider_id,
            evidence.endpoint_id,
            evidence.failure_stage,
            evidence.classification,
            evidence.provider_http_status,
            evidence.provider_error_code,
            evidence.provider_request_id,
            evidence.latency_ms,
        )
        return ModelDiscoveryFailure(evidence)


class OfficialModelProfileDiscovery:
    """Projection adapter from release descriptors to the canonical model port."""

    def __init__(self, discovery: OfficialModelDiscovery) -> None:
        self._discovery = discovery

    def discover(self, endpoint: EndpointProfile) -> tuple[ModelProfile, ...]:
        return tuple(descriptor_to_model_profile(item) for item in self._discovery.discover(endpoint))


__all__ = (
    "DiscoveryFailureEvidence",
    "ModelDiscoveryFailure",
    "OfficialModelDiscovery",
    "OfficialModelProfileDiscovery",
)
