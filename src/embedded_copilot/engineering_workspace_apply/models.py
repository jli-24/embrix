from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .contracts import (
    ApplyContract,
    FingerprintedApplyContract,
    deterministic_id,
    fingerprint,
    identifier,
    preview_text,
    relative_reference,
    safe_text,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    if not isinstance(value, dict):
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


class ApplyOperation(StrEnum):
    WORKSPACE_APPLY = "WORKSPACE_APPLY"
    EDA_APPLY = "EDA_APPLY"


class ChangeType(StrEnum):
    CREATE_FILE = "CREATE_FILE"
    MODIFY_FILE = "MODIFY_FILE"
    DELETE_FILE = "DELETE_FILE"
    RENAME_FILE = "RENAME_FILE"
    EDA_STRUCTURED_CHANGE = "EDA_STRUCTURED_CHANGE"


class ChangeSetStatus(StrEnum):
    CREATED = "CREATED"
    WAIT_APPROVAL = "WAIT_APPROVAL"
    STALE = "STALE"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ApprovalGrantStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    FAILED_RECOVERY_REQUIRED = "FAILED_RECOVERY_REQUIRED"
    RECOVERED = "RECOVERED"
    REPLAY_REJECTED = "REPLAY_REJECTED"
    REJECTED = "REJECTED"


class RecoveryStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_REQUIRED = "NOT_REQUIRED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERED = "RECOVERED"


class ValidationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    UNVERIFIED = "UNVERIFIED"


class WorkspaceFile(FingerprintedApplyContract):
    relative_file_reference: str
    exists: bool
    content_fingerprint: str
    size: int = Field(ge=0, le=64 * 1024 * 1024)

    @field_validator("relative_file_reference", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return relative_reference(value)

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fp(cls, value: object) -> str:
        return fingerprint(value, field="content_fingerprint")


class WorkspaceTarget(FingerprintedApplyContract):
    tenant_id: str
    project_id: str
    target_id: str
    project_fingerprint: str

    @field_validator("tenant_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_fingerprint", mode="before")
    @classmethod
    def _project_fp(cls, value: object) -> str:
        return fingerprint(value, field="project_fingerprint")


class WorkspaceSnapshot(FingerprintedApplyContract):
    tenant_id: str
    project_id: str
    target_id: str
    project_fingerprint: str
    target_fingerprint: str
    files: tuple[WorkspaceFile, ...]
    captured_at: datetime

    @field_validator("tenant_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_fingerprint", "target_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("files", mode="before")
    @classmethod
    def _files(cls, value: object) -> tuple[WorkspaceFile, ...]:
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("workspace files are required")
        items = tuple(_exact(item, WorkspaceFile, field="workspace file") for item in value)
        if len({item.relative_file_reference for item in items}) != len(items):
            raise ValueError("workspace file references must be unique")
        return tuple(sorted(items, key=lambda item: item.relative_file_reference))

    @field_validator("captured_at", mode="before")
    @classmethod
    def _captured(cls, value: object) -> datetime:
        return utc_datetime(value, field="captured_at")

    @model_validator(mode="after")
    def _bindings(self):
        if any(item.relative_file_reference.startswith(".embedded-copilot/") for item in self.files):
            raise ValueError("private state must not be part of project snapshot")
        return self


class ChangeIntent(ApplyContract):
    relative_file_reference: str
    change_type: ChangeType
    target_symbol: str | None = None
    before_fingerprint: str
    after_fingerprint: str
    safe_preview: str
    reason: str
    source_proposal_reference: str
    after_content: str | None = None

    @field_validator("relative_file_reference", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return relative_reference(value)

    @field_validator("change_type", mode="before")
    @classmethod
    def _type(cls, value: object) -> ChangeType:
        return value if isinstance(value, ChangeType) else ChangeType(value)

    @field_validator("target_symbol", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="target_symbol", maximum=256)

    @field_validator("before_fingerprint", "after_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("safe_preview", mode="before")
    @classmethod
    def _preview(cls, value: object) -> str:
        return preview_text(value, field="safe_preview")

    @field_validator("reason", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=1024)

    @field_validator("source_proposal_reference", mode="before")
    @classmethod
    def _source_reference(cls, value: object) -> str:
        return identifier(value, field="source_proposal_reference")

    @field_validator("after_content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("after_content is invalid")
        return value

    @model_validator(mode="after")
    def _supported(self):
        if self.change_type in {ChangeType.DELETE_FILE, ChangeType.RENAME_FILE}:
            raise ValueError("change type is unavailable in v6.5")
        if self.change_type is ChangeType.EDA_STRUCTURED_CHANGE and self.after_content is not None:
            raise ValueError("EDA changes must be structured")
        return self


class ChangeSetEntry(FingerprintedApplyContract):
    entry_id: str
    relative_file_reference: str
    change_type: ChangeType
    target_symbol: str | None = None
    before_fingerprint: str
    after_fingerprint: str
    safe_preview: str
    reason: str
    source_proposal_reference: str
    after_content: str | None = None

    @field_validator("entry_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_proposal_reference", mode="before")
    @classmethod
    def _source_reference(cls, value: object) -> str:
        return identifier(value, field="source_proposal_reference")

    @field_validator("relative_file_reference", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return relative_reference(value)

    @field_validator("change_type", mode="before")
    @classmethod
    def _type(cls, value: object) -> ChangeType:
        return value if isinstance(value, ChangeType) else ChangeType(value)

    @field_validator("target_symbol", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="target_symbol", maximum=256)

    @field_validator("before_fingerprint", "after_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("safe_preview", mode="before")
    @classmethod
    def _preview(cls, value: object) -> str:
        return preview_text(value, field="safe_preview")

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return safe_text(value, field="reason", maximum=1024)

    @field_validator("after_content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("after_content is invalid")
        return value

    @model_validator(mode="after")
    def _supported(self):
        if self.change_type in {ChangeType.DELETE_FILE, ChangeType.RENAME_FILE}:
            raise ValueError("change type is unavailable in v6.5")
        if self.change_type is ChangeType.EDA_STRUCTURED_CHANGE and self.after_content is not None:
            raise ValueError("EDA changes must be structured")
        return self


class ChangeSet(FingerprintedApplyContract):
    changeset_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    proposal_id: str
    proposal_fingerprint: str
    target: WorkspaceTarget
    target_fingerprint: str
    base_project_fingerprint: str
    validation_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    staging_fingerprint: str
    entries: tuple[ChangeSetEntry, ...]
    created_at: datetime
    expires_at: datetime
    status: ChangeSetStatus = ChangeSetStatus.WAIT_APPROVAL

    @field_validator(
        "changeset_id", "tenant_id", "project_id", "requester_id", "proposal_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "proposal_fingerprint", "target_fingerprint", "base_project_fingerprint", "validation_fingerprint", "staging_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("evidence_fingerprints is invalid")
        return tuple(fingerprint(item, field="evidence_fingerprint") for item in value)

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: object) -> WorkspaceTarget:
        return _exact(value, WorkspaceTarget, field="target")

    @field_validator("entries", mode="before")
    @classmethod
    def _entries(cls, value: object) -> tuple[ChangeSetEntry, ...]:
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("changeset entries are required")
        items = tuple(_exact(item, ChangeSetEntry, field="changeset entry") for item in value)
        if len({item.entry_id for item in items}) != len(items):
            raise ValueError("changeset entry identities must be unique")
        return items

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _time(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ChangeSetStatus:
        return value if isinstance(value, ChangeSetStatus) else ChangeSetStatus(value)

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.target.tenant_id != self.tenant_id
            or self.target.project_id != self.project_id
            or self.target.project_fingerprint != self.base_project_fingerprint
            or self.expires_at <= self.created_at
        ):
            raise ValueError("changeset binding mismatch")
        expected = tuple(
            deterministic_id("changeset-entry", self.changeset_id, str(index))
            for index, _ in enumerate(self.entries)
        )
        if tuple(item.entry_id for item in self.entries) != expected:
            raise ValueError("changeset entry identity mismatch")
        return self


class ChangePreview(FingerprintedApplyContract):
    changeset_id: str
    changeset_fingerprint: str
    project_id: str
    affected_files: tuple[str, ...]
    diff: str
    validation_status: ValidationStatus
    validation_fingerprint: str
    risk: str
    generated_at: datetime

    @field_validator("changeset_id", "project_id", "risk", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("changeset_fingerprint", "validation_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("affected_files", mode="before")
    @classmethod
    def _files(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("affected files are required")
        return tuple(relative_reference(item) for item in value)

    @field_validator("diff", mode="before")
    @classmethod
    def _diff(cls, value: object) -> str:
        return preview_text(value, field="diff")

    @field_validator("validation_status", mode="before")
    @classmethod
    def _validation_status(cls, value: object) -> ValidationStatus:
        return value if isinstance(value, ValidationStatus) else ValidationStatus(value)

    @field_validator("generated_at", mode="before")
    @classmethod
    def _generated(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class ApplyApprovalGrant(FingerprintedApplyContract):
    grant_id: str
    tenant_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    changeset_id: str
    changeset_fingerprint: str
    target_fingerprint: str
    project_fingerprint: str
    staging_fingerprint: str
    reviewer_id: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    status: ApprovalGrantStatus = ApprovalGrantStatus.ISSUED

    @field_validator(
        "grant_id", "tenant_id", "project_id", "proposal_id", "changeset_id", "reviewer_id", "nonce", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "proposal_fingerprint", "changeset_fingerprint", "target_fingerprint", "project_fingerprint", "staging_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def _time(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ApprovalGrantStatus:
        return value if isinstance(value, ApprovalGrantStatus) else ApprovalGrantStatus(value)

    @model_validator(mode="after")
    def _expiry(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("approval expiry is invalid")
        return self


class ApplyRequest(FingerprintedApplyContract):
    operation_id: str
    operation: ApplyOperation
    changeset: ChangeSet
    approval: ApplyApprovalGrant
    requested_at: datetime

    @field_validator("operation_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="operation_id")

    @field_validator("operation", mode="before")
    @classmethod
    def _operation(cls, value: object) -> ApplyOperation:
        return value if isinstance(value, ApplyOperation) else ApplyOperation(value)

    @field_validator("changeset", "approval", mode="before")
    @classmethod
    def _nested(cls, value: object, info):
        return _exact(value, ChangeSet if info.field_name == "changeset" else ApplyApprovalGrant, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        grant = self.approval
        change = self.changeset
        if (
            grant.tenant_id != change.tenant_id
            or grant.project_id != change.project_id
            or grant.proposal_id != change.proposal_id
            or grant.proposal_fingerprint != change.proposal_fingerprint
            or grant.changeset_id != change.changeset_id
            or grant.changeset_fingerprint != change.fingerprint
            or grant.target_fingerprint != change.target_fingerprint
            or grant.project_fingerprint != change.base_project_fingerprint
            or grant.staging_fingerprint != change.staging_fingerprint
        ):
            raise ValueError("apply approval binding mismatch")
        return self


class RecoveryTarget(ApplyContract):
    relative_file_reference: str
    recovery_object_reference: str
    checksum: str
    before_fingerprint: str
    after_fingerprint: str

    @field_validator("relative_file_reference", mode="before")
    @classmethod
    def _path(cls, value: object) -> str:
        return relative_reference(value)

    @field_validator("recovery_object_reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        return identifier(value, field="recovery_object_reference")

    @field_validator("checksum", "before_fingerprint", "after_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)


class RecoveryReceipt(FingerprintedApplyContract):
    recovery_id: str
    operation_id: str
    project_id: str
    status: RecoveryStatus
    targets: tuple[RecoveryTarget, ...]
    created_at: datetime

    @field_validator("recovery_id", "operation_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> RecoveryStatus:
        return value if isinstance(value, RecoveryStatus) else RecoveryStatus(value)

    @field_validator("targets", mode="before")
    @classmethod
    def _targets(cls, value: object) -> tuple[RecoveryTarget, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("recovery targets are invalid")
        return tuple(_exact(item, RecoveryTarget, field="recovery target") for item in value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


class ApplyReceipt(FingerprintedApplyContract):
    operation_id: str
    project_id: str
    changeset_id: str
    changeset_fingerprint: str
    status: OperationStatus
    recovery_status: RecoveryStatus
    recovery_id: str | None
    targets: tuple[RecoveryTarget, ...]
    validation_status: ValidationStatus
    before_project_fingerprint: str
    after_project_fingerprint: str
    created_at: datetime

    @field_validator("operation_id", "project_id", "changeset_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("changeset_fingerprint", "before_project_fingerprint", "after_project_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("recovery_id", mode="before")
    @classmethod
    def _recovery_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="recovery_id")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> OperationStatus:
        return value if isinstance(value, OperationStatus) else OperationStatus(value)

    @field_validator("recovery_status", mode="before")
    @classmethod
    def _recovery_status(cls, value: object) -> RecoveryStatus:
        return value if isinstance(value, RecoveryStatus) else RecoveryStatus(value)

    @field_validator("validation_status", mode="before")
    @classmethod
    def _validation_status(cls, value: object) -> ValidationStatus:
        return value if isinstance(value, ValidationStatus) else ValidationStatus(value)

    @field_validator("targets", mode="before")
    @classmethod
    def _targets(cls, value: object) -> tuple[RecoveryTarget, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("receipt targets are invalid")
        return tuple(_exact(item, RecoveryTarget, field="receipt target") for item in value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


class WorkspaceAuditEvent(FingerprintedApplyContract):
    event_id: str
    operation_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    reviewer_id: str | None
    proposal_id: str
    changeset_id: str
    approval_id: str | None
    before_fingerprint: str
    after_fingerprint: str
    status: OperationStatus
    recovery_status: RecoveryStatus
    validation_status: ValidationStatus
    occurred_at: datetime

    @field_validator(
        "event_id", "operation_id", "tenant_id", "project_id", "requester_id", "proposal_id", "changeset_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reviewer_id", "approval_id", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("before_fingerprint", "after_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> OperationStatus:
        return value if isinstance(value, OperationStatus) else OperationStatus(value)

    @field_validator("recovery_status", mode="before")
    @classmethod
    def _recovery_status(cls, value: object) -> RecoveryStatus:
        return value if isinstance(value, RecoveryStatus) else RecoveryStatus(value)

    @field_validator("validation_status", mode="before")
    @classmethod
    def _validation_status(cls, value: object) -> ValidationStatus:
        return value if isinstance(value, ValidationStatus) else ValidationStatus(value)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")


class OperationView(FingerprintedApplyContract):
    operation_id: str
    project_id: str
    changeset_id: str
    operation: ApplyOperation
    status: OperationStatus
    recovery_status: RecoveryStatus
    validation_status: ValidationStatus
    receipt_id: str | None = None
    reason_code: str
    detail: str
    created_at: datetime

    @field_validator("operation_id", "project_id", "changeset_id", "reason_code", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("operation", mode="before")
    @classmethod
    def _operation(cls, value: object) -> ApplyOperation:
        return value if isinstance(value, ApplyOperation) else ApplyOperation(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> OperationStatus:
        return value if isinstance(value, OperationStatus) else OperationStatus(value)

    @field_validator("recovery_status", mode="before")
    @classmethod
    def _recovery_status(cls, value: object) -> RecoveryStatus:
        return value if isinstance(value, RecoveryStatus) else RecoveryStatus(value)

    @field_validator("validation_status", mode="before")
    @classmethod
    def _validation_status(cls, value: object) -> ValidationStatus:
        return value if isinstance(value, ValidationStatus) else ValidationStatus(value)

    @field_validator("receipt_id", mode="before")
    @classmethod
    def _receipt_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="receipt_id")

    @field_validator("detail", mode="before")
    @classmethod
    def _detail(cls, value: object) -> str:
        return safe_text(value, field="detail", maximum=512)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {"copy", "datetime"})
