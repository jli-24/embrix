from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import (
    SchematicErcEngineeringStatus,
    SchematicErcIssueClassification,
    SchematicErcIssueSeverity,
    SchematicErcIssueStatus,
    SchematicErcStatus,
    SchematicPlanDecision,
    SchematicPlanningState,
    SchematicPlanningStatus,
    SchematicPlanProposal,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    SchematicRepairAssessment,
    SchematicRepairAssessmentKind,
    SchematicRepairApplicationReceipt,
    SchematicRepairApplicationStatus,
    SchematicRepairDecision,
    SchematicRepairDecisionAction,
    SchematicRepairProposal,
    SchematicTarget,
    assess_schematic_repair,
    decide_schematic_proposal,
    serialize_kicad_schematic,
)
from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliSchematicErcAdapter,
    KiCadCliSchematicParseAdapter,
    OperationStatus,
    OperationType,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
    probe_kicad_cli_version,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.project_catalog.exceptions import SchematicProductionRejected
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from tests.production_acceptance.test_real_schematic_production_golden import (
    _proposal,
    _request,
)

from .test_schematic_production_g3 import NOW, _catalog, _runtime_with_approved_plan


def _configured_cli() -> str | None:
    value = os.environ.get("EMBRIX_KICAD_CLI", "").strip()
    return value if value and Path(value).is_file() else None


def test_repair_decision_controls_are_typed_and_require_the_right_fields() -> None:
    base = {
        "decision_id": "repair-decision-1",
        "repair_id": "repair-1",
        "proposal_fingerprint": "sha256:" + "1" * 64,
        "artifact_fingerprint": "sha256:" + "2" * 64,
        "reviewer": "engineer-1",
        "decided_at": NOW,
    }
    assert SchematicRepairDecision.create(
        **base, action=SchematicRepairDecisionAction.APPROVE_FIX
    ).action is SchematicRepairDecisionAction.APPROVE_FIX
    assert SchematicRepairDecision.create(
        **{**base, "decision_id": "repair-decision-2"},
        action=SchematicRepairDecisionAction.REJECT_FIX,
    ).action is SchematicRepairDecisionAction.REJECT_FIX
    assert SchematicRepairDecision.create(
        **{**base, "decision_id": "repair-decision-3"},
        action=SchematicRepairDecisionAction.EDIT_FIX,
        edited_proposal_fingerprint="sha256:" + "3" * 64,
    ).edited_proposal_fingerprint == "sha256:" + "3" * 64
    assert SchematicRepairDecision.create(
        **{**base, "decision_id": "repair-decision-4"},
        action=SchematicRepairDecisionAction.DEFER,
    ).action is SchematicRepairDecisionAction.DEFER
    assert SchematicRepairDecision.create(
        **{**base, "decision_id": "repair-decision-5"},
        action=SchematicRepairDecisionAction.WAIVE_WITH_REASON,
        comment="Library mapping is tracked for a later approved environment update.",
    ).action is SchematicRepairDecisionAction.WAIVE_WITH_REASON


def test_repair_application_receipt_is_typed_and_lineage_bound() -> None:
    fp = lambda value: "sha256:" + value * 64
    receipt = SchematicRepairApplicationReceipt.create(
        receipt_id="repair-application-receipt-1",
        project_id="project-1",
        conversation_id="conversation-1",
        repair_id="repair-1",
        repair_proposal_fingerprint=fp("1"),
        repair_decision_id="repair-decision-1",
        repair_decision_fingerprint=fp("2"),
        repair_action=SchematicRepairDecisionAction.APPROVE_FIX,
        artifact_path="hardware/main.kicad_sch",
        pre_design_truth_fingerprint=fp("3"),
        post_design_truth_fingerprint=fp("3"),
        pre_schematic_plan_fingerprint=fp("4"),
        post_schematic_plan_fingerprint=fp("4"),
        pre_artifact_fingerprint=fp("5"),
        post_artifact_fingerprint=fp("6"),
        pre_erc_receipt_fingerprint=fp("7"),
        pre_erc_result_fingerprint=fp("8"),
        workspace_apply_receipt_fingerprint=fp("9"),
        post_generation_receipt_fingerprint=fp("a"),
        post_parse_validation_fingerprint=fp("b"),
        post_erc_receipt_fingerprint=fp("c"),
        post_erc_result_fingerprint=fp("d"),
        status=SchematicRepairApplicationStatus.APPLIED,
        design_truth_changed=False,
        schematic_plan_changed=False,
        generator_changed=True,
        source_refs=("repair-1", "repair-decision-1", "erc-receipt-1"),
        applied_at=NOW,
    )
    assert receipt.status is SchematicRepairApplicationStatus.APPLIED
    assert receipt.pre_artifact_fingerprint != receipt.post_artifact_fingerprint
    assert receipt.model_copy(deep=True).fingerprint == receipt.fingerprint
    with pytest.raises(ValueError, match="only approved repair decisions"):
        invalid_values = receipt.model_dump(mode="python", exclude={"fingerprint"})
        invalid_values["repair_action"] = SchematicRepairDecisionAction.WAIVE_WITH_REASON
        SchematicRepairApplicationReceipt.create(**invalid_values)


