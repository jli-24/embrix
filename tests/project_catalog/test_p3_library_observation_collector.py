"""Actual PDF/private library reads; canonical write/reference are explicit offline seams."""
from datetime import timedelta
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import hashlib

import pytest

from embedded_copilot.execution_worker.contracts import OperationRequest,SchematicReadBackOperation,parse_library_observation_payload
from embedded_copilot.execution_worker.service import ExecutionOperationService,ExecutionRejected
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
from tests.project_catalog.test_p3_library_terminal_maps import _runtime,NOW
from tests.execution_worker.test_s02_evidence_contracts import operation
from tests.engineering_copilot.test_p3_library_observation import XML


def _collector(tmp_path,monkeypatch):
    catalog,detail,values,root=_runtime(tmp_path,monkeypatch)
    scope=dict(tenant_id=detail.tenant_id,requester=detail.requester)
    candidate=catalog.create_library_terminal_map(detail.project_id,detail.conversation_id,values=values,**scope)
    reviewed=catalog.review_library_terminal_map(detail.project_id,candidate.evidence.evidence_id,
        evidence_fingerprint=candidate.evidence.fingerprint,decision="APPROVED",**scope)
    current=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    plan=current.schematic_planning.approved_plan
    # U5 is synthetic production-reference identity only; source/Plan are static fixtures, not real工程工程原件.
    material=current.model_copy(update={"schematic_production":SimpleNamespace(proposal=SimpleNamespace(
        components=(SimpleNamespace(reference="U5",component_id=values["component_instance_id"]),)))})
    op=operation(SchematicReadBackOperation,tenant_id=detail.tenant_id,project_id=detail.project_id,
        requester_id=detail.requester,conversation_id=detail.conversation_id,proposal_id=plan.plan_id,
        proposal_fingerprint=plan.fingerprint,schematic_plan_id=plan.plan_id,schematic_plan_fingerprint=plan.fingerprint,
        library_map_evidence_id=reviewed.evidence.evidence_id,library_map_evidence_fingerprint=reviewed.evidence.fingerprint,
        artifact_fingerprint="sha256:"+hashlib.sha256(b"offline schematic fixture").hexdigest())
    service=ExecutionOperationService(store=SQLiteExecutionStore(tmp_path/"execution.sqlite3"),artifact_store=ArtifactStore(tmp_path/"artifacts"),schematic_evidence_authorizer=lambda _:True,clock=lambda:op.created_at)
    service.create_operation(OperationRequest(operation=op))
    catalog._schematic_evidence_execution=service
    source=SimpleNamespace(operation_id=op.source_write_operation_id,target_id=op.target_id,target_fingerprint=op.target_fingerprint,
        workspace_snapshot_id=op.workspace_snapshot_id,workspace_fingerprint=op.workspace_fingerprint)
    generation=SimpleNamespace(artifact_path=op.artifact_path,artifact_fingerprint=op.artifact_fingerprint,fingerprint=op.artifact_fingerprint)
    def canonical(*args,**kwargs):
        latest=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
        latest=latest.model_copy(update={"schematic_production":material.schematic_production})
        return latest,plan,generation,source,SimpleNamespace(fingerprint=op.source_write_receipt_fingerprint)
    monkeypatch.setattr(catalog,"_canonical_schematic_sources",canonical)
    tree=ET.fromstring(XML)
    bindings={item.logical_symbol_pin:item.net_id for item in values["terminal_bindings"]}
    names={item.net_id:item.name for item in plan.nets}
    for net in tree.findall("./nets/net"):
        net.set("name",names[bindings[net.find("node").get("pin")]])
    return catalog,op,ET.tostring(tree),reviewed,root


