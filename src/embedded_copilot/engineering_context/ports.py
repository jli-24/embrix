from __future__ import annotations

from typing import NamedTuple, Protocol, runtime_checkable

from .models import (
    ContextConflict,
    EngineeringContextEvidence,
    EngineeringContextQuery,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)


class GraphProjection(NamedTuple):
    evidence: tuple[EngineeringContextEvidence, ...]
    conflicts: tuple[ContextConflict, ...]


@runtime_checkable
class EngineeringContextSourcePort(Protocol):
    """Read-only source boundary for normalized context evidence."""

    def read_memory(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]: ...

    def read_graph(
        self, request: EngineeringContextRequest
    ) -> GraphProjection: ...

    def read_knowledge(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]: ...

    def read_multimodal(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]: ...


@runtime_checkable
class EngineeringContextProviderPort(Protocol):
    def get_context(
        self, request: EngineeringContextRequest | EngineeringContextQuery
    ) -> EngineeringContextSnapshot | None: ...


@runtime_checkable
class ApprovedMemoryProjectionPort(Protocol):
    def list_approved(self, project_id: str) -> tuple[object, ...]: ...


@runtime_checkable
class EngineeringGraphProjectionPort(Protocol):
    def project(self, project_id: str) -> object | None: ...


@runtime_checkable
class KnowledgeEvidenceProjectionPort(Protocol):
    def list_evidence(
        self, project_id: str, task: str, constraints: tuple[str, ...]
    ) -> tuple[object, ...]: ...


@runtime_checkable
class ExternalKnowledgeEvidenceProjectionPort(Protocol):
    def list_evidence(
        self, project_id: str, task: str, constraints: tuple[str, ...]
    ) -> tuple[object, ...]: ...


@runtime_checkable
class MultimodalProjectionPort(Protocol):
    def list_projections(self, project_id: str) -> tuple[object, ...]: ...


@runtime_checkable
class EngineeringEvidenceProjectionPort(Protocol):
    """Read-only project evidence projection for internal domain adapters."""

    def list_evidence(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]: ...


@runtime_checkable
class V63MemoryContextProjectionPort(Protocol):
    def list_memory_context(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringContextEvidence, ...]: ...


@runtime_checkable
class V63KnowledgeContextProjectionPort(Protocol):
    def list_knowledge_context(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringContextEvidence, ...]: ...


__all__ = (
    "ApprovedMemoryProjectionPort",
    "EngineeringContextProviderPort",
    "EngineeringContextSourcePort",
    "EngineeringGraphProjectionPort",
    "GraphProjection",
    "KnowledgeEvidenceProjectionPort",
    "MultimodalProjectionPort",
    "EngineeringEvidenceProjectionPort",
    "V63MemoryContextProjectionPort",
    "V63KnowledgeContextProjectionPort",
)
