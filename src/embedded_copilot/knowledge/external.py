from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_OFFICIAL_DOMAINS = frozenset(
    {
        "st.com",
        "espressif.com",
        "arm.com",
        "freertos.org",
        "github.com",
    }
)
_PROVIDERS = frozenset({"brave", "unavailable"})
_OFFICIAL_GITHUB_ORGANIZATIONS = frozenset(
    {"stmicroelectronics", "espressif", "arm-software", "freertos"}
)
_HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,253}[a-z0-9]$", re.IGNORECASE)
_TEXT = re.compile(r"\s+")


class ExternalKnowledgeSearchUnavailable(Exception):
    """An explicit external evidence request could not complete safely."""


@dataclass(frozen=True, slots=True)
class ExternalKnowledgeResult:
    result_id: str
    title: str
    summary: str
    url: str
    source_fingerprint: str
    domain: str
    retrieved_at: str
    document_date: str | None
    license_reference: str | None


@dataclass(frozen=True, slots=True)
class OfficialWebSearchSettings:
    enabled: bool = False
    provider: str = "unavailable"
    endpoint: str | None = None
    api_key: str | None = None
    allowed_domains: tuple[str, ...] = tuple(sorted(_OFFICIAL_DOMAINS))
    timeout_seconds: float = 10.0
    max_response_bytes: int = 262_144
    top_k: int = 4

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("external search enabled is invalid")
        if not isinstance(self.provider, str) or self.provider not in _PROVIDERS:
            raise ValueError("external search provider is invalid")
        if self.enabled:
            if self.provider == "unavailable" or not self.endpoint or not self.api_key:
                raise ValueError("enabled external search requires a provider configuration")
        if self.endpoint is not None:
            parsed = urlparse(self.endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("external search endpoint is invalid")
        if self.api_key is not None and (
            not isinstance(self.api_key, str) or not self.api_key.strip() or len(self.api_key) > 512
        ):
            raise ValueError("external search api key is invalid")
        if type(self.allowed_domains) is not tuple or not self.allowed_domains:
            raise ValueError("external search allowed domains are invalid")
        normalized = tuple(domain.casefold().strip() for domain in self.allowed_domains)
        if (
            len(set(normalized)) != len(normalized)
            or any(domain not in _OFFICIAL_DOMAINS for domain in normalized)
        ):
            raise ValueError("external search allowed domains are invalid")
        if type(self.timeout_seconds) is not float or not 0.1 <= self.timeout_seconds <= 60.0:
            raise ValueError("external search timeout is invalid")
        if type(self.max_response_bytes) is not int or not 1024 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("external search response limit is invalid")
        if type(self.top_k) is not int or not 1 <= self.top_k <= 10:
            raise ValueError("external search top_k is invalid")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> OfficialWebSearchSettings:
        import os

        values = os.environ if environment is None else environment
        enabled = values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_ENABLED", "false").strip().casefold() == "true"
        domains = values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_ALLOWED_DOMAINS")
        return cls(
            enabled=enabled,
            provider=values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_PROVIDER", "unavailable").strip().casefold(),
            endpoint=values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_ENDPOINT") or None,
            api_key=values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_API_KEY") or None,
            allowed_domains=(
                tuple(part.strip() for part in domains.split(",") if part.strip())
                if domains is not None
                else tuple(sorted(_OFFICIAL_DOMAINS))
            ),
            timeout_seconds=float(values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_TIMEOUT_SECONDS", "10")),
            max_response_bytes=int(values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_MAX_RESPONSE_BYTES", "262144")),
            top_k=int(values.get("EMBEDDED_COPILOT_EXTERNAL_SEARCH_TOP_K", "4")),
        )


@runtime_checkable
class OfficialWebSearchTransport(Protocol):
    def search(self, *, query: str, settings: OfficialWebSearchSettings) -> object: ...


class BraveWebSearchTransport:
    """Small HTTP boundary for the configured Brave Search endpoint."""

    def search(self, *, query: str, settings: OfficialWebSearchSettings) -> object:
        if not settings.enabled or settings.provider != "brave" or not settings.endpoint or not settings.api_key:
            raise ExternalKnowledgeSearchUnavailable()
        try:
            from urllib.parse import urlencode

            separator = "&" if "?" in settings.endpoint else "?"
            url = f"{settings.endpoint}{separator}{urlencode({'q': query, 'count': settings.top_k})}"
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "X-Subscription-Token": settings.api_key,
                    "User-Agent": "EmbeddedCopilot/6.0",
                },
                method="GET",
            )
            with urlopen(request, timeout=settings.timeout_seconds) as response:  # noqa: S310 - endpoint is validated configuration
                content_type = response.headers.get_content_type()
                length = response.headers.get("Content-Length")
                if content_type != "application/json" or (
                    length is not None and int(length) > settings.max_response_bytes
                ):
                    raise ExternalKnowledgeSearchUnavailable()
                payload = response.read(settings.max_response_bytes + 1)
            if len(payload) > settings.max_response_bytes:
                raise ExternalKnowledgeSearchUnavailable()
            return json.loads(payload.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, ValueError, OSError) as error:
            raise ExternalKnowledgeSearchUnavailable() from error


def _safe_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError("external search result is invalid")
    normalized = _TEXT.sub(" ", value).strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError("external search result is invalid")
    return normalized


def _official_url(value: object, allowed_domains: tuple[str, ...]) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or not host
        or not _HOST.fullmatch(host)
        or not any(host == domain or host.endswith("." + domain) for domain in allowed_domains)
    ):
        return None
    if host == "github.com":
        parts = tuple(part for part in parsed.path.split("/") if part)
        if not parts or parts[0].casefold() not in _OFFICIAL_GITHUB_ORGANIZATIONS:
            return None
    return parsed.geturl()


