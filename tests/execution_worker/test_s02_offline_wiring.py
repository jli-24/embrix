"""Formal app/Owner/unique Worker with explicitly synthetic XML and fake ERC."""
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.application.circuit_publication import PredesignDecisionCommand
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceApplyPort,
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    SQLiteExecutionStore,
)
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.schematic_erc import KiCadCliSchematicErcAdapter
from embedded_copilot.execution_worker.schematic_xml import SchematicXmlAdapter
from embedded_copilot.project_catalog.models import ConversationTurn
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from embedded_copilot.services.config import Settings
from tests.engineering_copilot import test_schematic_production_g3 as g3
from tests.project_catalog.test_predesign_publication import _command, _service

XML = b'<export><components><comp ref="U1"><value>offline</value></comp></components><nets><net name="SCL"><node ref="U1" pin="2"/></net></nets></export>'
HEADERS = {"X-Environment-Tenant": "tenant-1", "X-Copilot-Requester": "engineer-1"}


def prepare(tmp_path, monkeypatch, *, rich=False):
    now = datetime.now(UTC)
    monkeypatch.setattr(g3, "NOW", now)

    if rich:
        from embedded_copilot.engineering_copilot import (
            ArchitectureDesign,
            ArchitectureDesignStatus,
            ArchitectureEvidenceBinding,
            approve_architecture_design,
            approve_engineering_plan,
        )
        from embedded_copilot.engineering_requirements import (
            RequirementContext,
            generate_engineering_plan,
        )
        from tests.engineering_copilot.test_s02_supporting_fulfillment import (
            _catalog_with_support,
        )
        original_architecture = g3._golden_architecture
        monkeypatch.setattr(g3, "_catalog", _catalog_with_support)
        def rich_architecture(project_id, conversation_id):
            context, architecture, _ = original_architecture(project_id, conversation_id)
            extra = {"S02_VOLTAGE_MIN_V": "3.2", "S02_VOLTAGE_MAX_V": "3.4", "S02_TEMPERATURE_MIN_C": "20",
                     "S02_TEMPERATURE_MAX_C": "30", "S02_INTERFACE_MODE": "I2C_STANDARD", "S02_CBUS_MAX_F": ".00000000005",
                     "S02_FREQUENCY_HZ": "100000", "S02_DECOUPLING_MIN_F": ".00000008"}
            context = RequirementContext.model_validate({**context.model_dump(mode="python"), "standard_fields": (*context.standard_fields,
                *({'category': k, 'value': v, 'status': "CONFIRMED", 'source_turn_id': "fixture-requirement", 'provenance': "USER_UTTERANCE"} for k,v in extra.items())), "fingerprint": "sha256:" + "0" * 64}, strict=False)
            plan, _ = approve_engineering_plan(generate_engineering_plan(context), reviewer="engineer-1", decided_at=now)
            architecture = ArchitectureDesign.create(**{**architecture.model_dump(mode="python"), "context_fingerprint": context.fingerprint,
                "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint, "status": ArchitectureDesignStatus.PROPOSED, "fingerprint": "sha256:"+"0"*64,
                "evidence_bindings": tuple(ArchitectureEvidenceBinding.create(**{**binding.model_dump(mode="python"), "plan_id": plan.plan_id, "fingerprint": "sha256:"+"0"*64}) for binding in architecture.evidence_bindings)})
            architecture, review = approve_architecture_design(architecture, reviewer="engineer-1", decided_at=now)
            return context, architecture, review
        monkeypatch.setattr(g3, "_golden_architecture", rich_architecture)

    def approve_intent(catalog, project, session):
        detail = catalog.conversation(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
        turn = ConversationTurn(turn_id="turn-design", request_id="s02-request", route="GREENFIELD_DESIGN", status="COMPLETED",
            question="Synthetic offline circuit", answer_text="Fixture design", created_at=now, completed_at=now)
        catalog.persist_conversation_detail(detail.model_copy(update={"turns": (*detail.turns, turn), "fingerprint": "sha256:" + "0" * 64}))
        detail = catalog.conversation(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
        command = _command(detail)
        if rich:
            command = rich_parameters_and_calculations(catalog, project, session, detail, command, now)
        candidate = _service(catalog).preview(project_id=project.project_id, tenant_id="tenant-1", requester="engineer-1", command=command)
        published = _service(catalog).decide(project_id=project.project_id, tenant_id="tenant-1", requester="engineer-1",
            command=PredesignDecisionCommand(purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint, source_fingerprint=candidate.proposal.context_fingerprint, decision="APPROVE"))
        return published.circuit.object_id, published.circuit.revision, published.circuit.fingerprint

    catalog, project, session, _ = g3._runtime_with_approved_plan(tmp_path, before_schematic_plan=approve_intent)
    workspace = WorkspaceApplyService(resolver=ProjectCatalogWorkspaceTargetResolver(catalog), store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace", clock=type("Clock", (), {"now": lambda self: now})())
    execution = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"), artifact_store=ArtifactStore(tmp_path / "artifacts"), clock=lambda: now)
    queued = catalog.decide_schematic_production(project.project_id, session.conversation.conversation_id, "APPROVED", tenant_id="tenant-1", requester="engineer-1",
        decided_at=now, bridge=SchematicExecutionBridge(workspace=workspace, execution=execution, clock=lambda: now))
    catalog._clock = type("Clock", (), {"now": lambda self: datetime.now(UTC)})()
    return catalog, project, session, workspace, execution, queued.schematic_production.operation_id, now



def rich_parameters_and_calculations(catalog, project, session, detail, command, now):
    import hashlib
    import json

    from embedded_copilot.component_recommendation import ComponentSourceRecord
    from embedded_copilot.engineering_copilot import SchematicPlanningService
    from embedded_copilot.engineering_copilot.schematic_evidence import (
        LocalParameterSnapshotPort,
        ParameterCandidate,
        ParameterConditions,
        ParameterLocator,
    )
    from embedded_copilot.engineering_runtime import (
        CalculationInputSource,
        CalculationRecord,
        CircuitBlock,
    )
    draft = SchematicPlanningService().create_proposal(detail.architecture_design, detail.requirement_context,
        detail.hardware_selection.hardware_truth, detail.hardware_selection.bom, design_truth=detail.design_truth,
        conversation_id=detail.conversation_id, selection=detail.hardware_selection)
    selected = detail.hardware_selection.proposal.selected_components
    parameters, originals, reviewed = {}, {}, {}
    constants = {"SUPPLY_MIN": ("3", "V", "MIN"), "SUPPLY_MAX": ("3.6", "V", "MAX"),
        "PULLUP_RESISTANCE": ("10000", "ohm", "EXACT"), "PULLUP_TOLERANCE": (".01", "ratio", "MAX"),
        "RISE_TIME_MAX": (".000001", "s", "MAX"), "BUS_FREQUENCY_MAX": ("100000", "Hz", "MAX"),
        "DECOUPLING_EFFECTIVE_MIN": (".0000001", "F", "MIN"), "VOL_MAX": (".4", "V", "MAX"), "VIL_MAX": (".8", "V", "MAX"),
        "IOL_MIN": (".003", "A", "MIN"), "LEAKAGE_MAX": (".000001", "A", "MAX")}
    for selected_part in selected:
        source = selected_part.source
        uri = "fixture://" + source.source_id
        original = json.dumps({"source": source.model_dump(mode="json"), "values": constants}, sort_keys=True).encode()
        originals[uri] = original
        keys = {"PULLUP_RESISTANCE", "PULLUP_TOLERANCE"} if selected_part.role == "I2C_PULLUPS" else {"DECOUPLING_EFFECTIVE_MIN"} if selected_part.role == "DECOUPLING" else set(constants) - {"PULLUP_RESISTANCE", "PULLUP_TOLERANCE", "DECOUPLING_EFFECTIVE_MIN"} if selected_part.role == "SENSOR" else {"SUPPLY_MIN", "SUPPLY_MAX"}
        for key, (value, unit, bound) in constants.items():
            if key not in keys:
                continue
            candidate = ParameterCandidate(evidence_id="param-" + hashlib.sha256((source.source_id+key).encode()).hexdigest()[:20],
                tenant_id="tenant-1", project_id=project.project_id, conversation_id=session.conversation.conversation_id,
                source_record_id=source.source_id, source_record_fingerprint=source.fingerprint, manufacturer=source.manufacturer, mpn=source.mpn,
                package=source.package, document_uri=uri, document_revision="SYNTHETIC-1", content_sha256="sha256:" + hashlib.sha256(original).hexdigest(),
                locator=ParameterLocator(page=1, table="values", row=key), parameter_key=key, bound_kind=bound, value_decimal=value, unit=unit,
                conditions=ParameterConditions(voltage_min="3.2", voltage_max="3.4", temperature_min="20", temperature_max="30", mode="I2C_STANDARD", package=source.package))
            parameters[(source.source_id, key)] = candidate
    def extract(content, locator):
        row = json.loads(content)
        base = parameters[(row["source"]["source_id"], locator.row)]
        value, unit, bound = row["values"][locator.row]
        return {**base.model_dump(mode="python"), "value_decimal": value, "unit": unit, "bound_kind": bound}
    port = LocalParameterSnapshotPort(read_document=lambda uri, revision: originals[uri], read_locator=extract,
        read_component_source=lambda contents, source_id: ComponentSourceRecord.model_validate(json.loads(contents[0])["source"], strict=False))
    catalog._schematic_parameter_port = port
    for parameter in parameters.values():
        catalog.propose_schematic_parameter(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1", candidate=parameter)
        catalog.review_schematic_parameter(project.project_id, parameter.evidence_id, tenant_id="tenant-1", requester="engineer-1", evidence_fingerprint=parameter.fingerprint, decision="APPROVED")
        reviewed[parameter.evidence_id] = catalog.schematic_parameter(project.project_id, parameter.evidence_id, tenant_id="tenant-1", requester="engineer-1")[0]
    catalog._fixture_parameter_ids = tuple(reviewed)
    catalog._fixture_parameter_port = port
    catalog._fixture_originals = originals
    units = [unit for connection in draft.supporting_connections for unit in connection.fulfillments]
    instances = {item.instance_id: item for item in draft.component_instances}
    sources = {item.slot_id: item.source for item in selected}
    calculations = []
    for index, owner in enumerate(units):
        resistor = next(unit for unit in units if unit.parent_slot_id == owner.parent_slot_id and unit.role == "I2C_PULLUPS" and (owner.role != "I2C_PULLUPS" or unit.support_instance_id == owner.support_instance_id))
        capacitor = next(unit for unit in units if unit.parent_slot_id == owner.parent_slot_id and unit.role == "DECOUPLING")
        parent = next(item for item in draft.component_instances if item.hardware_slot_id == owner.parent_slot_id)
        refs = {"r_ohm": (resistor.support_instance_id, "PULLUP_RESISTANCE"), "r_tolerance": (resistor.support_instance_id, "PULLUP_TOLERANCE"),
            "c_effective_f": (capacitor.support_instance_id, "DECOUPLING_EFFECTIVE_MIN"), **{name: (parent.instance_id,key) for name,key in
            (("rise_limit_s","RISE_TIME_MAX"),("frequency_limit_hz","BUS_FREQUENCY_MAX"),("vol_max_v","VOL_MAX"),("vil_max_v","VIL_MAX"),("iol_min_a","IOL_MIN"),("leakage_max_a","LEAKAGE_MAX"))}}
        inputs = []
        condition = next(iter(parameters.values())).conditions.model_dump_json(exclude={"package"})
        for name, (instance_id, key) in refs.items():
            candidate = parameters[(sources[instances[instance_id].hardware_slot_id].source_id, key)]
            value = reviewed[candidate.evidence_id]
            inputs.append(CalculationInputSource(input_name=name, value=candidate.value_decimal, unit=candidate.unit, source_kind="ENGINEERING_EVIDENCE",
                source_id=candidate.evidence_id, source_fingerprint=value.fingerprint, content_fingerprint=candidate.content_sha256,
                locator=candidate.locator.model_dump_json(), condition=condition, component_instance_id=instance_id))
        for name, category, unit in (("cbus_f","S02_CBUS_MAX_F","F"),("frequency_hz","S02_FREQUENCY_HZ","Hz"),("c_required_f","S02_DECOUPLING_MIN_F","F"),("vmax_v","S02_VOLTAGE_MAX_V","V")):
            value = next(field.value for field in detail.requirement_context.standard_fields if field.category == category)
            inputs.append(CalculationInputSource(input_name=name, value=value, unit=unit, source_kind="CATALOG_REQUIREMENT", source_id=detail.conversation_id,
                source_fingerprint=detail.requirement_context.fingerprint, content_fingerprint=detail.requirement_context.fingerprint, locator=category, condition=condition))
        calculations.append(CalculationRecord(calculation_id=f"fixture-calculation-{index}", owner_ref="block-power", component_instance_id=owner.support_instance_id,
            equation="S02_RC_DECOUPLING_V1", inputs=tuple((item.input_name,item.value) for item in inputs), input_sources=tuple(inputs), corner=condition))
    block = CircuitBlock.model_validate({**command.circuit_intent.blocks[0].model_dump(mode="python"), "calculation_refs": tuple(item.calculation_id for item in calculations), "fingerprint": "sha256:" + "0"*64})
    return command.model_copy(update={"circuit_intent": command.circuit_intent.model_copy(update={"blocks": (block,), "calculations": tuple(calculations)})})

def wait_for(predicate):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("offline Worker did not reach expected state")


def test_formal_app_separate_approval_and_reconciliation(tmp_path, monkeypatch):
    catalog, project, session, workspace, execution, write_id, now = prepare(tmp_path, monkeypatch)
    calls = []

    class Exporter:
        def export_xml(self, operation, input_bytes):
            calls.append(operation.operation_type.value)
            return 0, XML

    adapter = SchematicXmlAdapter(read_input=catalog.read_schematic_operation_input, exporter=Exporter(), tool_executable_sha256="sha256:" + "7" * 64, tool_version="OFFLINE_FAKE", clock=lambda: now)

    def forbidden(*args, **kwargs):
        raise AssertionError("real EDA/provider discovery or process forbidden")

    from embedded_copilot.api import main
    monkeypatch.setattr(main, "discover_kicad_cli", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    def erc_runner(argv, **kwargs):
        from pathlib import Path
        calls.append("KICAD_SCHEMATIC_ERC")
        Path(argv[argv.index("--output") + 1]).write_text("** ERC messages: 0  Errors 0  Warnings 0\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    erc_adapter = KiCadCliSchematicErcAdapter(executable="kicad-cli", workspace_resolver=lambda op: catalog.knowledge_snapshot_root(op.project_id, tenant_id=op.tenant_id), runner=erc_runner, kicad_version="OFFLINE_FAKE")
    app = create_app(settings=Settings(environment="test", project_root=tmp_path / "projects", desktop_state_database=tmp_path / "app.sqlite3", _env_file=None),
        hardware_component_source=catalog._hardware_component_source, project_catalog_runtime=catalog, workspace_apply_port=ProjectCatalogWorkspaceApplyPort(service=workspace, proposals=None), execution_operation_port=execution,
        schematic_parse_adapter=adapter, schematic_readback_adapter=adapter, schematic_erc_port=erc_adapter)
    base = f"/api/v1/copilot/projects/{project.project_id}/conversations/{session.conversation.conversation_id}"
    with TestClient(app) as client:
        wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=write_id))
        parse = wait_for(lambda: next((op for op in execution.store.list_operations("tenant-1", project.project_id, limit=100) if op.operation_type.value == "KICAD_SCHEMATIC_PARSE"), None))
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=parse.operation_id).status.value == "WAIT_APPROVAL"
        assert calls == []
        created = client.post(base + "/schematic-evidence/operations", headers=HEADERS, json={"kind": "PARSE"})
        assert created.status_code == 200, created.text
        assert created.json()["operation_id"] == parse.operation_id
        response = client.post(f"/api/v1/copilot/operations/{parse.operation_id}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id": "engineer-1", "nonce": "parse-only", "expires_at": (now + timedelta(minutes=5)).isoformat()})
        assert response.status_code == 200, response.text
        receipt = wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=parse.operation_id))
        assert receipt.schematic_parse_result.parse_status == "PASS"
        erc = wait_for(lambda: next((op for op in execution.store.list_operations("tenant-1", project.project_id, limit=100) if op.operation_type.value == "KICAD_SCHEMATIC_ERC"), None))
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=erc.operation_id).status.value == "WAIT_APPROVAL"
        assert calls == ["KICAD_SCHEMATIC_PARSE"]
        status_url = f"/api/v1/copilot/operations/{erc.operation_id}/status?project_id={project.project_id}"
        erc_status = client.get(status_url, headers=HEADERS)
        assert erc_status.status_code == 200, erc_status.text
        context = erc_status.json()["schematic_approval_context"]
        assert context["conversation_id"] == session.conversation.conversation_id
        assert context["source_operation_id"] == write_id
        assert context["source_parse_operation_id"] == parse.operation_id
        assert context["source_parse_receipt_fingerprint"] == receipt.fingerprint
        assert context["fingerprint"] == erc.fingerprint
        assert catalog.reconcile_schematic_terminal_receipts() == ()
        assert catalog.reconcile_schematic_terminal_receipts() == ()
        assert calls == ["KICAD_SCHEMATIC_PARSE"]
        assert client.post(base + "/schematic-evidence/operations", headers=HEADERS, json={"kind": "READBACK", "claims": []}).status_code == 422
        readback_response = client.post(base + "/schematic-evidence/operations", headers=HEADERS, json={"kind": "READBACK"})
        assert readback_response.status_code == 200, readback_response.text
        readback_id = readback_response.json()["operation_id"]
        assert calls == ["KICAD_SCHEMATIC_PARSE"]
        approve_readback = client.post(f"/api/v1/copilot/operations/{readback_id}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id": "engineer-1", "nonce": "readback-only", "expires_at": (now + timedelta(minutes=5)).isoformat()})
        assert approve_readback.status_code == 200, approve_readback.text
        readback_receipt = wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=readback_id))
        assert readback_receipt.status.value == "FAILED"
        assert readback_receipt.schematic_readback_result.parse_status == "UNKNOWN"
        root = catalog.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1")
        (root / "hardware/main.kicad_sch").write_bytes(b"stale")
        assert client.get(status_url, headers=HEADERS).json()["schematic_approval_context"] is None
        rejected = client.post(f"/api/v1/copilot/operations/{erc.operation_id}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id": "engineer-1", "nonce": "erc-stale", "expires_at": (now + timedelta(minutes=5)).isoformat()})
        assert rejected.status_code == 422
        assert execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=erc.operation_id) is None


