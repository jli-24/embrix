from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import SecretStr, ValidationError

from ..contracts import deterministic_id
from ..exceptions import (
    MultimodalProviderAuthenticationError,
    MultimodalProviderMalformedResponse,
    MultimodalProviderRateLimited,
    MultimodalProviderTimeout,
    MultimodalProviderUnavailable,
)
from ..models import (
    ComponentObservationContent,
    DebugObservationContent,
    DocumentObservationContent,
    EngineeringArtifact,
    EngineeringArtifactType,
    EngineeringObservation,
    ImageObservationContent,
    MultimodalModelReference,
    ObservationType,
    PCBObservationContent,
    ResolvedMediaReference,
    ResolvedPDFPage,
    SchematicObservationContent,
    VisualBoundingBox,
    VisualEvidenceLocatorType,
    VisualEvidenceReference,
    WaveformObservationContent,
    validate_artifact,
)
from ..policies import extraction_policy
from ..ports import MultimodalMediaResolverPort, PDFPageExtractorPort

_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
_ALLOWED_MODELS = frozenset({"glm-4.6v", "glm-4.6v-flash"})
_MAX_PAGES = 32
_TOP_KEYS = frozenset(
    {"observation_type", "structured_content", "confidence", "evidence"}
)
_EVIDENCE_KEYS = frozenset(
    {"locator_type", "page_number", "table_reference", "region", "label"}
)
_CONTENT_TYPES = {
    ObservationType.DOCUMENT: DocumentObservationContent,
    ObservationType.IMAGE: ImageObservationContent,
    ObservationType.COMPONENT: ComponentObservationContent,
    ObservationType.PCB: PCBObservationContent,
    ObservationType.SCHEMATIC: SchematicObservationContent,
    ObservationType.WAVEFORM: WaveformObservationContent,
    ObservationType.DEBUG: DebugObservationContent,
}
_CONTENT_KEYS = {
    ObservationType.DOCUMENT: frozenset(
        {"content_type", "title", "sections", "tables", "limitations"}
    ),
    ObservationType.IMAGE: frozenset(
        {"content_type", "objects", "visible_text", "issues"}
    ),
    ObservationType.COMPONENT: frozenset(
        {
            "content_type",
            "component_name",
            "voltages",
            "interfaces",
            "gpios",
            "package",
            "limitations",
        }
    ),
    ObservationType.PCB: frozenset(
        {"content_type", "components", "interfaces", "visible_labels", "issues"}
    ),
    ObservationType.SCHEMATIC: frozenset(
        {"content_type", "components", "nets", "interfaces", "issues"}
    ),
    ObservationType.WAVEFORM: frozenset(
        {"content_type", "channels", "measurements", "anomalies"}
    ),
    ObservationType.DEBUG: frozenset(
        {"content_type", "error_codes", "messages", "indicators"}
    ),
}


