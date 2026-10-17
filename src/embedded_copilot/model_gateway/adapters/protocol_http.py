"""Injected HTTP adapter shared by the supported protocol families."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from pydantic import SecretStr, ValidationError

from .._transport_metadata import ProviderExecutionMetadata, ProviderExecutionResult
from ..contracts import (
    ModelImageInput,
    ModelRequest,
    ModelResponse,
    ProviderCapability,
    identifier,
    validate_model_request,
)
from ..exceptions import (
    ModelGatewayRejected,
    ModelProviderConfigurationError,
    ModelProviderError,
    ModelProviderHTTPError,
    ModelProviderModelUnavailable,
    ModelProviderNetworkError,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
)
from ..ports import ModelProviderPort
from ..profiles import ProtocolFamily
from ..protocols import (
    AnthropicMessagesProtocolAdapter,
    GoogleGenAIProtocolAdapter,
    LocalOpenAIProtocolAdapter,
    OpenAIChatProtocolAdapter,
    OpenAIResponsesProtocolAdapter,
)

# The current HTTP adapter capability boundary. Provider templates and
# connection creation both consume this source of truth.
SUPPORTED_PROTOCOL_FAMILIES = frozenset(
    {
        ProtocolFamily.OPENAI_RESPONSES,
        ProtocolFamily.OPENAI_CHAT,
        ProtocolFamily.ANTHROPIC_MESSAGES,
        ProtocolFamily.GOOGLE_GENAI,
        ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
    }
)


class ProtocolHTTPProviderAdapter(ModelProviderPort):
    """Normalize all provider responses through a protocol-family adapter."""

    __slots__ = ("_api_key", "_async_transport", "_base_url", "_endpoint_id", "_family", "_provider", "_supports_image_input", "_sync_transport", "_timeout_seconds")

    def __init__(
        self,
        *,
        provider: str,
        endpoint_id: str | None = None,
        base_url: str,
        protocol_family: ProtocolFamily,
        api_key: SecretStr | str | None = None,
        timeout_seconds: float = 30.0,
        supports_image_input: bool = False,
        http_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(provider, str) or not provider or not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")) or (base_url.startswith("http://") and not _is_loopback_url(base_url)):
            raise ValueError("provider configuration is invalid")
        if type(timeout_seconds) not in (int, float) or not 0.1 <= timeout_seconds <= 120.0:
            raise ValueError("provider timeout is invalid")
        if type(supports_image_input) is not bool:
            raise TypeError("image input capability is invalid")
        if protocol_family not in SUPPORTED_PROTOCOL_FAMILIES:
            raise ValueError("provider protocol is invalid")
        if protocol_family is not ProtocolFamily.LOCAL_OPENAI_COMPATIBLE and api_key is None:
            raise ValueError("provider credential is invalid")
        if isinstance(api_key, SecretStr):
            checked_key = api_key
        elif isinstance(api_key, str) and api_key.strip() and "\n" not in api_key and "\r" not in api_key:
            checked_key = SecretStr(api_key.strip())
        elif api_key is None:
            checked_key = None
        else:
            raise ValueError("provider credential is invalid")
        self._provider = provider
        self._endpoint_id = identifier(endpoint_id or f"endpoint-{provider}", field="endpoint_id")
        self._base_url = base_url.rstrip("/") + "/"
        self._family = protocol_family
        self._supports_image_input = supports_image_input
        self._api_key = checked_key
        self._timeout_seconds = float(timeout_seconds)
        self._sync_transport = http_transport
        self._async_transport = async_transport

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability.TEXT,
            *((ProviderCapability.VISION,) if self._supports_image_input else ()),
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        return self._generate_with_metadata(request).response

    def _generate_with_metadata(self, request: ModelRequest, *, _dispatch_guard=None) -> ProviderExecutionResult:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        payload = self._prepare_transport_bytes(checked)
        path = self._path(checked)
        headers = {**self._headers(), "Content-Type": "application/json"}
        transport_identity = self._transport_identity()
        if _dispatch_guard is not None:
            _dispatch_guard(checked, self._family, payload, self._output_parameter(), self._budget_wire_target(path, headers))
            if transport_identity != self._transport_identity() or path != self._path(checked):
                raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        started = time.perf_counter()
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds, transport=self._sync_transport, trust_env=False, follow_redirects=False) as client:
                response = client.post(path, headers=headers, content=payload)
            try:
                normalized = self._normalize(checked, response)
            except ModelProviderError as error:
                if _dispatch_guard is not None:
                    from ..invocation.bundle_http import capture_http_failure
                    capture_http_failure(error, payload, response, self._budget_wire_target(path, headers))
                raise
            metadata = _execution_metadata(response, normalized, started)
            return ProviderExecutionResult(
                response=_response_with_latency(normalized, metadata),
                metadata=metadata,
            )
        except ModelProviderError:
            raise
        except httpx.TimeoutException as error:
            raise ModelProviderTimeout(
                safe_transport_error_code=_httpx_error_code(error),
                safe_transport_stage=_httpx_transport_stage(error),
            ) from None
        except httpx.HTTPError as error:
            raise ModelProviderNetworkError(
                safe_transport_error_code=_httpx_error_code(error),
                safe_transport_stage=_httpx_transport_stage(error),
            ) from None

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        return (await self._agenerate_with_metadata(request)).response

    async def _agenerate_with_metadata(self, request: ModelRequest, *, _dispatch_guard=None) -> ProviderExecutionResult:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        payload = self._prepare_transport_bytes(checked)
        path = self._path(checked)
        headers = {**self._headers(), "Content-Type": "application/json"}
        transport_identity = self._transport_identity()
        if _dispatch_guard is not None:
            await _dispatch_guard(checked, self._family, payload, self._output_parameter(), self._budget_wire_target(path, headers))
            if transport_identity != self._transport_identity() or path != self._path(checked):
                raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout_seconds, transport=self._async_transport, trust_env=False, follow_redirects=False) as client:
                response = await client.post(path, headers=headers, content=payload)
            try:
                normalized = self._normalize(checked, response)
            except ModelProviderError as error:
                if _dispatch_guard is not None:
                    from ..invocation.bundle_http import capture_http_failure
                    capture_http_failure(error, payload, response, self._budget_wire_target(path, headers))
                raise
            metadata = _execution_metadata(response, normalized, started)
            return ProviderExecutionResult(
                response=_response_with_latency(normalized, metadata),
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except ModelProviderError:
            raise
        except httpx.TimeoutException as error:
            raise ModelProviderTimeout(
                safe_transport_error_code=_httpx_error_code(error),
                safe_transport_stage=_httpx_transport_stage(error),
            ) from None
        except httpx.HTTPError as error:
            raise ModelProviderNetworkError(
                safe_transport_error_code=_httpx_error_code(error),
                safe_transport_stage=_httpx_transport_stage(error),
            ) from None

    def prepare_transport(self, request: ModelRequest) -> tuple[ProtocolFamily, bytes, str, tuple]:
        """No credential value access, client construction or network dispatch."""
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            if self._family is ProtocolFamily.ANTHROPIC_MESSAGES:
                headers.update({"x-api-key": "", "anthropic-version": "2023-06-01"})
            elif self._family is ProtocolFamily.GOOGLE_GENAI:
                headers["x-goog-api-key"] = ""
            else:
                headers["Authorization"] = ""
        return (self._family, self._prepare_transport_bytes(checked),
                self._output_parameter(), self._budget_wire_target(self._path(checked), headers))

    def _prepare_transport_bytes(self, request: ModelRequest) -> bytes:
        return json.dumps(self._payload(request), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def _output_parameter(self) -> str:
        if self._family is ProtocolFamily.OPENAI_RESPONSES:
            return "max_output_tokens"
        if self._family in {ProtocolFamily.OPENAI_CHAT, ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, ProtocolFamily.ANTHROPIC_MESSAGES}:
            return "max_tokens"
        if self._family is ProtocolFamily.GOOGLE_GENAI:
            return "generationConfig.maxOutputTokens"
        return "UNAVAILABLE"

    def _budget_wire_target(self, path, headers):
        # Qualification sees actual non-secret routing facts, not just an entry
        # claiming an endpoint revision. Credential values never leave transport.
        allowed = {"content-type", "anthropic-version", "authorization", "x-api-key", "x-goog-api-key"}
        if any(name.lower() not in allowed for name in headers):
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        url = httpx.URL(self._base_url)
        if url.username or url.password or url.query or url.fragment:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        return (
            ("provider_id", self._provider), ("endpoint_id", self._endpoint_id),
            ("base_url", self._base_url), ("path", path),
            ("protocol", self._family.value),
            ("noncredential_headers", tuple(sorted((key.lower(), value) for key, value in headers.items() if key.lower() in {"content-type", "anthropic-version"}))),
            ("credential_header_names", tuple(sorted(key.lower() for key in headers if key.lower() in {"authorization", "x-api-key", "x-goog-api-key"}))),
        )

    def _transport_identity(self):
        return (self._base_url, self._endpoint_id, self._provider, self._family, self._supports_image_input, self._timeout_seconds, id(self._api_key), id(self._sync_transport), id(self._async_transport))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            value = self._api_key.get_secret_value()
            if self._family is ProtocolFamily.ANTHROPIC_MESSAGES:
                headers["x-api-key"] = value
                headers["anthropic-version"] = "2023-06-01"
            elif self._family is ProtocolFamily.GOOGLE_GENAI:
                headers["x-goog-api-key"] = value
            else:
                headers["Authorization"] = f"Bearer {value}"
        return headers

    def _path(self, request: ModelRequest) -> str:
        if self._family is ProtocolFamily.OPENAI_RESPONSES:
            return "responses"
        if self._family in {ProtocolFamily.OPENAI_CHAT, ProtocolFamily.LOCAL_OPENAI_COMPATIBLE}:
            return "chat/completions"
        if self._family is ProtocolFamily.ANTHROPIC_MESSAGES:
            return "messages"
        return "models/" + request.model_policy.model_name + ":generateContent"

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        if request.model_policy.service_tier is not None and self._family is not ProtocolFamily.OPENAI_RESPONSES:
            raise ModelProviderConfigurationError()
        messages = (
            [item.model_dump(mode="json") for item in request.messages]
            if request.messages
            else [{"role": "user", "content": request.task}]
            if request.task_type == "connection_probe"
            else [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": request.task},
            ]
        )
        tools = [
            {
                "type": "function",
                "function": {"name": name, "description": description},
            }
            for name, description in request.tool_definitions
        ]
        if self._family is ProtocolFamily.OPENAI_RESPONSES:
            input_value: object = messages if request.messages else request.task
            if request.images:
                input_value = _with_openai_response_images(messages, request.images)
            payload: dict[str, object] = {
                "model": request.model_policy.model_name,
                "instructions": request.system_instruction,
                "input": input_value,
                "max_output_tokens": request.model_policy.max_output_tokens,
            }
            if request.model_policy.service_tier is not None:
                payload["service_tier"] = request.model_policy.service_tier
            if tools:
                payload["tools"] = tools
            if request.structured_output_schema:
                payload["text"] = {"format": {"type": "json_object"}}
            return payload
        if self._family in {ProtocolFamily.OPENAI_CHAT, ProtocolFamily.LOCAL_OPENAI_COMPATIBLE}:
            if request.images:
                messages = _with_openai_chat_images(messages, request.images)
            payload = {
                "model": request.model_policy.model_name,
                "messages": messages,
            }
            if request.task_type != "connection_probe":
                payload["temperature"] = request.model_policy.temperature
                payload["max_tokens"] = request.model_policy.max_output_tokens
            if tools:
                payload["tools"] = tools
            if request.structured_output_schema:
                payload["response_format"] = {"type": "json_object"}
            return payload
        if self._family is ProtocolFamily.ANTHROPIC_MESSAGES:
            if request.images:
                messages = _with_anthropic_images(messages, request.images)
            payload = {
                "model": request.model_policy.model_name,
                "system": request.system_instruction,
                "messages": messages if request.messages or request.images else [{"role": "user", "content": request.task}],
                "max_tokens": request.model_policy.max_output_tokens,
            }
            if tools:
                payload["tools"] = tools
            return payload
        contents = []
        for item in messages:
            parts: list[dict[str, object]] = [{"text": item["content"]}]
            if request.images and item["role"] == "user":
                parts.extend(
                    {"fileData": {"mimeType": image.mime_type, "fileUri": image.content_ref}}
                    for image in request.images
                )
            contents.append(
                {
                    "role": "model" if item["role"] == "assistant" else "user",
                    "parts": parts,
                }
            )
        return {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": request.system_instruction}]},
            "generationConfig": {"temperature": request.model_policy.temperature, "maxOutputTokens": request.model_policy.max_output_tokens},
        }

    def _normalize(self, request: ModelRequest, response: httpx.Response) -> ModelResponse:
        if response.status_code in {401, 403}:
            raise ModelProviderUnauthorized(safe_http_status=response.status_code)
        if response.status_code == 404:
            raise ModelProviderModelUnavailable(safe_http_status=response.status_code)
        if response.status_code == 429:
            raise ModelProviderRateLimited(safe_http_status=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            raise ModelProviderHTTPError(safe_http_status=response.status_code)
        try:
            payload = response.json()
            adapter = {
                ProtocolFamily.OPENAI_RESPONSES: OpenAIResponsesProtocolAdapter(),
                ProtocolFamily.OPENAI_CHAT: OpenAIChatProtocolAdapter(),
                ProtocolFamily.LOCAL_OPENAI_COMPATIBLE: LocalOpenAIProtocolAdapter(),
                ProtocolFamily.ANTHROPIC_MESSAGES: AnthropicMessagesProtocolAdapter(),
                ProtocolFamily.GOOGLE_GENAI: GoogleGenAIProtocolAdapter(),
            }[self._family]
            normalized = adapter.normalize(payload, provider_id=self._provider, endpoint_id=self._endpoint_id, requested_model=request.model_policy.model_name)
            return normalized.to_model_response(request)
        except (ValueError, TypeError, KeyError, ValidationError) as error:
            raise ModelProviderError() from error


__all__ = ("ProtocolHTTPProviderAdapter", "SUPPORTED_PROTOCOL_FAMILIES")


def _with_openai_chat_images(
    messages: list[dict[str, object]], images: tuple[ModelImageInput, ...]
) -> list[dict[str, object]]:
    result = [dict(item) for item in messages]
    for item in reversed(result):
        if item.get("role") == "user":
            item["content"] = [
                {"type": "text", "text": item["content"]},
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": image.content_ref, "detail": "auto"},
                    }
                    for image in images
                ),
            ]
            break
    return result


def _with_openai_response_images(
    messages: list[dict[str, object]], images: tuple[ModelImageInput, ...]
) -> list[dict[str, object]]:
    result = [dict(item) for item in messages]
    for item in reversed(result):
        if item.get("role") == "user":
            item["content"] = [
                {"type": "input_text", "text": item["content"]},
                *(
                    {"type": "input_image", "image_url": image.content_ref}
                    for image in images
                ),
            ]
            break
    return result


def _with_anthropic_images(
    messages: list[dict[str, object]], images: tuple[ModelImageInput, ...]
) -> list[dict[str, object]]:
    result = [dict(item) for item in messages]
    for item in reversed(result):
        if item.get("role") == "user":
            item["content"] = [
                {"type": "text", "text": item["content"]},
                *(
                    {
                        "type": "image",
                        "source": {"type": "url", "url": image.content_ref},
                    }
                    for image in images
                ),
            ]
            break
    return result


def _safe_header(response: httpx.Response, name: str) -> str | None:
    try:
        value = response.headers.get(name)
    except (AttributeError, RuntimeError):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _execution_metadata(
    response: httpx.Response, normalized: ModelResponse, started: float
) -> ProviderExecutionMetadata:
    candidate = next(
        (
            _safe_header(response, name)
            for name in ("x-request-id", "request-id", "trace-id")
            if _safe_header(response, name) is not None
        ),
        None,
    )
    if candidate is None:
        candidate = normalized.provider_response_id
    try:
        provider_request_id = ProviderExecutionMetadata(
            provider_request_id=candidate
        ).provider_request_id
    except (TypeError, ValueError):
        provider_request_id = None
    return ProviderExecutionMetadata(
        provider_request_id=provider_request_id,
        http_status=response.status_code,
        latency_ms=max((time.perf_counter() - started) * 1000.0, 0.0),
        transport_name="httpx",
    )


def _response_with_latency(
    response: ModelResponse, metadata: ProviderExecutionMetadata
) -> ModelResponse:
    material = response.model_dump(mode="python")
    material["latency_ms"] = metadata.latency_ms
    if metadata.provider_request_id is not None:
        material["provider_response_id"] = metadata.provider_request_id
    material["response_id"] = ""
    material["fingerprint"] = "sha256:" + "0" * 64
    return ModelResponse.model_validate(material)


def _is_loopback_url(value: str) -> bool:
    scheme, separator, remainder = value.partition("://")
    if not separator or scheme != "http":
        return False
    authority = remainder.split("/", 1)[0]
    host = authority[1:].split("]", 1)[0] if authority.startswith("[") and "]" in authority else authority.split(":", 1)[0]
    return host.casefold() in {"localhost", "127.0.0.1", "::1"}


def _httpx_error_code(error: httpx.HTTPError) -> str:
    """Map transport failures to fixed, content-free diagnostic categories."""

    if isinstance(error, httpx.ProxyError):
        return "PROXY_ERROR"
    if isinstance(error, httpx.ConnectTimeout):
        return "CONNECT_TIMEOUT"
    if isinstance(error, httpx.ReadTimeout):
        return "READ_TIMEOUT"
    if isinstance(error, httpx.WriteTimeout):
        return "WRITE_TIMEOUT"
    if isinstance(error, httpx.PoolTimeout):
        return "POOL_TIMEOUT"
    if isinstance(error, httpx.ConnectError):
        return "CONNECT_ERROR"
    if isinstance(error, httpx.ReadError):
        return "READ_ERROR"
    if isinstance(error, httpx.WriteError):
        return "WRITE_ERROR"
    if isinstance(error, httpx.CloseError):
        return "CLOSE_ERROR"
    if isinstance(error, httpx.ProtocolError):
        return "PROTOCOL_ERROR"
    return "HTTP_TRANSPORT_ERROR"


def _httpx_transport_stage(error: httpx.HTTPError) -> str:
    """Map transport exception types to a fixed pre-response stage."""

    if isinstance(error, httpx.ConnectError):
        cause: BaseException | None = error
        for _ in range(6):
            cause_type = type(cause)
            cause_module = getattr(cause_type, "__module__", "")
            cause_name = getattr(cause_type, "__name__", "")
            if cause_module == "socket" and cause_name == "gaierror":
                return "DNS"
            if cause_module == "ssl" and cause_name == "SSLError":
                return "TLS"
            next_cause = cause.__cause__ or cause.__context__ if cause is not None else None
            if next_cause is cause:
                break
            cause = next_cause
            if cause is None:
                break
        return "TCP"
    if isinstance(error, httpx.ConnectTimeout):
        return "TCP"
    return "HTTP"
