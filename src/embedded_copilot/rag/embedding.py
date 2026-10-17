from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingProviderPort(EmbeddingProvider, Protocol):
    """Internal production/test seam with safe provider identity metadata."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int | None: ...

    @property
    def identity(self) -> str: ...


class EmbeddingProviderError(RuntimeError):
    """Base error for production embedding provider failures."""


class EmbeddingServiceUnavailable(EmbeddingProviderError):
    """The configured embedding service cannot be reached or is unhealthy."""


class EmbeddingModelUnavailable(EmbeddingProviderError):
    """The configured embedding model is not available in the provider."""


class EmbeddingResponseInvalid(EmbeddingProviderError):
    """The provider returned an unsafe or malformed embedding response."""


class EmbeddingDimensionMismatch(EmbeddingProviderError):
    """The provider returned vectors with an incompatible dimension."""


@dataclass(frozen=True, slots=True)
class EmbeddingProbe:
    provider: str
    model: str
    dimension: int
    identity: str


class OllamaEmbeddingAdapter:
    """Production local embedding adapter for Ollama's native API."""

    provider = "ollama"
    adapter_version = "ollama-embed-v1"
    _allowed_hosts = frozenset({"127.0.0.1", "localhost", "::1"})
    _probe_text = "Embedded Copilot v6.1 embedding preflight"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("embedding model is required")
        if type(timeout_seconds) not in (int, float) or not 0.1 <= float(timeout_seconds) <= 120.0:
            raise ValueError("embedding timeout is invalid")
        self._base_url = self._validate_base_url(base_url)
        self._model = model.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport
        self._dimension: int | None = None

    @classmethod
    def _validate_base_url(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Ollama base URL is invalid")
        checked = value.strip().rstrip("/")
        parsed = urlsplit(checked)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in cls._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Ollama base URL is invalid")
        return checked

    @property
    def provider_name(self) -> str:
        return self.provider

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int | None:
        return self._dimension

    @property
    def identity(self) -> str:
        dimension = self._dimension
        if dimension is None:
            return f"{self.provider}|{self._model}|{self.adapter_version}|unprobed"
        return f"{self.provider}|{self._model}|{self.adapter_version}|{dimension}"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"{self._base_url}/",
            timeout=self._timeout_seconds,
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
        )

    def _post_embed(self, value: str | list[str]) -> list[list[float]]:
        try:
            with self._client() as client:
                response = client.post(
                    "api/embed",
                    json={"model": self._model, "input": value},
                )
        except httpx.TimeoutException as error:
            raise EmbeddingServiceUnavailable("Ollama embedding request timed out") from error
        except httpx.HTTPError as error:
            raise EmbeddingServiceUnavailable("Ollama embedding service is unavailable") from error
        if response.status_code in {404, 400}:
            raise EmbeddingModelUnavailable("Ollama embedding model is unavailable")
        if not 200 <= response.status_code < 300:
            raise EmbeddingServiceUnavailable("Ollama embedding request failed")
        try:
            payload = response.json()
        except ValueError as error:
            raise EmbeddingResponseInvalid("Ollama embedding response is not JSON") from error
        if not isinstance(payload, Mapping):
            raise EmbeddingResponseInvalid("Ollama embedding response is invalid")
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list):
            raise EmbeddingResponseInvalid("Ollama embedding response has no vectors")
        return self._validate_vectors(vectors, expected_count=(1 if isinstance(value, str) else len(value)))

    @staticmethod
    def _validate_vectors(value: object, *, expected_count: int) -> list[list[float]]:
        if not isinstance(value, list) or len(value) != expected_count:
            raise EmbeddingResponseInvalid("Ollama embedding batch cardinality is invalid")
        validated: list[list[float]] = []
        dimension: int | None = None
        for vector in value:
            if not isinstance(vector, list) or not vector:
                raise EmbeddingResponseInvalid("Ollama embedding vector is empty")
            if any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                for item in vector
            ):
                raise EmbeddingResponseInvalid("Ollama embedding vector contains invalid values")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise EmbeddingDimensionMismatch("Ollama embedding batch dimensions differ")
            validated.append([float(item) for item in vector])
        return validated

    def _accept_dimension(self, vectors: list[list[float]]) -> None:
        dimension = len(vectors[0])
        if self._dimension is None:
            self._dimension = dimension
        elif self._dimension != dimension:
            raise EmbeddingDimensionMismatch("Ollama embedding dimension changed")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        if any(not isinstance(text, str) for text in values):
            raise TypeError("embedding texts must be strings")
        if not values:
            return []
        vectors = self._post_embed(values)
        self._accept_dimension(vectors)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError("embedding query must be a string")
        vectors = self._post_embed(text)
        self._accept_dimension(vectors)
        return vectors[0]

    def _check_service(self) -> None:
        try:
            with self._client() as client:
                response = client.get("api/version")
        except httpx.TimeoutException as error:
            raise EmbeddingServiceUnavailable("Ollama service probe timed out") from error
        except httpx.HTTPError as error:
            raise EmbeddingServiceUnavailable("Ollama service is unavailable") from error
        if not 200 <= response.status_code < 300:
            raise EmbeddingServiceUnavailable("Ollama service probe failed")

    def _check_model(self) -> None:
        try:
            with self._client() as client:
                response = client.get("api/tags")
        except httpx.TimeoutException as error:
            raise EmbeddingServiceUnavailable("Ollama model probe timed out") from error
        except httpx.HTTPError as error:
            raise EmbeddingServiceUnavailable("Ollama model service is unavailable") from error
        if not 200 <= response.status_code < 300:
            raise EmbeddingServiceUnavailable("Ollama model probe failed")
        try:
            payload = response.json()
        except ValueError as error:
            raise EmbeddingResponseInvalid("Ollama model response is not JSON") from error
        models = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(models, list):
            raise EmbeddingResponseInvalid("Ollama model response is invalid")
        if not any(
            isinstance(item, Mapping)
            and (item.get("name") == self._model or item.get("model") == self._model)
            for item in models
        ):
            raise EmbeddingModelUnavailable("Ollama configured model is unavailable")

    def probe(self) -> EmbeddingProbe:
        self._check_service()
        self._check_model()
        first = self.embed_query(self._probe_text)
        second = self.embed_query(self._probe_text)
        if len(first) != len(second):
            raise EmbeddingDimensionMismatch("Ollama probe dimension is unstable")
        return EmbeddingProbe(
            provider=self.provider,
            model=self._model,
            dimension=len(first),
            identity=self.identity,
        )


class HashEmbedding:
    """Deterministic offline embedding for tests and the v0.1 demo."""

    def __init__(self, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    @staticmethod
    def _tokens(text: str) -> list[str]:
        lowered = text.lower()
        tokens = re.findall(
            r"0x[0-9a-f]+|[a-z_][a-z0-9_]*|\d+",
            lowered,
        )
        for sequence in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.extend(sequence)
            tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
        return tokens or [lowered.strip() or "<empty>"]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in self._tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, byteorder="big") % self.dimension
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
