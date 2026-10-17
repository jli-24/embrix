from __future__ import annotations

import copy

from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_governance.contracts import (
    validate_approved_knowledge,
)

from .models import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionProposalRequest,
    EvolutionReviewRequest,
    EvolutionSinkReceipt,
    KnowledgeEvolutionProposal,
    identifier,
)


def _validate_exact(value: object, cls, *, message: str):
    if type(value) is not cls:
        raise TypeError(message)
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_source_knowledge(value: object) -> ApprovedKnowledge:
    return validate_approved_knowledge(value)


def validate_proposal_request(value: object) -> EvolutionProposalRequest:
    return _validate_exact(
        value, EvolutionProposalRequest, message="proposal request is invalid"
    )


def validate_proposal(value: object) -> KnowledgeEvolutionProposal:
    return _validate_exact(
        value, KnowledgeEvolutionProposal, message="evolution proposal is invalid"
    )


def validate_review(value: object) -> EvolutionReviewRequest:
    return _validate_exact(value, EvolutionReviewRequest, message="review is invalid")


def validate_apply_request(value: object) -> EvolutionApplyRequest:
    return _validate_exact(
        value, EvolutionApplyRequest, message="apply request is invalid"
    )


def validate_receipt(value: object) -> EvolutionSinkReceipt:
    return _validate_exact(value, EvolutionSinkReceipt, message="receipt is invalid")


def validate_application_record(value: object) -> EvolutionApplicationRecord:
    return _validate_exact(
        value, EvolutionApplicationRecord, message="application record is invalid"
    )


def validate_project_id(value: object) -> str:
    return identifier(value, field="project_id")


def validate_proposal_id(value: object) -> str:
    return identifier(value, field="proposal_id")


__all__ = (
    "validate_application_record",
    "validate_apply_request",
    "validate_project_id",
    "validate_proposal",
    "validate_proposal_id",
    "validate_proposal_request",
    "validate_receipt",
    "validate_review",
    "validate_source_knowledge",
)
