"""Synthetic complete canonical Worker/Store chain; no real collector or KiCad."""
from datetime import timedelta
import pytest
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.contracts import OperationRequest,OperationStatus,ApprovalStatus,RecoveryState
from tests.execution_worker.test_drc_baseline_collector import fixture


def setup(tmp_path):
    collector,op,calls,_=fixture(tmp_path)
    store=SQLiteExecutionStore(tmp_path/"execution.sqlite3");artifacts=ArtifactStore(tmp_path/"artifacts");live=[True]
    service=ExecutionOperationService(store=store,artifact_store=artifacts,clock=lambda:op.created_at,drc_baseline_revalidator=lambda _:live[0])
    service.create_operation(OperationRequest(operation=op))
    grant=service.issue_approval_for_id(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id,reviewer_id=op.requester_id,nonce="grant-nonce",expires_at=op.expires_at)
    service.enqueue_by_id(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id,approval_id=grant.grant_id)
    worker=ExecutionWorker(worker_id="baseline-worker",store=store,registry=None,workspace_resolver=None,targets=None,artifact_store=artifacts,drc_baseline_collector=collector,drc_baseline_revalidator=lambda _:live[0],recovery_workspace=collector.workspace,clock=lambda:op.created_at)
    return worker,store,artifacts,op,grant,calls,live


def test_single_fake_complete_receipt_actual_counts_and_restart(tmp_path):
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    status=worker.run_once()
    assert status.status is OperationStatus.SUCCEEDED and len(calls)==1
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.safe_diagnostics_summary=="DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count=0 warning_count=1"
    assert receipt.artifact is not None and receipt.pcb_drc_result is None and not receipt.workspace_mutations
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.CONSUMED
    assert all(store.get_artifact(op.tenant_id,op.project_id,ref) is not None for ref in receipt.evidence_references)
    assert SQLiteExecutionStore(store._database).get_receipt(op.tenant_id,op.project_id,op.operation_id)==receipt
    assert worker.run_once() is None and worker.recover_after_restart()==()


def test_revoked_before_consume_preserves_issued_grant_and_zero_tool(tmp_path):
    worker,store,_,op,grant,calls,live=setup(tmp_path);live[0]=False
    assert worker.run_once().status is OperationStatus.FAILED
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.ISSUED
    assert calls==[]


@pytest.mark.parametrize("drift",("authority","workspace","cancel","expiry"))
def test_member_persistence_slow_io_prevents_success_and_keeps_partial_history(tmp_path,drift):
    worker,store,artifacts,op,grant,calls,live=setup(tmp_path)
    original=artifacts.put
    def changed(identity,content):
        value=original(identity,content)
        if drift=="authority":live[0]=False
        elif drift=="workspace":(tmp_path/"project"/"main.c").write_bytes(b"changed")
        elif drift=="expiry":worker._clock=lambda:op.expires_at
        else:
            original_status=store.get_status(op.tenant_id,op.project_id,op.operation_id)
            store.save_status(original_status.model_validate({**original_status.model_dump(mode="python"),"cancellation_requested":True,"fingerprint":"sha256:"+"0"*64}))
        return value
    artifacts.put=changed
    status=worker.run_once()
    assert status.status is not OperationStatus.SUCCEEDED and len(calls)==1
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.CONSUMED
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.artifact is None
    assert op.operation_id+"-raw-report" in receipt.evidence_references
    assert op.operation_id+"-workspace-after" in receipt.evidence_references
    assert artifacts.read(store.get_artifact(op.tenant_id,op.project_id,op.operation_id+"-raw-report"))
    assert worker.run_once() is None and worker.recover_after_restart()==()


@pytest.mark.parametrize("change",(None,"main.c","board.kicad_prl","board.kicad_pcb"))
def test_restart_classifies_actual_whole_workspace_and_prl_without_replay(tmp_path,change):
    worker,store,_,op,grant,calls,_=setup(tmp_path)
    assert store.claim_next(worker_id=worker.worker_id,lease_seconds=60,now=op.created_at)
    assert store.start_operation_transition(op,approval_id=grant.grant_id,worker_id=worker.worker_id,now=op.created_at,baseline_revalidate=lambda:True)
    if change:(tmp_path/"project"/change).write_bytes(b"changed")
    status=worker.recover_after_restart()[0]
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    assert status.recovery_state is (RecoveryState.INTERRUPTED if change is None else RecoveryState.TARGET_STATE_UNKNOWN)
    assert calls==[] and worker.run_once() is None
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.CONSUMED


