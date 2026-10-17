"""Proposal, approval and apply receipts for controlled mutation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import field_validator

from embedded_copilot.engineering_runtime.common import (
    ZERO_FINGERPRINT,
    RuntimeContract,
    canonical_fingerprint,
    validate_fingerprint,
    validate_identifier,
    validate_utc_datetime,
)
from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    ObjectType,
)


class ProposalStatus(str, Enum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class ObjectProposal(RuntimeContract):
    proposal_id: str
    project_id: str
    target_object_id: str
    target_object_type: ObjectType
    proposed_object: EngineeringObject
    base_revision: str | None = None
    base_fingerprint: str | None = None
    context_fingerprint: str
    proposer: str
    status: ProposalStatus = ProposalStatus.CREATED
    fingerprint: str = ZERO_FINGERPRINT

    _ids = field_validator("proposal_id", "project_id", "target_object_id", "proposer")(
        lambda v: validate_identifier(v, field="proposal_identity")
    )
    _context = field_validator("context_fingerprint")(validate_fingerprint)
    _base = field_validator("base_fingerprint")(
        lambda v: (
            None if v is None else validate_fingerprint(v, field="base_fingerprint")
        )
    )

    @classmethod
    def create(cls, **values) -> ObjectProposal:
        draft = cls(**values, fingerprint=ZERO_FINGERPRINT)
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(
                    draft, exclude={"fingerprint", "status"}
                )
            }
        )


class ApprovalRecord(RuntimeContract):
    approval_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    reviewer: str
    decision: ProposalStatus
    reviewed_at: datetime
    fingerprint: str = ZERO_FINGERPRINT

    _ids = field_validator("approval_id", "project_id", "proposal_id", "reviewer")(
        lambda v: validate_identifier(v, field="approval_identity")
    )
    _proposal_fp = field_validator("proposal_fingerprint")(validate_fingerprint)
    _reviewed_at = field_validator("reviewed_at")(validate_utc_datetime)

    @classmethod
    def approve(cls, **values) -> ApprovalRecord:
        draft = cls(
            **values, decision=ProposalStatus.APPROVED, fingerprint=ZERO_FINGERPRINT
        )
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})
            }
        )


class ObjectMutationRecord(RuntimeContract):
    apply_id: str
    project_id: str
    proposal_id: str
    new_revision: str
    object_fingerprint: str
    applied_at: datetime
    status: str = "APPLIED"
    fingerprint: str = ZERO_FINGERPRINT

    _ids = field_validator("apply_id", "project_id", "proposal_id")(
        lambda v: validate_identifier(v, field="apply_identity")
    )
    _object_fp = field_validator("object_fingerprint")(validate_fingerprint)
    _applied_at = field_validator("applied_at")(validate_utc_datetime)

    @classmethod
    def create(cls, **values) -> ObjectMutationRecord:
        draft = cls(**values, fingerprint=ZERO_FINGERPRINT)
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})
            }
        )
