import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    Artifact,
    ArtifactType,
    SchematicComponentProposal,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicErcViolation,
    SchematicNetProposal,
    SchematicPinProposal,
    SchematicPowerProposal,
    SchematicProductionRequest,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    SchematicRepairAssessmentKind,
    assess_schematic_repair,
    decide_schematic_proposal,
    generate_schematic_repair_proposal,
    parse_kicad_schematic,
    serialize_kicad_schematic,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_pcb import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBSourceFormat,
    content_fingerprint,
)
from embedded_copilot.engineering_pcb.parser import (
    parse_kicad_schematic as parse_engineering_pcb_schematic,
)
from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
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
from embedded_copilot.schematic_execution import SchematicExecutionBridge


class _WorkspaceClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 8, 12, tzinfo=UTC)


def _proposal() -> SchematicProposal:
    component = SchematicComponentProposal.create(
        component_id="U1", reference="U1", symbol_binding="MCU:STM32F0", value="Controller", source_refs=("truth-1", "component-1")
    )
    connector = SchematicComponentProposal.create(
        component_id="J1", reference="J1", symbol_binding="Connector_Generic:Conn_01x01", value="Connector", source_refs=("truth-1", "component-2")
    )
    pin = SchematicPinProposal.create(
        pin_id="pin-1", component_id="U1", pin_number="1", pin_name="VCC", signal_id="sig-1", source_refs=("truth-1", "pin-1")
    )
    connector_pin = SchematicPinProposal.create(
        pin_id="pin-2", component_id="J1", pin_number="1", pin_name="VCC", signal_id="sig-1", source_refs=("truth-1", "pin-2")
    )
    ground_pin = SchematicPinProposal.create(
        pin_id="pin-3", component_id="U1", pin_number="2", pin_name="GND", signal_id="sig-2", source_refs=("truth-1", "pin-3")
    )
    connector_ground_pin = SchematicPinProposal.create(
        pin_id="pin-4", component_id="J1", pin_number="2", pin_name="GND", signal_id="sig-2", source_refs=("truth-1", "pin-4")
    )
    net = SchematicNetProposal.create(
        net_id="net-1", net_name="VCC", signal_id="sig-1", nodes=("U1.1", "J1.1"), source_refs=("truth-1", "net-1")
    )
    ground_net = SchematicNetProposal.create(
        net_id="net-2", net_name="GND", signal_id="sig-2", nodes=("U1.2", "J1.2"), source_refs=("truth-1", "net-2")
    )
    power = SchematicPowerProposal.create(
        power_domain_id="power-1", name="Logic", voltage="3V3", ground_net="GND", component_refs=("U1",), source_refs=("truth-1", "power-1")
    )
    return SchematicProposal.create(
        proposal_id="proposal-1", request_id="request-1", project_id="project-1", conversation_id="conversation-1",
        target_relative_path="hardware/main.kicad_sch", components=(component, connector), pins=(pin, connector_pin, ground_pin, connector_ground_pin), nets=(net, ground_net), power_symbols=(power,),
        source_refs=("request-1", "truth-1"), design_truth_fingerprint="sha256:" + "1" * 64,
        connectivity_fingerprint="sha256:" + "2" * 64,
    )


def _request(proposal: SchematicProposal) -> SchematicProductionRequest:
    return SchematicProductionRequest.create(
        request_id=proposal.request_id,
        source_request_id="application-request-schematic",
        trace_id="application-trace-schematic",
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        requester="engineer-1",
        architecture_id="architecture-1",
        design_truth_id="truth-1",
        hardware_design_id="hardware-1",
        implementation_id="implementation-1",
        implementation_task_id="implementation-task-1",
        authorization_id="authorization-1",
        authorization_request_id="authorization-request-1",
        design_truth_fingerprint=proposal.design_truth_fingerprint,
        hardware_design_fingerprint="sha256:" + "3" * 64,
        implementation_fingerprint="sha256:" + "4" * 64,
        authorization_fingerprint="sha256:" + "5" * 64,
        workspace_identity="project-root",
        target_relative_path=proposal.target_relative_path,
        source_refs=("request-1", "truth-1"),
        readiness_fingerprint="sha256:" + "6" * 64,
    )


