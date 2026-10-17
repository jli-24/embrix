from __future__ import annotations

import copy

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_validation import ValidationFinding, ValidationReport

from .models import (
    ApprovedKnowledgeReference,
    OptimizationProposal,
    OptimizationRequest,
    ValidationFindingReference,
    identifier,
)


def _validate_exact(value: object, cls, *, message: str):
    if type(value) is not cls:
        raise TypeError(message)
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_finding_reference(value: object) -> ValidationFindingReference:
    return _validate_exact(
        value, ValidationFindingReference, message="finding reference is invalid"
    )


def validate_knowledge_reference(value: object) -> ApprovedKnowledgeReference:
    return _validate_exact(
        value, ApprovedKnowledgeReference, message="knowledge reference is invalid"
    )


def validate_request(value: object) -> OptimizationRequest:
    return _validate_exact(value, OptimizationRequest, message="request is invalid")


def validate_proposal(value: object) -> OptimizationProposal:
    return _validate_exact(value, OptimizationProposal, message="proposal is invalid")


def validate_context_snapshot(value: object) -> EngineeringContextSnapshot:
    return _validate_exact(
        value, EngineeringContextSnapshot, message="context snapshot is invalid"
    )


def validate_finding(value: object) -> ValidationFinding:
    return _validate_exact(value, ValidationFinding, message="finding is invalid")


def validate_report(value: object) -> ValidationReport:
    return _validate_exact(value, ValidationReport, message="report is invalid")


def validate_approved_knowledge(value: object) -> ApprovedKnowledge:
    return _validate_exact(
        value, ApprovedKnowledge, message="approved knowledge is invalid"
    )


def validate_project_id(value: object) -> str:
    return identifier(value, field="project_id")


def validate_proposal_id(value: object) -> str:
    return identifier(value, field="proposal_id")


__all__ = (
    "validate_approved_knowledge",
    "validate_context_snapshot",
    "validate_finding",
    "validate_finding_reference",
    "validate_knowledge_reference",
    "validate_project_id",
    "validate_proposal",
    "validate_proposal_id",
    "validate_report",
    "validate_request",
)

