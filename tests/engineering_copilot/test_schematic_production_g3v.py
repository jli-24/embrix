from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import (
    SchematicParseValidationEvidence,
    SchematicPlanDecision,
    SchematicPlanningState,
    SchematicPlanningStatus,
    SchematicPlanProposal,
    SchematicTarget,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    OperationStatus,
    OperationType,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.project_catalog.exceptions import (
    SchematicPlanningRejected,
    SchematicProductionRejected,
)
from embedded_copilot.schematic_execution import SchematicExecutionBridge

from .test_schematic_production_g3 import NOW, _runtime_with_approved_plan


def _generate_unvalidated(tmp_path: Path):
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
        workspace=workspace, execution=execution, clock=lambda: NOW
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
    operation_id = queued.schematic_production.operation_id
    worker = ExecutionWorker(
        worker_id="g3v-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,),
        clock=lambda: NOW,
    )
    status = worker.run_once()
    assert status is not None and status.status is OperationStatus.SUCCEEDED
    operation = execution.get_operation(
        tenant_id="tenant-1", project_id=project.project_id, operation_id=operation_id
    )
    receipt = execution.get_receipt(
        tenant_id="tenant-1", project_id=project.project_id, operation_id=operation_id
    )
    completed = runtime.record_schematic_generation(
        operation=operation,
        receipt=receipt,
        external_validation_status="UNAVAILABLE",
    )
    artifact = (
        runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1")
        / "hardware/main.kicad_sch"
    )
    return runtime, project, session, completed, artifact


def test_g3v_parse_evidence_persists_and_becomes_stale_with_artifact(
    tmp_path: Path,
) -> None:
    runtime, project, session, generated, artifact = _generate_unvalidated(tmp_path)
    receipt = generated.schematic_production.generation_receipt
    evidence = SchematicParseValidationEvidence.create(
        validation_id="schematic-parse-validation-test-1",
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        operation_id=receipt.operation_id,
        artifact_path="hardware/main.kicad_sch",
        artifact_fingerprint="sha256:"
        + hashlib.sha256(artifact.read_bytes()).hexdigest(),
        kicad_cli_path=r"D:\ruanjian\32\kicad\bin\kicad-cli.exe",
        kicad_version="10.0.5",
        command_identity="kicad-cli sch export netlist",
        exit_code=0,
        validation_status="PARSE_VERIFIED",
        artifact_unchanged=True,
        stdout_summary="parsed",
        stderr_summary="registry diagnostic",
        validated_at=NOW,
    )

    verified = runtime.record_schematic_parse_validation(evidence=evidence)
    verified_receipt = verified.schematic_production.generation_receipt
    assert verified_receipt.status.value == "SUCCESS"
    assert verified_receipt.validation_status == "PARSE_VERIFIED"
    assert verified_receipt.parse_validation == evidence

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=__import__(
            "tests.engineering_copilot.test_schematic_planning_g2",
            fromlist=["_catalog"],
        )._catalog(),
    )
    restored = restarted.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.schematic_production.generation_receipt.parse_validation == evidence

    planning = restored.schematic_planning
    assert planning is not None
    target_values = planning.proposal.target.model_dump(mode="python")
    target_values.update({"sheet_name": "MainV2", "fingerprint": ZERO})
    changed_target = SchematicTarget.create(**target_values)
    proposal_values = planning.proposal.model_dump(mode="python")
    proposal_values.update(
        {"target": changed_target, "status": "REVIEW", "fingerprint": ZERO}
    )
    changed_proposal = SchematicPlanProposal.create(**proposal_values)
    reopened = restarted.persist_conversation_detail(
        restored.model_copy(
            update={
                "schematic_planning": SchematicPlanningState(
                    proposal=changed_proposal,
                    status=SchematicPlanningStatus.REVIEW,
                ),
                "fingerprint": ZERO,
            }
        )
    )
    with pytest.raises(SchematicPlanningRejected, match="source is not current"):
        restarted.decide_schematic_plan(
            project.project_id,
            session.conversation.conversation_id,
            SchematicPlanDecision.APPROVED,
            tenant_id="tenant-1",
            requester="engineer-1",
            decided_at=NOW,
        )
    assert reopened.schematic_production is not None
    after_rejection = restarted.conversation(
        project.project_id, session.conversation.conversation_id,
        tenant_id="tenant-1", requester="engineer-1",
    )
    assert after_rejection.schematic_production.generation_receipt.parse_validation == evidence
    artifact.write_bytes(b"changed schematic")
    with pytest.raises(SchematicProductionRejected, match="artifact fingerprint mismatch"):
        restarted.record_schematic_parse_validation(evidence=evidence)