def test_schematic_proposal_is_strict_tuple_and_serializer_is_parser_compatible() -> None:
    proposal = _proposal()
    assert type(proposal.components) is tuple
    assert SchematicProposal.model_validate_json(proposal.model_dump_json()) == proposal
    content = serialize_kicad_schematic(proposal)
    artifact = Artifact.create(
        artifact_id="artifact-schematic", tenant_id="tenant-1", project_id="project-1", type=ArtifactType.SCHEMATIC,
        name="main.kicad_sch", path_reference="hardware/main.kicad_sch", hash="sha256:" + "3" * 64,
        metadata=(("conversation_id", "conversation-1"),),
    )
    assert parse_kicad_schematic(artifact, content)
    # The Engineering PCB parser is the source of observed pin/net evidence.
    parsed = parse_engineering_pcb_schematic(
        PCBArtifactReference.create(
            project_id="project-1",
            artifact_id="artifact-schematic",
            artifact_type=PCBArtifactType.SCHEMATIC,
            source_format=PCBSourceFormat.KICAD_SCH,
            source_reference="artifact://project-1/artifact-schematic",
            filename="main.kicad_sch",
            byte_size=len(content),
            content_fingerprint=content_fingerprint(content),
            captured_at=datetime.now(UTC),
        ),
        content,
    )
    uuids = re.findall(rb"\(uuid ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)", content)
    assert len(uuids) >= len(proposal.components) + 1
    assert content.count(b"(symbol (lib_id ") == len(proposal.components)
    assert b"(wire (pts" in content
    assert {
        (item.component_reference, item.pin_number, item.net_id)
        for item in parsed.connections
    } == {
        ("J1", "1", "VCC"),
        ("J1", "2", "GND"),
        ("U1", "1", "VCC"),
        ("U1", "2", "GND"),
    }


def test_existing_schematic_modify_requires_old_fingerprint_and_uses_worker(tmp_path: Path) -> None:
    proposal = _proposal()
    request = _request(proposal)
    decision = decide_schematic_proposal(
        proposal,
        decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
    )
    root = tmp_path / "project"
    (root / "hardware").mkdir(parents=True)
    old_content = serialize_kicad_schematic(proposal) + b"\n"
    target = root / proposal.target_relative_path
    target.write_bytes(old_content)
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id="tenant-golden", project_id=proposal.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_WorkspaceClock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: datetime(2026, 9, 8, 12, tzinfo=UTC),
    )
    bridge = SchematicExecutionBridge(workspace=workspace, execution=execution, clock=lambda: datetime(2026, 9, 8, 12, tzinfo=UTC))

    with pytest.raises(ValueError, match="existing schematic requires an explicit fingerprint"):
        bridge.approve_and_enqueue(
            production_request=request,
            proposal=proposal,
            decision=decision,
            tenant_id="tenant-golden",
        )
    assert target.read_bytes() == old_content

    operation_request = bridge.approve_and_enqueue(
        production_request=request,
        proposal=proposal,
        decision=decision,
        tenant_id="tenant-golden",
        expected_existing_fingerprint=content_fingerprint(old_content),
    )
    operation = operation_request.operation
    assert operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE
    assert operation.correlation is not None
    assert operation.correlation.request_id == "application-request-schematic"
    assert operation.correlation.trace_id == "application-trace-schematic"
    assert operation.correlation.task_id == request.implementation_task_id
    assert operation.changeset.entries[0].change_type.value == "MODIFY_FILE"
    worker = ExecutionWorker(
        worker_id="schematic-modify-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_write=SchematicWriteAdapter(workspace),
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,),
        clock=lambda: datetime(2026, 9, 8, 12, tzinfo=UTC),
    )
    status = worker.run_once()
    assert status is not None and status.status is OperationStatus.SUCCEEDED
    assert target.read_bytes() == serialize_kicad_schematic(proposal)
    receipt = execution.get_receipt(
        tenant_id="tenant-golden",
        project_id=proposal.project_id,
        operation_id=operation.operation_id,
    )
    assert receipt is not None and receipt.correlation is not None
    assert receipt.correlation.request_id == "application-request-schematic"
    assert receipt.correlation.trace_id == "application-trace-schematic"
    assert receipt.correlation.approval_id is not None