class GLM46VisionAdapter:
    __slots__ = (
        "_api_key",
        "_async_transport",
        "_http_transport",
        "_model_reference",
        "_page_extractor",
        "_resolver",
        "_timeout",
    )

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        media_resolver: MultimodalMediaResolverPort,
        page_extractor: PDFPageExtractorPort,
        model_name: str = "glm-4.6v-flash",
        timeout_seconds: float = 30.0,
        http_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        secret = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        if not secret.get_secret_value().strip():
            raise ValueError("multimodal provider credential is missing")
        if model_name not in _ALLOWED_MODELS:
            raise ValueError("multimodal provider model is not allowed")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (float, int))
            or not 0 < float(timeout_seconds) <= 120
        ):
            raise ValueError("multimodal provider timeout is invalid")
        if not isinstance(media_resolver, MultimodalMediaResolverPort):
            raise TypeError("multimodal media resolver is invalid")
        if not isinstance(page_extractor, PDFPageExtractorPort):
            raise TypeError("PDF page extractor is invalid")
        self._api_key = secret
        self._resolver = media_resolver
        self._page_extractor = page_extractor
        self._model_reference = MultimodalModelReference(
            provider="bigmodel", model_name=model_name
        )
        self._timeout = float(timeout_seconds)
        self._http_transport = http_transport
        self._async_transport = async_transport

    @property
    def model_reference(self) -> MultimodalModelReference:
        return MultimodalModelReference.model_validate(
            self._model_reference.model_dump(mode="python")
        )

    @staticmethod
    def _binding(artifact: EngineeringArtifact, value) -> None:
        if (
            value.project_id != artifact.project_id
            or value.artifact_id != artifact.artifact_id
            or value.artifact_fingerprint != artifact.fingerprint
        ):
            raise MultimodalProviderMalformedResponse()

    def _media(
        self, artifact: EngineeringArtifact
    ) -> tuple[tuple[dict[str, object], ...], frozenset[int]]:
        try:
            if artifact.artifact_type in {
                EngineeringArtifactType.PDF,
                EngineeringArtifactType.DATASHEET,
            }:
                raw_pages = self._page_extractor.extract_pages(artifact)
                if (
                    type(raw_pages) is not tuple
                    or not 1 <= len(raw_pages) <= _MAX_PAGES
                ):
                    raise ValueError("PDF pages are invalid")
                pages = tuple(
                    ResolvedPDFPage.model_validate(item.model_dump(mode="python"))
                    for item in raw_pages
                )
                numbers = tuple(item.page_number for item in pages)
                if numbers != tuple(sorted(numbers)) or len(numbers) != len(
                    set(numbers)
                ):
                    raise ValueError("PDF pages are not ordered and unique")
                for page in pages:
                    self._binding(artifact, page)
                return (
                    tuple(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": page.location.get_secret_value(),
                            },
                        }
                        for page in pages
                    ),
                    frozenset(numbers),
                )
            raw_media = self._resolver.resolve(artifact)
            media = ResolvedMediaReference.model_validate(
                raw_media.model_dump(mode="python")
            )
            self._binding(artifact, media)
            return (
                (
                    {
                        "type": "image_url",
                        "image_url": {"url": media.location.get_secret_value()},
                    },
                ),
                frozenset(),
            )
        except MultimodalProviderMalformedResponse:
            raise
        except Exception as error:
            raise MultimodalProviderUnavailable() from error

    def _payload(
        self, artifact: EngineeringArtifact
    ) -> tuple[dict[str, object], frozenset[int]]:
        media, page_numbers = self._media(artifact)
        content = (
            *media,
            {"type": "text", "text": extraction_policy(artifact.artifact_type)},
        )
        return (
            {
                "model": self._model_reference.model_name,
                "messages": ({"role": "user", "content": content},),
                "thinking": {"type": "disabled"},
                "stream": False,
            },
            page_numbers,
        )

    @staticmethod
    def _http_error(status_code: int) -> Exception:
        if status_code in {401, 403}:
            return MultimodalProviderAuthenticationError()
        if status_code == 429:
            return MultimodalProviderRateLimited()
        return MultimodalProviderUnavailable()

    @staticmethod
    def _response_content(response: httpx.Response) -> str:
        if response.status_code < 200 or response.status_code >= 300:
            raise GLM46VisionAdapter._http_error(response.status_code)
        try:
            payload = response.json()
            choices = payload["choices"]
            if type(choices) is not list or len(choices) != 1:
                raise ValueError("choices are invalid")
            message = choices[0]["message"]
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("content is invalid")
            return content
        except MultimodalProviderUnavailable:
            raise
        except Exception as error:
            raise MultimodalProviderMalformedResponse() from error

    @staticmethod
    def _tuples(value: object) -> object:
        if type(value) is list:
            return tuple(GLM46VisionAdapter._tuples(item) for item in value)
        if type(value) is dict:
            return {
                key: GLM46VisionAdapter._tuples(item) for key, item in value.items()
            }
        return value

    def _parse_evidence(
        self,
        *,
        artifact: EngineeringArtifact,
        raw: object,
        index: int,
        allowed_pages: frozenset[int],
    ) -> VisualEvidenceReference:
        if type(raw) is not dict or frozenset(raw) != _EVIDENCE_KEYS:
            raise MultimodalProviderMalformedResponse()
        try:
            locator = VisualEvidenceLocatorType(raw["locator_type"])
            region_raw = raw["region"]
            region = None
            if region_raw is not None:
                if type(region_raw) is not dict or frozenset(region_raw) != {
                    "x_min",
                    "y_min",
                    "x_max",
                    "y_max",
                }:
                    raise ValueError("region is invalid")
                region = VisualBoundingBox.model_validate(region_raw)
            page_number = raw["page_number"]
            if (
                locator
                in {
                    VisualEvidenceLocatorType.PDF_PAGE,
                    VisualEvidenceLocatorType.DATASHEET_TABLE,
                }
                and page_number not in allowed_pages
            ):
                raise ValueError("evidence page was not supplied")
            return VisualEvidenceReference(
                evidence_id=deterministic_id(
                    "visual-evidence", artifact.fingerprint, str(index)
                ),
                project_id=artifact.project_id,
                artifact_id=artifact.artifact_id,
                artifact_fingerprint=artifact.fingerprint,
                locator_type=locator,
                page_number=page_number,
                table_reference=raw["table_reference"],
                region=region,
                label=raw["label"],
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise MultimodalProviderMalformedResponse() from error

    def _parse(
        self,
        artifact: EngineeringArtifact,
        content: str,
        allowed_pages: frozenset[int],
    ) -> EngineeringObservation:
        try:
            raw = json.loads(content)
            if type(raw) is not dict or frozenset(raw) != _TOP_KEYS:
                raise ValueError("provider object keys are invalid")
            observation_type = ObservationType(raw["observation_type"])
            expected = ObservationType.for_artifact(artifact.artifact_type)
            if observation_type is not expected:
                raise ValueError("provider observation type is invalid")
            content_raw = raw["structured_content"]
            if (
                type(content_raw) is not dict
                or frozenset(content_raw) != _CONTENT_KEYS[expected]
            ):
                raise ValueError("provider content keys are invalid")
            structured = _CONTENT_TYPES[expected].model_validate(
                self._tuples(content_raw)
            )
            evidence_raw = raw["evidence"]
            if type(evidence_raw) is not list or not evidence_raw:
                raise ValueError("provider evidence is missing")
            evidence = tuple(
                self._parse_evidence(
                    artifact=artifact,
                    raw=item,
                    index=index,
                    allowed_pages=allowed_pages,
                )
                for index, item in enumerate(evidence_raw)
            )
            return EngineeringObservation.create(
                artifact_reference=artifact,
                observation_type=observation_type,
                structured_content=structured,
                confidence=raw["confidence"],
                evidence_refs=evidence,
                model_reference=self.model_reference,
            )
        except MultimodalProviderMalformedResponse:
            raise
        except Exception as error:
            raise MultimodalProviderMalformedResponse() from error

    def analyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        checked = validate_artifact(artifact)
        payload, allowed_pages = self._payload(checked)
        try:
            with httpx.Client(
                transport=self._http_transport,
                timeout=self._timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    _ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as error:
            raise MultimodalProviderTimeout() from error
        except httpx.HTTPError as error:
            raise MultimodalProviderUnavailable() from error
        content = self._response_content(response)
        return self._parse(checked, content, allowed_pages)

    async def aanalyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        checked = validate_artifact(artifact)
        payload, allowed_pages = self._payload(checked)
        try:
            async with httpx.AsyncClient(
                transport=self._async_transport,
                timeout=self._timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    _ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise MultimodalProviderTimeout() from error
        except httpx.HTTPError as error:
            raise MultimodalProviderUnavailable() from error
        content = self._response_content(response)
        return self._parse(checked, content, allowed_pages)


__all__ = ("GLM46VisionAdapter",)
