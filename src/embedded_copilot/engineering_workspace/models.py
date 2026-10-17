from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from .contracts import (
    FingerprintedWorkspaceContract,
    deterministic_id,
    fingerprint,
    identifier,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


class WorkspaceDomain(StrEnum):
    ENGINEERING_CONTEXT = "ENGINEERING_CONTEXT"
    PCB = "PCB"
    MULTIMODAL = "MULTIMODAL"
    CAPABILITY = "CAPABILITY"
    REPAIR = "REPAIR"
    EDA = "EDA"
    HARDWARE_RUNTIME = "HARDWARE_RUNTIME"
    HIL = "HIL"
    OPTIMIZATION = "OPTIMIZATION"
    MEMORY = "MEMORY"


class WorkspaceObjectKind(StrEnum):
    ANALYSIS = "ANALYSIS"
    PROPOSAL = "PROPOSAL"
    REVIEW = "REVIEW"
    DECISION = "DECISION"
    RECEIPT = "RECEIPT"
    OBSERVATION = "OBSERVATION"
    OVERLAY = "OVERLAY"
    MEMORY_CANDIDATE = "MEMORY_CANDIDATE"
    AUDIT_EVENT = "AUDIT_EVENT"


class WorkspaceLifecycleStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class WorkspaceRelationType(StrEnum):
    DERIVED_FROM = "DERIVED_FROM"
    SUPPORTS = "SUPPORTS"
    REVIEW_OF = "REVIEW_OF"
    DECIDES = "DECIDES"
    PRODUCES = "PRODUCES"
    OBSERVES = "OBSERVES"
    PROJECTS = "PROJECTS"
    CANDIDATE_FROM = "CANDIDATE_FROM"
    AUDITS = "AUDITS"


class WorkspaceObjectReference(FingerprintedWorkspaceContract):
    project_id: str
    source_domain: WorkspaceDomain
    source_kind: WorkspaceObjectKind
    source_object_id: str
    source_fingerprint: str
    occurred_at: datetime
    status: WorkspaceLifecycleStatus

    @field_validator("project_id", "source_object_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _occurred_at(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")

    @property
    def identity(self) -> tuple[WorkspaceDomain, WorkspaceObjectKind, str]:
        return (self.source_domain, self.source_kind, self.source_object_id)


class WorkspaceLineageLink(FingerprintedWorkspaceContract):
    source_domain: WorkspaceDomain
    source_kind: WorkspaceObjectKind
    source_object_id: str
    source_fingerprint: str
    relation_type: WorkspaceRelationType

    @field_validator("source_object_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="source_object_id")

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @property
    def identity(self) -> tuple[WorkspaceDomain, WorkspaceObjectKind, str]:
        return (self.source_domain, self.source_kind, self.source_object_id)


class WorkspaceResolvedReference(FingerprintedWorkspaceContract):
    reference: WorkspaceObjectReference
    lineage: tuple[WorkspaceLineageLink, ...]

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> WorkspaceObjectReference:
        return _exact(value, WorkspaceObjectReference, field="workspace reference")

    @field_validator("lineage", mode="before")
    @classmethod
    def _lineage(cls, value: object) -> tuple[WorkspaceLineageLink, ...]:
        items = tuple(
            _exact(item, WorkspaceLineageLink, field="workspace lineage")
            for item in tuple_only(value, field="lineage")
        )
        if not items or len({item.fingerprint for item in items}) != len(items):
            raise ValueError("workspace lineage is invalid")
        return tuple(sorted(items, key=lambda item: item.fingerprint))

    @model_validator(mode="after")
    def _binding(self):
        if any(
            item.identity == self.reference.identity
            and item.source_fingerprint == self.reference.source_fingerprint
            for item in self.lineage
        ):
            raise ValueError("workspace lineage cannot self-reference")
        return self


class WorkspaceTimelineEntry(FingerprintedWorkspaceContract):
    entry_id: str
    sequence: int
    reference: WorkspaceObjectReference
    lineage: tuple[WorkspaceLineageLink, ...]

    @field_validator("entry_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="entry_id")

    @field_validator("sequence", mode="before")
    @classmethod
    def _sequence(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("workspace sequence is invalid")
        return value

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> WorkspaceObjectReference:
        return _exact(value, WorkspaceObjectReference, field="workspace reference")

    @field_validator("lineage", mode="before")
    @classmethod
    def _lineage(cls, value: object) -> tuple[WorkspaceLineageLink, ...]:
        return WorkspaceResolvedReference._lineage(value)

    @model_validator(mode="after")
    def _binding(self):
        expected = deterministic_id(
            "workspace-entry", self.reference.fingerprint, str(self.sequence)
        )
        if self.entry_id != expected:
            raise ValueError("workspace entry identity mismatch")
        return self


class EngineeringWorkspaceRequest(FingerprintedWorkspaceContract):
    workspace_id: str
    project_id: str
    references: tuple[WorkspaceObjectReference, ...]

    @field_validator("workspace_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("references", mode="before")
    @classmethod
    def _references(cls, value: object) -> tuple[WorkspaceObjectReference, ...]:
        items = tuple(
            _exact(item, WorkspaceObjectReference, field="workspace reference")
            for item in tuple_only(value, field="references")
        )
        if not items:
            raise ValueError("workspace references are required")
        identities = tuple(item.identity for item in items)
        if len(identities) != len(set(identities)):
            raise ValueError("workspace duplicate reference is invalid")
        return items

    @model_validator(mode="after")
    def _binding(self):
        if any(item.project_id != self.project_id for item in self.references):
            raise ValueError("workspace reference project mismatch")
        return self


class EngineeringWorkspaceSnapshot(FingerprintedWorkspaceContract):
    workspace_id: str
    project_id: str
    source_references: tuple[WorkspaceObjectReference, ...]
    timeline: tuple[WorkspaceTimelineEntry, ...]
    generated_at: datetime

    @field_validator("workspace_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_references", mode="before")
    @classmethod
    def _references(cls, value: object) -> tuple[WorkspaceObjectReference, ...]:
        return EngineeringWorkspaceRequest._references(value)

    @field_validator("timeline", mode="before")
    @classmethod
    def _timeline(cls, value: object) -> tuple[WorkspaceTimelineEntry, ...]:
        items = tuple(
            _exact(item, WorkspaceTimelineEntry, field="workspace timeline entry")
            for item in tuple_only(value, field="timeline")
        )
        if not items:
            raise ValueError("workspace timeline is required")
        return items

    @field_validator("generated_at", mode="before")
    @classmethod
    def _generated_at(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")

    @model_validator(mode="after")
    def _binding(self):
        if any(item.project_id != self.project_id for item in self.source_references):
            raise ValueError("workspace source project mismatch")
        if tuple(item.sequence for item in self.timeline) != tuple(
            range(1, len(self.timeline) + 1)
        ):
            raise ValueError("workspace timeline sequence mismatch")
        expected_references = tuple(item.reference for item in self.timeline)
        if self.source_references != expected_references:
            raise ValueError("workspace source reference ordering mismatch")
        ordering = tuple(
            (
                item.reference.occurred_at,
                item.reference.source_domain.value,
                item.reference.source_kind.value,
                item.reference.source_object_id,
            )
            for item in self.timeline
        )
        if ordering != tuple(sorted(ordering)):
            raise ValueError("workspace timeline ordering mismatch")
        if self.generated_at < self.timeline[-1].reference.occurred_at:
            raise ValueError("workspace generated time is invalid")
        return self


__all__ = (
    "EngineeringWorkspaceRequest",
    "EngineeringWorkspaceSnapshot",
    "WorkspaceDomain",
    "WorkspaceLifecycleStatus",
    "WorkspaceLineageLink",
    "WorkspaceObjectKind",
    "WorkspaceObjectReference",
    "WorkspaceRelationType",
    "WorkspaceResolvedReference",
    "WorkspaceTimelineEntry",
)
