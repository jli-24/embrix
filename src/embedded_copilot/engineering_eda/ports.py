from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    EDAAnalysisResult,
    EDAApplyReceipt,
    EDAApplyRequest,
    EDAArtifactReference,
    EDAAuditEvent,
    EDAChangeSet,
    EDAReviewRecord,
    EDAReviewRequest,
    EDAValidationKind,
    EDAValidationReport,
    PCBDesignProposal,
)


@runtime_checkable
class EDAArtifactSourcePort(Protocol):
    def get_artifact(
        self, project_id: str, artifact_id: str
    ) -> EDAArtifactReference | None: ...


@runtime_checkable
class EDAProposalPort(Protocol):
    def propose(self, result: EDAAnalysisResult) -> tuple[PCBDesignProposal, ...]: ...


@runtime_checkable
class EDAChangeSetSourcePort(Protocol):
    def get_change_set(self, change_set_id: str) -> EDAChangeSet | None: ...


@runtime_checkable
class EDAReviewPort(Protocol):
    def submit(self, request: EDAReviewRequest) -> EDAReviewRecord: ...

    def get_record(self, review_id: str) -> EDAReviewRecord | None: ...


@runtime_checkable
class EDARevisionWorkspacePort(Protocol):
    def create_staging(
        self, artifact: EDAArtifactReference, staging_revision: str
    ) -> EDAArtifactReference: ...


@runtime_checkable
class EDAApplyPort(Protocol):
    def apply(
        self, request: EDAApplyRequest, staging_artifact: EDAArtifactReference
    ) -> EDAApplyReceipt: ...


@runtime_checkable
class EDAValidationPort(Protocol):
    def validate(
        self, artifact: EDAArtifactReference, kind: EDAValidationKind
    ) -> EDAValidationReport: ...


@runtime_checkable
class EDAAuditSinkPort(Protocol):
    def append(self, event: EDAAuditEvent) -> EDAAuditEvent: ...

    def get(self, event_id: str) -> EDAAuditEvent | None: ...


@runtime_checkable
class EDAClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
