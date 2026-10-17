from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ConfigDict, field_validator

from embedded_copilot.engineering_validation_proposal import (
    OperationProposal,
    ProposalChange,
    TargetIdentity,
    ValidationResult,
    ValidationType,
)
from embedded_copilot.engineering_validation_proposal.models import V64Contract, fingerprint, identifier, safe_text, utc_datetime


class ValidationCreateBody(V64Contract):
    validation_id: str
    project_id: str
    project_fingerprint: str
    validation_type: ValidationType
    target_identity: TargetIdentity
    target_fingerprint: str
    evidence_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()
    requested_at: datetime

    @field_validator("validation_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("validation_type", mode="before")
    @classmethod
    def _validation_type(cls, value: object) -> ValidationType:
        return value if isinstance(value, ValidationType) else ValidationType(value)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(value)

    @field_validator("project_fingerprint", "target_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @field_validator("evidence_ids", "memory_ids", "knowledge_ids", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        if type(value) not in {tuple, list}:
            raise TypeError(f"{info.field_name} must be an array")
        return tuple(identifier(item, field=info.field_name) for item in value)


class ProposalCreateBody(V64Contract):
    project_id: str
    finding_id: str
    proposed_changes: tuple[ProposalChange, ...] = ()
    rationale: str | None = None

    @field_validator("project_id", "finding_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposed_changes", mode="before")
    @classmethod
    def _changes(cls, value: object) -> tuple[ProposalChange, ...]:
        if type(value) not in {tuple, list}:
            raise TypeError("proposed_changes must be an array")
        return tuple(ProposalChange.model_validate(item) for item in value)

    @field_validator("rationale", mode="before")
    @classmethod
    def _rationale(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="rationale", maximum=4096)


__all__ = ("OperationProposal", "ProposalCreateBody", "ValidationCreateBody", "ValidationResult")
