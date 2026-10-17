from __future__ import annotations

import httpx
import pytest
import math

from embedded_copilot.rag.embedding import (
    EmbeddingDimensionMismatch,
    EmbeddingModelUnavailable,
    EmbeddingResponseInvalid,
    EmbeddingServiceUnavailable,
    OllamaEmbeddingAdapter,
)


def _adapter(handler, *, model: str = "embeddinggemma") -> OllamaEmbeddingAdapter:
    return OllamaEmbeddingAdapter(
        model=model,
        base_url="http://127.0.0.1:11434",
        transport=httpx.MockTransport(handler),
    )


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.0.test"})
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "embeddinggemma"}]})
    if request.url.path == "/api/embed":
        body = request.read()
        assert b'"model":"embeddinggemma"' in body
        return httpx.Response(200, json={"model": "embeddinggemma", "embeddings": [[0.1, 0.2, 0.3]]})
    return httpx.Response(404)


def test_ollama_single_embedding_and_probe() -> None:
    adapter = _adapter(_handler)

    vector = adapter.embed_query("I2C1 bus speed")
    probe = adapter.probe()

    assert vector == [0.1, 0.2, 0.3]
    assert probe.provider == "ollama"
    assert probe.model == "embeddinggemma"
    assert probe.dimension == 3
    assert "0.1" not in probe.identity


def test_ollama_batch_embedding_preserves_cardinality() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(
                200,
                json={"embeddings": [[1.0, 2.0], [3.0, 4.0]]},
            )
        return httpx.Response(200, json={"models": [{"name": "embeddinggemma"}]})

    vectors = _adapter(handler).embed_documents(["one", "two"])

    assert vectors == [[1.0, 2.0], [3.0, 4.0]]
    assert len(vectors) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"embeddings": [[]]},
        {"embeddings": [[True, 1.0]]},
        {"embeddings": [[float("nan")]]},
        {"embeddings": [[float("inf")]]},
        {"embeddings": [["not-a-number"]]},
    ],
)
def test_ollama_rejects_unsafe_vectors(payload: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        item = payload.get("embeddings")
        scalar = item[0][0] if isinstance(item, list) and item and isinstance(item[0], list) and item[0] else None
        if isinstance(scalar, float) and math.isnan(scalar):
            return httpx.Response(200, content=b'{"embeddings":[[NaN]]}', headers={"content-type": "application/json"})
        if scalar == float("inf"):
            return httpx.Response(200, content=b'{"embeddings":[[Infinity]]}', headers={"content-type": "application/json"})
        return httpx.Response(200, json=payload)

    with pytest.raises(EmbeddingResponseInvalid):
        _adapter(handler).embed_query("probe")


def test_ollama_rejects_batch_cardinality_and_dimension_mismatch() -> None:
    def cardinality(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0, 2.0]]})

    with pytest.raises(EmbeddingResponseInvalid):
        _adapter(cardinality).embed_documents(["one", "two"])

    calls = 0

    def dimensions(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"embeddings": [[1.0, 2.0]] if calls == 1 else [[1.0, 2.0, 3.0]]},
        )

    adapter = _adapter(dimensions)
    adapter.embed_query("first")
    with pytest.raises(EmbeddingDimensionMismatch):
        adapter.embed_query("second")


def test_ollama_model_missing_is_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"version": "test"})

    with pytest.raises(EmbeddingModelUnavailable):
        _adapter(handler).probe()


def test_ollama_service_failure_and_timeout_are_fail_closed() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(EmbeddingServiceUnavailable):
        _adapter(unavailable).probe()

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(EmbeddingServiceUnavailable):
        _adapter(timeout).embed_query("probe")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://127.0.0.2:11434",
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434?token=secret",
        "http://user:pass@127.0.0.1:11434",
    ],
)
def test_ollama_endpoint_requires_exact_allow_list(base_url: str) -> None:
    with pytest.raises(ValueError):
        OllamaEmbeddingAdapter(model="embeddinggemma", base_url=base_url)


def test_ollama_does_not_accept_credentials() -> None:
    adapter = OllamaEmbeddingAdapter(model="embeddinggemma")
    assert not hasattr(adapter, "api_key")