def test_collector_derives_actual_pin_pad_and_raw_xml_nets_without_expected_fill(tmp_path,monkeypatch):
    catalog,op,xml,reviewed,_root=_collector(tmp_path,monkeypatch)
    identity,raw=catalog.collect_schematic_library_observation(op,xml,created_at=NOW,tool_version="9.0")
    observed=parse_library_observation_payload(raw,identity)
    assert observed.observed_symbol_pins==("1","2","3","4","5")
    assert observed.observed_physical_pad_ids==("1","2","3","4","5")
    assert {item.manufacturer_terminal for item in observed.observed_net_bindings}=={"1","2","3","4","EP"}
    assert catalog.revalidate_library_observation_payload(op,identity,raw,xml)
    tree=ET.fromstring(xml)
    supply=next(net for net in tree.findall("./nets/net") if any(node.get("pin")=="1" for node in net.findall("node")))
    ground=next(net for net in tree.findall("./nets/net") if any(node.get("pin")=="5" for node in net.findall("node")))
    ep=next(node for node in ground.findall("node") if node.get("pin")=="5")
    ground.remove(ep);supply.append(ep)
    changed=ET.tostring(tree)
    changed_identity,changed_raw=catalog.collect_schematic_library_observation(op,changed,created_at=NOW,tool_version="9.0")
    actual=parse_library_observation_payload(changed_raw,changed_identity)
    observed_ep=next(item for item in actual.observed_net_bindings if item.manufacturer_terminal=="EP")
    expected_ep=next(item for item in reviewed.evidence.terminal_bindings if item.manufacturer_terminal=="EP")
    assert observed_ep.net_id!=expected_ep.net_id
    assert not catalog.revalidate_library_observation_payload(op,identity,raw,changed)


@pytest.mark.parametrize("fault",("library_bytes","unknown_net","missing_ep","wrong_operation","source_sha"))
def test_collector_rejects_unavailable_original_or_xml_binding(tmp_path,monkeypatch,fault):
    catalog,op,xml,_reviewed,root=_collector(tmp_path,monkeypatch)
    if fault=="library_bytes":(root/"symbol.kicad_sym").write_bytes(b"changed")
    elif fault=="unknown_net":xml=xml.replace(b'name="',b'name="UNKNOWN_',1)
    elif fault=="missing_ep":xml=xml.replace(b'<node ref="U5" pin="5" />',b'')
    elif fault=="wrong_operation":op=op.model_copy(update={"operation_id":"other"})
    else:
        op=op.model_copy(update={"library_map_evidence_fingerprint":"sha256:"+"9"*64})
    with pytest.raises((ProjectCatalogError,ValueError)):
        catalog.collect_schematic_library_observation(op,xml,created_at=NOW,tool_version="9.0")


def _run_collector_worker(catalog,op,xml):
    from embedded_copilot.execution_worker.schematic_xml import SchematicXmlAdapter
    from embedded_copilot.execution_worker.worker import ExecutionWorker
    service=catalog._schematic_evidence_execution
    request=OperationRequest(operation=op)
    grant=service.issue_approval(operation=request,reviewer_id="reviewer",nonce="approval-library",expires_at=op.created_at+timedelta(minutes=5))
    service.enqueue(operation=request,approval_id=grant.grant_id)
    class Exporter:
        def export_xml(self,operation,input_bytes):return 0,xml
    adapter=SchematicXmlAdapter(read_input=lambda _:b"offline schematic fixture",exporter=Exporter(),
        tool_executable_sha256=op.artifact_fingerprint,tool_version="9.0",clock=lambda:op.created_at,
        library_observer=catalog.collect_schematic_library_observation)
    worker=ExecutionWorker(worker_id="collector-worker",store=service._store,registry=None,workspace_resolver=None,targets=None,
        artifact_store=service.artifact_store,schematic_readback=adapter,clock=lambda:op.created_at,
        design_context_revalidator=catalog.revalidate_schematic_evidence_operation,
        library_observation_authorizer=catalog.revalidate_library_observation_payload)
    status=worker.run_once()
    assert status.status.value=="SUCCEEDED"
    return service.get_receipt(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id)


def test_actual_collector_worker_registered_receipt_and_catalog_observation_consumer(tmp_path,monkeypatch):
    catalog,op,xml,reviewed,_root=_collector(tmp_path,monkeypatch)
    receipt=_run_collector_worker(catalog,op,xml)
    material,passed=catalog._library_verification_observation(op,receipt,xml,(reviewed.evidence.evidence_id,),(reviewed.evidence.fingerprint,))
    assert passed
    assert material["review"]==reviewed.review.fingerprint
    assert receipt.evidence_references and len(receipt.evidence_references)==1


