from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from embedded_copilot.engineering_copilot import (
    AuthorizationDecisionStatus,
    FirmwareSourceProposalDecisionStatus,
    decide_execution_authorization_request,
    decide_firmware_source_proposal,
    evaluate_authorization,
    evaluate_capabilities,
    generate_execution_intent,
    generate_execution_requests,
    generate_firmware_source_proposal,
    generate_firmware_source_request,
    generate_implementation_spec,
)
from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    FirmwareSourceWriteAdapter,
    FirmwareSourceWriteOperation,
    OperationStatus,
    OperationType,
    SQLiteExecutionStore,
)
from embedded_copilot.firmware_source_execution import FirmwareSourceExecutionBridge

from tests.engineering_copilot.test_implementation_planning_layer import (
    _with_explicit_sources,
)


NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _approved_source_proposal():
    hardware, firmware, simulation, verification, workflow = _with_explicit_sources()
    implementation = generate_implementation_spec(
        hardware, firmware, simulation, verification, workflow
    )
    evaluation = evaluate_capabilities(implementation)
    intents = tuple(generate_execution_intent(task) for task in implementation.tasks)
    requests = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=implementation.conversation_id,
        requested_by="engineer-1",
    )
    authorization = evaluate_authorization(evaluation, intents, requests)
    auth_request = next(
        item for item in requests if item.capability_id == "FIRMWARE_CODE_GENERATION"
    )
    authorization = decide_execution_authorization_request(
        evaluation,
        intents,
        authorization,
        request_id=auth_request.request_id,
        decision=AuthorizationDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    auth_request = next(
        item for item in authorization.requests if item.request_id == auth_request.request_id
    )
    intent = next(item for item in intents if item.intent_id == auth_request.intent_id)
    task = next(item for item in implementation.tasks if item.task_id in intent.source_task_refs)
    module = next(item for item in firmware.modules if item.module_id in task.source_refs)
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=auth_request.request_id,
        tenant_id="tenant-golden",
        source_request_id="application-request-firmware-source",
        trace_id="application-trace-firmware-source",
    )
    proposal = generate_firmware_source_proposal(generation_request, module)
    decision = decide_firmware_source_proposal(
        proposal,
        decision=FirmwareSourceProposalDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    return generation_request, proposal, decision


def _workspace(tmp_path: Path, *, project_id: str) -> tuple[Path, WorkspaceApplyService]:
    root = tmp_path / "project"
    root.mkdir(parents=True)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    service = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(
            root, tenant_id="tenant-golden", project_id=project_id
        ),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    return root, service


def test_bridge_queues_canonical_source_operation_and_worker_writes_real_files(
    tmp_path: Path,
) -> None:
    request, proposal, decision = _approved_source_proposal()
    root, workspace = _workspace(tmp_path, project_id=request.project_id)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    bridge = FirmwareSourceExecutionBridge(
        workspace=workspace,
        execution=operations,
        clock=lambda: NOW,
    )

    operation_request = bridge.approve_and_enqueue(
        generation_request=request,
        proposal=proposal,
        decision=decision,
    )

    assert isinstance(operation_request.operation, FirmwareSourceWriteOperation)
    assert operation_request.operation.correlation is not None
    assert operation_request.operation.correlation.request_id == "application-request-firmware-source"
    assert operation_request.operation.correlation.trace_id == "application-trace-firmware-source"
    assert operation_request.operation.correlation.task_id == request.implementation_task_id
    assert operation_request.operation.correlation.operation_id == operation_request.operation.operation_id
    assert operation_request.operation.operation_type is OperationType.FIRMWARE_SOURCE_WRITE
    assert operations.get_status(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation_request.operation.operation_id,
    ).status is OperationStatus.QUEUED
    assert not (root / proposal.files[0].relative_path).exists()

    worker = ExecutionWorker(
        worker_id="firmware-source-worker",
        store=store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        firmware_source_write=FirmwareSourceWriteAdapter(workspace),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
        clock=lambda: NOW,
    )
    completed = worker.run_once()

    assert completed is not None
    assert completed.status is OperationStatus.SUCCEEDED
    for item in proposal.files:
        path = root / item.relative_path
        assert path.read_text(encoding="utf-8") == item.content
    receipt = operations.get_receipt(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation_request.operation.operation_id,
    )
    assert receipt is not None
    assert receipt.correlation is not None
    assert receipt.correlation.request_id == "application-request-firmware-source"
    assert receipt.correlation.trace_id == "application-trace-firmware-source"
    assert receipt.correlation.approval_id is not None
    assert tuple(item.relative_path for item in receipt.workspace_mutations) == tuple(
        item.relative_path for item in proposal.files
    )
    assert tuple(item.content_fingerprint for item in receipt.workspace_mutations) == tuple(
        item.content_fingerprint for item in proposal.files
    )
    assert receipt.firmware_evidence_parse == "UNAVAILABLE"
    assert tuple(event.event_type.value for event in operations.list_operation_events(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation_request.operation.operation_id,
    )) == (
        "OPERATION_CREATED",
        "APPROVAL_ISSUED",
        "OPERATION_QUEUED",
        "OPERATION_CLAIMED",
        "OPERATION_STARTED",
        "OPERATION_SUCCEEDED",
    )


def test_bridge_is_idempotent_and_existing_file_conflicts_without_overwrite(
    tmp_path: Path,
) -> None:
    request, proposal, decision = _approved_source_proposal()
    root, workspace = _workspace(tmp_path, project_id=request.project_id)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    bridge = FirmwareSourceExecutionBridge(workspace=workspace, execution=operations, clock=lambda: NOW)

    first = bridge.approve_and_enqueue(
        generation_request=request, proposal=proposal, decision=decision
    )
    repeated = bridge.approve_and_enqueue(
        generation_request=request, proposal=proposal, decision=decision
    )
    assert repeated == first

    conflict_root, conflict_workspace = _workspace(
        tmp_path / "conflict", project_id=request.project_id
    )
    target = conflict_root / proposal.files[0].relative_path
    target.parent.mkdir(parents=True)
    target.write_text("user content\n", encoding="utf-8")
    conflict_bridge = FirmwareSourceExecutionBridge(
        workspace=conflict_workspace,
        execution=ExecutionOperationService(
            store=SQLiteExecutionStore(tmp_path / "conflict-execution.sqlite3"),
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )
    try:
        conflict_bridge.approve_and_enqueue(
            generation_request=request, proposal=proposal, decision=decision
        )
    except Exception as error:
        assert "exist" in str(error).casefold() or "create" in str(error).casefold()
    else:
        raise AssertionError("existing source file must fail closed")
    assert target.read_text(encoding="utf-8") == "user content\n"


def test_source_only_worker_does_not_claim_other_operation_types(tmp_path: Path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    store.enqueue(tenant_id="tenant-1", project_id="project-1", operation_id="build-1")
    worker = ExecutionWorker(
        worker_id="firmware-source-worker",
        store=store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        firmware_source_write=object(),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
        clock=lambda: NOW,
    )
    assert worker.run_once() is None


def test_source_worker_restart_marks_only_claimable_source_operation_for_recovery(
    tmp_path: Path,
) -> None:
    request, proposal, decision = _approved_source_proposal()
    _, workspace = _workspace(tmp_path, project_id=request.project_id)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation_request = FirmwareSourceExecutionBridge(
        workspace=workspace,
        execution=operations,
        clock=lambda: NOW,
    ).approve_and_enqueue(
        generation_request=request,
        proposal=proposal,
        decision=decision,
    )
    operation = operation_request.operation
    queued = operations.get_status(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation.operation_id,
    )
    assert queued is not None and queued.approval_id is not None
    assert store.claim_next(
        worker_id="interrupted-source-worker",
        lease_seconds=60,
        now=NOW,
        operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
    ) is not None
    running = store.start_operation_transition(
        operation,
        approval_id=queued.approval_id,
        worker_id="interrupted-source-worker",
        now=NOW,
    )
    assert running is not None and running.status is OperationStatus.RUNNING

    worker = ExecutionWorker(
        worker_id="firmware-source-worker",
        store=store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        firmware_source_write=FirmwareSourceWriteAdapter(workspace),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
        clock=lambda: NOW + timedelta(seconds=1),
    )
    recovered = worker.recover_after_restart()

    assert len(recovered) == 1
    assert recovered[0].status is OperationStatus.RECOVERY_REQUIRED
    assert operations.list_operation_events(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation.operation_id,
    )[-1].event_type.value == "RECOVERY_REQUIRED"


def test_source_worker_rejects_workspace_drift_before_apply(tmp_path: Path) -> None:
    request, proposal, decision = _approved_source_proposal()
    root, workspace = _workspace(tmp_path, project_id=request.project_id)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation_request = FirmwareSourceExecutionBridge(
        workspace=workspace, execution=operations, clock=lambda: NOW
    ).approve_and_enqueue(
        generation_request=request, proposal=proposal, decision=decision
    )
    (root / "README.md").write_text("drifted\n", encoding="utf-8")
    worker = ExecutionWorker(
        worker_id="firmware-source-worker",
        store=store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        firmware_source_write=FirmwareSourceWriteAdapter(workspace),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
        clock=lambda: NOW,
    )
    result = worker.run_once()
    assert result is not None and result.status is OperationStatus.FAILED
    assert all(not (root / item.relative_path).exists() for item in proposal.files)