@pytest.mark.parametrize("cause",("workspace","expiry","cancel"))
def test_collector_failed_observation_is_persisted_without_success_or_replay(tmp_path,cause):
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    fake=worker._drc_baseline_collector._runner
    original=fake.run_drc_baseline
    def failed(argv,**kwargs):
        value=original(argv,**kwargs)
        if cause=="workspace":(tmp_path/"project"/"main.c").write_bytes(b"side-effect")
        elif cause=="expiry":worker._clock=lambda:op.expires_at;worker._drc_baseline_collector._clock=lambda:op.expires_at
        else:
            status=store.get_status(op.tenant_id,op.project_id,op.operation_id)
            store.save_status(status.model_validate({**status.model_dump(mode="python"),"cancellation_requested":True,"fingerprint":"sha256:"+"0"*64}))
        return value
    fake.run_drc_baseline=failed
    status=worker.run_once()
    assert status.status is {"workspace":OperationStatus.FAILED,"expiry":OperationStatus.TIMED_OUT,"cancel":OperationStatus.CANCELLED}[cause]
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.artifact is None
    assert all(op.operation_id+"-"+role in receipt.evidence_references for role in ("raw-report","workspace-after","prl-after"))
    for ref in receipt.evidence_references:assert artifacts.read(store.get_artifact(op.tenant_id,op.project_id,ref))
    assert len(calls)==1 and worker.run_once() is None and worker.recover_after_restart()==()


@pytest.mark.parametrize("role",("raw-report","provenance","context","workspace-after"))
def test_final_transaction_reloads_actual_members_and_context(tmp_path,role):
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    original=store.terminal_transition;tampered=[False]
    def final(*args,**kwargs):
        if args[2].status is OperationStatus.SUCCEEDED and not tampered[0]:
            tampered[0]=True;(artifacts._root/(op.operation_id+"-"+role)).write_bytes(b"changed-after-validation")
        return original(*args,**kwargs)
    store.terminal_transition=final
    assert worker.run_once().status is OperationStatus.FAILED
    assert tampered[0] and len(calls)==1
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.artifact is None and worker.run_once() is None


def test_final_transaction_checks_frozen_extra_member_identity(tmp_path):
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    original=store.terminal_transition;tampered=[False]
    def final(*args,**kwargs):
        if args[2].status is OperationStatus.SUCCEEDED and not tampered[0]:
            tampered[0]=True
            identity=store.get_artifact(op.tenant_id,op.project_id,op.operation_id+"-workspace-after")
            store.save_artifact(identity.model_validate({**identity.model_dump(mode="python"),"toolchain_version":"changed","fingerprint":"sha256:"+"0"*64}))
        return original(*args,**kwargs)
    store.terminal_transition=final
    assert worker.run_once().status is OperationStatus.FAILED and len(calls)==1


def test_member_database_error_keeps_failed_history_and_zero_replay(tmp_path):
    import sqlite3
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    original=store.save_artifact;failed=[False]
    def save(identity):
        if not failed[0]:failed[0]=True;raise sqlite3.OperationalError("synthetic diagnostic write failure")
        return original(identity)
    store.save_artifact=save
    assert worker.run_once().status is OperationStatus.FAILED
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.artifact is None and op.operation_id+"-raw-report" in receipt.evidence_references
    assert len(calls)==1 and worker.run_once() is None

