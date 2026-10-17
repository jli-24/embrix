from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_knowledge_routes import router
from embedded_copilot.api.main import create_app
from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.knowledge.models import KnowledgeResult, KnowledgeSource
from embedded_copilot.services.config import Settings


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}


class _KnowledgeSearch:
    def __init__(self, values: tuple[KnowledgeResult, ...] = ()) -> None:
        self.values = values
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        sources: tuple[KnowledgeSource, ...],
        top_k: int,
    ) -> tuple[KnowledgeResult, ...]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "query": query,
                "sources": sources,
                "top_k": top_k,
            }
        )
        return self.values[:top_k]


def _app(port: object | None) -> FastAPI:
    application = FastAPI()
    application.state.copilot_knowledge_search_port = port

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    application.include_router(router)
    return application


def result(*, source: KnowledgeSource = KnowledgeSource.LOCAL) -> KnowledgeResult:
    return KnowledgeResult(
        id="doc-1",
        title="ESP32-S3 I2C timing",
        content="private document body must not cross the API boundary",
        source=source,
        score=0.91,
        metadata={
            "chip": "ESP32-S3",
            "chapter": "I2C",
            "page": 42,
        },
    )


def test_knowledge_search_is_project_bound_and_returns_citations_only() -> None:
    port = _KnowledgeSearch((result(),))
    with TestClient(_app(port)) as client:
        response = client.post(
            "/api/v1/copilot/knowledge/search",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "query": "I2C timing",
                "sources": ["LOCAL"],
                "top_k": 2,
            },
        )

    assert response.status_code == 200
    assert response.json()[0]["citation_id"] == "doc-1"
    assert response.json()[0]["source"] == "LOCAL"
    assert response.json()[0]["metadata"] == {
        "chip": "ESP32-S3",
        "chapter": "I2C",
        "page": 42,
    }
    assert "content" not in response.text
    assert "absolute_path" not in response.text
    assert port.calls == [
        {
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "query": "I2C timing",
            "sources": (KnowledgeSource.LOCAL,),
            "top_k": 2,
        }
    ]


def test_knowledge_search_is_unavailable_without_an_injected_port() -> None:
    with TestClient(_app(None)) as client:
        response = client.post(
            "/api/v1/copilot/knowledge/search",
            headers=HEADERS,
            json={"project_id": "project-1", "query": "I2C"},
        )

    assert response.status_code == 503
    assert response.json()["error"] == "KNOWLEDGE_SEARCH_UNAVAILABLE"


def test_knowledge_search_rejects_cross_project_result_and_bad_identity() -> None:
    mismatched = KnowledgeResult(
        id="doc-other",
        title="Other project document",
        content="not exposed",
        source=KnowledgeSource.LOCAL,
        score=0.8,
        metadata={"project_id": "project-2"},
    )
    with TestClient(_app(_KnowledgeSearch((mismatched,)))) as client:
        response = client.post(
            "/api/v1/copilot/knowledge/search",
            headers=HEADERS,
            json={"project_id": "project-1", "query": "I2C"},
        )
        missing_identity = client.post(
            "/api/v1/copilot/knowledge/search",
            headers={"X-Environment-Tenant": "tenant-1"},
            json={"project_id": "project-1", "query": "I2C"},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "KNOWLEDGE_SEARCH_REJECTED"
    assert missing_identity.status_code == 401


def test_create_app_exposes_knowledge_search_only_with_explicit_port(tmp_path) -> None:
    port = _KnowledgeSearch((result(),))
    app = create_app(
        service=object(),
        settings=Settings(_env_file=None),
        copilot_knowledge_search_port=port,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/copilot/knowledge/search",
            headers=HEADERS,
            json={"project_id": "project-1", "query": "I2C"},
        )

    assert response.status_code == 200
    assert response.json()[0]["citation_id"] == "doc-1"


def test_knowledge_search_and_import_fail_closed_when_ollama_is_unavailable() -> None:
    port = _KnowledgeSearch((result(),))
    application = _app(port)
    application.state.runtime_capability_statuses = {
        "embedding_provider": "unavailable",
        "embedding_provider_configured": "ollama",
        "embedding_runtime_status": "unavailable",
        "embedding_model_status": "unavailable",
    }

    with TestClient(application) as client:
        search = client.post(
            "/api/v1/copilot/knowledge/search",
            headers=HEADERS,
            json={"project_id": "project-1", "query": "I2C"},
        )
        imported = client.post(
            "/api/v1/copilot/knowledge/import",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "document_id": "document-1",
                "source_reference": "spi.md",
            },
        )

    assert search.status_code == 503
    assert search.json()["error"] == "KNOWLEDGE_SEARCH_UNAVAILABLE"
    assert imported.status_code == 503
    assert imported.json()["error"] == "KNOWLEDGE_INGESTION_UNAVAILABLE"
    assert port.calls == []
