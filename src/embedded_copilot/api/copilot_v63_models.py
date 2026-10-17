from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from embedded_copilot.v63_memory_governance.models import (
    Freshness,
    KnowledgeStatus,
    MemoryStatus,
    MemoryType,
)


class V63MemoryView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    memory_type: MemoryType
    subject: str
    statement: str
    status: MemoryStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    review_id: str
    freshness: Freshness
    provenance_references: tuple[str, ...]


class V63KnowledgeView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    knowledge_id: str
    subject: str
    predicate: str
    value: str
    status: KnowledgeStatus
    verification_status: str
    evidence_count: int = Field(ge=0)
    review_id: str
    provenance_references: tuple[str, ...]


class V63ProjectionEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    project_id: str
    generated_at: datetime
    items: tuple[V63MemoryView | V63KnowledgeView, ...]


__all__ = ("V63KnowledgeView", "V63MemoryView", "V63ProjectionEnvelope")
