from __future__ import annotations

import hashlib

from .contracts import (
    ApprovalRecord,
    ApprovedCandidateProjection,
    ReviewDecision,
    ReviewTask,
)
from .exceptions import GovernanceRejected


def reviewed_task(review: ReviewTask, approval: ApprovalRecord) -> ReviewTask:
    if approval.review_id != review.review_id or approval.input_fingerprint != review.fingerprint:
        raise GovernanceRejected()
    if approval.timestamp < review.created_at:
        raise GovernanceRejected()
    status = {
        ReviewDecision.APPROVED: "APPROVED",
        ReviewDecision.REJECTED: "REJECTED",
        ReviewDecision.NEEDS_CHANGE: "NEEDS_CHANGE",
    }[approval.decision]
    material = review.model_dump(mode="python")
    material.pop("fingerprint")
    material["status"] = status
    return ReviewTask.create(**material)


def approved_candidate(review: ReviewTask, approval: ApprovalRecord) -> ApprovedCandidateProjection | None:
    if approval.decision is not ReviewDecision.APPROVED:
        return None
    candidate_id = "candidate-" + hashlib.sha256(f"{review.review_id}:{approval.fingerprint}".encode()).hexdigest()
    return ApprovedCandidateProjection.create(
        candidate_id=candidate_id, review_id=review.review_id, project_id=review.project_id,
        source_type=review.source_type, source_id=review.source_id,
        content_fingerprint=review.content_fingerprint, evidence_refs=review.evidence_refs,
        approval_id=approval.approval_id, approval_fingerprint=approval.fingerprint,
    )


__all__ = ("approved_candidate", "reviewed_task")