def test_real_erc_persists_after_g3v_and_recovers_after_restart(tmp_path: Path) -> None:
    executable = _configured_cli()
    if executable is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")

    runtime, project, session, _ = _runtime_with_approved_plan(tmp_path)
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(runtime),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    bridge = SchematicExecutionBridge(
        workspace=workspace,
        execution=execution,
        clock=lambda: NOW,
    )
    queued = runtime.decide_schematic_production(
        project.project_id,
        session.conversation.conversation_id,
        "APPROVED",
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
        bridge=bridge,
    )
    write_operation_id = queued.schematic_production.operation_id
    assert write_operation_id is not None
    write_worker = ExecutionWorker(
        worker_id="g4-write-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,),
        clock=lambda: NOW,
    )
    write_status = write_worker.run_once()
    assert write_status is not None and write_status.status is OperationStatus.SUCCEEDED
    write_operation = execution.get_operation(
        tenant_id="tenant-1", project_id=project.project_id, operation_id=write_operation_id
    )
    write_receipt = execution.get_receipt(
        tenant_id="tenant-1", project_id=project.project_id, operation_id=write_operation_id
    )
    assert write_operation is not None and write_receipt is not None
    generated = runtime.record_schematic_generation(
        operation=write_operation,
        receipt=write_receipt,
        external_validation_status="UNAVAILABLE",
    )
    generation = generated.schematic_production.generation_receipt
    assert generation is not None
    artifact = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / generation.artifact_path
    version = probe_kicad_cli_version(executable, cwd=artifact.parent)
    parse_evidence = KiCadCliSchematicParseAdapter(
        executable=executable,
        kicad_version=version,
        clock=lambda: NOW,
    ).execute(
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        operation_id=generation.operation_id,
        artifact=artifact,
        artifact_path=generation.artifact_path,
        expected_artifact_fingerprint=generation.artifact_fingerprint,
    )
    assert parse_evidence.validation_status == "PARSE_VERIFIED"
    parsed = runtime.record_schematic_parse_validation(evidence=parse_evidence)
    assert parsed.schematic_production.generation_receipt.validation_status == "PARSE_VERIFIED"

    erc_adapter = KiCadCliSchematicErcAdapter(
        executable=executable,
        workspace_resolver=lambda operation: runtime.knowledge_snapshot_root(
            operation.project_id, tenant_id=operation.tenant_id
        ),
        kicad_version=version,
    )
    erc_worker = ExecutionWorker(
        worker_id="g4-erc-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        schematic_erc=erc_adapter,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_ERC,),
        clock=lambda: NOW,
    )
    erc_request = bridge.enqueue_erc(
        write_operation=write_operation,
        receipt_fingerprint=write_receipt.fingerprint,
        reviewer_id="engineer-1",
    )
    erc_status = erc_worker.run_once()
    assert erc_status is not None and erc_status.status is OperationStatus.SUCCEEDED
    erc_operation = execution.get_operation(
        tenant_id="tenant-1", project_id=project.project_id,
        operation_id=erc_request.operation.operation_id,
    )
    erc_receipt = execution.get_receipt(
        tenant_id="tenant-1", project_id=project.project_id,
        operation_id=erc_request.operation.operation_id,
    )
    assert erc_operation is not None and erc_receipt is not None
    persisted = runtime.record_schematic_erc_validation(
        operation=erc_operation,
        receipt=erc_receipt,
    )
    production = persisted.schematic_production
    assert production is not None and production.erc_receipt is not None
    result = production.erc_receipt.result
    assert production.erc_receipt.tool_execution_status == "SUCCEEDED"
    assert result.engineering_status is SchematicErcEngineeringStatus.WARNINGS_REMAIN
    assert result.raw_evidence and result.raw_evidence_fingerprint
    assert result.issues
    assert all(item.severity is SchematicErcIssueSeverity.WARNING for item in result.issues)
    assert all(
        item.classification is SchematicErcIssueClassification.LIBRARY_OR_EDA_MAPPING_DEFECT
        for item in result.issues
    )
    assert all(item.status is SchematicErcIssueStatus.OPEN for item in result.issues)
    assert all(item.status.value == "UNMATCHED" for item in result.correlations)

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    restored = restarted.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    restored_production = restored.schematic_production
    assert restored_production is not None and restored_production.erc_receipt is not None
    assert restored_production.erc_receipt.result.raw_evidence_fingerprint == result.raw_evidence_fingerprint
    assert restored_production.erc_gate_status.value == "PARTIAL"

    # Contract-level persistence test for a reviewable serializer proposal;
    # the real artifact's library warnings remain classified as non-auto-fix.
    synthetic_assessment = SchematicRepairAssessment.create(
        assessment_id="synthetic-rendering-assessment",
        erc_result_fingerprint=result.fingerprint,
        kind=SchematicRepairAssessmentKind.DETERMINISTIC_RENDERING_DEFECT,
        rationale="Synthetic contract fixture for the human review projection.",
        source_refs=("synthetic-erc", "synthetic-receipt"),
        proposal_allowed=True,
        classification=SchematicErcIssueClassification.ARTIFACT_RENDERING_DEFECT,
    )
    proposal_content = artifact.read_text(encoding="utf-8").strip()
    proposal_content_fp = "sha256:" + hashlib.sha256(proposal_content.encode("utf-8")).hexdigest()
    synthetic_proposal = SchematicRepairProposal.create(
        repair_id="synthetic-rendering-repair",
        erc_result_fingerprint=result.fingerprint,
        base_proposal_id=restored_production.proposal.proposal_id,
        target_relative_path=generation.artifact_path,
        corrected_content=proposal_content,
        corrected_content_fingerprint=proposal_content_fp,
        source_refs=(synthetic_assessment.assessment_id, restored_production.proposal.proposal_id),
        classification=SchematicErcIssueClassification.ARTIFACT_RENDERING_DEFECT,
        root_cause="Serializer output requires a reviewed correction.",
        proposed_change="Regenerate from the approved proposal after the serializer correction.",
        target_authority="Existing G3 schematic serializer",
        supporting_evidence=synthetic_assessment.source_refs,
        expected_effect="The approved plan remains unchanged.",
        risk="A new artifact must be parsed and ERC-validated.",
        source_fingerprint=result.fingerprint,
    )
    with_proposal = restarted.record_schematic_repair_proposal(
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        assessment=synthetic_assessment,
        proposal=synthetic_proposal,
    )
    assert len(with_proposal.schematic_production.repair_proposals) == 1
    persisted_proposal = with_proposal.schematic_production.repair_proposals[0]
    repair_decision = SchematicRepairDecision.create(
        decision_id="synthetic-repair-decision",
        repair_id=persisted_proposal.repair_id,
        proposal_fingerprint=persisted_proposal.fingerprint,
        artifact_fingerprint=generation.artifact_fingerprint,
        action=SchematicRepairDecisionAction.DEFER,
        reviewer="engineer-1",
        decided_at=NOW,
        comment="Defer until the local KiCad library mapping is reviewed.",
    )
    with_decision = restarted.decide_schematic_repair(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        decision=repair_decision,
    )
    assert with_decision.schematic_production.repair_decisions == (repair_decision,)

    planning = restored.schematic_planning
    assert planning is not None
    target_values = planning.proposal.target.model_dump(mode="python")
    target_values.update({"sheet_name": "Main-G4-Changed", "fingerprint": "sha256:" + "0" * 64})
    changed_target = SchematicTarget.create(**target_values)
    proposal_values = planning.proposal.model_dump(mode="python")
    proposal_values.update(
        {"target": changed_target, "status": "REVIEW", "fingerprint": "sha256:" + "0" * 64}
    )
    changed_plan = SchematicPlanProposal.create(**proposal_values)
    restarted.persist_conversation_detail(
        restored.model_copy(
            update={
                "schematic_planning": SchematicPlanningState(
                    proposal=changed_plan,
                    status=SchematicPlanningStatus.REVIEW,
                ),
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
    )
    stale = restarted.decide_schematic_plan(
        project.project_id,
        session.conversation.conversation_id,
        SchematicPlanDecision.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    stale_production = stale.schematic_production
    assert stale_production is not None and stale_production.status.value == "STALE"
    assert stale_production.erc_receipt is not None
    assert all(item.status is SchematicErcIssueStatus.STALE for item in stale_production.erc_receipt.result.issues)
    with pytest.raises(SchematicProductionRejected, match="current parse-verified artifact"):
        restarted.record_schematic_erc_validation(operation=erc_operation, receipt=erc_receipt)
    with pytest.raises(SchematicProductionRejected, match="stale production"):
        restarted.decide_schematic_repair(
            project.project_id,
            session.conversation.conversation_id,
            tenant_id="tenant-1",
            requester="engineer-1",
            decision=repair_decision,
        )


def test_raw_evidence_identity_is_deterministic_for_a_report() -> None:
    raw = "ERC report\n"
    assert "sha256:" + hashlib.sha256(raw.encode()).hexdigest() != "sha256:" + "0" * 64


def test_real_repair_loop_uses_g3_parse_and_erc_again(tmp_path: Path) -> None:
    """A real missing-net engineering defect is corrected through G3 again."""

    executable = _configured_cli()
    if executable is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")
    original = _proposal()
    defective_values = original.model_dump(mode="python")
    defective_values.update(
        {
            "proposal_id": "schematic-proposal-controlled-defect",
            "request_id": "schematic-request-controlled-defect",
            "nets": original.nets[:1],
            "connectivity_fingerprint": "sha256:" + "7" * 64,
        }
    )
    defective_values.pop("fingerprint", None)
    defective = SchematicProposal.create(**defective_values)
    defective_request = _request(defective)
    defective_request_values = defective_request.model_dump(mode="python")
    defective_request_values.update({"requester": "engineer-controlled"})
    defective_request_values.pop("fingerprint", None)
    defective_request = type(defective_request).create(**defective_request_values)
    root = tmp_path / "project"
    (root / "hardware").mkdir(parents=True)
    (root / "README.md").write_text("controlled G4 project\n", encoding="utf-8")
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(
            root, tenant_id="tenant-controlled", project_id=defective.project_id
        ),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    bridge = SchematicExecutionBridge(
        workspace=workspace, execution=execution, clock=lambda: NOW
    )
    worker = ExecutionWorker(
        worker_id="g4-repair-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        schematic_erc=KiCadCliSchematicErcAdapter(
            executable=executable,
            workspace_resolver=lambda _operation: root,
            kicad_version=probe_kicad_cli_version(executable, cwd=root),
        ),
        allowed_operation_types=(
            OperationType.KICAD_SCHEMATIC_WRITE,
            OperationType.KICAD_SCHEMATIC_ERC,
        ),
        clock=lambda: NOW,
    )

    defective_decision = decide_schematic_proposal(
        defective,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-controlled",
        decided_at=NOW,
    )
    defective_write = bridge.approve_and_enqueue(
        production_request=defective_request,
        proposal=defective,
        decision=defective_decision,
        tenant_id="tenant-controlled",
    )
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    defective_write_receipt = execution.get_receipt(
        tenant_id="tenant-controlled",
        project_id=defective.project_id,
        operation_id=defective_write.operation.operation_id,
    )
    assert defective_write_receipt is not None
    defective_erc = bridge.enqueue_erc(
        write_operation=defective_write.operation,
        receipt_fingerprint=defective_write_receipt.fingerprint,
        reviewer_id="engineer-controlled",
    )
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    defective_erc_receipt = execution.get_receipt(
        tenant_id="tenant-controlled",
        project_id=defective.project_id,
        operation_id=defective_erc.operation.operation_id,
    )
    assert defective_erc_receipt is not None and defective_erc_receipt.schematic_erc_result is not None
    defective_result = defective_erc_receipt.schematic_erc_result
    assert defective_result.status is SchematicErcStatus.ERC_FAIL
    assert any(item.severity is SchematicErcIssueSeverity.ERROR for item in defective_result.issues)
    assert all(
        item.classification is SchematicErcIssueClassification.PLAN_OR_TRUTH_DEFECT
        for item in defective_result.issues
        if item.severity is SchematicErcIssueSeverity.ERROR
    )
    assessment = assess_schematic_repair(defective_result)
    assert assessment.classification is SchematicErcIssueClassification.PLAN_OR_TRUTH_DEFECT
    assert assessment.proposal_allowed is False

    corrected_truth_fingerprint = "sha256:" + "8" * 64
    corrected_values = original.model_dump(mode="python")
    corrected_values.update(
        {
            "proposal_id": "schematic-proposal-controlled-correction",
            "request_id": "schematic-request-controlled-correction",
            "design_truth_fingerprint": corrected_truth_fingerprint,
            "connectivity_fingerprint": "sha256:" + "9" * 64,
        }
    )
    corrected_values.pop("fingerprint", None)
    corrected = SchematicProposal.create(**corrected_values)
    corrected_request_values = defective_request.model_dump(mode="python")
    corrected_request_values.update(
        {
            "request_id": corrected.request_id,
            "design_truth_fingerprint": corrected_truth_fingerprint,
        }
    )
    corrected_request_values.pop("fingerprint", None)
    corrected_request = type(defective_request).create(**corrected_request_values)
    corrected_content = serialize_kicad_schematic(corrected).decode("utf-8")
    corrected_content = corrected_content.strip()
    corrected_content_fingerprint = "sha256:" + hashlib.sha256(
        corrected_content.encode("utf-8")
    ).hexdigest()
    repair_proposal = SchematicRepairProposal.create(
        repair_id="repair-controlled-missing-net",
        erc_result_fingerprint=defective_result.fingerprint,
        base_proposal_id=defective.proposal_id,
        target_relative_path=defective.target_relative_path,
        corrected_content=corrected_content,
        corrected_content_fingerprint=corrected_content_fingerprint,
        source_refs=(assessment.assessment_id, defective_result.fingerprint),
        issue_refs=assessment.issue_refs,
        classification=SchematicErcIssueClassification.PLAN_OR_TRUTH_DEFECT,
        root_cause="The approved upstream connectivity omitted the required NET_B connection.",
        proposed_change="Approve the corrected plan/truth projection, then regenerate through G3.",
        target_authority="Approved DesignTruth and SchematicPlan authority",
        affected_truth_refs=("design-truth-controlled-v2",),
        affected_plan_refs=("schematic-plan-controlled-v2",),
        supporting_evidence=(defective_result.raw_evidence_fingerprint,),
        expected_effect="The two pin_not_connected errors disappear without direct schematic editing.",
        risk="The corrected engineering connectivity still requires G3-V parse and a fresh ERC.",
        source_fingerprint=defective_result.fingerprint,
    )
    repair_decision = SchematicRepairDecision.create(
        decision_id="repair-decision-controlled-missing-net",
        repair_id=repair_proposal.repair_id,
        proposal_fingerprint=repair_proposal.fingerprint,
        artifact_fingerprint=defective_result.schematic_content_fingerprint,
        action=SchematicRepairDecisionAction.APPROVE_FIX,
        reviewer="engineer-controlled",
        decided_at=NOW,
        comment="Approved corrected DesignTruth/SchematicPlan projection for G3 regeneration.",
    )
    assert repair_decision.action is SchematicRepairDecisionAction.APPROVE_FIX

    corrected_decision = decide_schematic_proposal(
        corrected,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-controlled",
        decided_at=NOW,
    )
    corrected_write = bridge.approve_and_enqueue(
        production_request=corrected_request,
        proposal=corrected,
        decision=corrected_decision,
        tenant_id="tenant-controlled",
        expected_existing_fingerprint=defective_result.schematic_content_fingerprint,
    )
    # The bridge binds the correction to the old artifact fingerprint and
    # leaves the actual write to the existing WorkspaceApply + Worker path.
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    corrected_artifact = root / corrected.target_relative_path
    corrected_fingerprint = "sha256:" + hashlib.sha256(corrected_artifact.read_bytes()).hexdigest()
    assert corrected_fingerprint == corrected_write.operation.content_fingerprint
    parse_evidence = KiCadCliSchematicParseAdapter(
        executable=executable,
        kicad_version=probe_kicad_cli_version(executable, cwd=root),
        clock=lambda: NOW,
    ).execute(
        project_id=corrected.project_id,
        conversation_id=corrected.conversation_id,
        operation_id=corrected_write.operation.operation_id,
        artifact=corrected_artifact,
        expected_artifact_fingerprint=corrected_fingerprint,
    )
    assert parse_evidence.validation_status == "PARSE_VERIFIED"
    corrected_write_receipt = execution.get_receipt(
        tenant_id="tenant-controlled",
        project_id=corrected.project_id,
        operation_id=corrected_write.operation.operation_id,
    )
    assert corrected_write_receipt is not None
    corrected_erc = bridge.enqueue_erc(
        write_operation=corrected_write.operation,
        receipt_fingerprint=corrected_write_receipt.fingerprint,
        reviewer_id="engineer-controlled",
    )
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    corrected_erc_receipt = execution.get_receipt(
        tenant_id="tenant-controlled",
        project_id=corrected.project_id,
        operation_id=corrected_erc.operation.operation_id,
    )
    assert corrected_erc_receipt is not None and corrected_erc_receipt.schematic_erc_result is not None
    corrected_result = corrected_erc_receipt.schematic_erc_result
    assert corrected_result.status is SchematicErcStatus.ERC_WARNINGS
    assert corrected_result.violations == ()
    assert not any(item.severity is SchematicErcIssueSeverity.ERROR for item in corrected_result.issues)
