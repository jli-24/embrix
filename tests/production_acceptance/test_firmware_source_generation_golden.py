from __future__ import annotations

from pathlib import Path

from embedded_copilot.engineering_copilot import ArtifactIntelligenceService
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    FirmwareSourceWriteAdapter,
    OperationStatus,
    OperationType,
    SQLiteExecutionStore,
)
from embedded_copilot.firmware_source_execution import FirmwareSourceExecutionBridge

from tests.execution_worker.test_firmware_source_write import (
    NOW,
    _approved_source_proposal,
    _workspace,
)


class _ForbiddenAdapter:
    calls = 0

    def execute(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("unapproved execution capability was invoked")


def test_stm32_motor_source_generation_uses_canonical_worker_and_real_inventory(
    tmp_path: Path,
) -> None:
    request, proposal, decision = _approved_source_proposal()
    root, workspace = _workspace(tmp_path, project_id=request.project_id)
    operation_service = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    operation = FirmwareSourceExecutionBridge(
        workspace=workspace,
        execution=operation_service,
        clock=lambda: NOW,
    ).approve_and_enqueue(
        generation_request=request,
        proposal=proposal,
        decision=decision,
    ).operation
    forbidden = _ForbiddenAdapter()
    worker = ExecutionWorker(
        worker_id="firmware-source-worker",
        store=operation_service.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        build=forbidden,
        flash=forbidden,
        hil=forbidden,
        firmware_source_write=FirmwareSourceWriteAdapter(workspace),
        allowed_operation_types=(OperationType.FIRMWARE_SOURCE_WRITE,),
        clock=lambda: NOW,
    )

    completed = worker.run_once()
    assert completed is not None and completed.status is OperationStatus.SUCCEEDED
    receipt = operation_service.get_receipt(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        operation_id=operation.operation_id,
    )
    assert receipt is not None and receipt.firmware_evidence_parse == "UNAVAILABLE"
    assert tuple(item.content_fingerprint for item in receipt.workspace_mutations) == tuple(
        item.content_fingerprint for item in proposal.files
    )
    for item in proposal.files:
        assert (root / item.relative_path).read_bytes().decode("utf-8") == item.content

    snapshot = ArtifactIntelligenceService().scan_directory(
        root=root,
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        conversation_id=request.conversation_id,
    )
    generated = tuple(
        item
        for item in snapshot.inventory.artifacts
        if item.path_reference.startswith("generated/")
    )
    assert tuple(item.path_reference for item in generated) == tuple(
        sorted(item.relative_path for item in proposal.files)
    )
    assert all(item.status.value == "AVAILABLE" for item in generated)
    assert snapshot.engineering_evidence_bundle is None
    assert forbidden.calls == 0
    assert tuple(
        event.event_type.value
        for event in operation_service.list_operation_events(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            operation_id=operation.operation_id,
        )
    ) == (
        "OPERATION_CREATED",
        "APPROVAL_ISSUED",
        "OPERATION_QUEUED",
        "OPERATION_CLAIMED",
        "OPERATION_STARTED",
        "OPERATION_SUCCEEDED",
    )
