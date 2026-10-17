from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_pcb import (
    EngineeringVerificationType,
    EngineeringEvidenceSource,
    ManufacturerCapabilityRule,
    ManufacturerProfile,
    ManufacturerRuleSeverity,
    PCBEngineeringVerificationService,
    PCBApplicability,
    PCBManufacturingMeasurements,
    ReadyForFabStatus,
    build_jlcpcb_dfm_inputs,
    build_jlcpcb_rigid_manufacturer_profile,
)
from embedded_copilot.verification_loop import VerificationResult, VerificationStatus

from .test_g7_final_closure import _inputs

SOURCE_FP = "sha256:" + "a" * 64


def _profile(project_id: str, *, source_fingerprint: str = SOURCE_FP):
    return build_jlcpcb_rigid_manufacturer_profile(
        project_id,
        source_fingerprint=source_fingerprint,
        retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
        source_revision="official-capabilities-2026-09-20",
        layer_count=2,
    )


def _measurements(state, **updates):
    values = {
        "project_id": state.project_id,
        "artifact_fingerprint": state.source_artifact_fingerprint,
        "layer_count": 2,
        "board_width_mm": 100.0,
        "board_height_mm": 80.0,
        "board_thickness_mm": 1.6,
        "outer_copper_weight": "1oz",
        "min_trace_width_mm": 0.10,
        "min_trace_spacing_mm": 0.10,
        "min_copper_to_edge_mm": 0.20,
        "vias_present": False,
    }
    values.update(updates)
    return PCBManufacturingMeasurements.create(**values)


def _closure_inputs(profile, measurements):
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=True)
    generated_evidence, generated_app, generated_checks = build_jlcpcb_dfm_inputs(profile, measurements)
    evidence = tuple(item for item in evidence if item.requirement_type != "DFM") + generated_evidence
    applicability = tuple(item for item in applicability if item.verifier is not EngineeringVerificationType.DFM) + (generated_app,)
    checks = tuple(item for item in checks if item.verifier is not EngineeringVerificationType.DFM) + generated_checks
    current_plan = PCBEngineeringVerificationService().extend_plan(
        plan,
        applicability=applicability,
        evidence=evidence,
    )
    refreshed_existing = []
    for result in existing:
        values = result.model_dump(mode="python", exclude={"fingerprint"})
        values["compared_fingerprints"] = (state.fingerprint, constraints.fingerprint, current_plan.fingerprint)
        refreshed_existing.append(VerificationResult.create(**values))
    return state, constraints, plan, tuple(refreshed_existing), evidence, applicability, checks


def test_jlcpcb_profile_is_user_selected_and_officially_provenanced() -> None:
    profile = _profile("project-g7b1")

    assert profile.manufacturer_id == "JLCPCB"
    assert profile.selection_provenance == "USER_SELECTED"
    assert profile.source_identity == "jlcpcb.com/capabilities/Capab"
    assert profile.source_revision == "official-capabilities-2026-09-20"
    assert profile.retrieved_at == datetime(2026, 9, 20, tzinfo=UTC)
    assert profile.rules
    assert profile.fingerprint != profile.source_fingerprint


def test_jlcpcb_dfm_passes_without_guessing_process_values() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(_inputs(require_dfm=True)[0]),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.PASS
    assert closure.ready_for_fab.status is ReadyForFabStatus.AWAITING_APPROVAL


def test_current_two_layer_one_oz_measurements_reverify_jlcpcb_dfm() -> None:
    state = _inputs(require_dfm=True)[0]
    profile = _profile(state.project_id)
    measurements = _measurements(
        state,
        board_width_mm=120.0,
        board_height_mm=90.0,
        min_trace_width_mm=0.25,
        min_trace_spacing_mm=0.85,
        min_copper_to_edge_mm=3.575,
    )

    evidence, applicability, checks = build_jlcpcb_dfm_inputs(profile, measurements)

    assert applicability.applicability is PCBApplicability.REQUIRED
    assert all(item.unknown_reason is None for item in checks)
    assert any(
        item.requirement_id == "dfm-process-copper-weight"
        and item.source is EngineeringEvidenceSource.USER_DECISION
        and item.value == "1oz"
        for item in evidence
    )


def test_jlcpcb_hard_limit_violation_blocks_without_mutating_pcb() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(_inputs(require_dfm=True)[0], min_trace_width_mm=0.05),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.FAIL
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED
    assert state.fingerprint == _inputs(require_dfm=True)[0].fingerprint


