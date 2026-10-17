from __future__ import annotations

import copy
import importlib
from typing import Any

from embedded_copilot.engineering_evolution import KnowledgeEvolutionProposal
from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_optimization_loop import OptimizationProposal
from embedded_copilot.engineering_validation import ValidationFinding

from .models import (
    HumanDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
    identifier,
)

ReviewTarget = (
    ApprovedKnowledge
    | ValidationFinding
    | OptimizationProposal
    | KnowledgeEvolutionProposal
    | Any
)

_TARGET_MODELS = {
    ReviewTargetType.KNOWLEDGE: ApprovedKnowledge,
    ReviewTargetType.VALIDATION: ValidationFinding,
    ReviewTargetType.OPTIMIZATION: OptimizationProposal,
    ReviewTargetType.EVOLUTION: KnowledgeEvolutionProposal,
}


def _engineering_evidence_bundle_type():
    # Keep the review package's static dependency allow-list narrow. The
    # evidence target is loaded only when that target type is actually used.
    return importlib.import_module(
        "embedded_copilot.engineering_copilot"
    ).EngineeringEvidenceBundle


def _validate_exact(value: object, cls, *, message: str):
    if type(value) is not cls:
        raise TypeError(message)
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_target_reference(value: object) -> ReviewTargetReference:
    return _validate_exact(
        value, ReviewTargetReference, message="review target reference is invalid"
    )


def validate_review_request(value: object) -> ReviewRequest:
    return _validate_exact(value, ReviewRequest, message="review request is invalid")


def validate_human_decision(value: object) -> HumanDecision:
    return _validate_exact(value, HumanDecision, message="human decision is invalid")


def validate_review_record(value: object) -> ReviewRecord:
    return _validate_exact(value, ReviewRecord, message="review record is invalid")


def validate_target(value: object, target_type: ReviewTargetType) -> ReviewTarget:
    checked_type = ReviewTargetType(target_type)
    model = (
        _engineering_evidence_bundle_type()
        if checked_type is ReviewTargetType.ENGINEERING_EVIDENCE
        else _TARGET_MODELS[checked_type]
    )
    return _validate_exact(value, model, message="review target is invalid")


def target_identity(
    value: ReviewTarget, target_type: ReviewTargetType
) -> tuple[str, str, str]:
    checked = validate_target(value, target_type)
    checked_type = ReviewTargetType(target_type)
    if checked_type is ReviewTargetType.KNOWLEDGE:
        return checked.project_id, checked.knowledge_id, checked.fingerprint
    if checked_type is ReviewTargetType.VALIDATION:
        return checked.project_id, checked.finding_id, checked.fingerprint
    if checked_type is ReviewTargetType.ENGINEERING_EVIDENCE:
        return checked.project_id, checked.bundle_id, checked.bundle_fingerprint
    return checked.project_id, checked.proposal_id, checked.fingerprint


def validate_review_id(value: object) -> str:
    return identifier(value, field="review_id")


__all__ = (
    "ReviewTarget",
    "target_identity",
    "validate_human_decision",
    "validate_review_id",
    "validate_review_record",
    "validate_review_request",
    "validate_target",
    "validate_target_reference",
)
