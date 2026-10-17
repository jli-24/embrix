"""Offline Worker persistence gates; typed synthetic XML/observation, no engineering proof."""
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import ArtifactIdentity, OperationRequest, OperationStatus, SchematicReadBackOperation, SchematicParseOperation, SchematicXmlResult
from embedded_copilot.execution_worker.ports import AdapterResult
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from tests.execution_worker.test_s02_evidence_contracts import operation
from tests.execution_worker.test_p3_library_observation_contracts import _payload, FP


def _run(tmp_path, monkeypatch, fault=None, model=SchematicReadBackOperation, paired=True):
    now=datetime.now(UTC)
    clock=[now]
    selectors=dict(library_map_evidence_id="map", library_map_evidence_fingerprint=FP) if paired else {}
    op=operation(model, created_at=now, expires_at=now+timedelta(minutes=10), **selectors)
    xml=b"synthetic XML fixture; not real EDA observation"
    def identity(kind, raw, artifact_id):
        return ArtifactIdentity(artifact_id=artifact_id,tenant_id=op.tenant_id,project_id=op.project_id,
            target_id=op.target_id,workspace_fingerprint=op.workspace_fingerprint,build_operation_id=op.operation_id,
            toolchain_id="KICAD_XML_NETLIST",toolchain_version="9.0",build_configuration="READ_ONLY_XML",
            artifact_type=kind,artifact_sha256="sha256:"+hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),created_at=now)
    primary=identity("NETLIST",xml,"xml")
    payload=_payload()
    payload.update(source_evidence_id="map",source_evidence_fingerprint=FP)
    if fault=="source":payload["source_evidence_id"]="other"
    raw=json.dumps(payload,sort_keys=True,separators=(",",":")).encode()
    additional=identity("MANIFEST",raw,"observation")
    if fault=="size":additional=ArtifactIdentity.model_validate({**additional.model_dump(mode="python"),"size_bytes":len(raw)+1,"fingerprint":"sha256:"+"0"*64})
    if fault=="scope":additional=ArtifactIdentity.model_validate({**additional.model_dump(mode="python"),"tenant_id":"other","fingerprint":"sha256:"+"0"*64})
    if fault=="kind":additional=identity("NETLIST",raw,"observation")
    evidence=SchematicXmlResult(operation_id=op.operation_id,source_write_operation_id=op.source_write_operation_id,
        source_write_receipt_fingerprint=op.source_write_receipt_fingerprint,input_artifact_path=op.artifact_path,
        fingerprint_before=op.artifact_fingerprint,fingerprint_after=op.artifact_fingerprint,tool_executable_sha256=FP,
        tool_version="9.0",command_identity="kicad-cli sch export netlist --format kicadxml",netlist_artifact=primary,
        coverage=("references","part_properties","physical_pin_numbers","nets"),loss_or_unknown=(),exit_code=0,parse_status="PASS",created_at=now)
    extras=() if fault=="missing" else ((additional,raw),)* (2 if fault=="multiple" else 1)
    result=AdapterResult(status="SUCCEEDED",summary="synthetic",artifact=primary,artifact_content=xml,
        additional_artifacts=extras,evidence_references=() if fault=="receipt_member" else (additional.artifact_id,),schematic_xml_result=evidence)
    store=SQLiteExecutionStore(tmp_path/"execution.sqlite3")
    artifacts=ArtifactStore(tmp_path/"artifacts")
    service=ExecutionOperationService(store=store,artifact_store=artifacts,schematic_evidence_authorizer=lambda _:True,clock=lambda:clock[0])
    request=OperationRequest(operation=op)
    service.create_operation(request)
    approval=service.issue_approval(operation=request,reviewer_id="reviewer",nonce="approval-nonce",expires_at=now+timedelta(minutes=5))
    service.enqueue(operation=request,approval_id=approval.grant_id)
    calls=[]
    valid=[True]
    def authorize(*args):
        calls.append(args)
        if fault=="authorize_db":raise sqlite3.OperationalError("offline candidate database fault")
        return valid[0] and fault!="unauthorized"
    original_save=store.save_artifact
    def save(value):
        if value.artifact_type=="MANIFEST":
            if fault=="save_os":raise OSError("offline save fault")
            if fault=="save_db":raise sqlite3.OperationalError("offline database fault")
        saved=original_save(value)
        if value.artifact_type=="MANIFEST":
            if fault=="revoke_after_save":valid[0]=False
            if fault=="expire_after_save":clock[0]=now+timedelta(minutes=6)
            if fault=="cancel_after_save":
                from embedded_copilot.execution_worker.contracts import CancellationRequest
                service.request_cancellation(CancellationRequest(request_id="cancel",tenant_id=op.tenant_id,project_id=op.project_id,
                    operation_id=op.operation_id,requester_id=op.requester_id,reason="offline cancellation",requested_at=now))
        return saved
    monkeypatch.setattr(store,"save_artifact",save)
    class Adapter:
        def execute(self, operation):return result
    worker=ExecutionWorker(worker_id="worker",store=store,registry=None,workspace_resolver=None,targets=None,
        artifact_store=artifacts,schematic_readback=Adapter(),schematic_parse=Adapter(),design_context_revalidator=lambda _:True,
        library_observation_authorizer=None if fault=="no_authorizer" else authorize,clock=lambda:clock[0])
    status=worker.run_once()
    return status,store,artifacts,op,calls,worker


def test_paired_readback_registers_exact_receipt_member_and_does_not_replay(tmp_path,monkeypatch):
    status,store,artifacts,op,calls,worker=_run(tmp_path,monkeypatch)
    assert status.status is OperationStatus.SUCCEEDED
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.evidence_references==("observation",)
    registered=store.get_artifact(op.tenant_id,op.project_id,"observation")
    assert len(artifacts.read(registered))==registered.size_bytes
    assert len(calls)>=3
    assert worker.run_once() is None


@pytest.mark.parametrize("fault",("missing","multiple","source","size","scope","kind","receipt_member","unauthorized","authorize_db","no_authorizer","save_os","save_db","revoke_after_save","expire_after_save","cancel_after_save"))
def test_paired_readback_rejects_invalid_or_late_observation(tmp_path,monkeypatch,fault):
    status,store,artifacts,op,calls,worker=_run(tmp_path,monkeypatch,fault)
    assert status.status is not OperationStatus.SUCCEEDED
    if fault=="cancel_after_save":
        assert status.status is OperationStatus.CANCELLED
        assert status.cancellation_requested
    elif fault=="expire_after_save":
        assert status.status is OperationStatus.TIMED_OUT
    elif fault=="revoke_after_save":
        assert status.status is OperationStatus.FAILED
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt is None or receipt.status is not OperationStatus.SUCCEEDED
    if fault=="authorize_db":
        assert status.status is OperationStatus.FAILED
        assert status.safe_detail=="library observation database read rejected"
        assert receipt is None
        assert store.get_artifact(op.tenant_id,op.project_id,"observation") is None
        assert store.get_artifact(op.tenant_id,op.project_id,"xml") is None
    assert worker.run_once() is None


@pytest.mark.parametrize("model",(SchematicParseOperation,SchematicReadBackOperation))
def test_unpaired_xml_operation_rejects_any_library_additional_artifact(tmp_path,monkeypatch,model):
    status,*_=_run(tmp_path,monkeypatch,model=model,paired=False)
    assert status.status is OperationStatus.FAILED