def test_schematic_contract_rejects_tamper_extra_and_unsafe_path() -> None:
    proposal = _proposal()
    tampered = proposal.model_dump(mode="python")
    tampered["components"][0]["value"] = "Different"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        SchematicProposal.model_validate(tampered, strict=False)
    extra = proposal.model_dump(mode="python")
    extra["unexpected"] = True
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="extra|unexpected"):
        SchematicProposal.model_validate(extra, strict=False)
    unsafe = proposal.model_dump(mode="python")
    unsafe["target_relative_path"] = "../main.kicad_sch"
    unsafe["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unsafe|path"):
        SchematicProposal.model_validate(unsafe, strict=False)

    invalid_net = proposal.model_dump(mode="python")
    invalid_net["nets"][0]["nodes"] = ("UNKNOWN.99", "J1.1")
    invalid_net["nets"][0]["fingerprint"] = ZERO
    invalid_net["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="endpoint"):
        SchematicProposal.model_validate(invalid_net, strict=False)

    invalid_symbol = proposal.model_dump(mode="python")
    invalid_symbol["components"][0]["symbol_binding"] = "../../etc"
    invalid_symbol["components"][0]["fingerprint"] = ZERO
    invalid_symbol["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="symbol_binding"):
        SchematicProposal.model_validate(invalid_symbol, strict=False)


def test_erc_unavailable_is_explicit_and_repair_is_never_allowed() -> None:
    result = SchematicErcResult.create(
        operation_id="erc-1", schematic_artifact_ref="hardware/main.kicad_sch", kicad_version="UNAVAILABLE",
        exit_status=-1, warnings=("KiCad CLI is unavailable",), started_at=datetime(2026, 9, 5, tzinfo=UTC),
        finished_at=datetime(2026, 9, 5, 0, 0, 1, tzinfo=UTC), receipt_ref="receipt-1", status=SchematicErcStatus.ERC_UNAVAILABLE,
    )
    assert result.status is SchematicErcStatus.ERC_UNAVAILABLE
    assert result.model_dump(mode="python")["status"] is SchematicErcStatus.ERC_UNAVAILABLE

    unsafe = result.model_dump(mode="python")
    unsafe["schematic_artifact_ref"] = "C:/private/design.kicad_sch"
    unsafe["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="schematic_artifact_ref"):
        SchematicErcResult.model_validate(unsafe, strict=False)


def test_deterministic_rendering_defect_can_create_reviewable_repair_proposal() -> None:
    base = _proposal()
    violation = SchematicErcViolation.create(
        severity="error",
        rule="SERIALIZER_FORMAT",
        message="invalid deterministic rendering",
        object_refs=("U1",),
        raw_source_ref="erc-report-1",
    )
    result = SchematicErcResult.create(
        operation_id="erc-2", schematic_artifact_ref="hardware/main.kicad_sch", kicad_version="9.0",
        exit_status=1, violations=(violation,), started_at=datetime(2026, 9, 5, tzinfo=UTC),
        finished_at=datetime(2026, 9, 5, 0, 0, 1, tzinfo=UTC), receipt_ref="receipt-2", status=SchematicErcStatus.ERC_FAIL,
    )
    assessment = assess_schematic_repair(result)
    assert assessment.kind is SchematicRepairAssessmentKind.DETERMINISTIC_RENDERING_DEFECT
    assert assessment.proposal_allowed is True
    repair = generate_schematic_repair_proposal(assessment, base, "(kicad_sch corrected)")
    assert repair.base_proposal_id == base.proposal_id
    assert repair.status.value == "AWAITING_APPROVAL"
    assert type(repair.corrected_content_fingerprint) is str
    assert type(repair.source_refs) is tuple
    assert repair.__class__.model_validate_json(repair.model_dump_json()) == repair
    tampered = repair.model_dump(mode="python")
    tampered["corrected_content"] = "(kicad_sch tampered)"
    with pytest.raises(ValidationError, match="fingerprint"):
        repair.__class__.model_validate(tampered, strict=False)
    assert SchematicProposal.model_validate_json(base.model_dump_json()) == base


def test_erc_result_rejects_inconsistent_status_shapes() -> None:
    common = {
        "operation_id": "erc-shape", "schematic_artifact_ref": "hardware/main.kicad_sch",
        "kicad_version": "9.0", "warnings": (), "started_at": datetime(2026, 9, 5, tzinfo=UTC),
        "finished_at": datetime(2026, 9, 5, 0, 0, 1, tzinfo=UTC), "receipt_ref": "receipt-shape",
    }
    with pytest.raises(ValidationError, match="ERC_PASS"):
        SchematicErcResult.create(**common, exit_status=0, status=SchematicErcStatus.ERC_PASS,
                                  violations=(SchematicErcViolation.create(severity="error", rule="RULE", message="x", raw_source_ref="report"),))
    with pytest.raises(ValidationError, match="ERC_FAIL"):
        SchematicErcResult.create(**common, exit_status=1, status=SchematicErcStatus.ERC_FAIL)
    with pytest.raises(ValidationError, match="ERC_BLOCKED"):
        SchematicErcResult.create(
            **common,
            exit_status=0,
            status=SchematicErcStatus.ERC_BLOCKED,
            violations=(SchematicErcViolation.create(severity="ERROR", rule="RULE", message="x", raw_source_ref="report"),),
        )
