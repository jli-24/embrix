from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import (
    DesignComponent,
    DesignEvidenceBinding,
    DesignPin,
    DesignPowerDomain,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    ExecutionAuthorization,
    ExecutionIntent,
    NetBinding,
    PlanProvenance,
    SchematicProposalDecisionStatus,
    TruthLevel,
    decide_execution_authorization_request,
    decide_schematic_proposal,
    evaluate_authorization,
    evaluate_capabilities,
    evaluate_schematic_readiness,
    generate_execution_intent,
    generate_execution_requests,
    generate_implementation_spec,
    generate_schematic_production_request,
    generate_schematic_proposal,
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

from tests.engineering_copilot.test_implementation_planning_layer import _with_explicit_sources
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _kicad_cli() -> str | None:
    executable = discover_test_kicad_cli()
    return None if executable is None else str(executable)


def _truth(project_id: str, conversation_id: str, architecture_id: str) -> DesignTruth:
    entities = (
        "truth-upstream",
        "R1",
        "R2",
        "sig-a",
        "sig-gnd",
        "pin-r1-1",
        "pin-r1-2",
        "pin-r2-1",
        "pin-r2-2",
        "net-a",
        "net-gnd",
        "power-logic",
    )
    evidence = tuple(
        DesignEvidenceBinding.create(
            binding_id=f"evidence-{entity}",
            entity_id=entity,
            project_id=project_id,
            source=DesignSource.ARCHITECTURE,
            source_id=architecture_id,
            artifact_ref="golden-design-truth",
            locator=entity,
            artifact_revision="v1",
            source_fingerprint="sha256:" + "a" * 64,
            relationship="explicit schematic production fact",
            truth_level=TruthLevel.EXPLICIT,
        )
        for entity in entities
    )
    components = (
        DesignComponent.create(
            component_id="R1", name="1K", type="passive", category="RESISTOR",
            description="Explicit current-limiting resistor", symbol_binding="Device:R",
            confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-R1",),
        ),
        DesignComponent.create(
            component_id="R2", name="1K", type="passive", category="RESISTOR",
            description="Explicit return resistor", symbol_binding="Device:R",
            confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-R2",),
        ),
    )
    signals = (
        DesignSignal.create(
            signal_id="sig-a", name="NET_A", type="DIGITAL", source="R1",
            targets=("R2",), protocol="NONE", direction="SOURCE_TO_TARGET",
            confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-sig-a",),
        ),
        DesignSignal.create(
            signal_id="sig-gnd", name="GND", type="POWER", source="R1",
            targets=("R2",), protocol="NONE", direction="SOURCE_TO_TARGET",
            confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-sig-gnd",),
        ),
    )
    pins = (
        DesignPin.create(pin_id="pin-r1-1", component_id="R1", pin_name="1", pin_number="1", function="NET_A", signal_id="sig-a", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-pin-r1-1",)),
        DesignPin.create(pin_id="pin-r1-2", component_id="R1", pin_name="2", pin_number="2", function="GND", signal_id="sig-gnd", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-pin-r1-2",)),
        DesignPin.create(pin_id="pin-r2-1", component_id="R2", pin_name="1", pin_number="1", function="NET_A", signal_id="sig-a", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-pin-r2-1",)),
        DesignPin.create(pin_id="pin-r2-2", component_id="R2", pin_name="2", pin_number="2", function="GND", signal_id="sig-gnd", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-pin-r2-2",)),
    )
    nets = (
        NetBinding.create(binding_id="net-a", net_name="NET_A", nodes=("R1.1", "R2.1"), signal="sig-a", purpose="explicit signal connection", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-net-a",)),
        NetBinding.create(binding_id="net-gnd", net_name="GND", nodes=("R1.2", "R2.2"), signal="sig-gnd", purpose="explicit ground connection", confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-net-gnd",)),
    )
    power = DesignPowerDomain.create(
        power_domain_id="power-logic", name="Logic", component_refs=("R1", "R2"),
        purpose="explicit logic return", voltage="3V3", ground_net="GND",
        confidence=1.0, truth_level=TruthLevel.EXPLICIT, evidence_refs=("evidence-power-logic",),
    )
    return DesignTruth.create(
        truth_id="truth-upstream", project_id=project_id, conversation_id=conversation_id,
        design_revision="v1", origin=DesignTruthOrigin.CREATE, status=DesignTruthStatus.APPROVED,
        architecture_id=architecture_id, components=components, signals=signals, pins=pins,
        nets=nets, power_domains=(power,), evidence_bindings=evidence,
        provenance=(PlanProvenance.create(project_id=project_id, source_type="ARCHITECTURE", source_id=architecture_id, source_fingerprint="sha256:" + "b" * 64),),
    )


def test_real_schematic_production_upstream_golden(tmp_path: Path) -> None:
    executable = _kicad_cli()
    if executable is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")
    library = Path(executable).resolve().parent.parent / "share" / "kicad" / "symbols" / "Device.kicad_sym"
    if not library.is_file():
        pytest.skip("the configured KiCad installation has no trusted Device symbol library")

    hardware, _, _, _, workflow = _with_explicit_sources()
    truth = _truth(hardware.project_id, workflow.conversation_id, hardware.architecture_id)
    implementation = generate_implementation_spec(*_with_explicit_sources())
    schematic_task = next(
        task for task in implementation.tasks
        if any(item in {"SCHEMATIC", *[artifact.artifact_id for artifact in implementation.expected_artifacts if artifact.artifact_type.value == "SCHEMATIC"]} for item in task.expected_artifacts)
    )
    evaluation = evaluate_capabilities(implementation)
    intents = tuple(generate_execution_intent(task) for task in implementation.tasks)
    requests = generate_execution_requests(evaluation, intents, conversation_id=implementation.conversation_id, requested_by="engineer-upstream")
    authorization = evaluate_authorization(evaluation, intents, requests)
    intent = next(item for item in intents if item.source_task_refs == (schematic_task.task_id,))
    auth_request = next(item for item in requests if item.intent_id == intent.intent_id)
    authorization = decide_execution_authorization_request(
        evaluation, intents, authorization, request_id=auth_request.request_id,
        decision="APPROVED", reviewer="engineer-upstream", decided_at=NOW,
    )
    auth_request = next(item for item in authorization.requests if item.request_id == auth_request.request_id)
    readiness = evaluate_schematic_readiness(truth, hardware, implementation)
    assert readiness.status.value == "READY"
    with pytest.raises(ValueError, match="requester binding"):
        generate_schematic_production_request(
            design_truth=truth, hardware_design=hardware, implementation=implementation,
            authorization=authorization, readiness=readiness, execution_intents=intents,
            conversation_id=implementation.conversation_id, requester="different-requester",
        )
    request = generate_schematic_production_request(
        design_truth=truth, hardware_design=hardware, implementation=implementation,
        authorization=authorization, readiness=readiness, execution_intents=intents,
        conversation_id=implementation.conversation_id, requester="engineer-upstream",
    )
    proposal = generate_schematic_proposal(request, truth)
    assert request.design_truth_fingerprint == truth.fingerprint
    assert proposal.design_truth_fingerprint == truth.fingerprint
    assert tuple(item.component_id for item in proposal.components) == tuple(item.component_id for item in truth.components)
    assert tuple(item.net_name for item in proposal.nets) == tuple(item.net_name for item in truth.nets)
    assert auth_request.status.value == "AUTHORIZED"
    decision = decide_schematic_proposal(proposal, decision=SchematicProposalDecisionStatus.APPROVED, reviewer="engineer-upstream", decided_at=NOW)

    root = tmp_path / "project"
    (root / "hardware").mkdir(parents=True)
    (root / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "Device") (type "KiCad") '
        f'(uri "{library.as_posix()}") (options "") (descr "")))', encoding="utf-8"
    )
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id="tenant-upstream", project_id=proposal.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"), state_root=tmp_path / "workspace-state",
        clock=_Clock(), approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"), clock=lambda: NOW)
    bridge = SchematicExecutionBridge(
        workspace=workspace, execution=execution, clock=lambda: NOW,
        symbol_binding_validator=SchematicSymbolLibraryValidator(lambda **kwargs: root),
    )
    tampered_decision = decision.model_copy(update={"reason": "tampered approval"})
    with pytest.raises(ValueError, match="fingerprint mismatch|decision integrity"):
        bridge.approve_and_enqueue(
            production_request=request,
            proposal=proposal,
            decision=tampered_decision,
            tenant_id="tenant-upstream",
        )
    write_request = bridge.approve_and_enqueue(production_request=request, proposal=proposal, decision=decision, tenant_id="tenant-upstream")
    worker = ExecutionWorker(
        worker_id="schematic-upstream-worker", store=execution.store, registry=None, workspace_resolver=None, targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        schematic_erc=KiCadCliSchematicErcAdapter(executable=executable, workspace_resolver=lambda operation: root, kicad_version=probe_kicad_cli_version(executable, cwd=root)),
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE, OperationType.KICAD_SCHEMATIC_ERC), clock=lambda: NOW,
    )
    write_status = worker.run_once()
    assert write_status is not None and write_status.status is OperationStatus.SUCCEEDED
    target = root / proposal.target_relative_path
    assert target.is_file()
    content = target.read_bytes()
    assert "sha256:" + hashlib.sha256(content).hexdigest() == write_request.operation.content_fingerprint
    write_receipt = execution.get_receipt(tenant_id="tenant-upstream", project_id=proposal.project_id, operation_id=write_request.operation.operation_id)
    assert write_receipt is not None and write_receipt.status is OperationStatus.SUCCEEDED
    erc_request = bridge.enqueue_erc(write_operation=write_request.operation, receipt_fingerprint=write_receipt.fingerprint, reviewer_id="engineer-upstream")
    erc_status = worker.run_once()
    assert erc_status is not None and erc_status.status is OperationStatus.SUCCEEDED
    erc_receipt = execution.get_receipt(tenant_id="tenant-upstream", project_id=proposal.project_id, operation_id=erc_request.operation.operation_id)
    assert erc_receipt is not None and erc_receipt.schematic_erc_status == "ERC_PASS"
    assert erc_receipt.schematic_erc_result is not None
    assert erc_receipt.schematic_erc_result.issues == ()
    assert erc_receipt.schematic_erc_result is not None
    assert erc_receipt.schematic_erc_result.schematic_content_fingerprint == write_request.operation.content_fingerprint
    assert erc_receipt.schematic_erc_result.report_artifact is not None
    assert erc_receipt.schematic_erc_result.violations == ()
    assert erc_receipt.schematic_erc_result.issues == ()
    assert erc_receipt.evidence_references
    assert tuple(event.event_type.value for event in execution.list_operation_events(tenant_id="tenant-upstream", project_id=proposal.project_id, operation_id=write_request.operation.operation_id))[-1] == "OPERATION_SUCCEEDED"
    assert tuple(event.operation_type.value for event in execution.list_operation_events(tenant_id="tenant-upstream", project_id=proposal.project_id, operation_id=erc_request.operation.operation_id)) == ("KICAD_SCHEMATIC_ERC",) * 6
    assert tuple(
        item.operation_type for item in execution.list_operations(
            tenant_id="tenant-upstream", project_id=proposal.project_id, limit=10
        )
    ) == (OperationType.KICAD_SCHEMATIC_ERC, OperationType.KICAD_SCHEMATIC_WRITE)
