from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    EvidenceReceiptReference,
    OperationReceiptEvidenceResolver,
)
from embedded_copilot.engineering_copilot import (
    ArtifactIntelligenceService,
    SchematicComponentProposal,
    SchematicNetProposal,
    SchematicPinProposal,
    SchematicProductionRequest,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    decide_schematic_proposal,
)
from embedded_copilot.engineering_runtime import (
    ContextRequest,
    EngineeringObjectRuntime,
    EvidenceObject,
    EvidenceQualificationStatus,
    LifecycleActor,
    LifecycleStatus,
    Provenance,
    RelationType,
    SchematicDesignObject,
)
from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliSchematicErcAdapter,
    OperationStatus,
    OperationType,
    SchematicSymbolLibraryValidator,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
    probe_kicad_cli_version,
)
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from tests.production_acceptance.kicad_toolchain import (
    LEGACY_TRUSTED_KICAD_ROOT,
    discover_test_kicad_cli,
)

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _kicad_cli() -> str | None:
    executable = discover_test_kicad_cli()
    return None if executable is None else str(executable)


def _proposal() -> SchematicProposal:
    components = (
        SchematicComponentProposal.create(
            component_id="R1",
            reference="R1",
            symbol_binding="Device:R",
            value="1K",
            source_refs=("truth-r1",),
        ),
        SchematicComponentProposal.create(
            component_id="R2",
            reference="R2",
            symbol_binding="Device:R",
            value="1K",
            source_refs=("truth-r2",),
        ),
    )
    pins = (
        SchematicPinProposal.create(
            pin_id="r1-1", component_id="R1", pin_number="1", pin_name="1", signal_id="NET_A", source_refs=("truth-r1-1",)
        ),
        SchematicPinProposal.create(
            pin_id="r1-2", component_id="R1", pin_number="2", pin_name="2", signal_id="NET_B", source_refs=("truth-r1-2",)
        ),
        SchematicPinProposal.create(
            pin_id="r2-1", component_id="R2", pin_number="1", pin_name="1", signal_id="NET_A", source_refs=("truth-r2-1",)
        ),
        SchematicPinProposal.create(
            pin_id="r2-2", component_id="R2", pin_number="2", pin_name="2", signal_id="NET_B", source_refs=("truth-r2-2",)
        ),
    )
    nets = (
        SchematicNetProposal.create(
            net_id="net-a", net_name="NET_A", signal_id="NET_A", nodes=("R1.1", "R2.1"), source_refs=("truth-net-a",)
        ),
        SchematicNetProposal.create(
            net_id="net-b", net_name="NET_B", signal_id="NET_B", nodes=("R1.2", "R2.2"), source_refs=("truth-net-b",)
        ),
    )
    return SchematicProposal.create(
        proposal_id="schematic-proposal-golden",
        request_id="schematic-request-golden",
        project_id="project-golden",
        conversation_id="conversation-golden",
        target_relative_path="hardware/main.kicad_sch",
        components=components,
        pins=pins,
        nets=nets,
        power_symbols=(),
        source_refs=("design-truth-golden", "hardware-design-golden"),
        design_truth_fingerprint="sha256:" + "1" * 64,
        connectivity_fingerprint="sha256:" + "2" * 64,
    )


def _request(proposal: SchematicProposal) -> SchematicProductionRequest:
    return SchematicProductionRequest.create(
        request_id=proposal.request_id,
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        requester="engineer-golden",
        architecture_id="architecture-golden",
        design_truth_id="design-truth-golden",
        hardware_design_id="hardware-design-golden",
        implementation_id="implementation-golden",
        implementation_task_id="implementation-task-golden",
        authorization_id="authorization-golden",
        authorization_request_id="authorization-request-golden",
        design_truth_fingerprint=proposal.design_truth_fingerprint,
        hardware_design_fingerprint="sha256:" + "3" * 64,
        implementation_fingerprint="sha256:" + "4" * 64,
        authorization_fingerprint="sha256:" + "5" * 64,
        workspace_identity="project-root",
        target_relative_path=proposal.target_relative_path,
        source_refs=("design-truth-golden", "hardware-design-golden", "implementation-task-golden"),
        readiness_fingerprint="sha256:" + "6" * 64,
    )


