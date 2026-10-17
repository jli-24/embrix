from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from types import SimpleNamespace

from embedded_copilot.api.copilot_capability_routes import router
from embedded_copilot.api.main import _record_runtime_component_status


def _app(*, authenticated: bool = True) -> FastAPI:
    application = FastAPI()
    application.state.runtime_component_statuses = {
        "project_catalog": "available",
        "model_gateway": "available",
        "provider_configuration": "available",
        "chat_model": "available",
        "engineering_analysis_completion": "available",
    }
    application.state.desktop_copilot_chat_runtime = object()
    application.state.copilot_knowledge_search_port = object()
    application.state.copilot_firmware_analysis_port = object()

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    if authenticated:
        application.state.desktop_identity_provider = type(
            "Provider",
            (),
            {
                "resolve": lambda self, request: __import__(
                    "embedded_copilot.api.desktop_identity",
                    fromlist=["TrustedDesktopIdentity"],
                ).TrustedDesktopIdentity(
                    requester="engineer-1",
                    tenant_id="tenant-1",
                    authentication="TEST",
                )
            },
        )()
    application.include_router(router)
    return application


def test_capabilities_report_read_only_boundary_and_verified_runtime_state() -> None:
    with TestClient(_app()) as client:
        response = client.get(
            "/api/v1/copilot/capabilities",
            headers={
                "X-Copilot-Requester": "spoofed",
                "X-Environment-Tenant": "spoofed",
            },
        )

    assert response.status_code == 200
    values = {item["capability"]: item for item in response.json()}
    assert values["PROJECT_CATALOG"]["status"] == "AVAILABLE"
    assert values["CHAT_ANALYSIS"]["status"] == "AVAILABLE"
    assert values["CHAT_ANALYSIS"]["runtime_status"] == "available"
    assert values["CHAT_ANALYSIS"]["model_status"] == "available"
    assert values["KNOWLEDGE_ANALYSIS"]["status"] == "UNVERIFIED"
    assert values["FIRMWARE_ANALYSIS"]["status"] == "UNVERIFIED"
    for capability in ("CONTROLLED_BUILD", "CONTROLLED_FLASH", "UART_OBSERVATION", "HIL_EXECUTION", "TARGET_IDENTITY"):
        assert values[capability]["status"] == "UNAVAILABLE"
    for capability in ("WORKSPACE_WRITE", "BUILD", "FLASH", "HIL", "EDA_APPLY", "REPAIR", "DEPLOY"):
        assert values[capability]["status"] == "UNAVAILABLE"
        assert values[capability]["reason_code"] == "READ_ONLY_DESKTOP_BOUNDARY"
    assert all("path" not in item and "command" not in item for item in response.json())


def test_capabilities_require_trusted_identity() -> None:
    with TestClient(_app(authenticated=False)) as client:
        response = client.get(
            "/api/v1/copilot/capabilities",
            headers={"X-Environment-Tenant": "tenant-1"},
        )

    assert response.status_code == 401


def test_engineering_analysis_completion_requires_canonical_runtime() -> None:
    application = FastAPI()
    projection = SimpleNamespace(
        credential_status="CONFIGURED",
        connection_status=SimpleNamespace(value="AVAILABLE"),
    )
    provider_service = SimpleNamespace(
        binding=SimpleNamespace(is_configured=True),
        projection=lambda: projection,
    )

    _record_runtime_component_status(
        application,
        project_catalog=None,
        chat_runtime=None,
        approved_memory_store=None,
        embedding_provider=None,
        knowledge_search_port=None,
        provider_service=provider_service,
    )

    assert application.state.runtime_capability_statuses["engineering_analysis_completion"] == "unavailable"


def test_capabilities_project_optional_ollama_state_without_claiming_ready() -> None:
    application = _app()
    application.state.runtime_capability_statuses = {
        "project_catalog": "available",
        "model_gateway": "available",
        "context_provider": "available",
        "embedding_provider": "unavailable",
        "embedding_reason_code": "EMBEDDING_RUNTIME_UNAVAILABLE",
        "embedding_provider_configured": "ollama",
        "embedding_runtime_status": "unavailable",
        "embedding_model_status": "unavailable",
        "knowledge_search": "unavailable",
    }
    application.state.copilot_knowledge_search_port = None

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/copilot/capabilities",
            headers={
                "X-Copilot-Requester": "desktop-user",
                "X-Environment-Tenant": "desktop-tenant",
            },
        )

    assert response.status_code == 200
    values = {item["capability"]: item for item in response.json()}
    embedding = values["KNOWLEDGE_EMBEDDING"]
    assert embedding["status"] == "UNAVAILABLE"
    assert embedding["reason_code"] == "EMBEDDING_RUNTIME_UNAVAILABLE"
    assert embedding["provider_configured"] == "ollama"
    assert embedding["runtime_status"] == "unavailable"
    assert embedding["model_status"] == "unavailable"
    assert values["KNOWLEDGE_ANALYSIS"]["status"] == "UNAVAILABLE"


def test_route_b_closure_does_not_uplift_unverified_or_unavailable_capabilities() -> None:
    application = _app()
    application.state.runtime_capability_statuses = {
        "project_catalog": "available",
        "model_gateway": "available",
        "provider_configuration": "unavailable",
        "chat_model": "unavailable",
        "engineering_analysis_completion": "unavailable",
    }
    for attribute in (
        "copilot_debug_analysis_port",
        "copilot_hardware_intelligence_port",
        "copilot_pcb_intelligence_port",
        "copilot_requirement_intelligence_port",
        "copilot_architecture_intelligence_port",
    ):
        setattr(application.state, attribute, object())

    with TestClient(application) as client:
        response = client.get("/api/v1/copilot/capabilities")

    assert response.status_code == 200
    values = {item["capability"]: item for item in response.json()}
    assert values["CHAT_MODEL"]["status"] == "UNAVAILABLE"
    assert values["CHAT_ANALYSIS"]["runtime_status"] == "available"
    assert values["CHAT_ANALYSIS"]["model_status"] == "unavailable"
    for capability in (
        "FIRMWARE_ANALYSIS",
        "DEBUG_INTELLIGENCE",
        "HARDWARE_INTELLIGENCE",
        "PCB_INTELLIGENCE",
        "REQUIREMENT_INTELLIGENCE",
        "ARCHITECTURE_INTELLIGENCE",
    ):
        assert values[capability]["status"] == "UNVERIFIED"
        assert "GOLDEN VERIFIED" not in values[capability]["detail"]

    assert values["MULTIMODAL_INTELLIGENCE"]["status"] == "UNAVAILABLE"
    for capability in (
        "WORKSPACE_WRITE",
        "BUILD",
        "FLASH",
        "HIL",
        "EDA_APPLY",
        "REPAIR",
        "DEPLOY",
    ):
        assert values[capability]["status"] == "UNAVAILABLE"
