from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_pcb import (
    ApprovalDecision,
    BlockingUnknownCategory,
    EngineeringApplicabilityDecision,
    EngineeringCheck,
    EngineeringEvidenceRequirement,
    EngineeringEvidenceSource,
    EngineeringVerificationType,
    HumanApprovalBinding,
    PCBApplicability,
    PCBEngineeringVerificationService,
    PCBVerificationPlan,
    ReadyForFabStatus,
    mapping_state_fingerprint,
)
from embedded_copilot.verification_loop import VerificationResult, VerificationStatus

from .test_g7b1_decoupling import _state

FP = "sha256:" + "a" * 64


def _evidence(requirement_id: str, kind: EngineeringVerificationType) -> EngineeringEvidenceRequirement:
    source = EngineeringEvidenceSource.DATASHEET if kind is not EngineeringVerificationType.DFM else EngineeringEvidenceSource.MANUFACTURER_PROFILE
    return EngineeringEvidenceRequirement.create(
        requirement_id=requirement_id,
        project_id="project-g7b1",
        scope="PCB",
        affected_objects=("U1",),
        requirement_type=kind.value,
        rule="authoritative engineering rule",
        unit="V" if kind is EngineeringVerificationType.ELECTRICAL_RATING else None,
        comparison="LE" if kind is EngineeringVerificationType.ELECTRICAL_RATING else None,
        source=source,
        source_ref=f"knowledge://g7/{requirement_id}",
        evidence_locator=f"knowledge://g7/{requirement_id}/locator",
        confidence=1.0,
        machine_evaluable=True,
        source_fingerprint=FP,
    )


def _app(kind: EngineeringVerificationType, status: PCBApplicability) -> EngineeringApplicabilityDecision:
    return EngineeringApplicabilityDecision.create(
        verifier=kind,
        applicability=status,
        reason=f"explicit project evidence for {kind.value}",
        evidence_refs=(f"app-{kind.value.lower()}",),
    )


def _check(check_id: str, kind: EngineeringVerificationType, requirement_id: str, actual, expected, comparison="EQ") -> EngineeringCheck:
    return EngineeringCheck.create(
        check_id=check_id,
        project_id="project-g7b1",
        verifier=kind,
        requirement_id=requirement_id,
        subject="U1",
        actual_value=actual,
        expected_value=expected,
        comparison=comparison,
        evidence_refs=(requirement_id,),
    )


def _base_plan(state, constraints, plan):
    names = tuple(sorted(set(plan.required_verifiers) | set(plan.unknown_applicability)))
    return PCBVerificationPlan.create(
        project_id=plan.project_id,
        revision=plan.revision,
        pcb_state_fingerprint=state.fingerprint,
        constraint_set_fingerprint=constraints.fingerprint,
        required_verifiers=names,
        optional_verifiers=plan.optional_verifiers,
        not_applicable_verifiers=plan.not_applicable_verifiers,
        unknown_applicability=(),
        evidence_refs=plan.evidence_refs,
    )


def _existing_results(state, constraints, plan):
    values = []
    for verifier in (*plan.required_verifiers, *plan.not_applicable_verifiers):
        status = VerificationStatus.NOT_APPLICABLE if verifier in plan.not_applicable_verifiers else VerificationStatus.PASS
        values.append(
            VerificationResult.create(
                verification_id=f"existing-{verifier}",
                project_id=state.project_id,
                target_id="pcb-engineering",
                status=status,
                attempt=0,
                compared_fingerprints=(state.fingerprint, constraints.fingerprint, plan.fingerprint),
                evidence_refs=(),
                summary=f"existing {verifier}",
                verifier=verifier,
                scope="PCB",
                applicability="NOT_APPLICABLE" if status is VerificationStatus.NOT_APPLICABLE else "REQUIRED",
                revision=state.fingerprint,
            )
        )
    return tuple(values)


