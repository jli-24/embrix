from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import SecretStr, ValidationError

from .._transport_metadata import ProviderExecutionMetadata, ProviderExecutionResult
from ..contracts import (
    ModelOpenQuestion,
    ModelRequest,
    ModelRequirementProposal,
    ModelResponse,
    ModelUsage,
    ProviderCapability,
    validate_model_request,
    validate_model_response,
)
from ..exceptions import (
    ModelProviderAnswerEmpty,
    ModelProviderConfigurationError,
    ModelProviderError,
    ModelProviderModelUnavailable,
    ModelProviderParseError,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
)
from ..ports import ModelProviderPort

logger = logging.getLogger(__name__)

_RESPONSE_TELEMETRY_EVENT = "provider_response_boundary_telemetry"
_REQUEST_TELEMETRY_EVENT = "provider_request_boundary_telemetry"


def _response_telemetry(
    stage: str,
    status: str,
    **fields: object,
) -> None:
    """Emit only fixed, non-content response-boundary diagnostics."""

    logger.info(
        _RESPONSE_TELEMETRY_EVENT,
        extra={
            "event_name": _RESPONSE_TELEMETRY_EVENT,
            "response_stage": stage,
            "telemetry_status": status,
            **fields,
        },
    )


def _request_telemetry(**fields: object) -> None:
    """Emit fixed request-boundary fields without prompt or credential content."""

    logger.info(
        _REQUEST_TELEMETRY_EVENT,
        extra={
            "event_name": _REQUEST_TELEMETRY_EVENT,
            "telemetry_status": "PASS",
            **fields,
        },
    )

_STRUCTURED_BLOCK = re.compile(
    r"```(?:json)?\s*(?P<payload>.+?)\s*```", re.IGNORECASE | re.DOTALL
)


@runtime_checkable
class OpenAICompatibleTransport(Protocol):
    """Legacy injected transport retained for offline compatibility tests."""

    def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def agenerate(self, request: ModelRequest) -> ModelResponse: ...