@pytest.mark.parametrize("fault",("selector","receipt_member","unregistered","raw_drift","io_original_drift","xml_size"))
def test_catalog_library_consumer_rejects_member_identity_and_io_drift(tmp_path,monkeypatch,fault):
    catalog,op,xml,reviewed,root=_collector(tmp_path,monkeypatch)
    receipt=_run_collector_worker(catalog,op,xml)
    ids=(reviewed.evidence.evidence_id,);fps=(reviewed.evidence.fingerprint,)
    execution=catalog._schematic_evidence_execution
    if fault=="selector":ids=("other",)
    elif fault=="receipt_member":receipt=receipt.model_copy(update={"evidence_references":()})
    elif fault=="unregistered":
        old=execution.get_artifact
        monkeypatch.setattr(execution,"get_artifact",lambda **kw:None if kw["artifact_id"]==receipt.evidence_references[0] else old(**kw))
    else:
        old=execution.artifact_store.read
        def read(identity):
            data=old(identity)
            if identity.artifact_type=="MANIFEST":
                if fault=="raw_drift":return data+b" "
                if fault=="io_original_drift":(root/"symbol.kicad_sym").write_bytes(b"changed during artifact read")
            if fault=="xml_size" and identity.artifact_type=="NETLIST":return data+b" "
            return data
        monkeypatch.setattr(execution.artifact_store,"read",read)
    original_receipt=execution._store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    original_status=execution._store.get_status(op.tenant_id,op.project_id,op.operation_id)
    with pytest.raises((ProjectCatalogError,ValueError,ExecutionRejected)) as rejected:
        catalog._library_verification_observation(op,receipt,xml,ids,fps)
    if fault in {"raw_drift","xml_size"}:
        assert type(rejected.value) is ExecutionRejected
        assert str(rejected.value)=="artifact size mismatch"
    assert execution._store.get_receipt(op.tenant_id,op.project_id,op.operation_id)==original_receipt
    assert execution._store.get_status(op.tenant_id,op.project_id,op.operation_id)==original_status


def test_profile2_persists_explicit_selectors_eight_blocked_claims_and_tracks_review_generation(tmp_path,monkeypatch):
    from embedded_copilot.project_catalog.models import ConversationDetail,LibraryTerminalMapReviewV2,ReviewedLibraryTerminalMapV2
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
    from embedded_copilot.engineering_copilot.schematic_evidence import REQUIRED_CLAIM_IDS
    catalog,op,xml,reviewed,root=_collector(tmp_path,monkeypatch)
    receipt=_run_collector_worker(catalog,op,xml)
    monkeypatch.setattr(catalog,"schematic_xml_receipt",lambda *a,**kw:receipt)
    # XML fixture contains just the sensor, so complete-plan connectivity must remain blocked.
    monkeypatch.setattr("embedded_copilot.engineering_copilot.schematic_evidence.compare_plan_xml",lambda *a:False)
    detail=catalog.conversation(op.project_id,op.conversation_id,tenant_id=op.tenant_id,requester=op.requester_id)
    plan=detail.schematic_planning.approved_plan
    selectors=dict(tenant_id=op.tenant_id,requester=op.requester_id,intent_id=plan.circuit_intent_id,
        intent_revision=plan.circuit_intent_revision,intent_fingerprint=plan.circuit_intent_fingerprint,
        plan_id=plan.plan_id,plan_fingerprint=plan.fingerprint,artifact_fingerprint=op.artifact_fingerprint,
        generation_operation_id=op.source_write_operation_id,erc_operation_id=None,parse_operation_id=None,
        readback_operation_id=op.operation_id,parameter_evidence_ids=(),library_map_evidence_ids=(reviewed.evidence.evidence_id,),
        library_map_evidence_fingerprints=(reviewed.evidence.fingerprint,))
    record=catalog.record_schematic_verification(op.project_id,op.conversation_id,**selectors)
    assert record.profile_version==2 and record.library_map_evidence_ids==selectors["library_map_evidence_ids"]
    assert tuple(item.claim_id for item in record.claims)==REQUIRED_CLAIM_IDS
    assert all(item.status=="BLOCKED" for item in record.claims)
    assert record.aggregate_status=="BLOCKED"
    catalog._repository=SQLiteProjectCatalogRepository(catalog._repository._database)
    assert catalog.schematic_verification(op.project_id,record.verification_id,tenant_id=op.tenant_id,requester=op.requester_id)==(record,"CURRENT")
    values=reviewed.review.model_dump(mode="python");values.update(review_id="replacement-review",fingerprint="sha256:"+"0"*64)
    replacement=ReviewedLibraryTerminalMapV2(evidence=reviewed.evidence,review=LibraryTerminalMapReviewV2(**values))
    body=detail.model_dump(mode="python");body.update(library_terminal_maps=(replacement,),fingerprint="sha256:"+"0"*64)
    catalog.persist_conversation_detail(ConversationDetail.model_validate(body))
    assert catalog.schematic_verification(op.project_id,record.verification_id,tenant_id=op.tenant_id,requester=op.requester_id)[1]=="STALE"
