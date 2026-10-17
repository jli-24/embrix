from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import unescape
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .contracts import (
    ArtifactCompleteness,
    FactProvenance,
    InitialRequirementBrief,
    LicenseState,
    ReferenceFact,
    ReferenceProjectCandidate,
    ResearchSourceType,
    SourceCapability,
    SourceCapabilityReport,
    SourceCapabilityStatus,
    SourceResearchResponse,
    SourceResultStatus,
    _identifier,
    _text,
    _url,
)


@runtime_checkable
class ResearchSourcePort(Protocol):
    source_type: ResearchSourceType
    capabilities: tuple[SourceCapabilityReport, ...]

    def search(
        self,
        queries: tuple[str, ...],
        brief: InitialRequirementBrief,
    ) -> SourceResearchResponse: ...


@runtime_checkable
class ResearchTransport(Protocol):
    def search(self, query: str) -> object: ...

    def fetch(self, url: str) -> object: ...


class SourceTransportFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _result_status(code: str) -> SourceResultStatus:
    aliases = {
        "SOURCE_UNAVAILABLE": SourceResultStatus.UNAVAILABLE,
        "API_UNAVAILABLE": SourceResultStatus.UNAVAILABLE,
        "INDEX_UNAVAILABLE": SourceResultStatus.UNAVAILABLE,
        "DIRECT_FETCH_UNAVAILABLE": SourceResultStatus.UNAVAILABLE,
        "INVALID_RESULT": SourceResultStatus.ERROR,
        "INVALID_URL": SourceResultStatus.ERROR,
    }
    return aliases[code] if code in aliases else SourceResultStatus(code)