class OpenAICompatibleHTTPAdapter(ModelProviderPort):
    """Request-scoped OpenAI-compatible Chat Completions adapter."""

    __slots__ = (
        "_api_key",
        "_async_transport",
        "_base_url",
        "_legacy_transport",
        "_provider",
        "_supports_image_input",
        "_sync_transport",
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: SecretStr | str | None = None,
        timeout_seconds: float = 30.0,
        supports_image_input: bool = False,
        http_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        transport: OpenAICompatibleTransport | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in (int, float)
            or not 0.1 <= timeout_seconds <= 120.0
        ):
            raise ValueError("provider timeout is invalid")
        if type(supports_image_input) is not bool:
            raise TypeError("image input capability is invalid")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider is invalid")
        if not isinstance(base_url, str) or not base_url.startswith("https://"):
            raise ValueError("provider endpoint is invalid")
        if api_key is None and transport is None:
            raise ValueError("provider credential is invalid")
        if isinstance(api_key, SecretStr):
            checked_key = api_key
        elif (
            isinstance(api_key, str)
            and api_key.strip()
            and "\n" not in api_key
            and "\r" not in api_key
        ):
            checked_key = SecretStr(api_key.strip())
        elif transport is not None:
            checked_key = SecretStr("")
        else:
            raise ValueError("provider credential is invalid")
        self._api_key = checked_key
        self._async_transport = async_transport
        self._base_url = base_url.rstrip("/") + "/"
        self._legacy_transport = transport
        self._provider = provider
        self._supports_image_input = supports_image_input or bool(
            getattr(transport, "supports_image_input", False)
        )
        self._sync_transport = http_transport
        self._timeout_seconds = float(timeout_seconds)

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability.TEXT,
            *((ProviderCapability.VISION,) if self._supports_image_input else ()),
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        return self._generate_with_metadata(request).response

    def _generate_with_metadata(self, request: ModelRequest) -> ProviderExecutionResult:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        if self._legacy_transport is not None:
            try:
                return ProviderExecutionResult(
                    response=validate_model_response(
                        self._legacy_transport.generate(copy.deepcopy(checked))
                    )
                )
            except ModelProviderError:
                raise
            except (TypeError, ValueError, ValidationError):
                raise ModelProviderError() from None
        started = time.perf_counter()
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._sync_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    "chat/completions",
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            result = self._response(checked, response)
            self._log_call(checked, started, success=True)
            metadata = _execution_metadata(response, result, started)
            return ProviderExecutionResult(
                response=_response_with_latency(result, metadata),
                metadata=metadata,
            )
        except ModelProviderError:
            self._log_call(checked, started, success=False)
            raise
        except httpx.TimeoutException:
            self._log_call(checked, started, success=False)
            raise ModelProviderTimeout() from None
        except httpx.HTTPError:
            self._log_call(checked, started, success=False)
            raise ModelProviderError() from None

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        return (await self._agenerate_with_metadata(request)).response

    async def _agenerate_with_metadata(self, request: ModelRequest) -> ProviderExecutionResult:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        if self._legacy_transport is not None:
            try:
                return ProviderExecutionResult(
                    response=validate_model_response(
                        await self._legacy_transport.agenerate(copy.deepcopy(checked))
                    )
                )
            except asyncio.CancelledError:
                raise
            except ModelProviderError:
                raise
            except (TypeError, ValueError, ValidationError):
                raise ModelProviderError() from None
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._async_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "chat/completions",
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            result = self._response(checked, response)
            self._log_call(checked, started, success=True)
            metadata = _execution_metadata(response, result, started)
            return ProviderExecutionResult(
                response=_response_with_latency(result, metadata),
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except ModelProviderError:
            self._log_call(checked, started, success=False)
            raise
        except httpx.TimeoutException:
            self._log_call(checked, started, success=False)
            raise ModelProviderTimeout() from None
        except httpx.HTTPError:
            self._log_call(checked, started, success=False)
            raise ModelProviderError() from None

    def _log_call(
        self, request: ModelRequest, started: float, *, success: bool
    ) -> None:
        logger.info(
            "model_provider_call",
            extra={
                "event_name": "model_provider_call",
                "provider": self._provider,
                "model": request.model_policy.model_name,
                "request_id": request.request_id,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "success": success,
            },
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        context = json.dumps(
            request.context.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        user_content = "\n".join(
            (
                "Engineering context:",
                context,
                "Task:",
                request.prompt,
                "Constraints:",
                json.dumps(
                    request.constraints, ensure_ascii=False, separators=(",", ":")
                ),
            )
        )
        user_content: object = "\n".join(
            (
                "Engineering context:",
                context,
                "Task:",
                request.prompt,
                "Constraints:",
                json.dumps(
                    request.constraints, ensure_ascii=False, separators=(",", ":")
                ),
            )
        )
        if request.images:
            user_content = [
                {"type": "text", "text": user_content},
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": image.content_ref, "detail": "auto"},
                    }
                    for image in request.images
                ),
            ]
        messages = (
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": user_content},
        )
        payload = {
            "model": request.model_policy.model_name,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        _request_telemetry(
            request_model=request.model_policy.model_name,
            request_message_count=len(messages),
            system_message_present=True,
            system_prompt_length=len(request.system_prompt),
            user_message_present=True,
            user_prompt_length=len(user_content),
            context_message_present=False,
            context_payload_length=len(context),
            project_context_attached=request.context is not None,
            requirement_context_attached="UNOBSERVED",
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p="UNOBSERVED",
            response_format_present=False,
            http_payload_valid=True,
            messages_array_length=len(messages),
            first_message_role=messages[0]["role"],
            last_message_role=messages[-1]["role"],
            task_mode="UNOBSERVED",
            project_mode="UNOBSERVED",
        )
        return payload

    def _response(
        self, request: ModelRequest, response: httpx.Response
    ) -> ModelResponse:
        if response.status_code in {401, 403}:
            raise ModelProviderUnauthorized(safe_http_status=response.status_code)
        if response.status_code == 429:
            raise ModelProviderRateLimited(safe_http_status=response.status_code)
        if response.status_code in {400, 404, 422}:
            raise ModelProviderModelUnavailable(safe_http_status=response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            raise ModelProviderError(safe_http_status=response.status_code)
        _response_telemetry("R0_RAW_RESPONSE_RECEIVED", "PASS", provider_response_received=True)
        try:
            payload = response.json()
            return self._parse_response(request, payload)
        except ModelProviderError:
            raise
        except (TypeError, ValueError):
            _response_telemetry(
                "R1_PROVIDER_ADAPTER_PARSE",
                "FAIL",
                adapter_parse_status="FAIL",
                parsed_field_names=(),
                reason_code="PROVIDER_PARSE",
            )
            _response_telemetry("R3_CONTENT_EXTRACTION", "NOT_REACHED")
            _response_telemetry("R4_ANSWER_TEXT_NORMALIZE", "NOT_REACHED")
            _response_telemetry("R2_MODEL_RESPONSE_OBJECT_CREATE", "NOT_REACHED", model_response_created=False)
            raise ModelProviderParseError() from None

    def _parse_response(self, request: ModelRequest, payload: object) -> ModelResponse:
        parsed_field_names: tuple[str, ...] = ()
        content_extraction_recorded = False
        normalization_recorded = False
        model_response_recorded = False
        try:
            if not isinstance(payload, Mapping):
                raise ModelProviderParseError()
            choices = payload.get("choices")
            if (
                not isinstance(choices, Sequence)
                or isinstance(choices, (str, bytes))
                or not choices
            ):
                raise ModelProviderParseError()
            parsed_field_names = ("choices",)
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise ModelProviderParseError()
            parsed_field_names = ("choices", "choice")
            message = choice.get("message")
            if not isinstance(message, Mapping):
                raise ModelProviderParseError()
            parsed_field_names = ("choices", "choice", "message")
            raw_content = message.get("content")
            content_present = isinstance(raw_content, str)
            content_type = (
                "STRING"
                if isinstance(raw_content, str) and raw_content
                else "EMPTY_STRING"
                if isinstance(raw_content, str)
                else "NULL"
                if raw_content is None
                else "OTHER"
            )
            content_length = len(raw_content) if isinstance(raw_content, str) else 0
            _response_telemetry(
                "R1_PROVIDER_ADAPTER_PARSE",
                "PASS" if content_present else "FAIL",
                adapter_parse_status="PASS" if content_present else "FAIL",
                parsed_field_names=("choices", "choice", "message", "content")
                if content_present
                else parsed_field_names,
                reason_code="-" if content_present else "CONTENT_INVALID",
            )
            _response_telemetry(
                "R3_CONTENT_EXTRACTION",
                "PASS" if content_present else "FAIL",
                choices_present=True,
                message_present=True,
                content_present=content_present,
                content_type=content_type,
                content_length=content_length,
            )
            content_extraction_recorded = True
            if not content_present:
                raise ModelProviderParseError()
            content = raw_content
            proposals = message.get("requirement_proposals", payload.get("requirement_proposals", ()))
            questions = message.get("open_questions", payload.get("open_questions", ()))
            content, proposals, questions = self._normalize_answer(
                content,
                proposals=proposals,
                questions=questions,
            )
            _response_telemetry(
                "R4_ANSWER_TEXT_NORMALIZE",
                "PASS" if content.strip() else "FAIL",
                answer_text_present=bool(content.strip()),
                answer_text_length=len(content),
                reason_code="-" if content.strip() else "ANSWER_EMPTY",
            )
            normalization_recorded = True
            if not content.strip():
                raise ModelProviderAnswerEmpty()
            finish_reason = choice.get("finish_reason", "STOP")
            if not isinstance(finish_reason, str) or not finish_reason:
                raise ModelProviderParseError()
            usage = self._usage(payload.get("usage"))
            result = ModelResponse(
                content=content,
                model_name=request.model_policy.model_name,
                provider=self._provider,
                usage=usage,
                finish_reason=finish_reason,
                provenance=request.context.provenance,
                requirement_proposals=proposals,
                open_questions=questions,
                provider_response_id=(
                    payload.get("id")
                    if isinstance(payload, Mapping) and isinstance(payload.get("id"), str)
                    else None
                ),
            )
            _response_telemetry(
                "R2_MODEL_RESPONSE_OBJECT_CREATE",
                "PASS",
                model_response_created=True,
            )
            model_response_recorded = True
            return result
        except ModelProviderError as error:
            if not parsed_field_names:
                _response_telemetry(
                    "R1_PROVIDER_ADAPTER_PARSE",
                    "FAIL",
                    adapter_parse_status="FAIL",
                    parsed_field_names=(),
                    reason_code=type(error).__name__.removeprefix("ModelProvider"),
                )
            if not content_extraction_recorded:
                _response_telemetry("R3_CONTENT_EXTRACTION", "NOT_REACHED")
            if not normalization_recorded:
                _response_telemetry("R4_ANSWER_TEXT_NORMALIZE", "NOT_REACHED")
            if not model_response_recorded:
                _response_telemetry(
                    "R2_MODEL_RESPONSE_OBJECT_CREATE",
                    "NOT_REACHED",
                    model_response_created=False,
                )
            raise
        except (TypeError, ValueError, ValidationError) as error:
            _response_telemetry(
                "R2_MODEL_RESPONSE_OBJECT_CREATE",
                "FAIL",
                model_response_created=False,
                reason_code=type(error).__name__,
            )
            raise ModelProviderParseError() from error

    @classmethod
    def _normalize_answer(
        cls,
        content: str,
        *,
        proposals: object,
        questions: object,
    ) -> tuple[
        str,
        tuple[ModelRequirementProposal, ...],
        tuple[ModelOpenQuestion, ...],
    ]:
        """Normalize text, typed JSON, and text plus fenced metadata.

        Optional structured metadata is deliberately best-effort.  Invalid or
        provider-owned status fields cannot promote a requirement and cannot
        discard an otherwise valid textual answer.
        """

        decoded: Mapping[str, object] | None = None
        typed_envelope = False
        answer_text = content
        try:
            candidate = json.loads(content)
        except (TypeError, ValueError):
            candidate = None
        if isinstance(candidate, Mapping):
            decoded = candidate
            typed_envelope = True
        else:
            match = _STRUCTURED_BLOCK.search(content)
            if match is not None:
                try:
                    candidate = json.loads(match.group("payload"))
                except (TypeError, ValueError):
                    candidate = None
                if isinstance(candidate, Mapping):
                    decoded = candidate
                    answer_text = _STRUCTURED_BLOCK.sub("", content, count=1).strip()

        if decoded is not None:
            typed_answer = decoded.get("answer_text", decoded.get("content"))
            if isinstance(typed_answer, str):
                answer_text = typed_answer
            elif typed_envelope:
                answer_text = ""
            proposals = decoded.get("proposed_requirements", decoded.get("requirement_proposals", proposals))
            questions = decoded.get("open_questions", questions)

        return (
            answer_text,
            cls._proposals(proposals),
            cls._questions(questions),
        )

    @staticmethod
    def _proposals(value: object) -> tuple[ModelRequirementProposal, ...]:
        if not isinstance(value, (tuple, list)):
            return ()
        normalized: list[ModelRequirementProposal] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            rationale = item.get("rationale")
            material = {
                "category": item.get("category"),
                "value": item.get("value"),
                "rationale": rationale if isinstance(rationale, str) else None,
            }
            try:
                normalized.append(ModelRequirementProposal.model_validate(material))
            except (TypeError, ValueError, ValidationError):
                continue
        return tuple(normalized)

    @staticmethod
    def _questions(value: object) -> tuple[ModelOpenQuestion, ...]:
        if not isinstance(value, (tuple, list)):
            return ()
        normalized: list[ModelOpenQuestion] = []
        for item in value:
            if isinstance(item, str):
                material = {"category": "OTHER", "question": item}
            elif isinstance(item, Mapping):
                material = {
                    "category": item.get("category"),
                    "question": item.get("question"),
                }
            else:
                continue
            try:
                normalized.append(ModelOpenQuestion.model_validate(material))
            except (TypeError, ValueError, ValidationError):
                continue
        return tuple(normalized)

    @staticmethod
    def _usage(value: Any) -> ModelUsage | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ModelProviderParseError()
        input_tokens = value.get("prompt_tokens")
        output_tokens = value.get("completion_tokens")
        total_tokens = value.get("total_tokens")
        if any(
            type(item) is not int
            for item in (input_tokens, output_tokens, total_tokens)
        ):
            raise ModelProviderParseError()
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


__all__ = ("OpenAICompatibleHTTPAdapter", "OpenAICompatibleTransport")


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
    ) or normalized.provider_response_id
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
