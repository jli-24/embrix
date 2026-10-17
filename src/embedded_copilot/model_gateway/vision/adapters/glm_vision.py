from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence

import httpx
from pydantic import SecretStr

from ...contracts import identifier
from ..contracts import (
    VisionObservation,
    VisionRequest,
    VisionResponse,
    validate_vision_request,
)
from ..exceptions import (
    VisionAuthenticationError,
    VisionMalformedResponseError,
    VisionProviderError,
    VisionRateLimitError,
    VisionTimeoutError,
    VisionUnavailableError,
)
from ..ports import VisionModelProviderPort

_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
_DEFAULT_MODEL = "glm-4.6v-flash"
_SYSTEM_INSTRUCTION = (
    "Return only a JSON object with exactly these fields: observation_id, objects, "
    "components, interfaces, issues, confidence, evidence. Use arrays of strings "
    "and a numeric confidence between 0 and 1. Do not include markdown, reasoning, "
    "credentials, or provider metadata."
)
_OBSERVATION_FIELDS = frozenset(
    {
        "observation_id",
        "objects",
        "components",
        "interfaces",
        "issues",
        "confidence",
        "evidence",
    }
)
_COLLECTION_FIELDS = ("objects", "components", "interfaces", "issues", "evidence")


class GLMVisionProviderAdapter(VisionModelProviderPort):
    """Request-scoped BigModel GLM vision adapter."""

    __slots__ = (
        "_api_key",
        "_async_transport",
        "_http_transport",
        "_model",
        "_timeout_seconds",
    )

    _api_key: SecretStr
    _model: str
    _timeout_seconds: float
    _http_transport: httpx.BaseTransport | None
    _async_transport: httpx.AsyncBaseTransport | None

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"provider", "model", "_model"}:
            raise AttributeError("vision provider binding is read-only")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        environment: Mapping[str, str] | None = None,
        http_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        values = os.environ if environment is None else environment
        raw_key: SecretStr | str | None = api_key
        if raw_key is None:
            raw_key = values.get("BIGMODEL_API_KEY")
        if isinstance(raw_key, SecretStr):
            checked_key = raw_key
        elif (
            isinstance(raw_key, str)
            and raw_key.strip()
            and "\n" not in raw_key
            and "\r" not in raw_key
        ):
            checked_key = SecretStr(raw_key.strip())
        else:
            raise ValueError("vision provider credential is invalid")

        checked_model = model
        if checked_model is None:
            checked_model = values.get("GLM_VISION_MODEL", _DEFAULT_MODEL)
        try:
            checked_model = identifier(checked_model, field="vision_model")
        except (TypeError, ValueError):
            raise ValueError("vision model binding is invalid")
        if (
            type(timeout_seconds) not in (int, float)
            or not 0.1 <= timeout_seconds <= 120.0
        ):
            raise ValueError("vision provider timeout is invalid")

        object.__setattr__(self, "_api_key", checked_key)
        object.__setattr__(self, "_model", checked_model)
        object.__setattr__(self, "_timeout_seconds", float(timeout_seconds))
        object.__setattr__(self, "_http_transport", http_transport)
        object.__setattr__(self, "_async_transport", async_transport)

    @property
    def provider(self) -> str:
        return "bigmodel"

    @property
    def model(self) -> str:
        return self._model

    def generate(self, request: VisionRequest) -> VisionResponse:
        checked = self._request(request)
        try:
            with httpx.Client(
                timeout=self._timeout_seconds,
                transport=self._http_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    _ENDPOINT,
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            return self._response(response)
        except asyncio.CancelledError:
            raise
        except VisionProviderError:
            raise
        except httpx.TimeoutException:
            raise VisionTimeoutError() from None
        except httpx.HTTPError:
            raise VisionUnavailableError() from None

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        checked = self._request(request)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._async_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    _ENDPOINT,
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            return self._response(response)
        except asyncio.CancelledError:
            raise
        except VisionProviderError:
            raise
        except httpx.TimeoutException:
            raise VisionTimeoutError() from None
        except httpx.HTTPError:
            raise VisionUnavailableError() from None

    @staticmethod
    def _request(request: VisionRequest) -> VisionRequest:
        try:
            return validate_vision_request(request)
        except (TypeError, ValueError):
            raise VisionMalformedResponseError() from None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: VisionRequest) -> dict[str, object]:
        context = json.dumps(
            request.context.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        metadata = json.dumps(
            request.metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        text = (
            f"Engineering context:\n{context}\n"
            f"Prompt:\n{request.prompt}\n"
            f"Metadata:\n{metadata}"
        )
        return {
            "model": self.model,
            "messages": (
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": (
                        {"type": "text", "text": text},
                        {
                            "type": "image_url",
                            "image_url": {"url": request.image_reference},
                        },
                    ),
                },
            ),
        }

    def _response(self, response: httpx.Response) -> VisionResponse:
        if response.status_code in {401, 403}:
            raise VisionAuthenticationError()
        if response.status_code == 429:
            raise VisionRateLimitError()
        if response.status_code < 200 or response.status_code >= 300:
            raise VisionUnavailableError()
        try:
            payload = response.json()
            content = self._content(payload)
            raw_observation = json.loads(content)
            observation = self._observation(raw_observation)
        except VisionProviderError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError):
            raise VisionMalformedResponseError() from None
        return VisionResponse(
            provider=self.provider,
            model=self.model,
            observation=observation,
            metadata=(),
        )

    @staticmethod
    def _content(payload: object) -> str:
        if not isinstance(payload, Mapping):
            raise VisionMalformedResponseError()
        choices = payload.get("choices")
        if (
            not isinstance(choices, Sequence)
            or isinstance(choices, (str, bytes))
            or not choices
            or not isinstance(choices[0], Mapping)
        ):
            raise VisionMalformedResponseError()
        message = choices[0].get("message")
        if not isinstance(message, Mapping) or not isinstance(
            message.get("content"), str
        ):
            raise VisionMalformedResponseError()
        return message["content"]

    @staticmethod
    def _observation(value: object) -> VisionObservation:
        if not isinstance(value, Mapping) or set(value) != _OBSERVATION_FIELDS:
            raise VisionMalformedResponseError()
        material = dict(value)
        for field in _COLLECTION_FIELDS:
            entries = material.get(field)
            if type(entries) is not list:
                raise VisionMalformedResponseError()
            material[field] = tuple(entries)
        try:
            return VisionObservation.model_validate(material)
        except (TypeError, ValueError):
            raise VisionMalformedResponseError() from None


__all__ = ("GLMVisionProviderAdapter",)
