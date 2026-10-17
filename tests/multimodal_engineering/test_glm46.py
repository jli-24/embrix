from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from embedded_copilot.multimodal_engineering import (
    EngineeringArtifactType,
    FakeMultimodalMediaResolver,
    FakePDFPageExtractor,
    GLM46VisionAdapter,
    MultimodalProviderAuthenticationError,
    MultimodalProviderMalformedResponse,
    MultimodalProviderRateLimited,
    MultimodalProviderTimeout,
    MultimodalProviderUnavailable,
)

from .fixtures import artifact


def _provider_content(artifact_type=EngineeringArtifactType.PCB_IMAGE) -> str:
    if artifact_type is EngineeringArtifactType.PDF:
        content = {
            "content_type": "DOCUMENT",
            "title": "Reference manual",
            "sections": ["GPIO"],
            "tables": ["Pin matrix"],
            "limitations": ["Revision must be checked"],
        }
        observation_type = "DOCUMENT"
        evidence = [
            {
                "locator_type": "PDF_PAGE",
                "page_number": 1,
                "table_reference": None,
                "region": None,
                "label": "GPIO page",
            }
        ]
    else:
        content = {
            "content_type": "PCB",
            "components": ["ESP32", "USB-UART", "LDO"],
            "interfaces": ["UART", "SPI"],
            "visible_labels": ["U1"],
            "issues": ["visual review required"],
        }
        observation_type = "PCB"
        evidence = [
            {
                "locator_type": "IMAGE_REGION",
                "page_number": None,
                "table_reference": None,
                "region": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.8, "y_max": 0.8},
                "label": "board region",
            }
        ]
    return json.dumps(
        {
            "observation_type": observation_type,
            "structured_content": content,
            "confidence": 0.8,
            "evidence": evidence,
        }
    )


def _response(content: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": _provider_content() if content is None else content
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )


def test_glm_image_payload_and_strict_response_mapping() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers["Authorization"]
        return _response()

    adapter = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(),
        http_transport=httpx.MockTransport(handler),
    )
    result = adapter.analyze(artifact())
    assert result.structured_content.content_type == "PCB"
    assert seen["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    body = seen["body"]
    assert body["model"] == "glm-4.6v-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is False
    assert body["messages"][0]["content"][0]["type"] == "image_url"
    assert "test-secret" not in json.dumps(body)
    assert seen["authorization"] == "Bearer test-secret"


def test_glm_pdf_uses_ordered_page_images() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _response(_provider_content(EngineeringArtifactType.PDF))

    adapter = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(page_count=2),
        http_transport=httpx.MockTransport(handler),
    )
    result = adapter.analyze(artifact(EngineeringArtifactType.PDF))
    media = seen["body"]["messages"][0]["content"][:-1]
    assert len(media) == 2
    assert all(item["type"] == "image_url" for item in media)
    assert result.evidence_refs[0].page_number == 1


@pytest.mark.parametrize(
    ("status", "error"),
    (
        (401, MultimodalProviderAuthenticationError),
        (403, MultimodalProviderAuthenticationError),
        (429, MultimodalProviderRateLimited),
        (500, MultimodalProviderUnavailable),
    ),
)
def test_glm_maps_http_failures_without_secret(status, error) -> None:
    adapter = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(),
        http_transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    )
    with pytest.raises(error) as raised:
        adapter.analyze(artifact())
    assert "test-secret" not in str(raised.value)


def test_glm_maps_timeout_and_malformed_response() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private detail", request=request)

    common = {
        "api_key": "test-secret",
        "media_resolver": FakeMultimodalMediaResolver(),
        "page_extractor": FakePDFPageExtractor(),
    }
    with pytest.raises(MultimodalProviderTimeout):
        GLM46VisionAdapter(
            **common, http_transport=httpx.MockTransport(timeout)
        ).analyze(artifact())
    with pytest.raises(MultimodalProviderMalformedResponse):
        GLM46VisionAdapter(
            **common,
            http_transport=httpx.MockTransport(lambda request: _response("not-json")),
        ).analyze(artifact())


def test_glm_rejects_unknown_model_and_non_exact_provider_schema() -> None:
    common = {
        "api_key": "test-secret",
        "media_resolver": FakeMultimodalMediaResolver(),
        "page_extractor": FakePDFPageExtractor(),
    }
    with pytest.raises(ValueError):
        GLM46VisionAdapter(**common, model_name="unapproved-vision")

    provider_object = json.loads(_provider_content())
    provider_object["provider_identity"] = "must-not-be-trusted"
    adapter = GLM46VisionAdapter(
        **common,
        http_transport=httpx.MockTransport(
            lambda request: _response(json.dumps(provider_object))
        ),
    )
    with pytest.raises(MultimodalProviderMalformedResponse):
        adapter.analyze(artifact())


def test_glm_rejects_evidence_page_not_supplied_to_provider() -> None:
    provider_object = json.loads(_provider_content(EngineeringArtifactType.PDF))
    provider_object["evidence"][0]["page_number"] = 2
    adapter = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(page_count=1),
        http_transport=httpx.MockTransport(
            lambda request: _response(json.dumps(provider_object))
        ),
    )
    with pytest.raises(MultimodalProviderMalformedResponse):
        adapter.analyze(artifact(EngineeringArtifactType.PDF))


def test_glm_async_and_cancellation() -> None:
    transport = httpx.MockTransport(lambda request: _response())
    adapter = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(),
        async_transport=transport,
    )
    assert asyncio.run(adapter.aanalyze(artifact())).observation_type.value == "PCB"

    class _Cancelled(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise asyncio.CancelledError

    cancelled = GLM46VisionAdapter(
        api_key="test-secret",
        media_resolver=FakeMultimodalMediaResolver(),
        page_extractor=FakePDFPageExtractor(),
        async_transport=_Cancelled(),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled.aanalyze(artifact()))