def _inputs(*, require_dfm: bool = True):
    state, constraints, plan = _state()
    plan = _base_plan(state, constraints, plan)
    evidence = tuple(
        _evidence(f"req-{kind.value.lower()}", kind)
        for kind in EngineeringVerificationType
        if kind not in {EngineeringVerificationType.POWER_BUDGET, EngineeringVerificationType.DERATING, EngineeringVerificationType.THERMAL, EngineeringVerificationType.MECHANICAL}
    )
    applicability = (
        _app(EngineeringVerificationType.DATASHEET_COMPLIANCE, PCBApplicability.REQUIRED),
        _app(EngineeringVerificationType.ELECTRICAL_RATING, PCBApplicability.REQUIRED),
        _app(EngineeringVerificationType.POWER_BUDGET, PCBApplicability.NOT_APPLICABLE),
        _app(EngineeringVerificationType.DERATING, PCBApplicability.NOT_APPLICABLE),
        _app(EngineeringVerificationType.THERMAL, PCBApplicability.NOT_APPLICABLE),
        _app(EngineeringVerificationType.MECHANICAL, PCBApplicability.NOT_APPLICABLE),
        _app(EngineeringVerificationType.DFM, PCBApplicability.REQUIRED if require_dfm else PCBApplicability.NOT_APPLICABLE),
        _app(EngineeringVerificationType.CRITICAL_DFT, PCBApplicability.REQUIRED),
    )
    checks = (
        _check("check-datasheet", EngineeringVerificationType.DATASHEET_COMPLIANCE, "req-datasheet_compliance", True, True),
        _check("check-voltage", EngineeringVerificationType.ELECTRICAL_RATING, "req-electrical_rating", 3.3, 3.6, "LE"),
        _check("check-dft", EngineeringVerificationType.CRITICAL_DFT, "req-critical_dft", True, True),
    )
    if require_dfm:
        evidence = evidence + (_evidence("req-dfm", EngineeringVerificationType.DFM),)
    else:
        checks = checks + (_check("check-dfm", EngineeringVerificationType.DFM, "req-dfm", 0.2, 0.15, "GE"),)
    current_plan = PCBEngineeringVerificationService().extend_plan(
        plan,
        applicability=applicability,
        evidence=evidence,
    )
    current_results = []
    for result in _existing_results(state, constraints, plan):
        values = result.model_dump(mode="python", exclude={"fingerprint"})
        values["compared_fingerprints"] = (state.fingerprint, constraints.fingerprint, current_plan.fingerprint)
        current_results.append(VerificationResult.create(**values))
    return state, constraints, plan, tuple(current_results), evidence, applicability, checks


def test_plan_extension_and_missing_profile_are_typed_and_fail_closed() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=True)
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    assert closure.verification_plan.engineering_required_verifiers
    assert closure.summary.required_results
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")
    assert dfm.status is VerificationStatus.UNKNOWN
    assert "MISSING_MANUFACTURER_PROFILE" in dfm.unknowns
    assert BlockingUnknownCategory.ENGINEERING_EVIDENCE_GAP in closure.summary.blocking_unknown_categories
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED
    assert closure.ready_for_fab.blocking_unknown_count > 0


def test_all_required_results_without_approval_await_human_approval() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=False)
    service = PCBEngineeringVerificationService()
    closure = service.close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    assert closure.ready_for_fab.status is ReadyForFabStatus.AWAITING_APPROVAL
    assert not closure.summary.blocking_issues
    assert not closure.summary.blocking_unknowns

    approval = HumanApprovalBinding.create(
        approval_id="approval-g7-final",
        reviewer="reviewer",
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        decision=ApprovalDecision.APPROVED,
        project_id=state.project_id,
        conversation_id="conversation-g7-final",
        summary_fingerprint=closure.summary.fingerprint,
        artifact_fingerprint=state.source_artifact_fingerprint,
        ready_for_fab_target_fingerprint=closure.ready_for_fab.fingerprint,
        selected_by="USER",
        provenance="EXPLICIT_USER_APPROVAL",
        blocking_issue_count=0,
        blocking_unknown_count=0,
    )
    approved = service.close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        approval=approval,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    assert approved.ready_for_fab.status is ReadyForFabStatus.PASS
    assert approved.ready_for_fab.approval_valid
    assert approved.approval == approval


