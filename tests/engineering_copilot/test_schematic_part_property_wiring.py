from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import (
    SchematicPlanningState,
    SchematicPlanningStatus,
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
from embedded_copilot.project_catalog.exceptions import SchematicProductionRejected
from embedded_copilot.project_catalog.source_publication import (
    ComponentSourcePublishCommandV1,
    PublishedComponentSourcePort,
    TrustedComponentSourcePublisher,
)
from embedded_copilot.schematic_execution import SchematicExecutionBridge

from .test_schematic_production_g3 import NOW, _runtime_with_approved_plan


def _prepare(tmp_path):
    runtime, project, session, proposed = _runtime_with_approved_plan(tmp_path)
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(runtime),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"), clock=lambda: NOW,
    )
    bridge = SchematicExecutionBridge(workspace=workspace, execution=execution, clock=lambda: NOW)
    return runtime, project, session, proposed, workspace, execution, bridge


def _queue(runtime, project, session, bridge):
    return runtime.decide_schematic_production(
        project.project_id, session.conversation.conversation_id, "APPROVED",
        tenant_id="tenant-1", requester="engineer-1", decided_at=NOW, bridge=bridge,
    )


def _drift_source(
    runtime: ProjectCatalogRuntime, project_id: str, conversation_id: str
) -> None:
    """Advance an owned synthetic publication; do not replace the read-side port."""
    from embedded_copilot.component_recommendation import ComponentSourceRecord

    source = runtime._hardware_component_source
    assert isinstance(source, PublishedComponentSourcePort)
    assert source.repository is runtime._repository
    detail = runtime.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1"
    )
    assert detail.hardware_selection is not None
    record = detail.hardware_selection.proposal.selected_components[0].source
    head = source.current_publication(project_id, record.source_id)
    assert head is not None and head.record == record
    values = record.model_dump(mode="python")
    values.update(package="CHANGED-SYNTHETIC-PACKAGE", fingerprint=ZERO)
    changed = ComponentSourceRecord.create(**values)
    publisher = TrustedComponentSourcePublisher(
        runtime._repository, tenant_id=source.tenant_id, project_id=project_id,
        publisher_id=source.publisher_id, requester="engineer-1",
    )
    publication, _ = publisher.publish(ComponentSourcePublishCommandV1(
        schema_version=1, request_id="property-source-drift", source_id=record.source_id,
        expected_head_publication_fingerprint=head.fingerprint, record=changed,
    ))
    assert publication.generation == head.generation + 1
    assert publication.record == changed


def _worker(runtime, workspace, execution, calls):
    delegate = SchematicWriteAdapter(workspace)
    class Adapter:
        def execute(self, operation, context=None):
            calls.append(operation.operation_id)
            return delegate.execute(operation, context)
    return ExecutionWorker(
        worker_id="part-properties-worker", store=execution.store,
        registry=None, workspace_resolver=None, targets=None,
        schematic_write=Adapter(), design_context_revalidator=runtime.revalidate_circuit_bound_operation,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,), clock=lambda: NOW,
    )


def test_owner_rejects_source_drift_before_enqueue(tmp_path: Path):
    runtime, project, session, _, _, execution, bridge = _prepare(tmp_path)
    _drift_source(runtime, project.project_id, session.conversation.conversation_id)
    with pytest.raises(SchematicProductionRejected):
        _queue(runtime, project, session, bridge)
    assert execution.list_operations(tenant_id="tenant-1", project_id=project.project_id) == ()


@pytest.mark.parametrize("drift", ("source", "plan_approval", "queued_identity", "owner_removed"))
def test_worker_rejects_property_context_drift_before_write(tmp_path: Path, drift):
    runtime, project, session, _, workspace, execution, bridge = _prepare(tmp_path)
    queued = _queue(runtime, project, session, bridge)
    if drift == "source":
        _drift_source(runtime, project.project_id, session.conversation.conversation_id)
    elif drift == "plan_approval":
        planning = queued.schematic_planning
        updated = SchematicPlanningState(proposal=planning.proposal, status=SchematicPlanningStatus.REVIEW)
        runtime.persist_conversation_detail(queued.model_copy(update={"schematic_planning": updated, "fingerprint": ZERO}))
    elif drift == "owner_removed":
        runtime.persist_conversation_detail(queued.model_copy(update={"schematic_production": None, "schematic_planning": None, "fingerprint": ZERO}))
    else:
        production = queued.schematic_production.model_copy(update={"operation_id": "other-operation", "fingerprint": ZERO})
        runtime.persist_conversation_detail(queued.model_copy(update={"schematic_production": production, "fingerprint": ZERO}))
    calls = []
    result = _worker(runtime, workspace, execution, calls).run_once()
    assert result.status is OperationStatus.FAILED
    assert calls == []
    root = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1")
    assert not (root / "hardware/main.kicad_sch").exists()


def test_approved_property_write_survives_owner_reload(tmp_path: Path):
    runtime, project, session, proposed, workspace, execution, bridge = _prepare(tmp_path)
    queued = _queue(runtime, project, session, bridge)
    opid = queued.schematic_production.operation_id
    operation = execution.get_operation(tenant_id="tenant-1", project_id=project.project_id, operation_id=opid)
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(), initialize_async=False,
    )
    source = runtime._hardware_component_source
    assert isinstance(source, PublishedComponentSourcePort)
    restarted.set_hardware_component_source(PublishedComponentSourcePort(
        restarted._repository, tenant_id=source.tenant_id, publisher_id=source.publisher_id,
    ))
    calls = []
    worker = _worker(restarted, workspace, execution, calls)
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    assert worker.run_once() is None
    assert calls == [opid]
    artifact = restarted.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / "hardware/main.kicad_sch"
    assert artifact.read_bytes() == operation.content.encode("utf-8")
    for selected in proposed.hardware_selection.proposal.selected_components:
        source = selected.source
        for key, value in (("Footprint", source.footprint), ("Manufacturer", source.manufacturer), ("MPN", source.mpn), ("Package", source.package)):
            assert f'(property "{key}" "{value}"' in operation.content
    receipt = execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=opid)
    assert receipt.status is OperationStatus.SUCCEEDED


