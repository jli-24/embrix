from __future__ import annotations

from embedded_copilot.verification_loop import (
    VerificationEvidenceReference,
    VerificationIssue,
    VerificationResult,
    VerificationStatus,
)


def test_verification_result_is_shared_across_pcb_and_execution_scopes() -> None:
    evidence = VerificationEvidenceReference.create(
        evidence_id="evidence-pcb-g7a",
        source_kind="pcb_artifact",
        source_fingerprint="sha256:" + "1" * 64,
        locator="artifact-pcb-g7a",
    )
    issue = VerificationIssue.create(
        issue_id="issue-pcb-g7a",
        severity="WARNING",
        category="RETURN_PATH",
        location="net:ctrl",
        affected_objects=("net-ctrl",),
        problem="Reference-plane continuity is not verified.",
        why="The current artifact does not contain enough plane evidence.",
        possible_effects=("increased return-path impedance",),
        evidence=(evidence.evidence_id,),
        recommendation="Review the reference layer and rerun verification.",
        reverification=("plane_continuity",),
    )
    result = VerificationResult.create(
        verification_id="verification-pcb-g7a",
        project_id="project-g7a",
        target_id="pcb-state-g7a",
        status=VerificationStatus.UNKNOWN,
        attempt=0,
        compared_fingerprints=("sha256:" + "2" * 64,),
        evidence_refs=(evidence.evidence_id,),
        summary="PCB verification is incomplete because required evidence is unavailable.",
        verifier="ReturnPathVerifier",
        scope="PCB",
        issues=(issue,),
        evidence=(evidence,),
        affected_objects=("net-ctrl",),
        confidence=0.25,
        assumptions=("The supplied PCB artifact is the current G5 output.",),
        unknowns=("plane_continuity",),
        recommendations=("Review the reference layer and rerun verification.",),
        revision="sha256:" + "3" * 64,
        applicability="UNKNOWN",
    )

    assert result.status is VerificationStatus.UNKNOWN
    assert result.verifier == "ReturnPathVerifier"
    assert result.issues[0].evidence == (evidence.evidence_id,)
    assert result.evidence[0].source_fingerprint == evidence.source_fingerprint
