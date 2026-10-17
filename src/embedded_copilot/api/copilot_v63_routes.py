from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.v63_memory_governance import V63MemoryGovernanceService, V63Error

from .copilot_v63_models import V63KnowledgeView, V63MemoryView


router = APIRouter(prefix="/api/v1/copilot", tags=["copilot-v63-projections"])


def _service(request: Request) -> V63MemoryGovernanceService | None:
    value = getattr(request.app.state, "v63_memory_governance", None)
    return value if isinstance(value, V63MemoryGovernanceService) else None


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


@router.get("/memory", response_model=tuple[V63MemoryView, ...])
def memory_projection(
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[V63MemoryView, ...] | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "MEMORY_UNAVAILABLE")
    try:
        if service.is_project_inaccessible(identity.tenant_id, project_id):
            return _error(request, 410, "PROJECT_DELETED")
        return tuple(
            V63MemoryView(
                memory_id=item.memory_id,
                memory_type=item.memory_type,
                subject=item.subject,
                statement=item.statement,
                status=item.status,
                confidence=item.confidence,
                evidence_count=item.evidence_count,
                review_id=item.review_id,
                freshness=item.freshness,
                provenance_references=item.source_references,
            )
            for item in service.project_memories(identity.tenant_id, project_id)
        )
    except V63Error:
        return _error(request, 410, "PROJECT_DELETED")


@router.get("/knowledge/governed", response_model=tuple[V63KnowledgeView, ...])
def governed_knowledge_projection(
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[V63KnowledgeView, ...] | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "KNOWLEDGE_GOVERNANCE_UNAVAILABLE")
    try:
        if service.is_project_inaccessible(identity.tenant_id, project_id):
            return _error(request, 410, "PROJECT_DELETED")
        return tuple(
            V63KnowledgeView(
                knowledge_id=item.knowledge_id,
                subject=item.subject,
                predicate=item.predicate,
                value=item.value,
                status=item.status,
                verification_status=item.verification_status,
                evidence_count=item.evidence_count,
                review_id=item.review_id,
                provenance_references=item.source_references,
            )
            for item in service.project_knowledge(identity.tenant_id, project_id)
        )
    except V63Error:
        return _error(request, 410, "PROJECT_DELETED")


__all__ = ("router",)
