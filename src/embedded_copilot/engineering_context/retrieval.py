from __future__ import annotations

import copy

from .models import EngineeringContextEvidence, EngineeringContextRequest
from .ranking import rank_evidence


def retrieve_context(
    values: tuple[EngineeringContextEvidence, ...],
    request: EngineeringContextRequest,
) -> tuple[EngineeringContextEvidence, ...]:
    checked_request = EngineeringContextRequest.model_validate(
        copy.deepcopy(request.model_dump(mode="python"))
    )
    if type(values) is not tuple:
        raise TypeError("context evidence must be a tuple")
    checked = tuple(
        EngineeringContextEvidence.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        for value in values
    )
    if any(item.project_id != checked_request.project_id for item in checked):
        raise ValueError("context evidence project binding is invalid")
    return rank_evidence(checked, checked_request.task, checked_request.constraints)


class EngineeringContextRetrievalService:
    def retrieve(
        self,
        *,
        values: tuple[EngineeringContextEvidence, ...],
        request: EngineeringContextRequest,
    ) -> tuple[EngineeringContextEvidence, ...]:
        return retrieve_context(values, request)


__all__ = ("EngineeringContextRetrievalService", "retrieve_context")