def test_legacy_queued_bytes_are_not_rewritten_or_upgraded(tmp_path: Path):
    from embedded_copilot.engineering_copilot import (
        SchematicProductionRequest,
        SchematicProductionState,
        SchematicProductionStatus,
        SchematicProposal,
        SchematicProposalDecisionStatus,
        decide_schematic_proposal,
    )
    runtime, project, session, detail, workspace, execution, bridge = _prepare(tmp_path)
    original = detail.schematic_production
    def legacy(model, cls):
        fields = model.model_dump(mode="python")
        fields.update(schematic_plan_id=None, schematic_plan_fingerprint=None, fingerprint=ZERO)
        return cls.create(**fields)
    request = legacy(original.request, SchematicProductionRequest)
    proposal = legacy(original.proposal, SchematicProposal)
    decision = decide_schematic_proposal(proposal, decision=SchematicProposalDecisionStatus.APPROVED, reviewer="engineer-1", decided_at=NOW)
    prepared = bridge.prepare_approved(production_request=request, proposal=proposal, decision=decision, tenant_id="tenant-1")
    operation = prepared.operation
    queued = SchematicProductionState.create(
        production_id=original.production_id, project_id=project.project_id,
        conversation_id=session.conversation.conversation_id, request=request,
        proposal=proposal, decision=decision, operation_id=operation.operation_id,
        operation_fingerprint=operation.fingerprint, status=SchematicProductionStatus.QUEUED,
    )
    runtime.persist_conversation_detail(detail.model_copy(update={"schematic_production": queued, "fingerprint": ZERO}))
    bridge.enqueue_prepared(operation_request=prepared, decision=decision)
    opid = operation.operation_id
    assert '(property "Footprint" ""' in operation.content
    assert '(property "Manufacturer"' not in operation.content
    before = operation.model_dump_json()
    calls = []
    assert _worker(runtime, workspace, execution, calls).run_once().status is OperationStatus.SUCCEEDED
    assert execution.get_operation(tenant_id="tenant-1", project_id=project.project_id, operation_id=opid).model_dump_json() == before
    artifact = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / "hardware/main.kicad_sch"
    assert artifact.read_bytes() == operation.content.encode("utf-8")


def test_worker_can_claim_immediately_when_queue_is_published(tmp_path: Path, monkeypatch):
    runtime, project, session, _, workspace, execution, bridge = _prepare(tmp_path)
    calls = []
    worker = _worker(runtime, workspace, execution, calls)
    enqueue = execution.enqueue
    observed = []
    def claim_on_publish(**kwargs):
        result = enqueue(**kwargs)
        observed.append(worker.run_once())
        return result
    monkeypatch.setattr(execution, "enqueue", claim_on_publish)
    queued = _queue(runtime, project, session, bridge)
    assert observed[0].status is OperationStatus.SUCCEEDED
    assert calls == [queued.schematic_production.operation_id]


@pytest.mark.parametrize("interruption", ("owner_save", "queue_publish"))
def test_interrupted_publication_keeps_nonclaimable_operation(tmp_path: Path, monkeypatch, interruption):
    runtime, project, session, proposed, workspace, execution, bridge = _prepare(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("synthetic publication interruption")
    if interruption == "owner_save":
        monkeypatch.setattr(runtime._repository, "save_conversation", fail)
        expected = OSError
    else:
        monkeypatch.setattr(execution, "enqueue", fail)
        expected = SchematicProductionRejected
    with pytest.raises(expected):
        _queue(runtime, project, session, bridge)
    operations = execution.list_operations(tenant_id="tenant-1", project_id=project.project_id)
    assert len(operations) == 1
    operation = operations[0]
    assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=operation.operation_id).status is OperationStatus.WAIT_APPROVAL
    calls = []
    assert _worker(runtime, workspace, execution, calls).run_once() is None
    assert calls == []
    restored = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(), initialize_async=False,
        hardware_component_source=runtime._hardware_component_source,
    ).conversation(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    if interruption == "owner_save":
        assert restored.schematic_production == proposed.schematic_production
    else:
        assert restored.schematic_production.operation_id == operation.operation_id
        assert restored.schematic_production.operation_fingerprint == operation.fingerprint
    artifact = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / "hardware/main.kicad_sch"
    assert not artifact.exists()


def test_plan_bound_bridge_cannot_omit_approved_property_pair(tmp_path: Path):
    from embedded_copilot.engineering_copilot import (
        SchematicProposalDecisionStatus,
        decide_schematic_proposal,
    )
    _runtime, project, _, detail, _, execution, bridge = _prepare(tmp_path)
    production = detail.schematic_production
    decision = decide_schematic_proposal(production.proposal, decision=SchematicProposalDecisionStatus.APPROVED, reviewer="engineer-1", decided_at=NOW)
    with pytest.raises(ValueError, match="approved"):
        bridge.approve_and_enqueue(production_request=production.request, proposal=production.proposal, decision=decision, tenant_id="tenant-1")
    assert execution.list_operations(tenant_id="tenant-1", project_id=project.project_id) == ()
