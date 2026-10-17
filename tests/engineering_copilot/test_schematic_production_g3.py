from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.application.circuit_publication import PredesignDecisionCommand
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureEvidenceBinding,
    AuthorizationDecisionStatus,
    DesignTruthBuilder,
    HardwareSelectionDecision,
    SchematicPlanDecision,
    SchematicPlanningService,
    SchematicPlanningState,
    SchematicPlanningStatus,
    SchematicPlanProposal,
    SchematicReadinessStatus,
    SchematicTarget,
    approve_architecture_design,
    approve_engineering_plan,
    evaluate_schematic_readiness,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_requirements import (
    generate_engineering_plan as _generate_plan,
)
from embedded_copilot.engineering_requirements.models import (
    RequirementContext,
    RequirementItemStatus,
    RequirementProvenance,
    StandardRequirementField,
)
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
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.exceptions import SchematicPlanningRejected
from embedded_copilot.project_catalog.models import (
    ConversationTurn,
    ConversationTurnStatus,
)

from .test_schematic_planning_g2 import _catalog, _golden_architecture

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


class _Answer:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        del mode
        return {
            "answer_text": "已完成工程阶段推进",
            "evidence_details": (),
            "recommendations": (),
        }


def _runtime_with_approved_plan(tmp_path: Path, before_schematic_plan=None):
    root = tmp_path / "projects"
    root.mkdir()
    runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    location = runtime.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = runtime.preview(
        ProjectCreationRequest(
            name="G3 Production",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = runtime.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    session = runtime.create_conversation(
        project.project_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    context, architecture, architecture_review = _golden_architecture(
        project.project_id, session.conversation.conversation_id
    )
    existing = {item.category for item in context.standard_fields}
    missing = tuple(StandardRequirementField(
        category=category, value=value, status=RequirementItemStatus.CONFIRMED,
        source_turn_id="g3-supply-turn", provenance=RequirementProvenance.USER_UTTERANCE,
    ) for category, value in (("S02_VOLTAGE_MIN_V", "3.0"), ("S02_VOLTAGE_MAX_V", "3.6"))
      if category not in existing)
    if missing:
        context = RequirementContext.model_validate({**context.model_dump(mode="python"),
            "standard_fields": (*context.standard_fields, *missing), "fingerprint": ZERO})
    plan, plan_review = approve_engineering_plan(
        _generate_plan(context), reviewer="engineer-1", decided_at=NOW,
    )
    if architecture.plan_fingerprint != plan.fingerprint:
        architecture = ArchitectureDesign.create(**{**architecture.model_dump(mode="python"),
            "context_fingerprint": context.fingerprint, "plan_id": plan.plan_id,
            "plan_fingerprint": plan.fingerprint, "status": ArchitectureDesignStatus.PROPOSED,
            "fingerprint": ZERO,
            "evidence_bindings": tuple(ArchitectureEvidenceBinding.create(**{
                **binding.model_dump(mode="python"), "plan_id": plan.plan_id,
                "fingerprint": ZERO,
            }) for binding in architecture.evidence_bindings),
        })
        architecture, architecture_review = approve_architecture_design(
            architecture, reviewer="engineer-1", decided_at=NOW,
        )
    turn_ids = tuple(dict.fromkeys(item.source_turn_id for item in context.standard_fields
                                  if item.source_turn_id is not None))
    turns = tuple(ConversationTurn(
        turn_id=turn_id, request_id=f"g3-requirement-{index}",
        status=ConversationTurnStatus.COMPLETED, question="Confirmed engineering range",
        answer_text="Confirmed offline fixture values", created_at=NOW, completed_at=NOW,
    ) for index, turn_id in enumerate(turn_ids))
    workflow = generate_engineering_workflow(architecture, plan)
    seeded = session.conversation.model_copy(
        update={
            "turns": turns,
            "requirement_context": context,
            "engineering_plan": plan,
            "engineering_plan_review": plan_review,
            "architecture_design": architecture,
            "architecture_review": architecture_review,
            "engineering_workflow": workflow,
            "design_truth": DesignTruthBuilder().build_from_approved_architecture(
                architecture,
                tenant_id="tenant-1",
                conversation_id=session.conversation.conversation_id,
                architecture_review=architecture_review,
            ),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    runtime.persist_conversation_detail(seeded)
    runtime.create_hardware_selection_proposal(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    runtime.decide_hardware_selection(
        project.project_id,
        session.conversation.conversation_id,
        HardwareSelectionDecision.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    if before_schematic_plan is not None:
        circuit_intent_selector = before_schematic_plan(runtime, project, session)
    else:
        from tests.project_catalog.test_predesign_publication import _command, _service
        current_detail = runtime.conversation(project.project_id, session.conversation.conversation_id,
            tenant_id="tenant-1", requester="engineer-1")
        design_turn = ConversationTurn(turn_id="turn-design", request_id="g3-circuit-design",
            route="GREENFIELD_DESIGN",
            status=ConversationTurnStatus.COMPLETED, question="Synthetic offline circuit",
            answer_text="Approved design-only source", created_at=NOW, completed_at=NOW)
        runtime.persist_conversation_detail(current_detail.model_copy(update={
            "turns": (*current_detail.turns, design_turn), "fingerprint": ZERO,
        }))
        current_detail = runtime.conversation(project.project_id, session.conversation.conversation_id,
            tenant_id="tenant-1", requester="engineer-1")
        publication = _service(runtime)
        command = _command(current_detail)
        preview = publication.preview(project_id=project.project_id, tenant_id="tenant-1",
                                      requester="engineer-1", command=command)
        published = publication.decide(project_id=project.project_id, tenant_id="tenant-1",
            requester="engineer-1", command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=preview.proposal.proposal_id,
                proposal_fingerprint=preview.proposal.fingerprint,
                source_fingerprint=preview.proposal.context_fingerprint, decision="APPROVE"))
        circuit_intent_selector = (published.circuit.object_id, published.circuit.revision,
                                   published.circuit.fingerprint)
    current_detail = runtime.conversation(project.project_id, session.conversation.conversation_id,
        tenant_id="tenant-1", requester="engineer-1")
    circuit = runtime.circuit_intent_revision(project.project_id, *circuit_intent_selector,
        tenant_id="tenant-1", requester="engineer-1")
    selection = current_detail.hardware_selection
    truth = current_detail.design_truth
    preview = SchematicPlanningService().create_proposal(
        current_detail.architecture_design, current_detail.requirement_context,
        selection.hardware_truth, selection.bom, design_truth=truth,
        conversation_id=current_detail.conversation_id, circuit_intent=circuit,
        selection=selection)
    domain = next(item for item in preview.power_domains if item.name != "GND")
    supply_net = next(item for item in preview.nets if item.name == domain.name)
    ground_net = next(item for item in preview.nets if item.name == "GND")
    component_ids = domain.source_component_ids + domain.consumer_component_ids
    evidence_refs = tuple(next(item.binding_id for item in truth.evidence_bindings
        if item.entity_id == component_id and item.source.value == "DATASHEET")
        for component_id in component_ids)
    from embedded_copilot.project_catalog.source_publication import (
        ComponentSourcePublishCommandV1, PublishedComponentSourcePort,
        TrustedComponentSourcePublisher,
    )
    source = runtime._hardware_component_source
    publisher = TrustedComponentSourcePublisher(runtime._repository,
        tenant_id="tenant-1", project_id=project.project_id,
        publisher_id="g3-fixture-publisher")
    for number, record in enumerate(source.records, start=1):
        publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
            request_id=f"g3-source-{number}", source_id=record.source_id,
            expected_head_publication_fingerprint=None, record=record))
    runtime.set_hardware_component_source(PublishedComponentSourcePort(runtime._repository,
        tenant_id="tenant-1", publisher_id="g3-fixture-publisher"))
    supply = runtime.create_project_supply_candidate(project.project_id, current_detail.conversation_id,
        tenant_id="tenant-1", requester="engineer-1", selectors={
            "design_truth_id": truth.truth_id, "design_truth_fingerprint": truth.fingerprint,
            "selection_id": selection.proposal.proposal_id,
            "selection_fingerprint": selection.proposal.fingerprint,
            "circuit_id": circuit.object_id, "circuit_fingerprint": circuit.fingerprint,
            "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint,
        }, relations=({
            "power_domain_id": domain.domain_id,
            "source_component_refs": domain.source_component_ids,
            "consumer_component_refs": domain.consumer_component_ids,
            "nominal_voltage_v": "3.3", "supply_net_ref": supply_net.net_id,
            "ground_net_ref": ground_net.net_id, "evidence_refs": evidence_refs,
        },))
    runtime.review_project_supply_candidate(project.project_id, current_detail.conversation_id,
        supply.candidate.candidate_id, supply.candidate.fingerprint, "APPROVED",
        tenant_id="tenant-1", requester="engineer-1")
    runtime.create_schematic_plan(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        circuit_intent_selector=circuit_intent_selector,
    )
    approved = runtime.decide_schematic_plan(
        project.project_id,
        session.conversation.conversation_id,
        SchematicPlanDecision.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    assert approved.schematic_planning is not None
    assert approved.schematic_planning.status is SchematicPlanningStatus.APPROVED
    project_root = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1")
    (project_root / "README.md").write_text("G3 production fixture\n", encoding="utf-8")

    runtime.begin_turn(
        session.session_id,
        request_id="g3-context",
        trace_id="g3-context-trace",
        question="准备工程执行",
        created_at=NOW,
    )
    runtime.complete_turn(session.session_id, "g3-context", _Answer(), NOW)
    current = runtime.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert current.execution_authorization is not None
    schematic_task = next(
        item
        for item in current.implementation_spec.tasks
        if "SCHEMATIC" in item.expected_artifacts
        or any(
            artifact.artifact_id in item.expected_artifacts
            and artifact.artifact_type.value == "SCHEMATIC"
            for artifact in current.implementation_spec.expected_artifacts
        )
    )
    schematic_intent = next(
        item
        for item in current.execution_intents
        if item.source_task_refs == (schematic_task.task_id,)
    )
    artifact_request = next(
        item
        for item in current.execution_authorization.requests
        if item.intent_id == schematic_intent.intent_id
        and item.capability_id == "ARTIFACT_CREATION"
    )
    runtime.decide_execution_authorization(
        project.project_id,
        session.conversation.conversation_id,
        artifact_request.request_id,
        AuthorizationDecisionStatus.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    runtime.begin_turn(
        session.session_id,
        request_id="g3-generation",
        trace_id="g3-generation-trace",
        question="生成原理图",
        created_at=NOW,
    )
    runtime.complete_turn(session.session_id, "g3-generation", _Answer(), NOW)
    proposed = runtime.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert proposed.schematic_production is not None
    assert proposed.schematic_production.status.value == "AWAITING_APPROVAL"
    return runtime, project, session, proposed


def test_g3_production_runtime_writes_real_artifact_and_persists_receipt(tmp_path: Path) -> None:
    runtime, project, session, _proposed = _runtime_with_approved_plan(tmp_path)
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
    from embedded_copilot.schematic_execution import SchematicExecutionBridge

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
    assert queued.schematic_production is not None
    operation_id = queued.schematic_production.operation_id
    assert operation_id is not None
    worker = ExecutionWorker(
        worker_id="g3-worker",
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
    assert operation is not None and receipt is not None
    completed = runtime.record_schematic_generation(
        operation=operation,
        receipt=receipt,
        external_validation_status="UNAVAILABLE",
    )
    generation = completed.schematic_production
    assert generation is not None
    assert generation.status.value == "GENERATED"
    assert generation.generation_receipt is not None
    artifact = runtime.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / "hardware/main.kicad_sch"
    assert artifact.is_file() and artifact.stat().st_size > 0
    assert generation.generation_receipt.artifact_fingerprint == "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert generation.generation_receipt.validation_status == "UNAVAILABLE"

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
    assert restored.schematic_production == completed.schematic_production
    assert restarted.record_schematic_generation(operation=operation, receipt=receipt) == restored

    current_planning = restored.schematic_planning
    assert current_planning is not None
    target_values = current_planning.proposal.target.model_dump(mode="python")
    target_values.update({"sheet_name": "MainV2", "fingerprint": ZERO})
    changed_target = SchematicTarget.create(**target_values)
    proposal_values = current_planning.proposal.model_dump(mode="python")
    proposal_values.update({"target": changed_target, "status": "REVIEW", "fingerprint": ZERO})
    changed_proposal = SchematicPlanProposal.create(**proposal_values)
    review_state = SchematicPlanningState(
        proposal=changed_proposal,
        status=SchematicPlanningStatus.REVIEW,
    )
    reopened_for_change = restarted.persist_conversation_detail(
        restored.model_copy(update={"schematic_planning": review_state, "fingerprint": ZERO})
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
    assert reopened_for_change.schematic_production is not None
    after_rejection = restarted.conversation(
        project.project_id, session.conversation.conversation_id,
        tenant_id="tenant-1", requester="engineer-1",
    )
    assert after_rejection.schematic_production == completed.schematic_production


def test_g3_readiness_binds_approved_plan_provenance_and_fail_closes(tmp_path: Path) -> None:
    _runtime, _project, _session, detail = _runtime_with_approved_plan(tmp_path)
    assert detail.schematic_planning is not None
    assert detail.schematic_planning.approved_plan is not None
    assert detail.hardware_design_spec is not None
    assert detail.implementation_spec is not None
    readiness = evaluate_schematic_readiness(
        detail.design_truth,
        detail.hardware_design_spec,
        detail.implementation_spec,
        approved_schematic_plan=detail.schematic_planning.approved_plan,
    )
    assert readiness.status is SchematicReadinessStatus.READY
    tampered = detail.design_truth.model_copy(
        update={"provenance": tuple(item for item in detail.design_truth.provenance if item.source_type != "SCHEMATIC_PLAN"), "fingerprint": "sha256:" + "0" * 64}
    )
    blocked = evaluate_schematic_readiness(
        tampered,
        detail.hardware_design_spec,
        detail.implementation_spec,
        approved_schematic_plan=detail.schematic_planning.approved_plan,
    )
    assert blocked.status is SchematicReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
    assert "schematic_plan_design_truth_binding" in blocked.missing_facts