@pytest.mark.parametrize("role", ("tool_bundle", "rules_manifest", "dependency_manifest", "environment", "input-pcb", "workspace-manifest", "prl-before", "raw-report", "workspace-after", "prl-after", "provenance", "context"))
@pytest.mark.parametrize("field", ("build_configuration", "artifact_type", "artifact_id", "artifact_sha256", "size_bytes", "toolchain_id"))
def test_collector_member_metadata_rejected_and_valid_failure_facts_preserved(tmp_path, role, field):
    from dataclasses import replace
    worker,store,artifacts,op,grant,calls,_=setup(tmp_path)
    collect=worker._drc_baseline_collector.execute
    rejected=[]
    def forged(*args,**kwargs):
        result=collect(*args,**kwargs)
        def change(identity,blob):
            if identity.artifact_id==op.operation_id+"-"+role:
                value={"build_configuration":"UNAPPROVED_CONFIG", "artifact_type":"LOG" if identity.artifact_type=="MANIFEST" else "MANIFEST", "artifact_id":op.operation_id+"-unknown-role", "artifact_sha256":"sha256:"+"0"*64, "size_bytes":identity.size_bytes+1, "toolchain_id":"unapproved-tool"}[field]
                identity=identity.model_validate({**identity.model_dump(mode="python"),field:value,"fingerprint":"sha256:"+"0"*64})
                rejected.append(identity.artifact_id)
            return identity,blob
        primary,blob=change(result.artifact,result.artifact_content)
        return replace(result,artifact=primary,artifact_content=blob,additional_artifacts=tuple(change(*pair) for pair in result.additional_artifacts))
    worker._drc_baseline_collector.execute=forged
    assert worker.run_once().status is OperationStatus.FAILED
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id)
    assert receipt.artifact is None and len(calls)==1
    assert rejected and rejected[0] not in receipt.evidence_references
    assert store.get_artifact(op.tenant_id,op.project_id,rejected[0]) is None
    assert len(receipt.evidence_references)==11
    for ref in receipt.evidence_references:
        assert artifacts.read(store.get_artifact(op.tenant_id,op.project_id,ref))
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.CONSUMED
    assert worker.run_once() is None and worker.recover_after_restart()==()

@pytest.mark.parametrize("change", (None, "main.c", "board.kicad_prl", "prl-removed"))
def test_recovery_reloads_whole_workspace_in_fresh_python_process_without_replay(tmp_path, change):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    worker,store,_,op,grant,calls,_=setup(tmp_path)
    assert store.claim_next(worker_id=worker.worker_id,lease_seconds=60,now=op.created_at)
    assert store.start_operation_transition(op,approval_id=grant.grant_id,worker_id=worker.worker_id,now=op.created_at,baseline_revalidate=lambda:True)
    if change=="prl-removed":(tmp_path/"project"/"board.kicad_prl").unlink()
    elif change:(tmp_path/"project"/change).write_bytes(b"changed")
    script='''
import json,os,sys,subprocess
from pathlib import Path
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService,FixedWorkspaceTargetResolver,SQLiteWorkspaceApplyStore,SystemWorkspaceClock
p=Path(sys.argv[1]);store=SQLiteExecutionStore(p/'execution.sqlite3')
workspace=WorkspaceApplyService(resolver=FixedWorkspaceTargetResolver(p/'project'),store=SQLiteWorkspaceApplyStore(p/'state.sqlite3'),state_root=p/'state',clock=SystemWorkspaceClock())
def forbidden(*args,**kwargs):raise AssertionError('no tool execution or replay permitted')
subprocess.Popen=forbidden
worker=ExecutionWorker(worker_id='fresh-baseline-recovery',store=store,registry=None,workspace_resolver=None,targets=None,recovery_workspace=workspace)
recovered=worker.recover_after_restart()
assert len(recovered)==1 and worker.run_once() is None and worker.recover_after_restart()==()
status=store.get_status(sys.argv[2],sys.argv[3],sys.argv[4]);approval=store.get_approval(sys.argv[2],sys.argv[3],sys.argv[5])
print(json.dumps({'pid':os.getpid(),'status':status.status.value,'recovery':status.recovery_state.value,'approval':approval.status.value,'receipt':store.get_receipt(sys.argv[2],sys.argv[3],sys.argv[4]) is not None}))
'''
    env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1","EMBRIX_RUN_S01_REAL_KICAD":"0","EMBRIX_KICAD_CLI":""}
    result=subprocess.run([sys.executable,"-B","-c",script,str(tmp_path),op.tenant_id,op.project_id,op.operation_id,grant.grant_id],cwd=Path(__file__).resolve().parents[2],env=env,capture_output=True,text=True,timeout=30,check=False)
    (tmp_path/"fresh-recovery.stdout").write_text(result.stdout,encoding="utf-8")
    (tmp_path/"fresh-recovery.stderr").write_text(result.stderr,encoding="utf-8")
    assert result.returncode==0,result.stderr
    fact=json.loads(result.stdout.splitlines()[-1])
    assert fact["pid"]!=os.getpid() and fact["status"]=="RECOVERY_REQUIRED"
    assert fact["recovery"]==("INTERRUPTED" if change is None else "TARGET_STATE_UNKNOWN")
    assert fact["approval"]=="CONSUMED" and not fact["receipt"] and calls==[]
    assert worker.run_once() is None