def test_real_schematic_production_golden(tmp_path: Path) -> None:
    if os.environ.get("EMBRIX_RUN_S01_REAL_KICAD") != "1":
        pytest.skip("S01 real KiCad acceptance is opt-in")
    executable = _kicad_cli()
    if executable is None:
        pytest.skip("authorized real KiCad CLI is unavailable")
    if Path(executable) != (LEGACY_TRUSTED_KICAD_ROOT / "bin" / "kicad-cli.exe"):
        pytest.fail("S01 real KiCad executable is outside the authorized path")

    root = tmp_path / "project"
    (root / "hardware").mkdir(parents=True)
    library = Path(executable).resolve().parent.parent / "share" / "kicad" / "symbols" / "Device.kicad_sym"
    if not library.is_file():
        pytest.skip("the configured KiCad installation has no trusted Device symbol library")
    (root / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "Device") (type "KiCad") '
        f'(uri "{library.as_posix()}") (options "") (descr "")))',
        encoding="utf-8",
    )

    proposal = _proposal()
    request = _request(proposal)
    decision = decide_schematic_proposal(
        proposal,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-golden",
        decided_at=NOW,
    )
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(
            root,
            tenant_id="tenant-golden",
            project_id=proposal.project_id,
        ),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    bridge = SchematicExecutionBridge(
        workspace=workspace,
        execution=execution,
        clock=lambda: NOW,
        symbol_binding_validator=SchematicSymbolLibraryValidator(lambda **kwargs: root),
    )
    write_request = bridge.approve_and_enqueue(
        production_request=request,
        proposal=proposal,
        decision=decision,
        tenant_id="tenant-golden",
    )
    assert write_request.operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE

    detected_kicad_version = probe_kicad_cli_version(executable, cwd=root)
    if detected_kicad_version != "10.0.5":
        pytest.fail("S01 real ERC golden is validated only for KiCad 10.0.5")
    erc_adapter = KiCadCliSchematicErcAdapter(
        executable=executable,
        workspace_resolver=lambda operation: root,
        kicad_version=detected_kicad_version,
    )
    worker = ExecutionWorker(
        worker_id="schematic-golden-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        schematic_erc=erc_adapter,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE, OperationType.KICAD_SCHEMATIC_ERC),
        clock=lambda: NOW,
    )
    write_status = worker.run_once()
    assert write_status is not None and write_status.status is OperationStatus.SUCCEEDED
    target = root / proposal.target_relative_path
    assert target.is_file()
    content = target.read_bytes()
    assert "sha256:" + hashlib.sha256(content).hexdigest() == write_request.operation.content_fingerprint
    write_receipt = execution.get_receipt(
        tenant_id="tenant-golden", project_id=proposal.project_id, operation_id=write_request.operation.operation_id
    )
    assert write_receipt is not None and write_receipt.status is OperationStatus.SUCCEEDED

    erc_request = bridge.enqueue_erc(
        write_operation=write_request.operation,
        receipt_fingerprint=write_receipt.fingerprint,
        reviewer_id="engineer-golden",
    )
    erc_status = worker.run_once()
    assert erc_status is not None and erc_status.status is OperationStatus.SUCCEEDED
    erc_receipt = execution.get_receipt(
        tenant_id="tenant-golden", project_id=proposal.project_id, operation_id=erc_request.operation.operation_id
    )
    assert erc_receipt is not None
    assert erc_receipt.schematic_erc_status == "ERC_PASS"
    assert erc_receipt.schematic_erc_result is not None
    assert erc_receipt.schematic_erc_result.issues == ()
    assert erc_receipt.schematic_erc_result is not None
    assert erc_receipt.schematic_erc_result.kicad_version == detected_kicad_version
    assert erc_receipt.schematic_erc_result.report_artifact is not None
    assert erc_receipt.schematic_erc_result.violations == ()
    assert erc_receipt.schematic_erc_result.issues == ()
    assert erc_receipt.evidence_references

    erc_result = erc_receipt.schematic_erc_result
    assert erc_result.schematic_content_fingerprint is not None
    assert erc_receipt.evidence_references
    reference = EvidenceReceiptReference(
        reference_id=erc_receipt.evidence_references[0],
        receipt_id=erc_receipt.operation_id,
        tenant_id="tenant-golden",
        project_id=proposal.project_id,
        source="caller-supplied",
        tool="caller-supplied",
        scope="SCHEMATIC_ERC",
        result_summary="caller-supplied",
        occurred_at=NOW,
        source_fingerprint=erc_result.schematic_content_fingerprint,
        receipt_fingerprint=erc_receipt.fingerprint,
        artifact_ref=erc_result.schematic_artifact_ref,
        artifact_fingerprint=erc_result.schematic_content_fingerprint,
        schematic_revision=erc_request.operation.schematic_revision,
        tool_version=erc_result.kicad_version,
        execution_result=erc_result.status.value,
    )
    resolved = OperationReceiptEvidenceResolver(execution).resolve(
        ApplicationQuestionRequest(
            request_id="real-evidence-request",
            trace_id="real-evidence-trace",
            tenant_id="tenant-golden",
            project_id=proposal.project_id,
            requester="engineer-golden",
            question="Bind the real KiCad ERC receipt to the schematic revision.",
            requested_at=NOW,
        ),
        reference,
    )

    runtime = EngineeringObjectRuntime(project_id=proposal.project_id)
    schematic = SchematicDesignObject.create(
        object_id="schematic-real-golden",
        project_id=proposal.project_id,
        revision=resolved.schematic_revision,
        symbols=("R1", "R2"),
        pins=("R1:1", "R1:2", "R2:1", "R2:2"),
        nets=("NET_A", "NET_B"),
        artifact_ref=resolved.artifact_ref,
        artifact_fingerprint=resolved.artifact_fingerprint,
        provenance=Provenance.create(
            source="KICAD_EXECUTION_WORKER",
            source_ref=f"{proposal.project_id}:{resolved.artifact_ref}",
            created_by="engineer-golden",
            created_at=NOW,
            source_fingerprint=resolved.artifact_fingerprint,
        ),
    )
    runtime.register_object(schematic)
    for status, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        schematic = runtime.transition_object(schematic.object_id, schematic.revision, status, actor)
    evidence = EvidenceObject.create(
        object_id="evidence-real-kicad-erc",
        project_id=proposal.project_id,
        source=resolved.source,
        tool=resolved.tool,
        result=resolved.execution_result,
        timestamp=resolved.occurred_at,
        scope=resolved.scope,
        canonical_evidence_ref=resolved.receipt_id,
        qualification_status=EvidenceQualificationStatus.QUALIFIED,
        subject_object_id=schematic.object_id,
        subject_revision=schematic.revision,
        subject_fingerprint=schematic.fingerprint,
        subject_artifact_ref=schematic.artifact_ref,
        subject_artifact_fingerprint=schematic.artifact_fingerprint,
        provenance=Provenance.create(
            source="KICAD_EXECUTION_WORKER",
            source_ref=f"{proposal.project_id}:receipt:{resolved.receipt_id}",
            created_by="engineer-golden",
            created_at=NOW,
            source_fingerprint=resolved.artifact_fingerprint,
        ),
    )
    runtime.register_object(evidence)
    for status, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        evidence = runtime.transition_object(evidence.object_id, evidence.revision, status, actor)
    runtime.add_relation(schematic, RelationType.VALIDATED_BY, evidence)
    context = runtime.resolve_context(
        ContextRequest.create(
            project_id=proposal.project_id,
            target_object_type="SCHEMATIC_DESIGN",
            required_object_ids=(schematic.object_id,),
            evidence_ids=(evidence.object_id,),
        )
    )
    assert resolved.verification_status.value == "VERIFIED"
    assert evidence.qualification_status is EvidenceQualificationStatus.QUALIFIED
    assert evidence.result == "ERC_PASS"
    assert context.evidence_bundle[0].subject_revision == schematic.revision
    assert context.evidence_bundle[0].subject_artifact_fingerprint == write_request.operation.content_fingerprint

    snapshot = ArtifactIntelligenceService().scan_directory(
        root=root,
        tenant_id="tenant-golden",
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        source_operation_id=write_request.operation.operation_id,
        source_proposal_fingerprint=proposal.fingerprint,
    )
    artifact = next(item for item in snapshot.inventory.artifacts if item.path_reference == proposal.target_relative_path)
    assert artifact.status.value == "PARSED"
    assert artifact.hash == write_request.operation.content_fingerprint
    assert dict(artifact.metadata)["source_operation_id"] == write_request.operation.operation_id
    assert tuple(event.event_type.value for event in execution.list_operation_events(
        tenant_id="tenant-golden", project_id=proposal.project_id, operation_id=write_request.operation.operation_id
    )) == ("OPERATION_CREATED", "APPROVAL_ISSUED", "OPERATION_QUEUED", "OPERATION_CLAIMED", "OPERATION_STARTED", "OPERATION_SUCCEEDED")
    assert tuple(event.event_type.value for event in execution.list_operation_events(
        tenant_id="tenant-golden", project_id=proposal.project_id, operation_id=erc_request.operation.operation_id
    )) == ("OPERATION_CREATED", "APPROVAL_ISSUED", "OPERATION_QUEUED", "OPERATION_CLAIMED", "OPERATION_STARTED", "OPERATION_SUCCEEDED")
    # The production golden is intentionally isolated to the schematic write
    # and read-only ERC operation families.  An empty Build/Flash/HIL/
    # Simulation path is part of the acceptance proof, not an implicit claim.
    assert tuple(
        item.operation_type for item in execution.list_operations(
            tenant_id="tenant-golden", project_id=proposal.project_id, limit=10
        )
    ) == (OperationType.KICAD_SCHEMATIC_ERC, OperationType.KICAD_SCHEMATIC_WRITE)
