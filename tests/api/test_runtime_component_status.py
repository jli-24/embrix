from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.rag.embedding import EmbeddingServiceUnavailable
from embedded_copilot.services.config import Settings


class _ChatService:
    async def chat(self, request):
        raise AssertionError("chat should not be called")


def test_missing_project_root_is_fail_closed_and_reports_safe_status(monkeypatch, caplog, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "EMBEDDED_COPILOT_PROJECT_ROOT",
        "CHAT_MODEL_PROVIDER",
        "MODEL_PROVIDER",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    app = create_app(service=_ChatService(), settings=Settings(_env_file=None))
    with caplog.at_level(logging.INFO, logger="embedded_copilot.api.main"):
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/copilot/projects",
                headers={
                    "X-Copilot-Requester": "desktop-user",
                    "X-Environment-Tenant": "desktop-tenant",
                },
            )

    assert response.status_code == 503
    assert response.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"
    assert app.state.runtime_component_statuses == {
        "project_catalog": "unavailable",
        "model_gateway": "unavailable",
        "context_provider": "unavailable",
        "memory_provider": "unavailable",
    }
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "runtime_component_status"
    )
    assert record.project_catalog == "unavailable"
    assert record.model_gateway == "unavailable"
    assert record.context_provider == "unavailable"
    assert record.memory_provider == "unavailable"
    assert not any(
        forbidden in record.getMessage().casefold()
        for forbidden in ("api_key", "secret", "token", "path")
    )


def test_explicit_external_project_root_starts_catalog(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "CHAT_MODEL_PROVIDER",
        "MODEL_PROVIDER",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    state_database = tmp_path / "state" / "desktop.sqlite3"
    metadata_directory = tmp_path / "metadata"
    settings = Settings(
        _env_file=None,
        project_root=project_root,
        desktop_state_database=state_database,
        desktop_project_metadata_directory=metadata_directory,
    )

    app = create_app(service=_ChatService(), settings=settings)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/copilot/projects",
            headers={
                "X-Copilot-Requester": "desktop-user",
                "X-Environment-Tenant": "desktop-tenant",
            },
        )

    assert response.status_code == 200
    assert app.state.runtime_component_statuses["project_catalog"] == "available"
    assert app.state.runtime_component_statuses["model_gateway"] == "unavailable"
    assert state_database.exists()
    assert not (project_root / ".embedded-copilot").exists()


def test_v63_store_initialization_failure_is_unavailable_without_fallback(monkeypatch, tmp_path) -> None:
    class _FailingV63Store:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("v63 store unavailable")

    monkeypatch.setattr("embedded_copilot.api.main.SQLiteV63Store", _FailingV63Store)
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    settings = Settings(
        _env_file=None,
        project_root=project_root,
        desktop_state_database=tmp_path / "state" / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
    )

    app = create_app(service=_ChatService(), settings=settings)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/copilot/capabilities",
            headers={
                "X-Copilot-Requester": "desktop-user",
                "X-Environment-Tenant": "desktop-tenant",
            },
        )

    assert response.status_code == 200
    assert app.state.v63_memory_governance is None
    values = {item["capability"]: item for item in response.json()}
    for capability in ("ENGINEERING_MEMORY", "KNOWLEDGE_GOVERNANCE", "KNOWLEDGE_EVOLUTION"):
        assert values[capability]["status"] == "UNAVAILABLE"
        assert values[capability]["reason_code"] == "V63_STORE_UNAVAILABLE"


def test_unavailable_ollama_does_not_block_default_composition(monkeypatch, tmp_path) -> None:
    class _UnavailableOllama:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def probe(self):
            raise EmbeddingServiceUnavailable("Ollama service is unavailable")

    monkeypatch.setattr("embedded_copilot.api.main.OllamaEmbeddingAdapter", _UnavailableOllama)
    monkeypatch.setattr(
        "embedded_copilot.services.canonical_runtime.OllamaEmbeddingAdapter",
        _UnavailableOllama,
    )
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    settings = Settings(
        _env_file=None,
        knowledge_dir=knowledge_dir,
        chroma_path=tmp_path / "chroma",
        embedding_provider="ollama",
        embedding_model="embeddinggemma",
        desktop_state_database=tmp_path / "state" / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
    )

    app = create_app(settings=settings)

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        capabilities = client.get(
            "/api/v1/copilot/capabilities",
            headers={
                "X-Copilot-Requester": "desktop-user",
                "X-Environment-Tenant": "desktop-tenant",
            },
        )

    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert capabilities.status_code == 200
    values = {item["capability"]: item for item in capabilities.json()}
    assert values["KNOWLEDGE_EMBEDDING"]["status"] == "UNAVAILABLE"
    assert values["KNOWLEDGE_EMBEDDING"]["provider_configured"] == "ollama"
    assert values["KNOWLEDGE_EMBEDDING"]["runtime_status"] == "unavailable"
    assert values["KNOWLEDGE_EMBEDDING"]["model_status"] == "unavailable"
    assert app.state.copilot_knowledge_search_port is None


def test_canonical_runtime_unavailable_keeps_health_endpoint_ready(
    monkeypatch, tmp_path
) -> None:
    def unavailable_runtime(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("canonical runtime unavailable")

    monkeypatch.setattr("embedded_copilot.api.main.build_canonical_runtime", unavailable_runtime)
    settings = Settings(
        _env_file=None,
        knowledge_dir=tmp_path / "knowledge",
        chroma_path=tmp_path / "chroma",
        desktop_state_database=tmp_path / "state" / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
    )

    app = create_app(settings=settings)

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
