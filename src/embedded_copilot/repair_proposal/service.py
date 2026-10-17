from __future__ import annotations

import copy

from embedded_copilot.engineering_copilot import ProposalReviewAdapter
from embedded_copilot.engineering_copilot.common import ident
from embedded_copilot.failure_analysis import FailureAnalysis, FailureCategory

from .contracts import RepairProposal, RepairTargetType, proposal_id
from .exceptions import RepairProposalRejected


class RepairProposalService:
    __slots__ = ("_handoff",)

    def __init__(self, *, handoff_adapter: ProposalReviewAdapter | None = None) -> None:
        self._handoff = handoff_adapter or ProposalReviewAdapter()

    def propose(
        self,
        analysis: FailureAnalysis,
        *,
        target_type: RepairTargetType,
        affected_component: str,
    ) -> RepairProposal:
        try:
            checked = FailureAnalysis.model_validate(
                copy.deepcopy(analysis.model_dump(mode="python"))
            )
            component = ident(affected_component, "affected_component")
            target = RepairTargetType(target_type)
        except (TypeError, ValueError) as error:
            raise RepairProposalRejected() from error
        summary = _summary(checked.category, target, component)
        return RepairProposal.create(
            proposal_id=proposal_id(checked.failure_id, checked.fingerprint, target),
            project_id=checked.project_id,
            failure_id=checked.failure_id,
            failure_analysis_fingerprint=checked.fingerprint,
            context_fingerprint=checked.context_fingerprint,
            target_type=target,
            change_summary=summary,
            affected_component=component,
            evidence_refs=checked.evidence_refs,
            confidence=min(checked.confidence, 0.7),
        )

    def handoff(
        self,
        proposal: RepairProposal,
        *,
        governance_reference: str,
    ):
        try:
            checked = RepairProposal.model_validate(
                copy.deepcopy(proposal.model_dump(mode="python"))
            )
            return self._handoff.project(
                checked,
                source_type="REPAIR_PROPOSAL",
                source_id=checked.proposal_id,
                governance_reference=governance_reference,
            )
        except (TypeError, ValueError) as error:
            raise RepairProposalRejected() from error


def _summary(
    category: FailureCategory, target: RepairTargetType, component: str
) -> str:
    return f"Review {component} for {target.value} in response to {category.value}."


__all__ = ("RepairProposalService",)
