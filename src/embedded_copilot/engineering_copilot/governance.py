from __future__ import annotations

from .contracts import ProposalReviewHandoff


class ProposalReviewAdapter:
    """Stateless projection adapter; it never retains the source proposal."""

    def project(
        self,
        proposal: object,
        *,
        source_type: str,
        source_id: str,
        governance_reference: str,
    ) -> ProposalReviewHandoff:
        project_id = getattr(proposal, "project_id", None)
        proposal_fingerprint = getattr(proposal, "fingerprint", None)
        evidence_refs = getattr(proposal, "evidence_refs", None)
        if not isinstance(project_id, str) or not isinstance(proposal_fingerprint, str):
            raise TypeError("proposal handoff source is invalid")
        if type(evidence_refs) is not tuple:
            raise TypeError("proposal handoff evidence is invalid")
        return proposal_handoff(
            source_type=source_type,
            source_id=source_id,
            project_id=project_id,
            proposal_fingerprint=proposal_fingerprint,
            evidence_refs=evidence_refs,
            governance_reference=governance_reference,
        )


def proposal_handoff(
    *,
    source_type: str,
    source_id: str,
    project_id: str,
    proposal_fingerprint: str,
    evidence_refs: tuple[str, ...] = (),
    governance_reference: str,
) -> ProposalReviewHandoff:
    return ProposalReviewHandoff.create(
        source_type=source_type,
        source_id=source_id,
        project_id=project_id,
        proposal_fingerprint=proposal_fingerprint,
        evidence_refs=evidence_refs,
        governance_reference=governance_reference,
    )
