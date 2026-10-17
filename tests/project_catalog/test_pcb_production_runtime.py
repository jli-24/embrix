from __future__ import annotations

from pathlib import Path
from datetime import timedelta

from tests.pcb_production.test_service import _Availability, _request
from embedded_copilot.pcb_production import (
    PCBParseValidationEvidence,
    PCBParseValidationStatus,
    PCBProductionService,
    PCBProductionStatus,
    PCBProposalDecisionStatus,
)
from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver, SQLiteWorkspaceApplyStore, WorkspaceApplyService
from embedded_copilot.execution_worker import ExecutionOperationService, ExecutionWorker, KiCadCliPCBDrCAdapter, OperationStatus, OperationType, PCBWriteAdapter, SQLiteExecutionStore
from embedded_copilot.pcb_production.execution import PCBExecutionBridge
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)

from .owner_seed import seed_project_owner


def test_pcb_production_runtime_persists_proposal_and_rejection(tmp_path: Path) -> None:
    now = _request().requested_at
    project_root = tmp_path / "projects"
    project_root.mkdir()
    service = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
        pcb_production_service=PCBProductionService(footprints=_Availability()),
    )
    service._repository.save_project(
        ProjectView(
            project_id="project-golden",
            tenant_id="tenant-golden",
            name="PCB Golden",
            location_id="location-1",
            directory_name="pcb-golden",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="pcb-golden",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "project-golden", "engineer-golden")
    detail = ConversationDetail(
        conversation_id="conversation-golden",
        project_id="project-golden",
        tenant_id="tenant-golden",
        requester="engineer-golden",
        title="PCB Golden",
        turns=(),
        created_at=now,
        updated_at=now,
        pcb_production_request=_request(),
    )
    service._repository.save_conversation(detail)

    proposed = service.create_pcb_production_proposal(
        "project-golden",
        "conversation-golden",
        tenant_id="tenant-golden",
        requester="engineer-golden",
    )
    rejected = service.decide_pcb_production(
        "project-golden",
        "conversation-golden",
        PCBProposalDecisionStatus.REJECTED,
        tenant_id="tenant-golden",
        requester="engineer-golden",
        decided_at=now,
        bridge=None,
    )
    restored = service.conversation(
        "project-golden",
        "conversation-golden",
        tenant_id="tenant-golden",
        requester="engineer-golden",
    )

    assert proposed.pcb_production is not None
    assert proposed.pcb_production.status is PCBProductionStatus.AWAITING_APPROVAL
    assert rejected.pcb_production is not None
    assert rejected.pcb_production.decision.decision is PCBProposalDecisionStatus.REJECTED
    assert restored.pcb_production is not None
    assert restored.pcb_production.status is PCBProductionStatus.REJECTED


def test_pcb_generation_and_parse_projection_survive_restart(tmp_path: Path) -> None:
    request = _request()
    now = request.requested_at
    root = tmp_path / "projects" / "pcb-golden"
    root.mkdir(parents=True)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    class _Clock:
        def now(self):
            return now

    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id="tenant-golden", project_id=request.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"), clock=lambda: now)
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(request)
    bridge = PCBExecutionBridge(workspace=workspace, execution=execution, clock=lambda: now)
    from embedded_copilot.pcb_production import decide_pcb_proposal

    decision = decide_pcb_proposal(proposal, decision=PCBProposalDecisionStatus.APPROVED, reviewer=request.requester, decided_at=now)
    write = bridge.approve_and_enqueue(production_request=request, proposal=proposal, decision=decision, tenant_id="tenant-golden")
    worker = ExecutionWorker(worker_id="runtime-pcb-worker", store=execution.store, registry=None, workspace_resolver=None, targets=None, pcb_write=PCBWriteAdapter(workspace), allowed_operation_types=(OperationType.KICAD_PCB_WRITE,), clock=lambda: now)
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    write_receipt = execution.get_receipt(tenant_id="tenant-golden", project_id=request.project_id, operation_id=write.operation.operation_id)
    assert write_receipt is not None

    catalog = ProjectCatalogRuntime(project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3", initialize_async=False, pcb_production_service=PCBProductionService(footprints=_Availability()))
    location = catalog.locations(tenant_id="tenant-golden", requester=request.requester)[0]
    catalog._repository.save_project(ProjectView(project_id=request.project_id, tenant_id="tenant-golden", name="PCB Golden", location_id=location.location_id, directory_name="pcb-golden", creation_mode=ProjectCreationMode.CREATE_NEW, relative_path="pcb-golden", project_type=ProjectType.GENERIC_FIRMWARE, initialization_status=ProjectInitializationStatus.READY, created_at=now, updated_at=now, last_activity_at=now))
    seed_project_owner(tmp_path / "catalog.sqlite3", request.project_id, request.requester)
    detail = ConversationDetail(conversation_id=request.conversation_id, project_id=request.project_id, tenant_id="tenant-golden", requester=request.requester, title="PCB Golden", turns=(), created_at=now, updated_at=now, pcb_production_request=request, pcb_production=__import__("embedded_copilot.pcb_production", fromlist=["PCBProductionState"]).PCBProductionState.create(production_id="pcb-production-golden", project_id=request.project_id, conversation_id=request.conversation_id, request=request, proposal=proposal, decision=decision, operation_id=write.operation.operation_id, operation_fingerprint=write.operation.fingerprint, status=PCBProductionStatus.QUEUED))
    catalog._repository.save_conversation(detail)
    generated = catalog.record_pcb_generation(operation=write.operation, receipt=write_receipt)
    generation = generated.pcb_production.generation_receipt
    assert generation is not None and generation.status == "ARTIFACT_GENERATED"
    evidence = PCBParseValidationEvidence.create(validation_id="pcb-parse-validation", project_id=request.project_id, conversation_id=request.conversation_id, operation_id=write.operation.operation_id, artifact_path=request.target_relative_path, artifact_fingerprint=generation.artifact_fingerprint, kicad_cli_path="C:/KiCad/bin/kicad-cli.exe", kicad_version="10.0.5", command_identity="kicad-cli pcb parse", exit_code=0, validation_status=PCBParseValidationStatus.PARSE_VERIFIED, artifact_unchanged=True, validated_at=now)
    parsed = catalog.record_pcb_parse_validation(evidence=evidence)
    assert parsed.pcb_production.generation_receipt.validation_status is PCBParseValidationStatus.PARSE_VERIFIED
    drc_request = bridge.enqueue_drc(write_operation=write.operation, receipt_fingerprint=write_receipt.fingerprint, reviewer_id=request.requester, parse_evidence=evidence)

    def drc_command_builder(executable: str, board_path: Path, report_path: Path) -> tuple[str, ...]:
        return (executable, "pcb", "drc", "--output", str(report_path), str(board_path))

    def drc_runner(argv: tuple[str, ...], cwd: Path, timeout: int):
        report = Path(argv[argv.index("--output") + 1])
        report.write_text('{"violations": [], "warnings": []}', encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted test executable")
    drc_worker = ExecutionWorker(
        worker_id="runtime-pcb-drc-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        pcb_drc=KiCadCliPCBDrCAdapter(
            executable=str(executable),
            workspace_resolver=lambda operation: root,
            command_builder=drc_command_builder,
            kicad_version="10.0.5",
            runner=drc_runner,
            clock=lambda: now,
        ),
        allowed_operation_types=(OperationType.KICAD_PCB_DRC,),
        clock=lambda: now,
    )
    assert drc_worker.run_once().status is OperationStatus.SUCCEEDED
    drc_receipt = execution.get_receipt(tenant_id="tenant-golden", project_id=request.project_id, operation_id=drc_request.operation.operation_id)
    assert drc_receipt is not None
    drc_detail = catalog.record_pcb_drc_validation(operation=drc_request.operation, receipt=drc_receipt)
    assert drc_detail.pcb_production.drc_receipt is not None
    assert drc_detail.pcb_production.status.value == "DRC_PASS"

    restarted = ProjectCatalogRuntime(project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3", initialize_async=False, pcb_production_service=PCBProductionService(footprints=_Availability()))
    restored = restarted.conversation(request.project_id, request.conversation_id, tenant_id="tenant-golden", requester=request.requester)
    assert restored.pcb_production is not None
    assert restored.pcb_production.generation_receipt is not None
    assert restored.pcb_production.generation_receipt.parse_validation is not None
    assert restored.pcb_production.drc_receipt is not None
    assert restored.pcb_production.drc_gate_status.value == "PASS"
    stale = restarted.mark_pcb_production_stale(
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        tenant_id="tenant-golden",
        requester=request.requester,
        reason="upstream G4 artifact fingerprint changed",
    )
    assert stale.pcb_production.status is PCBProductionStatus.STALE
    assert stale.pcb_production.stale_reason == "upstream G4 artifact fingerprint changed"
    assert stale.pcb_production.drc_result is None