def test_ready_for_fab_approval_target_does_not_self_stale_after_pass() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=False)
    service = PCBEngineeringVerificationService()
    pending = service.close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    approval = HumanApprovalBinding.create(
        approval_id="approval-no-self-stale",
        reviewer="reviewer",
        approved_at=datetime(2026, 9, 20, tzinfo=UTC),
        decision=ApprovalDecision.APPROVED,
        project_id=state.project_id,
        conversation_id="conversation-g7-final",
        summary_fingerprint=pending.summary.fingerprint,
        artifact_fingerprint=state.source_artifact_fingerprint,
        ready_for_fab_target_fingerprint=pending.ready_for_fab.fingerprint,
        selected_by="USER",
        provenance="EXPLICIT_USER_APPROVAL",
        blocking_issue_count=0,
        blocking_unknown_count=0,
    )

    approved = service.close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        approval=approval,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )

    assert approved.ready_for_fab.status is ReadyForFabStatus.PASS
    assert approved.ready_for_fab.fingerprint != approval.ready_for_fab_target_fingerprint
    assert approved.approval == approval


def test_required_electrical_violation_is_a_blocking_unified_issue() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=False)
    bad_checks = tuple(
        check.model_copy(update={"actual_value": 5.0, "fingerprint": "sha256:" + "0" * 64})
        if check.check_id == "check-voltage" else check
        for check in checks
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=bad_checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    electrical = next(item for item in closure.summary.required_results if item.verifier == "ELECTRICAL_RATING")
    assert electrical.status is VerificationStatus.FAIL
    assert closure.summary.blocking_issues
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_accepted_risk_cannot_promote_required_unknown() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=True)
    with pytest.raises(ValueError, match="accepted risk"):
        PCBEngineeringVerificationService().close(
            state,
            constraints,
            plan,
            existing_results=existing,
            evidence=evidence,
            applicability=applicability,
            checks=checks,
            accepted_risks=(
                {
                    "risk_id": "risk-dfm",
                    "source_verification": "DFM",
                    "issue_or_unknown": "DFM:MISSING_MANUFACTURER_PROFILE",
                    "reason": "temporary acceptance",
                    "decision": "ACCEPT",
                    "actor": "reviewer",
                    "timestamp": datetime(2026, 9, 19, tzinfo=UTC),
                    "affected_objects": ("pcb-engineering",),
                },
            ),
        )


def test_old_physics_result_is_blocking_unknown_even_without_stale_flag() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=False)
    old = existing[0]
    old_values = old.model_dump(mode="python", exclude={"fingerprint"})
    old_values["compared_fingerprints"] = (state.fingerprint, constraints.fingerprint, plan.fingerprint)
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=(VerificationResult.create(**old_values), *existing[1:]),
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    assert any("STALE_EXISTING_G7_RESULT" in item for item in closure.summary.blocking_unknowns)
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_mapping_result_uses_owned_lineage_after_plan_extension() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=False)
    mapping_result = VerificationResult.create(
        verification_id="mapping-current-lineage",
        project_id=state.project_id,
        target_id="pcb-engineering",
        status=VerificationStatus.PASS,
        attempt=1,
        compared_fingerprints=(
            mapping_state_fingerprint(state),
            state.source_artifact_fingerprint,
            state.design_truth_fingerprint,
        ),
        evidence_refs=(),
        summary="current mapping result",
        verifier="FootprintPinConsistencyVerifier",
        scope="PCB",
        applicability="REQUIRED",
        revision=state.fingerprint,
    )
    prior_without_mapping = tuple(
        item for item in existing
        if item.verifier != "FootprintPinConsistencyVerifier"
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=(mapping_result, *prior_without_mapping),
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    mapping = next(
        item for item in closure.summary.required_results
        if item.verifier == "FootprintPinConsistencyVerifier"
    )
    assert mapping.status is VerificationStatus.PASS
    assert "STALE_EXISTING_G7_RESULT" not in closure.summary.blocking_unknowns
