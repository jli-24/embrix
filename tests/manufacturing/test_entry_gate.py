from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_pcb import (
    ApprovalDecision,
    HumanApprovalBinding,
    build_ready_for_fab_approval_target,
)
from embedded_copilot.manufacturing import CanonicalManufacturingEntryGate, ManufacturingEntryGateRejected
from embedded_copilot.verification_loop import VerificationStatus


FP = "sha256:" + "a" * 64
STATE_FP = "sha256:" + "b" * 64
SUMMARY_FP = "sha256:" + "c" * 64
READY_FP = "sha256:" + "d" * 64
PLAN_FP = "sha256:" + "e" * 64
DFM_FP = "sha256:" + "f" * 64


def _detail():
    profile = SimpleNamespace(fingerprint=FP, manufacturer_id="JLCPCB")
    schematic_fp = "sha256:" + "9" * 64
    source_binding = SimpleNamespace(
        status="BOUND",
        project_id="project",
        conversation_id="conversation",
        schematic_artifact_fingerprint=schematic_fp,
    )
    state = SimpleNamespace(
        fingerprint=STATE_FP,
        pcb_artifact_fingerprint=FP,
        source_artifact_fingerprint=FP,
        intent_source_binding=source_binding,
        element_bindings=(SimpleNamespace(status="BOUND"),),
    )
    summary = SimpleNamespace(
        project_id="project",
        fingerprint=SUMMARY_FP,
        pcb_state_fingerprint=STATE_FP,
        verification_plan_fingerprint=PLAN_FP,
        required_results=(SimpleNamespace(verifier="DFM", fingerprint=DFM_FP, status=VerificationStatus.PASS, compared_fingerprints=(STATE_FP, FP)),),
        optional_results=(),
        blocking_issues=(),
        blocking_unknowns=(),
    )
    approval_target_fp = build_ready_for_fab_approval_target(summary, FP).fingerprint
    ready = SimpleNamespace(
        fingerprint=READY_FP,
        status="PASS",
        approval_valid=True,
        summary_fingerprint=SUMMARY_FP,
        artifact_fingerprint=FP,
    )
    approval = HumanApprovalBinding.create(
        approval_id="approval-current",
        reviewer="reviewer",
        approved_at=datetime(2026, 9, 20, tzinfo=UTC),
        decision=ApprovalDecision.APPROVED,
        project_id="project",
        conversation_id="conversation",
        summary_fingerprint=SUMMARY_FP,
        artifact_fingerprint=FP,
        state_fingerprint=STATE_FP,
        manufacturer_profile_fingerprint=FP,
        dfm_fingerprint=DFM_FP,
        ready_for_fab_target_fingerprint=approval_target_fp,
        selected_by="USER",
        provenance="EXPLICIT_USER_APPROVAL",
        blocking_issue_count=0,
        blocking_unknown_count=0,
    )
    return SimpleNamespace(
        project_id="project",
        pcb_production=SimpleNamespace(
            request=SimpleNamespace(schematic_artifact_fingerprint=schematic_fp),
            generation_receipt=SimpleNamespace(
                artifact_fingerprint=FP,
                schematic_fingerprint=schematic_fp,
            ),
        ),
        pcb_engineering_state=state,
        pcb_constraint_set=SimpleNamespace(status="CURRENT", fingerprint="sha256:" + "1" * 64),
        pcb_verification_plan=SimpleNamespace(status="CURRENT", fingerprint=PLAN_FP),
        pcb_engineering_verification_summary=summary,
        pcb_ready_for_fab=ready,
        pcb_ready_for_fab_approval=approval,
        pcb_manufacturer_profile=profile,
        pcb_manufacturing_measurements=SimpleNamespace(artifact_fingerprint=FP),
        ready_for_fab_approval_target_fingerprint=approval_target_fp,
    )


