from __future__ import annotations

import copy
import hashlib

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_qa import EngineeringAnswer

from .contracts import EvidenceAudit, ReviewSourceType, ReviewTask, content_fingerprint
from .exceptions import GovernanceRejected


def build_evidence_audits(answer: EngineeringAnswer, snapshot: EngineeringContextSnapshot, review: ReviewTask) -> tuple[EvidenceAudit, ...]:
    checked_answer = EngineeringAnswer.model_validate(copy.deepcopy(answer.model_dump(mode="python")))
    checked_snapshot = EngineeringContextSnapshot.model_validate(copy.deepcopy(snapshot.model_dump(mode="python")))
    if (
        review.project_id != checked_snapshot.project_id
        or review.source_type is not ReviewSourceType.ENGINEERING_ANSWER
        or review.source_id != checked_answer.answer_id
        or review.content_fingerprint != content_fingerprint(checked_answer.content)
    ):
        raise GovernanceRejected()
    evidence = (*checked_snapshot.memory_evidence, *checked_snapshot.graph_evidence, *checked_snapshot.knowledge_evidence, *checked_snapshot.multimodal_evidence)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    if tuple(sorted(checked_answer.evidence_refs)) != tuple(sorted(evidence_by_id)):
        raise GovernanceRejected()
    provenance = {(item.source_id, item.source_reference, item.source_fingerprint) for item in checked_snapshot.provenance}
    if any((item.evidence_id, item.source_reference, item.source_fingerprint) not in provenance for item in evidence):
        raise GovernanceRejected()
    audits = []
    for evidence_id in review.evidence_refs:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            raise GovernanceRejected()
        audit_id = "audit-" + hashlib.sha256(f"{review.review_id}:{item.evidence_id}".encode()).hexdigest()
        audits.append(EvidenceAudit.create(
            audit_id=audit_id, review_id=review.review_id, project_id=checked_snapshot.project_id,
            evidence_id=item.evidence_id, source_reference=item.source_reference,
            source_fingerprint=item.source_fingerprint, confidence=item.confidence,
            verification_status=item.verification_status.value, input_fingerprint=checked_snapshot.fingerprint,
        ))
    return tuple(audits)


class EvidenceAuditService:
    def build(self, answer: EngineeringAnswer, snapshot: EngineeringContextSnapshot, review: ReviewTask) -> tuple[EvidenceAudit, ...]:
        return build_evidence_audits(answer, snapshot, review)


__all__ = ("EvidenceAuditService", "build_evidence_audits")
