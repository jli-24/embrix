from .models import EDAAnalysisResult, PCBDesignProposal


class StaticEDAProposalAdapter:
    """Offline proposal source; it never creates executable change sets."""

    __slots__ = ("_proposals",)

    def __init__(self, proposals: tuple[PCBDesignProposal, ...]) -> None:
        self._proposals = proposals

    def propose(self, result: EDAAnalysisResult) -> tuple[PCBDesignProposal, ...]:
        return tuple(
            item
            for item in self._proposals
            if item.analysis_fingerprint == result.fingerprint
        )


__all__ = ("StaticEDAProposalAdapter",)