class _LinkParser(HTMLParser):
    """Small, bounded HTML parser used only for source metadata and links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description: str | None = None
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = values.get("href")
            if href:
                self._current_href = href
                self._current_text = []
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").casefold()
            if name in {"description", "og:description"} and values.get("content"):
                self.description = values["content"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_href is not None:
            self.links.append((self._current_href, " ".join(self._current_text).strip()))
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._current_href is not None:
            self._current_text.append(data)


def _http_get(url: str, *, timeout_seconds: float, headers: Mapping[str, str]) -> bytes:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read(1_048_577)
    except HTTPError as error:
        if error.code == 429:
            raise SourceTransportFailure("RATE_LIMITED") from None
        if error.code in {401, 403}:
            raise SourceTransportFailure("AUTH_REQUIRED") from None
        if 400 <= error.code < 500:
            raise SourceTransportFailure("ERROR") from None
        raise SourceTransportFailure("UNAVAILABLE") from None
    except (URLError, TimeoutError, OSError):
        raise SourceTransportFailure("UNAVAILABLE") from None


def _html_metadata(payload: bytes, *, url: str) -> tuple[str, str, _LinkParser]:
    try:
        parser = _LinkParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        parser.close()
    except Exception as error:  # parser boundary is explicit
        raise SourceTransportFailure("ERROR") from error
    title = _text(" ".join(parser.title_parts) or urlsplit(url).path.rsplit("/", 1)[-1] or url, field="page title", maximum=256)
    description = _text(parser.description or title, field="page description", maximum=2048)
    return title, description, parser


def _capabilities(
    *,
    search: SourceCapabilityStatus,
    metadata: SourceCapabilityStatus,
    artifacts: SourceCapabilityStatus,
) -> tuple[SourceCapabilityReport, ...]:
    return (
        SourceCapabilityReport(capability=SourceCapability.SEARCH, status=search),
        SourceCapabilityReport(capability=SourceCapability.FETCH_METADATA, status=metadata),
        SourceCapabilityReport(capability=SourceCapability.FETCH_ARTIFACT_INDEX, status=artifacts),
    )


class UnavailableResearchSource:
    def __init__(self, source_type: ResearchSourceType, reason: str) -> None:
        self.source_type = source_type
        self.capabilities = _capabilities(
            search=SourceCapabilityStatus.UNAVAILABLE,
            metadata=SourceCapabilityStatus.UNAVAILABLE,
            artifacts=SourceCapabilityStatus.UNAVAILABLE,
        )
        self._reason = _identifier(reason, field="source unavailable reason")

    def search(self, queries: tuple[str, ...], brief: InitialRequirementBrief) -> SourceResearchResponse:
        del queries, brief
        return SourceResearchResponse(
            source_type=self.source_type,
            status=SourceResultStatus.UNAVAILABLE,
            error_code=self._reason,
        )


def _license(values: Mapping[str, object]) -> tuple[LicenseState, str | None]:
    raw_state = values.get("license_state")
    raw_name = values.get("license")
    if raw_state is not None:
        state = LicenseState(str(raw_state).strip().upper())
    elif raw_name is None:
        state = LicenseState.NOT_DECLARED
    else:
        state = LicenseState.KNOWN
    name = None if raw_name is None else _text(raw_name, field="license", maximum=128)
    if state is LicenseState.KNOWN and name is None:
        raise ValueError("known license requires a license name")
    if state is not LicenseState.KNOWN and name is not None:
        raise ValueError("unknown license cannot have a license name")
    return state, name


def _facts(values: Mapping[str, object]) -> tuple[ReferenceFact, ...]:
    raw = values.get("extracted_facts", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise TypeError("extracted facts are invalid")
    result: list[ReferenceFact] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise TypeError("extracted fact is invalid")
        category = _text(item.get("category"), field="fact category", maximum=96).upper()
        value = _text(item.get("value"), field="fact value", maximum=512)
        artifact = FactProvenance(str(item.get("artifact", "README")).strip().upper())
        location = _text(item.get("location", "source metadata"), field="fact location", maximum=512)
        fact_id = item.get("fact_id") or f"fact-{index}-{category.lower()}"
        result.append(
            ReferenceFact(
                fact_id=_identifier(fact_id, field="fact_id"),
                category=category,
                value=value,
                artifact=artifact,
                location=location,
            )
        )
    return tuple(result)


def _candidate(
    values: Mapping[str, object],
    *,
    source_type: ResearchSourceType,
    expected_host: str,
    official_required: bool = False,
    project_identity: str,
    owner_or_author: str,
    repository_name: str | None,
) -> ReferenceProjectCandidate:
    url = _url(values.get("url"), field="reference project url")
    parsed = urlsplit(url)
    if parsed.hostname.casefold() != expected_host:
        raise ValueError("reference project url host is invalid")
    if official_required and values.get("official") is not True:
        raise ValueError("vendor reference is not official")
    state, license_name = _license(values)
    title = _text(values.get("title"), field="reference project title", maximum=256)
    description = _text(values.get("description"), field="reference project description", maximum=2048)
    return ReferenceProjectCandidate(
        source_type=source_type,
        project_identity=_identifier(project_identity, field="project identity"),
        url=url,
        owner_or_author=_identifier(owner_or_author, field="owner or author"),
        repository_name=None if repository_name is None else _text(repository_name, field="repository", maximum=160),
        revision=None if values.get("revision") is None else _text(values.get("revision"), field="revision", maximum=256),
        license_state=state,
        license_name=license_name,
        title=title,
        description=description,
        artifacts=ArtifactCompleteness.from_mapping(values.get("artifact_hints")),
        extracted_facts=_facts(values),
        official=bool(values.get("official", False)),
    )


class GitHubResearchSource:
    source_type = ResearchSourceType.GITHUB

    def __init__(self, transport: ResearchTransport | None = None) -> None:
        self._transport = transport or HttpGitHubResearchTransport()
        self.capabilities = _capabilities(
            search=SourceCapabilityStatus.SUPPORTED,
            metadata=SourceCapabilityStatus.SUPPORTED,
            artifacts=SourceCapabilityStatus.LIMITED,
        )

    def candidate_from_mapping(self, values: Mapping[str, object]) -> ReferenceProjectCandidate:
        if not isinstance(values, Mapping):
            raise TypeError("GitHub result is invalid")
        url = _url(values.get("url"), field="GitHub project url")
        parsed = urlsplit(url)
        if parsed.hostname.casefold() != "github.com":
            raise ValueError("GitHub project url is invalid")
        parts = tuple(part for part in parsed.path.split("/") if part)
        if len(parts) != 2:
            raise ValueError("GitHub project identity is invalid")
        owner, repository = parts
        expected_identity = f"{owner}/{repository}"
        supplied_identity = values.get("project_identity", expected_identity)
        if supplied_identity != expected_identity:
            raise ValueError("GitHub project identity does not match URL")
        owner = values.get("owner", owner)
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("GitHub owner is invalid")
        return _candidate(
            values,
            source_type=ResearchSourceType.GITHUB,
            expected_host="github.com",
            project_identity=expected_identity,
            owner_or_author=owner,
            repository_name=str(values.get("repository", repository)),
        )

    def search(self, queries: tuple[str, ...], brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        if type(queries) is not tuple or not queries:
            return SourceResearchResponse(
                source_type=self.source_type,
                status=SourceResultStatus.PARTIAL,
                error_code="INVALID_QUERY",
            )
        results: list[ReferenceProjectCandidate] = []
        for query in queries:
            try:
                raw = self._transport.search(query)
                if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
                    raise TypeError("GitHub result is invalid")
                for item in raw:
                    try:
                        results.append(self.candidate_from_mapping(item))
                    except (TypeError, ValueError):
                        continue
            except SourceTransportFailure as error:
                return SourceResearchResponse(
                    source_type=self.source_type,
                    status=_result_status(error.code),
                    candidates=tuple(results),
                    error_code=error.code,
                )
            except Exception:  # noqa: BLE001 - injected source boundary is fail-closed
                return SourceResearchResponse(
                    source_type=self.source_type,
                    status=SourceResultStatus.UNAVAILABLE,
                    candidates=tuple(results),
                    error_code="SOURCE_UNAVAILABLE",
                )
        return SourceResearchResponse(
            source_type=self.source_type,
            status=SourceResultStatus.SUCCESS if results else SourceResultStatus.NO_RESULT,
            candidates=tuple(results),
        )

    def fetch(self, url: str, brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        try:
            raw = self._transport.fetch(url)
            candidate = self.candidate_from_mapping(raw)
        except SourceTransportFailure as error:
            return SourceResearchResponse(source_type=self.source_type, status=_result_status(error.code), error_code=error.code)
        except (TypeError, ValueError):
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="INVALID_RESULT")
        except Exception:  # noqa: BLE001 - source boundary is fail-closed
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="SOURCE_ERROR")
        return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.SUCCESS, candidates=(candidate,))


class OSHWHubResearchSource:
    source_type = ResearchSourceType.OSHWHUB

    def __init__(self, transport: ResearchTransport | None = None, *, live: bool = False) -> None:
        self._transport = transport or (HttpOSHWHubResearchTransport() if live else None)
        status = SourceCapabilityStatus.LIMITED if self._transport is not None else SourceCapabilityStatus.UNAVAILABLE
        self.capabilities = _capabilities(search=status, metadata=status, artifacts=status)

    def _candidate_from_mapping(self, values: Mapping[str, object]) -> ReferenceProjectCandidate:
        if not isinstance(values, Mapping):
            raise TypeError("OSHWHub result is invalid")
        url = _url(values.get("url"), field="OSHWHub project url")
        if urlsplit(url).hostname.casefold() != "oshwhub.com":
            raise ValueError("OSHWHub project url is invalid")
        identity = _identifier(values.get("project_identity"), field="OSHWHub project identity")
        return _candidate(
            values,
            source_type=self.source_type,
            expected_host="oshwhub.com",
            project_identity=identity,
            owner_or_author=str(values.get("author", "unknown-author")),
            repository_name=None,
        )

    def search(self, queries: tuple[str, ...], brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        if self._transport is None:
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.UNAVAILABLE, error_code="API_UNAVAILABLE")
        results: list[ReferenceProjectCandidate] = []
        try:
            for query in queries:
                raw = self._transport.search(query)
                if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
                    raise TypeError
                for item in raw:
                    try:
                        results.append(self._candidate_from_mapping(item))
                    except (TypeError, ValueError):
                        continue
        except SourceTransportFailure as error:
            return SourceResearchResponse(source_type=self.source_type, status=_result_status(error.code), candidates=tuple(results), error_code=error.code)
        except Exception:  # noqa: BLE001 - injected source boundary is fail-closed
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, candidates=tuple(results), error_code="SOURCE_ERROR")
        return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.SUCCESS if results else SourceResultStatus.NO_RESULT, candidates=tuple(results))

    def fetch(self, url: str, brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        if self._transport is None:
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.UNAVAILABLE, error_code="API_UNAVAILABLE")
        try:
            raw = self._transport.fetch(url)
            candidate = self._candidate_from_mapping(raw)
        except SourceTransportFailure as error:
            return SourceResearchResponse(source_type=self.source_type, status=_result_status(error.code), error_code=error.code)
        except (TypeError, ValueError):
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="INVALID_RESULT")
        except Exception:  # noqa: BLE001 - source boundary is fail-closed
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="SOURCE_ERROR")
        return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.SUCCESS, candidates=(candidate,))


class VendorReferenceSource:
    source_type = ResearchSourceType.VENDOR_REFERENCE
    _ALLOWED_HOSTS = frozenset({"espressif.com", "st.com", "arm.com", "ti.com", "nxp.com", "silabs.com", "freertos.org"})

    def __init__(self, transport: ResearchTransport | None = None, *, live: bool = False) -> None:
        self._transport = transport or (HttpVendorReferenceTransport() if live else None)
        status = SourceCapabilityStatus.SUPPORTED if self._transport is not None else SourceCapabilityStatus.UNAVAILABLE
        self.capabilities = _capabilities(search=status, metadata=status, artifacts=SourceCapabilityStatus.LIMITED if self._transport is not None else status)

    def _candidate_from_mapping(self, values: Mapping[str, object]) -> ReferenceProjectCandidate:
        if not isinstance(values, Mapping):
            raise TypeError("vendor reference result is invalid")
        url = _url(values.get("url"), field="vendor reference url")
        host = urlsplit(url).hostname.casefold()
        if host not in self._ALLOWED_HOSTS and not any(host.endswith("." + item) for item in self._ALLOWED_HOSTS):
            raise ValueError("vendor reference host is invalid")
        identity = _identifier(values.get("project_identity"), field="vendor project identity")
        return _candidate(
            values,
            source_type=self.source_type,
            expected_host=host,
            official_required=True,
            project_identity=identity,
            owner_or_author=str(values.get("vendor", host)),
            repository_name=None,
        )

    def search(self, queries: tuple[str, ...], brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        if self._transport is None:
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.UNAVAILABLE, error_code="INDEX_UNAVAILABLE")
        results: list[ReferenceProjectCandidate] = []
        try:
            for query in queries:
                raw = self._transport.search(query)
                if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
                    raise TypeError
                for item in raw:
                    try:
                        results.append(self._candidate_from_mapping(item))
                    except (TypeError, ValueError):
                        continue
        except SourceTransportFailure as error:
            return SourceResearchResponse(source_type=self.source_type, status=_result_status(error.code), candidates=tuple(results), error_code=error.code)
        except Exception:  # noqa: BLE001 - injected source boundary is fail-closed
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, candidates=tuple(results), error_code="SOURCE_ERROR")
        return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.SUCCESS if results else SourceResultStatus.NO_RESULT, candidates=tuple(results))

    def fetch(self, url: str, brief: InitialRequirementBrief) -> SourceResearchResponse:
        del brief
        if self._transport is None:
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.UNAVAILABLE, error_code="INDEX_UNAVAILABLE")
        try:
            raw = self._transport.fetch(url)
            candidate = self._candidate_from_mapping(raw)
        except SourceTransportFailure as error:
            return SourceResearchResponse(source_type=self.source_type, status=_result_status(error.code), error_code=error.code)
        except (TypeError, ValueError):
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="INVALID_RESULT")
        except Exception:  # noqa: BLE001 - source boundary is fail-closed
            return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.ERROR, error_code="SOURCE_ERROR")
        return SourceResearchResponse(source_type=self.source_type, status=SourceResultStatus.SUCCESS, candidates=(candidate,))


class HttpGitHubResearchTransport:
    """Minimal public GitHub repository search transport; no token or repo content is stored."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        if type(timeout_seconds) not in {int, float} or not 0.5 <= timeout_seconds <= 30:
            raise ValueError("GitHub timeout is invalid")
        self._timeout_seconds = float(timeout_seconds)

    def search(self, query: str) -> object:
        if not isinstance(query, str) or not query.strip():
            raise SourceTransportFailure("INVALID_QUERY")
        url = "https://api.github.com/search/repositories?q=" + quote(query.strip(), safe="") + "&per_page=10"
        request = Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Embrix-EngineeringResearch"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read(1_048_577)
            decoded = json.loads(payload.decode("utf-8"))
            items = decoded.get("items") if isinstance(decoded, Mapping) else None
            if not isinstance(items, list):
                raise SourceTransportFailure("INVALID_RESULT")
            return [
                {
                    "project_identity": item.get("full_name"),
                    "url": item.get("html_url"),
                    "owner": (item.get("owner") or {}).get("login"),
                    "repository": item.get("name"),
                    "title": item.get("name"),
                    "description": item.get("description") or item.get("full_name"),
                    "license": ((item.get("license") or {}).get("spdx_id") if isinstance(item.get("license"), Mapping) else None),
                    "artifact_hints": {},
                }
                for item in items
                if isinstance(item, Mapping)
            ]
        except HTTPError as error:
            if error.code == 429:
                raise SourceTransportFailure("RATE_LIMITED") from None
            if error.code in {401, 403}:
                raise SourceTransportFailure("AUTH_REQUIRED") from None
            raise SourceTransportFailure("SOURCE_UNAVAILABLE") from None
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SourceTransportFailure("SOURCE_UNAVAILABLE") from None

    def fetch(self, url: str) -> object:
        checked = _url(url, field="GitHub reference url")
        parsed = urlsplit(checked)
        parts = tuple(part for part in parsed.path.split("/") if part)
        if parsed.hostname != "github.com" or len(parts) != 2:
            raise SourceTransportFailure("INVALID_URL")
        api_url = f"https://api.github.com/repos/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}"
        payload = _http_get(
            api_url,
            timeout_seconds=self._timeout_seconds,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Embrix-EngineeringResearch"},
        )
        try:
            item = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourceTransportFailure("ERROR") from None
        if not isinstance(item, Mapping):
            raise SourceTransportFailure("ERROR")
        license_value = item.get("license")
        license_name = license_value.get("spdx_id") if isinstance(license_value, Mapping) else None
        return {
            "project_identity": item.get("full_name") or f"{parts[0]}/{parts[1]}",
            "url": item.get("html_url") or checked,
            "owner": (item.get("owner") or {}).get("login", parts[0]),
            "repository": item.get("name") or parts[1],
            "title": item.get("name") or parts[1],
            "description": item.get("description") or item.get("full_name") or checked,
            "revision": item.get("default_branch"),
            "license": license_name,
            "artifact_hints": {},
        }