@pytest.mark.parametrize("mode", ("drop_after", "forge_after", "forge_both", "missing_prl_after", "wrong_input_pcb", "wrong_prl", "duplicate_ref"))
def test_success_collection_requires_actual_complete_workspace_and_inputs(tmp_path, mode):
    from dataclasses import replace
    import hashlib
    worker,store,_,op,_,calls,_=setup(tmp_path)
    collect=worker._drc_baseline_collector.execute
    def changed(*args,**kwargs):
        result=collect(*args,**kwargs);pairs=[];refs=list(result.evidence_references)
        for identity,blob in result.additional_artifacts:
            role=identity.artifact_id[len(op.operation_id)+1:]
            if (mode=="drop_after" and role=="workspace-after") or (mode=="missing_prl_after" and role=="prl-after"):
                refs.remove(identity.artifact_id);continue
            if ((mode=="forge_after" and role=="workspace-after") or (mode=="forge_both" and role in {"workspace-manifest","workspace-after"})
                or (mode=="wrong_input_pcb" and role=="input-pcb") or (mode=="wrong_prl" and role=="prl-after")):
                blob=b'{"schema_version":1,"entries":[]}'
                identity=identity.model_validate({**identity.model_dump(mode="python"),"artifact_sha256":"sha256:"+hashlib.sha256(blob).hexdigest(),"size_bytes":len(blob),"fingerprint":"sha256:"+"0"*64})
            pairs.append((identity,blob))
        if mode=="duplicate_ref":refs.append(refs[0])
        return replace(result,additional_artifacts=tuple(pairs),evidence_references=tuple(refs))
    worker._drc_baseline_collector.execute=changed
    assert worker.run_once().status is OperationStatus.FAILED
    assert store.get_receipt(op.tenant_id,op.project_id,op.operation_id).artifact is None
    assert len(calls)==1 and worker.run_once() is None and worker.recover_after_restart()==()

def test_both_workspace_manifests_consistently_forged_must_fail(tmp_path):
    import dataclasses,json,hashlib
    from embedded_copilot.execution_worker.native_drc_provenance import build_drc_v2_context
    worker,store,artifacts,op,grant,calls,live=setup(tmp_path)
    collect=worker._drc_baseline_collector.execute
    def change(identity,blob):
        return (identity.model_validate({**identity.model_dump(mode='python'),'artifact_sha256':'sha256:'+hashlib.sha256(blob).hexdigest(),'size_bytes':len(blob),'fingerprint':'sha256:'+'0'*64}),blob)
    def encoded(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    def forged(*args,**kwargs):
        result=collect(*args,**kwargs);pairs=dict((i.artifact_id,(i,b)) for i,b in result.additional_artifacts)
        for role in ('workspace-manifest','workspace-after'):
            key=op.operation_id+'-'+role;i,b=pairs[key];doc=json.loads(b);before=len(doc['entries']);doc['entries']=[e for e in doc['entries'] if e['relative_path']!='main.c'];assert len(doc['entries'])==before-1;pairs[key]=change(i,encoded(doc))
        key=op.operation_id+'-provenance';i,b=pairs[key];doc=json.loads(b);doc['workspace_manifest']=pairs[op.operation_id+'-workspace-manifest'][0].model_dump(mode='json');pairs[key]=change(i,encoded(doc))
        context=build_drc_v2_context(pairs[op.operation_id+'-raw-report'][0],pairs[key][0],read_artifact=lambda identity:pairs[identity.artifact_id][1],policy=op.collector_policy)
        identity,blob=change(result.artifact,encoded(context))
        return dataclasses.replace(result,artifact=identity,artifact_content=blob,additional_artifacts=tuple(pairs.values()))
    worker._drc_baseline_collector.execute=forged
    status=worker.run_once();print('BOTH_MANIFEST_FACT',status.status.value)
    assert status.status.value=='FAILED'
    assert len(calls)==1 and worker.run_once() is None