def _fingerprint(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class OfficialWebSearchClient:
    """Explicit-only official search returning sanitized transient results."""

    __slots__ = ("_settings", "_transport")

    def __init__(self, *, settings: OfficialWebSearchSettings, transport: OfficialWebSearchTransport | None = None) -> None:
        if not isinstance(settings, OfficialWebSearchSettings):
            raise TypeError("external search settings are invalid")
        active_transport = transport or BraveWebSearchTransport()
        if not isinstance(active_transport, OfficialWebSearchTransport):
            raise TypeError("external search transport is invalid")
        self._settings = settings
        self._transport = active_transport

    def search(self, task: str, constraints: tuple[str, ...]) -> tuple[ExternalKnowledgeResult, ...]:
        if not self._settings.enabled:
            raise ExternalKnowledgeSearchUnavailable()
        if not isinstance(task, str) or not task.strip():
            raise ExternalKnowledgeSearchUnavailable()
        if type(constraints) is not tuple or any(not isinstance(item, str) for item in constraints):
            raise ExternalKnowledgeSearchUnavailable()
        query = " ".join((task.strip(), *(item.strip() for item in constraints if item.strip())))
        try:
            raw = self._transport.search(query=query, settings=self._settings)
            results = raw["web"]["results"] if isinstance(raw, Mapping) else None
            if not isinstance(results, list):
                raise ValueError("external search result is invalid")
            results_out: list[ExternalKnowledgeResult] = []
            retrieved_at = datetime.now(UTC).isoformat()
            for item in results:
                if not isinstance(item, Mapping):
                    raise ValueError("external search result is invalid")
                url = _official_url(item.get("url"), self._settings.allowed_domains)
                if url is None:
                    continue
                title = _safe_text(item.get("title"), maximum=256)
                summary = _safe_text(item.get("description"), maximum=512)
                record = {
                    "url": url,
                    "domain": urlparse(url).hostname.casefold(),
                    "retrieved_at": retrieved_at,
                    "published_at": item.get("page_age") if isinstance(item.get("page_age"), str) else None,
                    "license": item.get("license") if isinstance(item.get("license"), str) else None,
                    "provider": self._settings.provider,
                    "title": title,
                    "snippet": summary,
                }
                digest = _fingerprint(record)
                results_out.append(
                    ExternalKnowledgeResult(
                        result_id=f"external-{digest.removeprefix('sha256:')[:48]}",
                        title=title,
                        summary=summary,
                        url=url,
                        source_fingerprint=digest,
                        domain=record["domain"],
                        retrieved_at=retrieved_at,
                        document_date=record["published_at"],
                        license_reference=record["license"],
                    )
                )
                if len(results_out) == self._settings.top_k:
                    break
            if not results_out:
                raise ExternalKnowledgeSearchUnavailable()
            return tuple(results_out)
        except ExternalKnowledgeSearchUnavailable:
            raise
        except Exception as error:
            raise ExternalKnowledgeSearchUnavailable() from error


OfficialWebSearchAdapter = OfficialWebSearchClient


__all__ = (
    "BraveWebSearchTransport",
    "ExternalKnowledgeResult",
    "ExternalKnowledgeSearchUnavailable",
    "OfficialWebSearchClient",
    "OfficialWebSearchAdapter",
    "OfficialWebSearchSettings",
    "OfficialWebSearchTransport",
)
