from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .audit import MultimodalApplicationAuditEvent
from .models import (
    EngineeringArtifact,
    EngineeringObservation,
    MultimodalModelReference,
    ResolvedMediaReference,
    ResolvedPDFPage,
)


@runtime_checkable
class MultimodalModelPort(Protocol):
    @property
    def model_reference(self) -> MultimodalModelReference: ...

    def analyze(self, artifact: EngineeringArtifact) -> EngineeringObservation: ...

    async def aanalyze(
        self, artifact: EngineeringArtifact
    ) -> EngineeringObservation: ...


@runtime_checkable
class MultimodalMediaResolverPort(Protocol):
    def resolve(self, artifact: EngineeringArtifact) -> ResolvedMediaReference: ...


@runtime_checkable
class PDFPageExtractorPort(Protocol):
    def extract_pages(
        self, artifact: EngineeringArtifact
    ) -> tuple[ResolvedPDFPage, ...]: ...


@runtime_checkable
class MultimodalApplicationAuditSinkPort(Protocol):
    def append(
        self, event: MultimodalApplicationAuditEvent
    ) -> MultimodalApplicationAuditEvent: ...

    def get(self, event_id: str) -> MultimodalApplicationAuditEvent | None: ...


@runtime_checkable
class MultimodalClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