@pytest.mark.parametrize("default_xml_composition", (False, True), ids=("injected", "default-xml"))
def test_formal_shtc3_synthetic_eight_claims_then_source_stale(tmp_path, monkeypatch, default_xml_composition):
    import xml.etree.ElementTree as ET

    from embedded_copilot.api import main
    catalog, project, session, workspace, execution, write_id, now = prepare(tmp_path, monkeypatch, rich=True)
    python_run, python_popen = subprocess.run, subprocess.Popen
    calls = []
    detail = catalog.conversation(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1")
    plan = detail.schematic_planning.approved_plan
    root = ET.Element("export")
    components, nets = ET.SubElement(root, "components"), ET.SubElement(root, "nets")
    libraries = ET.SubElement(root, "libparts")
    seen_symbols = set()
    by_slot = {item.slot_id: item.source for item in detail.hardware_selection.proposal.selected_components}
    references = {item.component_id: item.reference for item in detail.schematic_production.proposal.components}
    for item in plan.component_instances:
        comp = ET.SubElement(components, "comp", ref=references[item.instance_id])
        lib, part = item.symbol.split(":", 1)
        ET.SubElement(comp, "libsource", lib=lib, part=part)
        if item.symbol not in seen_symbols:
            seen_symbols.add(item.symbol)
            pins = ET.SubElement(ET.SubElement(libraries, "libpart", lib=lib, part=part), "pins")
            for pin in by_slot[item.hardware_slot_id].pin_facts:
                ET.SubElement(pins, "pin", num=pin.number, name=pin.name)
        ET.SubElement(comp, "footprint").text = item.footprint
        for name,value in (("Manufacturer",item.manufacturer),("MPN",item.mpn),("Package",by_slot[item.hardware_slot_id].package)):
            ET.SubElement(comp, "property", name=name, value=value)
    for item in plan.nets:
        net = ET.SubElement(nets, "net", name=item.name)
        for node in item.endpoints:
            ET.SubElement(net, "node", ref=references[node.component_id], pin=node.pin_number)
    synthetic_xml = ET.tostring(root)

    class Exporter:
        def export_xml(self, operation, input_bytes):
            calls.append(operation.operation_type.value)
            return 0, synthetic_xml

    def forbidden(*args, **kwargs):
        raise AssertionError("real EDA/provider discovery forbidden")
    if default_xml_composition:
        from pathlib import Path

        from embedded_copilot.execution_worker import schematic_erc, schematic_xml

        binary = tmp_path / "tool" / "kicad-cli.exe"
        binary.parent.mkdir()
        binary.write_bytes(b"OFFLINE_COMPOSITION_FAKE_BINARY")
        monkeypatch.setattr(main, "discover_kicad_cli", lambda **kwargs: binary)
        monkeypatch.setattr(main, "probe_kicad_cli_version", lambda *args, **kwargs: "10.0.5")
        for name in ("discover_kicad_footprint_root", "discover_kicad_symbol_root", "discover_kicad_pcb_drc_command", "discover_kicad_pcb_parse_command"):
            monkeypatch.setattr(main, name, lambda *args, **kwargs: None)

        def xml_runner(argv, **kwargs):
            calls.append("KICAD_SCHEMATIC_XML")
            Path(argv[argv.index("--output") + 1]).write_bytes(synthetic_xml)
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(schematic_xml, "run_kicad_cli", xml_runner)
    else:
        monkeypatch.setattr(main, "discover_kicad_cli", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    def runner(argv, **kwargs):
        from pathlib import Path
        calls.append("KICAD_SCHEMATIC_ERC")
        Path(argv[argv.index("--output")+1]).write_text("** ERC messages: 0  Errors 0  Warnings 0\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
    if default_xml_composition:
        monkeypatch.setattr(schematic_erc, "run_kicad_cli", runner)
        schematic_adapters = {}
    else:
        erc_adapter = KiCadCliSchematicErcAdapter(executable="kicad-cli", workspace_resolver=lambda op: catalog.knowledge_snapshot_root(op.project_id, tenant_id=op.tenant_id), runner=runner, kicad_version="OFFLINE_FAKE")
        adapter = SchematicXmlAdapter(read_input=catalog.read_schematic_operation_input, exporter=Exporter(), tool_executable_sha256="sha256:"+"8"*64, tool_version="OFFLINE_FAKE")
        schematic_adapters = {"schematic_parse_adapter": adapter, "schematic_readback_adapter": adapter, "schematic_erc_port": erc_adapter}
    app = create_app(settings=Settings(environment="test", project_root=tmp_path/"projects", desktop_state_database=tmp_path/"app.sqlite3", _env_file=None),
        hardware_component_source=catalog._hardware_component_source, project_catalog_runtime=catalog,
        workspace_apply_port=ProjectCatalogWorkspaceApplyPort(service=workspace, proposals=None), execution_operation_port=execution,
        schematic_parameter_port=catalog._fixture_parameter_port, **schematic_adapters)
    base = f"/api/v1/copilot/projects/{project.project_id}/conversations/{session.conversation.conversation_id}"
    def approve(client, operation_id):
        result = client.post(f"/api/v1/copilot/operations/{operation_id}/approval", params={"project_id": project.project_id}, headers=HEADERS,
            json={"reviewer_id":"engineer-1", "nonce":"approve-"+operation_id, "expires_at":(now+timedelta(minutes=10)).isoformat()})
        assert result.status_code == 200, result.text
        return wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=operation_id))
    with TestClient(app) as client:
        parameter_id = catalog._fixture_parameter_ids[0]
        reviewed, _ = catalog.schematic_parameter(project.project_id, parameter_id, tenant_id="tenant-1", requester="engineer-1")
        parameter_url = f"/api/v1/copilot/projects/{project.project_id}/schematic-parameters/{parameter_id}"
        parameter_get = client.get(parameter_url, headers=HEADERS)
        assert parameter_get.status_code == 200, parameter_get.text
        assert parameter_get.json()["currentness"] == "CURRENT"
        parameter_post = client.post(base + "/schematic-parameters", headers=HEADERS, json=reviewed.candidate.model_dump(mode="json"))
        assert parameter_post.status_code == 200, parameter_post.text
        assert parameter_post.json()["fingerprint"] == reviewed.candidate.fingerprint
        review_post = client.post(parameter_url + "/review", headers=HEADERS,
            json={"decision": "APPROVED", "evidence_fingerprint": reviewed.candidate.fingerprint})
        assert review_post.status_code == 200, review_post.text
        assert review_post.json()["fingerprint"] == reviewed.review.fingerprint
        rejected_review = client.post(parameter_url + "/review", headers=HEADERS,
            json={"decision": "REJECTED", "evidence_fingerprint": reviewed.candidate.fingerprint})
        assert rejected_review.status_code == 409, rejected_review.text
        wait_for(lambda: execution.get_receipt(tenant_id="tenant-1", project_id=project.project_id, operation_id=write_id))
        parse = wait_for(lambda: next((op for op in execution.store.list_operations("tenant-1", project.project_id, limit=100) if op.operation_type.value=="KICAD_SCHEMATIC_PARSE"),None))
        assert calls == []
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=parse.operation_id).status.value == "WAIT_APPROVAL"
        assert approve(client, parse.operation_id).status.value == "SUCCEEDED"
        assert calls == (["KICAD_SCHEMATIC_XML"] if default_xml_composition else ["KICAD_SCHEMATIC_PARSE"])
        erc = wait_for(lambda: next((op for op in execution.store.list_operations("tenant-1", project.project_id, limit=100) if op.operation_type.value=="KICAD_SCHEMATIC_ERC"),None))
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=erc.operation_id).status.value == "WAIT_APPROVAL"
        assert len(calls) == 1
        assert approve(client, erc.operation_id).schematic_erc_result.status.value == "ERC_PASS"
        assert len(calls) == 2
        readback = client.post(base+"/schematic-evidence/operations",headers=HEADERS,json={"kind":"READBACK"})
        assert readback.status_code == 200, readback.text
        readback_id = readback.json()["operation_id"]
        assert execution.get_status(tenant_id="tenant-1", project_id=project.project_id, operation_id=readback_id).status.value == "WAIT_APPROVAL"
        assert len(calls) == 2
        assert approve(client, readback_id).status.value == "SUCCEEDED"
        assert len(calls) == 3
        wait_for(lambda: catalog._repository.schematic_evidence(project_id=project.project_id, tenant_id="tenant-1", requester="engineer-1", kind="READBACK",evidence_id=readback_id))
        detail = catalog.conversation(project.project_id, session.conversation.conversation_id,tenant_id="tenant-1",requester="engineer-1")
        generation = detail.schematic_production.generation_receipt
        selectors = {"intent_id":plan.circuit_intent_id,"intent_revision":plan.circuit_intent_revision,"intent_fingerprint":plan.circuit_intent_fingerprint,
            "plan_id":plan.plan_id,"plan_fingerprint":plan.fingerprint,"artifact_fingerprint":generation.artifact_fingerprint,
            "generation_operation_id":write_id,"parse_operation_id":parse.operation_id,"erc_operation_id":erc.operation_id,
            "readback_operation_id":readback_id,"parameter_evidence_ids":list(catalog._fixture_parameter_ids)}
        response = client.post(base+"/schematic-verifications",headers=HEADERS,json=selectors)
        assert response.status_code == 200, response.text
        record = response.json()["record"]
        assert record["aggregate_status"] == "SCHEMATIC_VERIFIED", record["claims"]
        assert len(record["claims"]) == 8
        assert all(claim["status"]=="PASS" for claim in record["claims"])
        missing = client.post(base+"/schematic-verifications",headers=HEADERS,json={**selectors,"parameter_evidence_ids":[]})
        assert missing.status_code == 200, missing.text
        assert missing.json()["record"]["aggregate_status"] == "BLOCKED"
        assert len(missing.json()["record"]["claims"]) == 8
        assert client.post(base+"/schematic-verifications",headers=HEADERS,json={**selectors,"required_claim_ids":["S02-I2C-08"]}).status_code==422
        url=f"/api/v1/copilot/projects/{project.project_id}/schematic-verifications/{record['verification_id']}"
        current_material = catalog._s02_verification_material(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1", **selectors)
        assert current_material[-1] == record["context_fingerprint"], (current_material[-2:], record["context_fingerprint"])
        assert client.get(url,headers=HEADERS).json()["currentness"]=="CURRENT"
        # A distinct interpreter reloads all canonical receipts, reviews and blobs.
        # Only synthetic source bytes are transferred; no EDA/provider discovery.
        snapshot = tmp_path / "synthetic-source-snapshot.json"
        snapshot.write_text(json.dumps({
            "originals": {uri: content.decode() for uri, content in catalog._fixture_originals.items()},
        }), encoding="utf-8")
        script = textwrap.dedent("""
            import json, os, sys, subprocess
            from pathlib import Path
            from embedded_copilot.component_recommendation import ComponentSourceRecord
            from embedded_copilot.engineering_copilot.schematic_evidence import LocalParameterSnapshotPort, ParameterCandidate
            from embedded_copilot.project_catalog import ProjectCatalogRuntime
            from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
            from embedded_copilot.execution_worker import ExecutionOperationService, SQLiteExecutionStore, ExecutionWorker
            from embedded_copilot.execution_worker.artifacts import ArtifactStore
            def forbidden(*args, **kwargs):
                raise AssertionError('real EDA/provider forbidden in recovery interpreter')
            subprocess.Popen = forbidden
            p = Path(sys.argv[1])
            data = json.loads((p / 'synthetic-source-snapshot.json').read_text(encoding='utf-8'))
            c = ProjectCatalogRuntime(project_root=p/'projects', state_database=p/'desktop.sqlite3', initialize_async=False)
            c.set_hardware_component_source(PublishedComponentSourcePort(c._repository, tenant_id='tenant-1', publisher_id='g3-fixture-publisher'))
            e = ExecutionOperationService(store=SQLiteExecutionStore(p/'execution.sqlite3'), artifact_store=ArtifactStore(p/'artifacts'))
            record = c._repository.schematic_verification(sys.argv[2], sys.argv[3], tenant_id='tenant-1', requester='engineer-1')
            parameters = tuple(ParameterCandidate.model_validate_json(c._repository.schematic_evidence(project_id=sys.argv[2], tenant_id='tenant-1', requester='engineer-1', kind='PARAMETER', evidence_id=identity), strict=False) for identity in record.parameter_evidence_ids)
            def extract(content, locator):
                original = json.loads(content)
                candidate = next(item for item in parameters if item.source_record_id == original['source']['source_id'] and item.parameter_key == locator.row)
                value, unit, bound = original['values'][locator.row]
                return ParameterCandidate.model_validate({**candidate.model_dump(mode='python'), 'value_decimal':value, 'unit':unit, 'bound_kind':bound})
            port = LocalParameterSnapshotPort(read_document=lambda uri, revision: data['originals'][uri].encode(), read_locator=extract,
                read_component_source=lambda contents, source_id: ComponentSourceRecord.model_validate(json.loads(contents[0])['source'], strict=False))
            c.set_schematic_evidence_context(execution=e, parameter_port=port)
            w = ExecutionWorker(worker_id='s02-reopened', store=e.store, registry=None, workspace_resolver=None, targets=None, design_context_revalidator=c.revalidate_circuit_bound_operation)
            assert w.recover_after_restart() == ()
            assert c.reconcile_schematic_terminal_receipts() == ()
            assert w.run_once() is None
            reopened, currentness = c.schematic_verification(sys.argv[2], sys.argv[3], tenant_id='tenant-1', requester='engineer-1')
            print(json.dumps({'pid':os.getpid(), 'fingerprint':reopened.fingerprint, 'currentness':currentness}))
        """)
        def python_only(argv, *args, **kwargs):
            assert isinstance(argv, list) and argv[0] == sys.executable
            return python_popen(argv, *args, **kwargs)
        with monkeypatch.context() as child_patch:
            child_patch.setattr(subprocess, "Popen", python_only)
            reopened = python_run([sys.executable, "-c", script, str(tmp_path), project.project_id, record["verification_id"]], capture_output=True, text=True, timeout=35, check=False)
        assert reopened.returncode == 0, reopened.stderr
        restored = json.loads(reopened.stdout.splitlines()[-1])
        assert restored["pid"] != os.getpid()
        assert restored["fingerprint"] == record["fingerprint"]
        assert restored["currentness"] == "CURRENT"
        uri = next(iter(catalog._fixture_originals))
        catalog._fixture_originals[uri]=b"replaced source"
        stale=client.get(url,headers=HEADERS).json()
        assert stale["currentness"]=="STALE"
        assert stale["record"]["fingerprint"]==record["fingerprint"]
        assert calls == (["KICAD_SCHEMATIC_XML", "KICAD_SCHEMATIC_ERC", "KICAD_SCHEMATIC_XML"] if default_xml_composition
                         else ["KICAD_SCHEMATIC_PARSE", "KICAD_SCHEMATIC_ERC", "KICAD_SCHEMATIC_READ_BACK"])
