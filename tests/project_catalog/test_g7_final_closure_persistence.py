from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_pcb import (
    ApprovalDecision,
    EngineeringApplicabilityDecision,
    EngineeringVerificationType,
    HumanApprovalBinding,
    PCBApplicability,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime

from .test_g7a_persistence import _detail, _foundation, _runtime


def test_g7_final_summary_and_ready_for_fab_survive_restart(tmp_path) -> None:
    state, constraints, plan = _foundation()
    applicability = tuple(
        EngineeringApplicabilityDecision.create(
            verifier=kind,
            applicability=PCBApplicability.UNKNOWN,
            reason="authoritative applicability evidence is not yet available",
            evidence_refs=(),
        )
        for kind in EngineeringVerificationType
    )
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    stored = runtime.close_pcb_engineering_verification(
        "project-g7a-persist",
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=applicability,
        erc_status="UNKNOWN",
        drc_status="UNKNOWN",
        hw_fw_consistency_status="UNKNOWN",
    )

    approval = HumanApprovalBinding.create(
        approval_id="approval-g7-persist",
        reviewer="reviewer-g7-persist",
        approved_at=datetime(2026, 9, 19, tzinfo=UTC),
        decision=ApprovalDecision.APPROVED,
        summary_fingerprint=stored.pcb_engineering_verification_summary.fingerprint,
        artifact_fingerprint=state.source_artifact_fingerprint,
        blocking_issue_count=len(stored.pcb_engineering_verification_summary.blocking_issues),
        blocking_unknown_count=len(stored.pcb_engineering_verification_summary.blocking_unknowns),
    )
    stored = runtime.close_pcb_engineering_verification(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
        applicability=applicability,
        approval=approval,
        erc_status="UNKNOWN",
        drc_status="UNKNOWN",
        hw_fw_consistency_status="UNKNOWN",
    )

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    assert restored.pcb_engineering_verification_summary is not None
    assert restored.pcb_ready_for_fab is not None
    assert restored.pcb_engineering_verification_summary.fingerprint == stored.pcb_engineering_verification_summary.fingerprint
    assert restored.pcb_ready_for_fab.fingerprint == stored.pcb_ready_for_fab.fingerprint
    assert restored.pcb_ready_for_fab.status.value == "BLOCKED"
    assert restored.pcb_ready_for_fab_approval is not None
    assert restored.pcb_ready_for_fab_approval.summary_fingerprint == restored.pcb_engineering_verification_summary.fingerprint


def test_g7_final_gate_is_removed_when_foundation_becomes_stale(tmp_path) -> None:
    state, constraints, plan = _foundation()
    applicability = tuple(
        EngineeringApplicabilityDecision.create(
            verifier=kind,
            applicability=PCBApplicability.UNKNOWN,
            reason="authoritative applicability evidence is not yet available",
            evidence_refs=(),
        )
        for kind in EngineeringVerificationType
    )
    runtime = _runtime(tmp_path)
    runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    current = runtime.close_pcb_engineering_verification(
        "project-g7a-persist",
        "conversation-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        applicability=applicability,
    )

    stale = runtime.persist_conversation_detail(
        current.model_copy(
            update={
                "pcb_engineering_state": None,
                "pcb_constraint_set": None,
                "pcb_verification_plan": None,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
    )

    assert stale.pcb_engineering_state is not None
    assert stale.pcb_engineering_state.status.value == "STALE"
    assert stale.pcb_engineering_verification_summary is None
    assert stale.pcb_ready_for_fab is None