class HttpOSHWHubResearchTransport:
    """Public OSHWHub web transport; it only returns first-party project pages."""

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        if type(timeout_seconds) not in {int, float} or not 0.5 <= timeout_seconds <= 30:
            raise ValueError("OSHWHub timeout is invalid")
        self._timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _project_url(value: str) -> str | None:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname != "oshwhub.com":
            return None
        path = "/" + "/".join(part for part in parsed.path.split("/") if part)
        parts = tuple(part for part in path.split("/") if part)
        if len(parts) < 2 or parts[0].casefold() in {"search", "activities", "explore", "user", "users"}:
            return None
        return urlunsplit(("https", "oshwhub.com", path, "", ""))

    def _mapping(self, url: str, *, title: str, description: str) -> dict[str, object]:
        parsed = urlsplit(url)
        identity = "/".join(part for part in parsed.path.split("/") if part)
        return {
            "project_identity": identity,
            "url": url,
            "author": identity.split("/", 1)[0],
            "title": title,
            "description": description,
            "artifact_hints": {},
        }

    def search(self, query: str) -> object:
        if not isinstance(query, str) or not query.strip():
            raise SourceTransportFailure("INVALID_QUERY")
        url = "https://oshwhub.com/search?keyword=" + quote(query.strip(), safe="")
        payload = _http_get(
            url,
            timeout_seconds=self._timeout_seconds,
            headers={"Accept": "text/html", "User-Agent": "Embrix-EngineeringResearch"},
        )
        title, description, parser = _html_metadata(payload, url=url)
        results: list[dict[str, object]] = []
        seen: set[str] = set()
        for href, link_title in parser.links:
            absolute = href if href.startswith("https://") else f"https://oshwhub.com{href if href.startswith('/') else '/' + href}"
            project_url = self._project_url(absolute)
            if project_url is None or project_url in seen or "/search" in project_url:
                continue
            seen.add(project_url)
            results.append(self._mapping(project_url, title=_text(unescape(link_title) or title, field="project title", maximum=256), description=description))
            if len(results) >= 10:
                break
        return results

    def fetch(self, url: str) -> object:
        checked = _url(url, field="OSHWHub reference url")
        if self._project_url(checked) != checked:
            raise SourceTransportFailure("INVALID_URL")
        payload = _http_get(
            checked,
            timeout_seconds=self._timeout_seconds,
            headers={"Accept": "text/html", "User-Agent": "Embrix-EngineeringResearch"},
        )
        title, description, _ = _html_metadata(payload, url=checked)
        return self._mapping(checked, title=title, description=description)


