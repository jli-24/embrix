from __future__ import annotations

from embedded_copilot.failure_analysis import FailureAnalysis, FailureCategory
from embedded_copilot.repair_proposal import (
    RepairProposal,
    RepairProposalService,
    RepairTargetType,
)


def _analysis() -> FailureAnalysis:
    return FailureAnalysis.create(
        failure_id="failure-1",
        project_id="project-1",
        target_id="target-1",
        category=FailureCategory.BUILD_FAILED,
        observation_fingerprint="sha256:" + "a" * 64,
        context_fingerprint="sha256:" + "b" * 64,
        root_cause_candidate="Build evidence requires review.",
        evidence_refs=("evidence-1",),
        confidence=0.8,
        provenance=(),
        verification_status="VERIFIED",
    )


def test_deterministic_proposal_and_handoff_do_not_retain_patch_content() -> None:
    service = RepairProposalService()
    proposal = service.propose(
        _analysis(),
        target_type=RepairTargetType.CONFIGURATION_CHANGE,
        affected_component="build_config",
    )
    same = service.propose(
        _analysis(),
        target_type=RepairTargetType.CONFIGURATION_CHANGE,
        affected_component="build_config",
    )
    handoff = service.handoff(proposal, governance_reference="review-external-1")
    assert proposal.fingerprint == same.fingerprint
    assert handoff.proposal_fingerprint == proposal.fingerprint
    assert {"patch", "code", "command", "prompt"}.isdisjoint(
        RepairProposal.model_fields
    )
    assert {"change_summary", "affected_component", "evidence_refs"}.issubset(
        RepairProposal.model_fields
    )
