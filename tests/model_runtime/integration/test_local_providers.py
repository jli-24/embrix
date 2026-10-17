from __future__ import annotations

import httpx

from embedded_copilot.model_runtime.integration import (
    ProductionModelSettings,
    create_production_provider,
)

from .fixtures import model_request


def _response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": "Local grounded answer."},
                    "finish_reason": "stop",
                }
            ]
        },
    )


def test_ollama_uses_loopback_openai_compatible_endpoint_without_api_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        return _response()

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": "qwen2.5:7b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
        }
    )
    provider = create_production_provider(
        settings, http_transport=httpx.MockTransport(handler)
    )
    response = provider.generate(model_request(settings.to_model_policy()))
    assert response.provider == "ollama"
    assert seen == {
        "url": "http://127.0.0.1:11434/v1/chat/completions",
        "authorization": None,
    }


def test_vllm_supports_explicit_api_key_boundary() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        return _response()

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "vllm",
            "MODEL_NAME": "Qwen2.5-7B-Instruct",
            "VLLM_BASE_URL": "https://models.example/v1",
            "VLLM_API_KEY": "production-secret",
        }
    )
    provider = create_production_provider(
        settings, http_transport=httpx.MockTransport(handler)
    )
    response = provider.generate(model_request(settings.to_model_policy()))
    assert response.provider == "vllm"
    assert seen == {
        "url": "https://models.example/v1/chat/completions",
        "authorization": "Bearer production-secret",
    }
