from __future__ import annotations

from .models import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    tokens,
)


def evidence_tier(item: EngineeringContextEvidence) -> int:
    if (
        item.source_type is ContextSourceType.KNOWLEDGE_GATEWAY
        and item.verification_status is ContextVerificationStatus.VERIFIED
    ):
        return 500
    if (
        item.source_type is ContextSourceType.ENGINEERING_MEMORY
        and item.verification_status is ContextVerificationStatus.APPROVED
    ):
        return 450
    if (
        item.source_type is ContextSourceType.KNOWLEDGE_GRAPH
        and item.verification_status
        in {ContextVerificationStatus.APPROVED, ContextVerificationStatus.VERIFIED}
    ):
        return 400
    if item.source_type is ContextSourceType.MULTIMODAL_OBSERVATION:
        return 300
    if item.verification_status is ContextVerificationStatus.UNVERIFIED:
        return 100
    return 200


def rank_evidence(
    values: tuple[EngineeringContextEvidence, ...],
    task: str,
    constraints: tuple[str, ...],
) -> tuple[EngineeringContextEvidence, ...]:
    query_tokens = tokens(" ".join((task, *constraints)))
    constraint_tokens = tokens(" ".join(constraints))

    def key(item: EngineeringContextEvidence) -> tuple[int, int, int, int, float, str]:
        item_tokens = tokens(f"{item.subject} {item.summary}")
        exact = int(item.subject.casefold() == task.casefold())
        constraint_overlap = len(item_tokens & constraint_tokens)
        overlap = len(item_tokens & query_tokens)
        confidence = item.confidence if item.confidence is not None else 0.0
        return (
            -evidence_tier(item),
            -exact,
            -constraint_overlap,
            -overlap,
            -confidence,
            item.evidence_id,
        )

    return tuple(sorted(values, key=key))


def rank_items(
    items: tuple[EngineeringContextEvidence, ...],
    query: str,
    graph: object | None = None,
    limit: int = 100,
) -> tuple[EngineeringContextEvidence, ...]:
    del graph
    return rank_evidence(items, query, ())[:limit]


__all__ = ("evidence_tier", "rank_evidence", "rank_items")
