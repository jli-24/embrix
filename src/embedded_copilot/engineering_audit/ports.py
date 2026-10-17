from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_review import ReviewRecord

from .models import (
    AuditEvent,
    AuditSourceType,
    AuditTrail,
    EngineeringAuditReport,
    EvidenceBundle,
)


@runtime_checkable
class ReviewRecordSourcePort(Protocol):
    def get_review_record(self, review_id: str) -> ReviewRecord | None: ...


@runtime_checkable
class EvidenceSourcePort(Protocol):
    def get_evidence(
        self,
        project_id: str,
        source_type: AuditSourceType,
        source_id: str,
        source_fingerprint: str,
    ) -> EvidenceBundle | None: ...


@runtime_checkable
class AuditStorePort(Protocol):
    def append(self, event: AuditEvent) -> AuditTrail: ...

    def get_trail(self, audit_id: str) -> AuditTrail | None: ...


@runtime_checkable
class ReportGeneratorPort(Protocol):
    def generate(
        self, trail: AuditTrail, generated_at: datetime
    ) -> EngineeringAuditReport: ...


@runtime_checkable
class AuditClockPort(Protocol):
    def now(self) -> datetime: ...

