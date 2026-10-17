from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_copilot.contracts import ProposalReviewHandoff

from .contracts import RepairProposal


@runtime_checkable
class ProposalHandoffPort(Protocol):
    def project(
        self,
        proposal: RepairProposal,
        *,
        source_type: str,
        source_id: str,
        governance_reference: str,
    ) -> ProposalReviewHandoff: ...


__all__ = ("ProposalHandoffPort",)
