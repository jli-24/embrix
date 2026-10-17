from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from embedded_copilot.api.copilot_review_routes import router as review_router
from embedded_copilot.api.copilot_review_models import (
    CopilotReviewCreateRequest,
    CopilotReviewTargetType,
)
from embedded_copilot.api.copilot_v63_routes import router as v63_router
from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.v63_memory_governance import SQLiteV63Store, V63MemoryGovernanceService
from embedded_copilot.v63_memory_governance.models import EvidenceIdentity, MemoryType, StructuredCandidateInput


def _app(tmp_path) -> tuple[FastAPI, V63MemoryGovernanceService]:
    application = FastAPI()
    service = V63MemoryGovernanceService(SQLiteV63Store(tmp_path / "v63.sqlite3"))
    application.state.v63_memory_governance = service
    application.state.desktop_identity_provider = type(
        "Provider", (), {
            "resolve": lambda self, request: TrustedDesktopIdentity(
                requester="engineer-1", tenant_id="tenant-1", authentication="TEST"
            )
        }
    )()

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-v63"
        return await call_next(request)

    application.include_router(review_router)
    application.include_router(v63_router)
    return application, service


def _seed_memory(service: V63MemoryGovernanceService) -> str:
    evidence = EvidenceIdentity(
        evidence_id="evidence-1", tenant_id="tenant-1", project_id="project-1",
        source_reference="src:evidence-1", source_fingerprint="sha256:" + "a" * 64,
        evidence_fingerprint="sha256:" + "b" * 64,
    )
    candidate = service.extract_memory_candidate(StructuredCandidateInput(
        tenant_id="tenant-1", requester_id="engineer-1", project_id="project-1",
        memory_type=MemoryType.DECISION, subject="rail", statement="Use 3V3.",
        evidence=(evidence,), confidence=0.9,
    ))
    review = service.request_memory_review(
        tenant_id="tenant-1", project_id="project-1",
        candidate_id=candidate.memory_candidate_id, requester_id="engineer-1",
    )
    service.decide_review(
        tenant_id="tenant-1", project_id="project-1", review_id=review.review_id,
        reviewer_id="reviewer-1", decision="APPROVE",
    )
    return candidate.memory_candidate_id


def test_v63_openapi_and_projection_routes_are_additive(tmp_path) -> None:
    application, service = _app(tmp_path)
    candidate_id = _seed_memory(service)
    with TestClient(application) as client:
        queue = client.get("/api/v1/copilot/reviews", params={"project_id": "project-1"})
        memory = client.get("/api/v1/copilot/memory", params={"project_id": "project-1"})
        governed = client.get("/api/v1/copilot/knowledge/governed", params={"project_id": "project-1"})
    assert queue.status_code == 200
    assert memory.status_code == 200
    assert memory.json()[0]["statement"] == "Use 3V3."
    assert governed.status_code == 200 and governed.json() == []
    paths = application.openapi()["paths"]
    assert "/api/v1/copilot/reviews" in paths
    assert "/api/v1/copilot/memory" in paths
    assert "/api/v1/copilot/knowledge/governed" in paths
    enum = paths["/api/v1/copilot/reviews"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert enum  # schema remains generated for old and additive DTOs
    assert candidate_id.startswith("memory-candidate-")


def test_v63_tombstone_closes_all_projections(tmp_path) -> None:
    application, service = _app(tmp_path)
    _seed_memory(service)
    service.tombstone_project("tenant-1", "project-1")
    with TestClient(application) as client:
        response = client.get("/api/v1/copilot/memory", params={"project_id": "project-1"})
    assert response.status_code == 410
    assert response.json()["error"] == "PROJECT_DELETED"


def test_old_python_review_contract_rejects_unknown_target_enum() -> None:
    assert CopilotReviewTargetType("KNOWLEDGE") is CopilotReviewTargetType.KNOWLEDGE
    try:
        CopilotReviewCreateRequest(
            project_id="project-1",
            target_type="FUTURE_TARGET",
            target_id="target-1",
            target_fingerprint="sha256:" + "a" * 64,
        )
    except ValidationError as error:
        assert "target_type" in str(error)
    else:
        raise AssertionError("strict Python client accepted an unknown target enum")