def test_jlcpcb_layer_specific_board_dimension_violation_blocks() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(_inputs(require_dfm=True)[0], board_width_mm=671.0),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.FAIL
    assert any(issue.location == "BOARD_DIMENSIONS" for issue in dfm.issues)
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_missing_copper_selection_is_unknown_and_user_decision_required() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(_inputs(require_dfm=True)[0], outer_copper_weight=None),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.UNKNOWN
    assert "dfm-process-copper-weight:USER_DECISION_REQUIRED:Select copper weight" in dfm.unknowns
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_missing_board_thickness_is_unknown_without_defaulting_to_1_6_mm() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(_inputs(require_dfm=True)[0], board_thickness_mm=None),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.UNKNOWN
    assert any("MISSING_CURRENT_PCB_MEASUREMENT" in unknown for unknown in dfm.unknowns)
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_jlcpcb_unsupported_blind_buried_via_is_hard_failure() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _closure_inputs(
        _profile("project-g7b1"),
        _measurements(
            _inputs(require_dfm=True)[0],
            vias_present=True,
            min_via_drill_mm=0.15,
            min_via_diameter_mm=0.25,
            blind_buried_via_present=True,
        ),
    )
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=existing,
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=_profile("project-g7b1"),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.FAIL
    assert any(issue.location == "BLIND_BURIED_VIA" for issue in dfm.issues)
    assert closure.ready_for_fab.status is ReadyForFabStatus.BLOCKED


def test_profile_layer_applicability_mismatch_is_rejected() -> None:
    state = _inputs(require_dfm=True)[0]
    with pytest.raises(ValueError, match="layer applicability mismatch"):
        build_jlcpcb_dfm_inputs(_profile("project-g7b1"), _measurements(state, layer_count=4))


def test_recommendation_does_not_become_hard_fail() -> None:
    state, constraints, plan, existing, evidence, applicability, checks = _inputs(require_dfm=True)
    rule = ManufacturerCapabilityRule.create(
        rule_id="recommended-trace-width",
        category="TRACE_WIDTH",
        measurement="min_trace_width_mm",
        expected_value=0.10,
        comparison="GE",
        unit="mm",
        severity=ManufacturerRuleSeverity.RECOMMENDED,
        source_semantics="RECOMMENDED",
        source_evidence_ref="knowledge://manufacturer/JLCPCB/rigid-capabilities",
        blocking=False,
    )
    profile = ManufacturerProfile.create(
        project_id=state.project_id,
        manufacturer_id="JLCPCB",
        process_id="JLCPCB-RIGID-PCB",
        evidence_refs=("knowledge://manufacturer/JLCPCB/rigid-capabilities",),
        source_fingerprint=SOURCE_FP,
        profile_id="jlcpcb-recommendation",
        pcb_type="RIGID",
        source_identity="jlcpcb.com/capabilities/Capab",
        source_revision="official-capabilities-2026-09-20",
        retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
        selection_provenance="USER_SELECTED",
        rules=(rule,),
    )
    generated_evidence, generated_app, generated_checks = build_jlcpcb_dfm_inputs(
        profile,
        _measurements(state, min_trace_width_mm=0.05),
    )
    evidence = tuple(item for item in evidence if item.requirement_type != "DFM") + generated_evidence
    applicability = tuple(item for item in applicability if item.verifier is not EngineeringVerificationType.DFM) + (generated_app,)
    checks = tuple(item for item in checks if item.verifier is not EngineeringVerificationType.DFM) + generated_checks
    current_plan = PCBEngineeringVerificationService().extend_plan(plan, applicability=applicability, evidence=evidence)
    refreshed_existing = []
    for result in existing:
        values = result.model_dump(mode="python", exclude={"fingerprint"})
        values["compared_fingerprints"] = (state.fingerprint, constraints.fingerprint, current_plan.fingerprint)
        refreshed_existing.append(VerificationResult.create(**values))
    closure = PCBEngineeringVerificationService().close(
        state,
        constraints,
        plan,
        existing_results=tuple(refreshed_existing),
        evidence=evidence,
        applicability=applicability,
        checks=checks,
        manufacturer_profile=profile,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    dfm = next(item for item in closure.summary.required_results if item.verifier == "DFM")

    assert dfm.status is VerificationStatus.WARNING
    assert not closure.summary.blocking_issues
    assert closure.ready_for_fab.status is ReadyForFabStatus.AWAITING_APPROVAL


def test_wrong_manufacturer_profile_is_rejected() -> None:
    profile = ManufacturerProfile.create(
        project_id="project-g7b1",
        manufacturer_id="OTHER",
        process_id="OTHER-RIGID-PCB",
        evidence_refs=("knowledge://manufacturer/other/rigid-capabilities",),
        source_fingerprint=SOURCE_FP,
    )
    measurements = PCBManufacturingMeasurements.create(
        project_id="project-g7b1",
        artifact_fingerprint="sha256:" + "b" * 64,
        layer_count=2,
    )

    with pytest.raises(ValueError, match="not applicable"):
        build_jlcpcb_dfm_inputs(profile, measurements)