class _Catalog:
    def __init__(self, detail):
        self.detail = detail
        self.calls = []

    def conversation(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.detail


def test_current_project_catalog_authority_allows_g8_entry() -> None:
    catalog = _Catalog(_detail())

    receipt = CanonicalManufacturingEntryGate(catalog).assert_current(
        tenant_id="tenant",
        project_id="project",
        conversation_id="conversation",
        requester="reviewer",
        pcb_artifact_fingerprint=FP,
    )

    assert receipt.pcb_artifact_fingerprint == FP
    assert receipt.pcb_state_fingerprint == STATE_FP
    assert receipt.summary_fingerprint == SUMMARY_FP
    assert catalog.calls


def test_schematic_fingerprint_cannot_be_used_as_current_pcb_artifact() -> None:
    detail = _detail()
    schematic_fp = detail.pcb_production.request.schematic_artifact_fingerprint

    with pytest.raises(ManufacturingEntryGateRejected, match="PCB artifact fingerprint"):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=schematic_fp,
        )


def test_pcb_fingerprint_cannot_be_used_as_schematic_source_binding() -> None:
    detail = _detail()
    detail.pcb_engineering_state.intent_source_binding.schematic_artifact_fingerprint = FP

    with pytest.raises(ManufacturingEntryGateRejected, match="source lineage"):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=FP,
        )


def test_stale_pcb_generation_binding_blocks_g8_entry() -> None:
    detail = _detail()
    detail.pcb_production.generation_receipt.artifact_fingerprint = "sha256:" + "8" * 64

    with pytest.raises(ManufacturingEntryGateRejected, match="artifact binding is stale"):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=FP,
        )


@pytest.mark.parametrize(
    "mutate, message",
    (
        (lambda detail: setattr(detail.pcb_ready_for_fab, "status", "BLOCKED"), "READY_FOR_FAB"),
        (lambda detail: setattr(detail.pcb_ready_for_fab, "status", "AWAITING_APPROVAL"), "READY_FOR_FAB"),
        (lambda detail: setattr(detail, "pcb_ready_for_fab_approval", None), "approval"),
        (lambda detail: setattr(detail, "pcb_ready_for_fab_approval", detail.pcb_ready_for_fab_approval.model_copy(update={"artifact_fingerprint": "sha256:" + "e" * 64})), "artifact"),
        (lambda detail: setattr(detail, "pcb_ready_for_fab_approval", detail.pcb_ready_for_fab_approval.model_copy(update={"summary_fingerprint": "sha256:" + "f" * 64})), "summary"),
    ),
)
def test_stale_or_missing_current_approval_blocks_before_g8(mutate, message) -> None:
    detail = _detail()
    mutate(detail)

    with pytest.raises(ManufacturingEntryGateRejected, match=message):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=FP,
        )


def test_gate_requires_state_and_profile_fingerprint_binding() -> None:
    detail = _detail()
    detail.pcb_ready_for_fab_approval = detail.pcb_ready_for_fab_approval.model_copy(update={"state_fingerprint": None})

    with pytest.raises(ManufacturingEntryGateRejected, match="PCB state"):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=FP,
        )


@pytest.mark.parametrize(
    "update, message",
    (
        ({"project_id": "wrong-project"}, "project"),
        ({"conversation_id": "wrong-conversation"}, "conversation"),
        ({"dfm_fingerprint": "sha256:" + "2" * 64}, "DFM"),
        ({"ready_for_fab_target_fingerprint": "sha256:" + "3" * 64}, "target"),
        ({"selected_by": None}, "selected"),
        ({"provenance": None}, "provenance"),
    ),
)
def test_g8_entry_requires_explicit_current_approval_bundle(update, message) -> None:
    detail = _detail()
    detail.pcb_ready_for_fab_approval = detail.pcb_ready_for_fab_approval.model_copy(update=update)

    with pytest.raises(ManufacturingEntryGateRejected, match=message):
        CanonicalManufacturingEntryGate(_Catalog(detail)).assert_current(
            tenant_id="tenant",
            project_id="project",
            conversation_id="conversation",
            requester="reviewer",
            pcb_artifact_fingerprint=FP,
        )
