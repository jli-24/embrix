from __future__ import annotations

import copy

from .models import (
    ApprovedKnowledge,
    KnowledgeCandidate,
    ReviewRequest,
    identifier,
)


def validate_candidate(value: object) -> KnowledgeCandidate:
    if type(value) is not KnowledgeCandidate:
        raise TypeError("candidate is invalid")
    return KnowledgeCandidate.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def validate_review(value: object) -> ReviewRequest:
    if type(value) is not ReviewRequest:
        raise TypeError("review request is invalid")
    return ReviewRequest.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_approved_knowledge(value: object) -> ApprovedKnowledge:
    if type(value) is not ApprovedKnowledge:
        raise TypeError("approved knowledge is invalid")
    return ApprovedKnowledge.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def validate_candidate_id(value: object) -> str:
    return identifier(value, field="candidate_id")


__all__ = (
    "validate_approved_knowledge",
    "validate_candidate",
    "validate_candidate_id",
    "validate_review",
)
