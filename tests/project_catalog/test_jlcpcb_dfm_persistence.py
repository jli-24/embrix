from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_pcb import (
    EngineeringApplicabilityDecision,
    EngineeringVerificationType,
    PCBApplicability,
    PCBManufacturingMeasurements,
    build_jlcpcb_rigid_manufacturer_profile,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.verification_loop import VerificationResult, VerificationStatus

from .test_g7a_persistence import _detail, _foundation, _runtime


def _applicability() -> tuple[EngineeringApplicabilityDecision, ...]:
    return tuple(
        EngineeringApplicabilityDecision.create(
            verifier=kind,
            applicability=PCBApplicability.UNKNOWN,
            reason="authoritative applicability is supplied by the current verification inputs",
            evidence_refs=(),
        )
        for kind in EngineeringVerificationType
    )


def _profile(project_id: str, source_fingerprint: str = "sha256:" + "a" * 64):
    return build_jlcpcb_rigid_manufacturer_profile(
        project_id,
        source_fingerprint=source_fingerprint,
        retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
        source_revision="official-capabilities-2026-09-20",
        layer_count=1,
    )


def _measurements(state):
    return PCBManufacturingMeasurements.create(
        project_id=state.project_id,
        artifact_fingerprint=state.source_artifact_fingerprint,
        layer_count=1,
        board_width_mm=100.0,
        board_height_mm=80.0,
        board_thickness_mm=1.6,
        outer_copper_weight="1oz",
        min_trace_width_mm=0.10,
        min_trace_spacing_mm=0.10,
        min_copper_to_edge_mm=0.20,
        vias_present=False,
    )


def test_jlcpcb_profile_dfm_summary_and_gate_survive_two_restarts(tmp_path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    profile = _profile(state.project_id)
    measurements = _measurements(state)

    stored = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=profile,
        manufacturer_measurements=measurements,
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    assert stored.pcb_manufacturer_profile is not None
    assert stored.pcb_manufacturer_profile.fingerprint == profile.fingerprint
    assert stored.pcb_manufacturing_measurements is not None
    assert stored.pcb_manufacturing_measurements.outer_copper_weight == "1oz"
    assert stored.pcb_engineering_verification_summary is not None
    dfm = next(item for item in stored.pcb_engineering_verification_summary.required_results if item.verifier == "DFM")
    assert dfm.status.value == "PASS"
    assert profile.fingerprint in dfm.compared_fingerprints

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
    )
    assert restored.pcb_manufacturer_profile is not None
    assert restored.pcb_manufacturer_profile.fingerprint == profile.fingerprint
    assert restored.pcb_manufacturing_measurements is not None
    assert restored.pcb_manufacturing_measurements.outer_copper_weight == "1oz"
    assert restored.pcb_engineering_verification_summary.fingerprint == stored.pcb_engineering_verification_summary.fingerprint

    second_restart = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored_again = second_restart.conversation(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
    )
    assert restored_again.pcb_manufacturer_profile.fingerprint == profile.fingerprint
    assert restored_again.pcb_engineering_verification_summary.fingerprint == stored.pcb_engineering_verification_summary.fingerprint


def test_jlcpcb_source_change_rebuilds_dfm_dependency_and_summary(tmp_path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    first = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=_profile(state.project_id),
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    updated_profile = _profile(state.project_id, "sha256:" + "c" * 64)
    second = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=updated_profile,
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )

    assert second.pcb_manufacturer_profile.fingerprint == updated_profile.fingerprint
    assert second.pcb_engineering_verification_summary.fingerprint != first.pcb_engineering_verification_summary.fingerprint
    dfm = next(item for item in second.pcb_engineering_verification_summary.required_results if item.verifier == "DFM")
    assert updated_profile.fingerprint in dfm.compared_fingerprints
    assert first.pcb_manufacturer_profile.fingerprint not in dfm.compared_fingerprints


def test_jlcpcb_profile_source_change_marks_old_projection_stale(tmp_path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    first = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=_profile(state.project_id),
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    updated_profile = _profile(state.project_id, "sha256:" + "c" * 64)
    stale = runtime.persist_conversation_detail(
        first.model_copy(
            update={
                "pcb_manufacturer_profile": updated_profile,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
    )

    assert stale.pcb_manufacturer_profile.fingerprint == updated_profile.fingerprint
    assert stale.pcb_engineering_verification_summary is None
    assert stale.pcb_ready_for_fab is None


def test_jlcpcb_source_change_does_not_rerun_current_physics_results(tmp_path, monkeypatch) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    first = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=_profile(state.project_id),
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )
    physics_result = VerificationResult.create(
        verification_id="persisted-decoupling-result",
        project_id=state.project_id,
        target_id="pcb-engineering",
        status=VerificationStatus.PASS,
        attempt=1,
        compared_fingerprints=(state.fingerprint, constraints.fingerprint, first.pcb_verification_plan.fingerprint),
        evidence_refs=(),
        summary="persisted current physics result",
        verifier="DECOUPLING",
        scope="PCB",
        applicability="REQUIRED",
        revision=state.fingerprint,
    )
    runtime.persist_conversation_detail(
        first.model_copy(
            update={
                "pcb_decoupling_verification": physics_result,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
    )

    def unexpected_rerun(*args, **kwargs):
        raise AssertionError("DFM-only source change must not rerun B1-B5")

    monkeypatch.setattr(runtime, "verify_pcb_decoupling", unexpected_rerun)
    second = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=_profile(state.project_id, "sha256:" + "c" * 64),
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )

    assert second.pcb_decoupling_verification is not None
    assert second.pcb_decoupling_verification.status is VerificationStatus.PASS
    assert second.pcb_verification_plan.fingerprint in second.pcb_decoupling_verification.compared_fingerprints


def test_pcb_foundation_change_clears_dfm_summary_and_gate_but_keeps_profile(tmp_path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    profile = _profile(state.project_id)
    stored = runtime.close_pcb_engineering_verification(
        state.project_id,
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=_applicability(),
        manufacturer_profile=profile,
        manufacturer_measurements=_measurements(state),
        erc_status="PASS",
        drc_status="PASS",
        hw_fw_consistency_status="PASS",
    )

    incoming = stored.model_copy(
        update={
            "pcb_engineering_state": None,
            "pcb_constraint_set": None,
            "pcb_verification_plan": None,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    stale = runtime.persist_conversation_detail(incoming)

    assert stale.pcb_manufacturer_profile is not None
    assert stale.pcb_manufacturer_profile.fingerprint == profile.fingerprint
    assert stale.pcb_engineering_state is not None
    assert stale.pcb_engineering_state.status.value == "STALE"
    assert stale.pcb_engineering_verification_summary is None
    assert stale.pcb_ready_for_fab is None
