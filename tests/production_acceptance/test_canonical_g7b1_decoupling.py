from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from embedded_copilot.engineering_pcb import (
    DecouplingPreventEvaluator,
    DecouplingVerifier,
    PCBConstraint,
    PCBConstraintCompiler,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBConstraintSource,
    PCBConstraintType,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBPlacementCandidate,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.verification_loop import VerificationStatus
from scripts.bootstrap_canonical_golden_project import REQUESTER, TENANT_ID, bootstrap
from tests.production_acceptance.kicad_toolchain import (
    discover_test_kicad_cli,
    discover_test_symbol_root,
)

ZERO = "sha256:" + "0" * 64


def _distance_constraints(
    state,
    constraint_set,
    *,
    evidence: PCBFoundationEvidence,
    threshold: float,
):
    base = next(
        item
        for item in constraint_set.constraints
        if item.constraint_type is PCBConstraintType.DECOUPLING
    )
    rule = PCBConstraint.create(
        **{
            **base.model_dump(mode="python", exclude={"fingerprint"}),
            "source": PCBConstraintSource.DATASHEET,
            "evidence_refs": (evidence,),
            "source_fingerprint": evidence.fingerprint,
            "evaluation_method": "MAX_PAD_DISTANCE",
            "threshold": threshold,
            "threshold_unit": "mm",
            "comparison": "<=",
            "enforcement_mode": PCBConstraintEnforcementMode.HARD,
        }
    )
    return PCBConstraintSet.create(
        project_id=constraint_set.project_id,
        revision=constraint_set.revision,
        state_fingerprint=state.fingerprint,
        constraints=tuple(
            rule if item.constraint_id == base.constraint_id else item
            for item in constraint_set.constraints
        ),
        evidence_refs=tuple(dict.fromkeys((*constraint_set.evidence_refs, evidence))),
    )


def _with_pad_net(state, pad_id: str, net_id: str):
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    for component in values["components"]:
        changed = False
        pads = []
        for pad in component["pads"]:
            if pad["pad_id"] == pad_id:
                pad = {**pad, "net_id": net_id, "fingerprint": ZERO}
                changed = True
            pads.append(pad)
        if changed:
            component["pads"] = tuple(pads)
            component["fingerprint"] = ZERO
    return type(state).create(**values)


def _without_relation_evidence(state):
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["evidence_refs"] = ()
    values["decoupling_relations"] = tuple(
        {**relation, "evidence_refs": (), "fingerprint": ZERO}
        for relation in values["decoupling_relations"]
    )
    return type(state).create(**values)


def _artifact_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_g7b1_decoupling_uses_real_g5_artifact_and_preserves_restart_stale_boundaries(
    tmp_path: Path,
) -> None:
    executable = discover_test_kicad_cli()
    symbol_dir = None if executable is None else discover_test_symbol_root(executable)
    if executable is None or symbol_dir is None:
        pytest.skip("real KiCad CLI, footprint library, and symbol library are required for the G7-B1 acceptance test")

    report = bootstrap(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        executable=str(executable),
        stop_after_g5=True,
    )
    assert report["g5_parse_validation"] == "PARSE_VERIFIED"
    assert report["g5_production_status"] == "DRC_PASS"

    runtime = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    project_id = report["project_id"]
    conversation_id = report["conversation_id"]
    projected = runtime.refresh_pcb_engineering_state(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    state = projected.pcb_engineering_state
    constraint_set = projected.pcb_constraint_set
    plan = projected.pcb_verification_plan
    assert state is not None and constraint_set is not None and plan is not None
    assert state.decoupling_relations
    assert all(
        pad.x_mm is not None and pad.y_mm is not None and pad.geometry_source == "PAD"
        for component in state.components
        for pad in component.pads
        if any(
            pad.pad_id in {
                relation.load_pad_id,
                relation.capacitor_power_pad_id,
                relation.capacitor_return_pad_id,
            }
            for relation in state.decoupling_relations
        )
    )

    artifact_path = (
        tmp_path
        / "projects"
        / str(report["project_relative_path"])
        / projected.pcb_production.generation_receipt.artifact_path
    )
    before_hash = _artifact_hash(artifact_path)
    assert before_hash == report["pcb_sha256"]

    verified = runtime.verify_pcb_decoupling(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    result = verified.pcb_decoupling_verification
    assert result is not None
    assert result.verifier == "DECOUPLING"
    assert result.scope == "PCB"
    assert result.status is VerificationStatus.UNKNOWN
    assert result.measurement_refs
    assert {item.measurement_id for item in verified.pcb_decoupling_measurements} == set(result.measurement_refs)

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert restored.pcb_decoupling_verification is not None
    assert restored.pcb_decoupling_verification.fingerprint == result.fingerprint
    assert restored.pcb_decoupling_measurements

    # The persisted qualitative constraint has no invented distance threshold;
    # Prevent therefore remains UNKNOWN and cannot mutate the canonical file.
    prevent = restarted.evaluate_pcb_decoupling_prevent(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
        candidate=PCBPlacementCandidate.create(
            candidate_id="canonical-current-placement",
            base_state_fingerprint=state.fingerprint,
            placements=state.placement,
        ),
    )
    assert prevent.status.value == "UNKNOWN"
    assert _artifact_hash(artifact_path) == before_hash

    datasheet = PCBFoundationEvidence.create(
        evidence_id="canonical-g7b1-datasheet-rule",
        project_id=project_id,
        source_type=PCBFoundationEvidenceSource.DATASHEET,
        source_id="stm32f103c8t6-datasheet",
        source_fingerprint=projected.design_truth.fingerprint,
        claim="Canonical datasheet evidence used to exercise the approved pad-distance rule.",
        locator="datasheet://stm32f103c8.pdf",
    )
    valid_constraints = _distance_constraints(
        state,
        constraint_set,
        evidence=datasheet,
        threshold=1000.0,
    )
    valid_plan = PCBConstraintCompiler().build_plan(state, valid_constraints)
    valid = DecouplingVerifier().verify(state, valid_constraints, valid_plan)
    assert valid.status is VerificationStatus.PASS

    violation_constraints = _distance_constraints(
        state,
        constraint_set,
        evidence=datasheet,
        threshold=0.001,
    )
    violation_plan = PCBConstraintCompiler().build_plan(state, violation_constraints)
    violation = DecouplingVerifier().verify(state, violation_constraints, violation_plan)
    assert violation.status is VerificationStatus.FAIL
    assert any(issue.category == "DECOUPLING:PAD_DISTANCE" for issue in violation.issues)
    violation_prevent = DecouplingPreventEvaluator().evaluate(
        state,
        violation_constraints,
        PCBPlacementCandidate.create(
            candidate_id="canonical-violating-placement",
            base_state_fingerprint=state.fingerprint,
            placements=state.placement,
        ),
        violation_plan,
    )
    assert violation_prevent.status.value == "BLOCK"

    relation = state.decoupling_relations[0]
    wrong_state = _with_pad_net(state, relation.capacitor_power_pad_id, "net-intentionally-wrong")
    wrong_constraints = _distance_constraints(
        wrong_state,
        constraint_set,
        evidence=datasheet,
        threshold=1000.0,
    )
    wrong_plan = PCBConstraintCompiler().build_plan(wrong_state, wrong_constraints)
    wrong = DecouplingVerifier().verify(wrong_state, wrong_constraints, wrong_plan)
    assert wrong.status is VerificationStatus.FAIL
    assert any(issue.category == "DECOUPLING:SAME_POWER_DOMAIN" for issue in wrong.issues)

    missing_state = _without_relation_evidence(state)
    missing_constraints = PCBConstraintCompiler().compile(missing_state)
    missing_plan = PCBConstraintCompiler().build_plan(missing_state, missing_constraints)
    missing = DecouplingVerifier().verify(missing_state, missing_constraints, missing_plan)
    assert missing.status is VerificationStatus.UNKNOWN
    assert "MISSING_DECOUPLING_EVIDENCE" in missing.unknowns

    stale = restarted.refresh_pcb_engineering_state(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
        datasheet_evidence=(datasheet,),
    )
    assert stale.pcb_decoupling_verification is not None
    assert stale.pcb_decoupling_verification.stale is True
    assert stale.pcb_decoupling_verification.stale_reason
    restored_stale = restarted.conversation(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert restored_stale.pcb_decoupling_verification is not None
    assert restored_stale.pcb_decoupling_verification.stale is True
    assert _artifact_hash(artifact_path) == before_hash
