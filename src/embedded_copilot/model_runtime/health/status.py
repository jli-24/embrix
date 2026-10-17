from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from embedded_copilot.intelligence.models import ModelCapability
from embedded_copilot.model_status import ModelStatusResponse, UnavailableStatusPort


@dataclass(frozen=True, slots=True)
class OllamaStatusPort:
    _base_url: str
    _model: str
    _timeout_seconds: float
    _transport: httpx.AsyncBaseTransport | None = None

    async def status(self) -> ModelStatusResponse:
        available = False
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url.rstrip("/"),
                timeout=self._timeout_seconds,
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                available = _contains_model(response.json(), self._model)
        except Exception:
            available = False
        return ModelStatusResponse(
            provider="ollama",
            status="available" if available else "unavailable",
            capabilities=tuple(
                capability.value
                for capability in (
                    ModelCapability.CHAT,
                    ModelCapability.CODE,
                    ModelCapability.REASONING,
                )
            ),
            model=self._model,
        )


def _contains_model(payload: Any, model: str) -> bool:
    if not isinstance(payload, Mapping):
        return False
    models = payload.get("models")
    if not isinstance(models, Sequence) or isinstance(
        models,
        (str, bytes, bytearray),
    ):
        return False
    for item in models:
        if not isinstance(item, Mapping):
            continue
        if item.get("model") == model or item.get("name") == model:
            return True
    return False
