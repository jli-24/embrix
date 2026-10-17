from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from pydantic import ConfigDict, Field, field_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    identifier,
    safe_text,
    tuple_only,
)


class CopilotKnowledgeSource(StrEnum):
    LOCAL = "LOCAL"
    GITHUB = "GITHUB"
    WEB = "WEB"


class CopilotKnowledgeSearchRequest(FingerprintedChatContract):
    """Project-bound, read-only query contract for the Desktop Knowledge surface."""

    project_id: str
    query: str
    sources: tuple[CopilotKnowledgeSource, ...] = ()
    top_k: int = Field(default=4, ge=1, le=20)

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("query", mode="before")
    @classmethod
    def _query(cls, value: object) -> str:
        return safe_text(value, field="query", maximum=2048)

    @field_validator("sources", mode="before")
    @classmethod
    def _sources(cls, value: object) -> tuple[CopilotKnowledgeSource, ...]:
        if value is None:
            return ()
        if isinstance(value, list):
            normalized: list[CopilotKnowledgeSource] = []
            for item in value:
                if isinstance(item, str):
                    item = CopilotKnowledgeSource(item.strip().upper())
                normalized.append(item)
            value = tuple(normalized)
        return tuple_only(value, field="sources")

    @field_validator("sources")
    @classmethod
    def _deduplicate_sources(
        cls, value: tuple[CopilotKnowledgeSource, ...]
    ) -> tuple[CopilotKnowledgeSource, ...]:
        return tuple(dict.fromkeys(value))


class CopilotKnowledgeImportRequest(FingerprintedChatContract):
    project_id: str
    document_id: str
    source_reference: str

    @field_validator("project_id", "document_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def _source_reference(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("source_reference is invalid")
        checked = value.replace("\\", "/").strip()
        if (
            not checked
            or checked.startswith(("/", "\\"))
            or "://" in checked
            or ":" in checked.split("/", 1)[0]
            or any(part in {"", ".", ".."} for part in checked.split("/"))
            or checked.lower().endswith((".exe", ".dll"))
        ):
            raise ValueError("source_reference is invalid")
        return safe_text(checked, field="source_reference", maximum=1024)


class CopilotKnowledgeManifestView(FingerprintedChatContract):
    document_id: str
    project_id: str
    tenant: str
    source_reference: str
    document_fingerprint: str
    content_fingerprint: str
    parser_version: str
    chunker_version: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    ingestion_status: str
    chunk_count: int
    ingestion_revision: str
    embedding_identity: str = ""
    error_code: str | None = None
    deleted: bool = False

    @field_validator("document_id", "project_id", "tenant", mode="before")
    @classmethod
    def _manifest_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)


class CopilotKnowledgeSearchResult(FingerprintedChatContract):
    """Boundary DTO used to revalidate an injected provider result."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        from_attributes=True,
        revalidate_instances="always",
    )

    id: str
    title: str
    content: str
    source: CopilotKnowledgeSource
    score: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("id", "title", "content", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=4096)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> CopilotKnowledgeSource:
        if isinstance(value, str):
            return CopilotKnowledgeSource(value.strip().upper())
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError("metadata must be a mapping")
        return dict(value)


class CopilotKnowledgeCitation(FingerprintedChatContract):
    """Safe citation projection; document content never crosses this boundary."""

    citation_id: str
    project_id: str
    title: str
    source: CopilotKnowledgeSource
    score: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("citation_id", "project_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return safe_text(value, field="title", maximum=512)


__all__ = (
    "CopilotKnowledgeCitation",
    "CopilotKnowledgeImportRequest",
    "CopilotKnowledgeManifestView",
    "CopilotKnowledgeSearchRequest",
    "CopilotKnowledgeSearchResult",
    "CopilotKnowledgeSource",
)