class HttpVendorReferenceTransport:
    """Allow-listed vendor documentation transport with a small deterministic registry."""

    _ALLOWED_HOSTS = frozenset({"espressif.com", "st.com", "arm.com", "ti.com", "nxp.com", "silabs.com", "freertos.org"})
    _REGISTRY = (
        ("ESP32", "https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/"),
        ("ESP32-S3", "https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/"),
        ("STM32", "https://www.st.com/en/microcontrollers-microprocessors/stm32-32-bit-arm-cortex-mcus.html"),
        ("ARM", "https://developer.arm.com/documentation"),
        ("FREERTOS", "https://www.freertos.org/Documentation/"),
    )

    def __init__(self, *, timeout_seconds: float = 8.0) -> None:
        if type(timeout_seconds) not in {int, float} or not 0.5 <= timeout_seconds <= 30:
            raise ValueError("vendor timeout is invalid")
        self._timeout_seconds = float(timeout_seconds)

    @classmethod
    def _allowed(cls, url: str) -> bool:
        host = urlsplit(url).hostname
        return bool(host and (host in cls._ALLOWED_HOSTS or any(host.endswith("." + item) for item in cls._ALLOWED_HOSTS)))

    def search(self, query: str) -> object:
        if not isinstance(query, str) or not query.strip():
            raise SourceTransportFailure("INVALID_QUERY")
        lowered = query.casefold()
        urls = tuple(url for marker, url in self._REGISTRY if marker.casefold() in lowered)
        if not urls:
            return []
        results: list[dict[str, object]] = []
        for url in urls:
            try:
                results.append(self.fetch(url))
            except SourceTransportFailure:
                if not results:
                    raise
        return results

    def fetch(self, url: str) -> object:
        checked = _url(url, field="vendor reference url")
        if not self._allowed(checked):
            raise SourceTransportFailure("INVALID_URL")
        payload = _http_get(
            checked,
            timeout_seconds=self._timeout_seconds,
            headers={"Accept": "text/html", "User-Agent": "Embrix-EngineeringResearch"},
        )
        title, description, _ = _html_metadata(payload, url=checked)
        parsed = urlsplit(checked)
        identity = f"{parsed.hostname}:{parsed.path.strip('/') or 'root'}"
        return {
            "project_identity": identity,
            "url": checked,
            "vendor": parsed.hostname,
            "title": title,
            "description": description,
            "official": True,
            "artifact_hints": {},
        }


__all__ = (
    "GitHubResearchSource",
    "HttpGitHubResearchTransport",
    "HttpOSHWHubResearchTransport",
    "HttpVendorReferenceTransport",
    "OSHWHubResearchSource",
    "ResearchSourcePort",
    "ResearchTransport",
    "SourceTransportFailure",
    "UnavailableResearchSource",
    "VendorReferenceSource",
)
