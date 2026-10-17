from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity

from .copilot_knowledge_models import (
    CopilotKnowledgeCitation,
    CopilotKnowledgeImportRequest,
    CopilotKnowledgeManifestView,
    CopilotKnowledgeSearchRequest,
    CopilotKnowledgeSearchResult,
    CopilotKnowledgeSource,
)


class KnowledgeSearchPort(Protocol):
    def search(
        self,
        *,
        tenant_id: str,
        project_id: str,
        query: str,
        sources: tuple[CopilotKnowledgeSource, ...],
        top_k: int,
    ) -> Any:
        """Search only within the already authorized tenant/project scope."""


router = APIRouter(prefix="/api/v1/copilot/knowledge", tags=["copilot-knowledge"])

_SAFE_METADATA_KEYS = frozenset(
    {"chip", "manufacturer", "category", "chapter", "page", "document_type"}
)


def _error(request: Request, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    projected: dict[str, object] = {}
    for key in _SAFE_METADATA_KEYS | {
        "document_id", "document_fingerprint", "chunk_id", "chunk_fingerprint",
        "source_reference", "ingestion_revision", "section",
        "embedding_identity",
    }:
        value = metadata.get(key)
        if value is None:
            continue
        if key == "page":
            if type(value) is int and value >= 1:
                projected[key] = value
            continue
        if isinstance(value, str) and value.strip():
            projected[key] = value.strip()
    return {key: projected[key] for key in sorted(projected)}


def _embedding_unavailable(request: Request) -> bool:
    statuses = getattr(request.app.state, "runtime_capability_statuses", {})
    return (
        statuses.get("embedding_provider_configured") == "ollama"
        and statuses.get("embedding_provider") != "available"
    )


def _manifest_view(value: object, *, project_id: str, tenant: str) -> CopilotKnowledgeManifestView:
    material = dict(value.__dict__) if hasattr(value, "__dict__") else {
        field: getattr(value, field) for field in (
            "document_id", "project_id", "tenant", "source_reference", "document_fingerprint",
            "content_fingerprint", "parser_version", "chunker_version", "embedding_provider",
            "embedding_model", "embedding_dimension", "ingestion_status", "chunk_count",
            "ingestion_revision", "embedding_identity", "error_code", "deleted",
        )
    }
    if material.get("project_id") != project_id or material.get("tenant") != tenant:
        raise ValueError("knowledge manifest identity mismatch")
    return CopilotKnowledgeManifestView.model_validate(material)


def _validated_results(value: object) -> tuple[CopilotKnowledgeSearchResult, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("knowledge search returned an invalid result collection")
    results: list[CopilotKnowledgeSearchResult] = []
    seen: set[str] = set()
    for item in value:
        result = CopilotKnowledgeSearchResult.model_validate(item, strict=True)
        if result.id in seen:
            raise ValueError("knowledge search returned duplicate citations")
        seen.add(result.id)
        results.append(result)
    return tuple(results)


@router.post("/search", response_model=tuple[CopilotKnowledgeCitation, ...])
async def search_knowledge(
    request: Request,
    body: CopilotKnowledgeSearchRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[CopilotKnowledgeCitation, ...] | JSONResponse:
    port = getattr(request.app.state, "copilot_knowledge_search_port", None)
    if _embedding_unavailable(request) or port is None or not callable(getattr(port, "search", None)):
        return _error(request, 503, "KNOWLEDGE_SEARCH_UNAVAILABLE")

    try:
        raw = port.search(
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            query=body.query,
            sources=body.sources,
            top_k=body.top_k,
        )
        if inspect.isawaitable(raw):
            raw = await raw
        results = _validated_results(raw)
        citations: list[CopilotKnowledgeCitation] = []
        for result in results[: body.top_k]:
            metadata = result.metadata
            bound_project = metadata.get("project_id")
            if bound_project is not None and bound_project != body.project_id:
                raise ValueError("knowledge result is bound to another project")
            citations.append(
                CopilotKnowledgeCitation(
                    citation_id=result.id,
                    project_id=body.project_id,
                    title=result.title,
                    source=CopilotKnowledgeSource(result.source),
                    score=result.score,
                    metadata=_safe_metadata(metadata),
                )
            )
        return tuple(citations)
    except (TypeError, ValueError) as error:
        del error
        return _error(request, 422, "KNOWLEDGE_SEARCH_REJECTED")
    except Exception as error:
        del error
        return _error(request, 503, "KNOWLEDGE_SEARCH_FAILED")


@router.post("/import", response_model=CopilotKnowledgeManifestView, status_code=201)
async def import_knowledge(
    request: Request,
    body: CopilotKnowledgeImportRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotKnowledgeManifestView | JSONResponse:
    port = getattr(request.app.state, "copilot_knowledge_search_port", None)
    if _embedding_unavailable(request) or port is None or not callable(getattr(port, "import_document", None)):
        return _error(request, 503, "KNOWLEDGE_INGESTION_UNAVAILABLE")
    try:
        raw = port.import_document(
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            requester=identity.requester,
            document_id=body.document_id,
            source_reference=body.source_reference,
        )
        records = port.status(tenant_id=identity.tenant_id, project_id=body.project_id)
        matching = next((item for item in records if item.document_id == body.document_id), None)
        if matching is None:
            raise ValueError("knowledge manifest was not persisted")
        return _manifest_view(matching, project_id=body.project_id, tenant=identity.tenant_id)
    except (TypeError, ValueError) as error:
        del error
        return _error(request, 422, "KNOWLEDGE_IMPORT_REJECTED")
    except Exception as error:
        del error
        return _error(request, 503, "KNOWLEDGE_INGESTION_UNAVAILABLE")


@router.get("/status", response_model=tuple[CopilotKnowledgeManifestView, ...])
async def knowledge_status(
    request: Request,
    project_id: str,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[CopilotKnowledgeManifestView, ...] | JSONResponse:
    port = getattr(request.app.state, "copilot_knowledge_search_port", None)
    if port is None or not callable(getattr(port, "status", None)):
        return _error(request, 503, "KNOWLEDGE_STATUS_UNAVAILABLE")
    try:
        values = port.status(tenant_id=identity.tenant_id, project_id=project_id)
        return tuple(_manifest_view(item, project_id=project_id, tenant=identity.tenant_id) for item in values)
    except (TypeError, ValueError) as error:
        del error
        return _error(request, 422, "KNOWLEDGE_STATUS_REJECTED")
    except Exception as error:
        del error
        return _error(request, 503, "KNOWLEDGE_STATUS_UNAVAILABLE")


__all__ = ("KnowledgeSearchPort", "router", "search_knowledge")
