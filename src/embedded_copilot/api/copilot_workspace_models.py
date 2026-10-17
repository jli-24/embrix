from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from embedded_copilot.engineering_workspace_apply.contracts import fingerprint, identifier, relative_reference, safe_text, utc_datetime
from embedded_copilot.engineering_workspace_apply.models import (
    ApplyApprovalGrant,
    ApplyOperation,
    ApplyReceipt,
    ChangePreview,
    ChangeSet,
    OperationView,
    RecoveryReceipt,
)


class WorkspaceApiContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ChangeSetCreateRequest(WorkspaceApiContract):
    project_id: str
    target_id: str
    proposal_id: str
    proposal_fingerprint: str

    @field_validator("project_id", "target_id", "proposal_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fp(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")


class PreviewRequest(WorkspaceApiContract):
    project_id: str
    changeset_id: str
    changeset_fingerprint: str

    @field_validator("project_id", "changeset_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("changeset_fingerprint", mode="before")
    @classmethod
    def _changeset_fp(cls, value: object) -> str:
        return fingerprint(value, field="changeset_fingerprint")


class ApplyApprovalRequest(WorkspaceApiContract):
    project_id: str
    changeset_id: str
    changeset_fingerprint: str
    reviewer_id: str
    nonce: str

    @field_validator("project_id", "changeset_id", "reviewer_id", "nonce", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("changeset_fingerprint", mode="before")
    @classmethod
    def _changeset_fp(cls, value: object) -> str:
        return fingerprint(value, field="changeset_fingerprint")


class ApplyOperationRequest(WorkspaceApiContract):
    project_id: str
    changeset_id: str
    changeset_fingerprint: str
    approval_id: str
    operation: ApplyOperation = ApplyOperation.WORKSPACE_APPLY

    @field_validator("project_id", "changeset_id", "approval_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("changeset_fingerprint", mode="before")
    @classmethod
    def _changeset_fp(cls, value: object) -> str:
        return fingerprint(value, field="changeset_fingerprint")

    @field_validator("operation", mode="before")
    @classmethod
    def _operation(cls, value: object) -> ApplyOperation:
        return value if isinstance(value, ApplyOperation) else ApplyOperation(value)


class ChangeSetEntryView(WorkspaceApiContract):
    entry_id: str
    relative_file_reference: str
    change_type: str
    target_symbol: str | None
    before_fingerprint: str
    after_fingerprint: str
    safe_preview: str
    reason: str


class ChangeSetView(WorkspaceApiContract):
    changeset_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    target_fingerprint: str
    base_project_fingerprint: str
    staging_fingerprint: str
    entries: tuple[ChangeSetEntryView, ...]
    created_at: datetime
    expires_at: datetime
    status: str
    fingerprint: str


class PreviewView(WorkspaceApiContract):
    changeset_id: str
    changeset_fingerprint: str
    project_id: str
    affected_files: tuple[str, ...]
    diff: str
    validation_status: str
    validation_fingerprint: str
    risk: str
    generated_at: datetime
    fingerprint: str


class ApplyApprovalView(WorkspaceApiContract):
    grant_id: str
    project_id: str
    changeset_id: str
    changeset_fingerprint: str
    target_fingerprint: str
    project_fingerprint: str
    staging_fingerprint: str
    reviewer_id: str
    expires_at: datetime
    status: str
    fingerprint: str


class ReceiptTargetView(WorkspaceApiContract):
    relative_file_reference: str
    recovery_object_reference: str
    checksum: str
    before_fingerprint: str
    after_fingerprint: str


class ReceiptView(WorkspaceApiContract):
    operation_id: str
    project_id: str
    changeset_id: str
    changeset_fingerprint: str
    status: str
    recovery_status: str
    recovery_id: str | None
    targets: tuple[ReceiptTargetView, ...]
    validation_status: str
    before_project_fingerprint: str
    after_project_fingerprint: str
    created_at: datetime
    fingerprint: str


def changeset_view(value: ChangeSet) -> ChangeSetView:
    return ChangeSetView(
        changeset_id=value.changeset_id,
        project_id=value.project_id,
        proposal_id=value.proposal_id,
        proposal_fingerprint=value.proposal_fingerprint,
        target_fingerprint=value.target_fingerprint,
        base_project_fingerprint=value.base_project_fingerprint,
        staging_fingerprint=value.staging_fingerprint,
        entries=tuple(
            ChangeSetEntryView(
                entry_id=item.entry_id,
                relative_file_reference=item.relative_file_reference,
                change_type=item.change_type.value,
                target_symbol=item.target_symbol,
                before_fingerprint=item.before_fingerprint,
                after_fingerprint=item.after_fingerprint,
                safe_preview=item.safe_preview,
                reason=item.reason,
            )
            for item in value.entries
        ),
        created_at=value.created_at,
        expires_at=value.expires_at,
        status=value.status.value,
        fingerprint=value.fingerprint,
    )


def preview_view(value: ChangePreview) -> PreviewView:
    return PreviewView(**value.model_dump(mode="python"))


def approval_view(value: ApplyApprovalGrant) -> ApplyApprovalView:
    return ApplyApprovalView(
        grant_id=value.grant_id,
        project_id=value.project_id,
        changeset_id=value.changeset_id,
        changeset_fingerprint=value.changeset_fingerprint,
        target_fingerprint=value.target_fingerprint,
        project_fingerprint=value.project_fingerprint,
        staging_fingerprint=value.staging_fingerprint,
        reviewer_id=value.reviewer_id,
        expires_at=value.expires_at,
        status=value.status.value,
        fingerprint=value.fingerprint,
    )


def receipt_view(value: ApplyReceipt | RecoveryReceipt) -> ReceiptView:
    if isinstance(value, RecoveryReceipt):
        raise TypeError("recovery receipt is not an apply receipt")
    return ReceiptView(
        operation_id=value.operation_id,
        project_id=value.project_id,
        changeset_id=value.changeset_id,
        changeset_fingerprint=value.changeset_fingerprint,
        status=value.status.value,
        recovery_status=value.recovery_status.value,
        recovery_id=value.recovery_id,
        targets=tuple(ReceiptTargetView(**item.model_dump(mode="python")) for item in value.targets),
        validation_status=value.validation_status.value,
        before_project_fingerprint=value.before_project_fingerprint,
        after_project_fingerprint=value.after_project_fingerprint,
        created_at=value.created_at,
        fingerprint=value.fingerprint,
    )


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {"datetime"})
