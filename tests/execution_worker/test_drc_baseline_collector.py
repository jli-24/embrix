"""Fixed process-boundary probes with synthetic bytes; no actual KiCad launch."""
from pathlib import Path
import pytest
from embedded_copilot.execution_worker.adapters import SafeProcessRunner


def test_actual_fixed_runner_checks_revoked_phase_before_popen(tmp_path,monkeypatch):
    root=tmp_path/"workspace";root.mkdir();pcb=root/"board.kicad_pcb";pcb.write_bytes(b"synthetic")
    tool=tmp_path/"kicad-cli.exe";tool.write_bytes(b"synthetic-cli")
    report=tmp_path/"reports"/"out.json"
    calls=[]
    monkeypatch.setattr("embedded_copilot.execution_worker.adapters.subprocess.Popen",lambda *a,**k:calls.append((a,k)))
    class Revoked:
        def is_cancelled(self):return True
    with pytest.raises(InterruptedError):
        SafeProcessRunner().run_drc_baseline((str(tool),"pcb","drc","--format","json","--output",str(report),str(pcb)),cwd=root,timeout_seconds=30,environment={},cancellation=Revoked())
    assert calls==[]

import json
from datetime import timedelta
from types import SimpleNamespace
from embedded_copilot.execution_worker.drc_baseline import BaselineClosureFile,PcbDrcBaselineCollector,_encoded,_sha
from embedded_copilot.execution_worker.contracts import PcbDrcBaselineCollectorPolicy,PcbDrcBaselineOperation,drc_baseline_proposal_fingerprint
from tests.execution_worker.test_drc_baseline_contracts import _operation
from tests.engineering_workspace_apply.test_core import _service
from tests.execution_worker.test_native_drc_provenance_v2 import issue


def fixture(tmp_path,runner=None):
    workspace=_service(tmp_path)
    root=tmp_path/"project";(root/"board.kicad_pcb").write_bytes(b"synthetic-board")
    (root/"board.kicad_prl").write_bytes(b"synthetic-prl")
    tool=tmp_path/"kicad-cli.exe";tool.write_bytes(b"synthetic-cli")
    rule=tmp_path/"rule";rule.write_bytes(b"synthetic-rule")
    dep=tmp_path/"dep";dep.write_bytes(b"synthetic-dep")
    def entry(role,path,actual):
        blob=actual.read_bytes();return BaselineClosureFile(role,path,actual,_sha(blob),len(blob))
    bundle=(entry("EXECUTABLE","bin/kicad-cli.exe",tool),)
    rules=(entry("RULE","rules/check",rule),);dependencies=(entry("DEPENDENCY","lib/dep",dep),)
    env=(("PATH","synthetic-frozen"),)
    policy=PcbDrcBaselineCollectorPolicy(collector_id="KICAD_DRC_COLLECTOR",collector_version="1",tool_id="kicad-cli",tool_version="10.0.5",executable_sha256=_sha(tool.read_bytes()),tool_bundle_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(bundle)),rules_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(rules)),dependency_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(dependencies)),environment_content_fingerprint=_sha(_encoded({"schema_version":1,"entries":[{"name":"PATH","value":"synthetic-frozen"}]})),timeout_seconds=30)
    data=_operation().model_dump(mode="python",exclude={"fingerprint"})
    data.update(tenant_id="tenant-golden",project_id="project-golden")
    snapshot=workspace.snapshot(tenant_id=data["tenant_id"],project_id=data["project_id"],target_id=data["target_id"])
    data.update(workspace_snapshot_id=snapshot.fingerprint,workspace_fingerprint=snapshot.project_fingerprint,target_fingerprint=snapshot.target_fingerprint,pcb_sha256=_sha((root/"board.kicad_pcb").read_bytes()),prl_before_sha256=_sha((root/"board.kicad_prl").read_bytes()),collector_policy=policy)
    data["proposal_fingerprint"]=drc_baseline_proposal_fingerprint(data);op=PcbDrcBaselineOperation(**data)
    raw={"$schema":"https://schemas.kicad.org/drc.v1.json","coordinate_units":"mm","date":"2026-10-11T00:00:00","ignored_checks":[],"included_severities":["error","warning"],"kicad_version":"10.0.5","schematic_parity":[],"source":"board.kicad_pcb","unconnected_items":[],"violations":[issue()]}
    calls=[]
    class Fake:
        def run_drc_baseline(self,argv,**kwargs):
            calls.append((argv,kwargs));Path(argv[6]).write_bytes(_encoded(raw));return (0,b"",b"")
    collector=PcbDrcBaselineCollector(workspace=workspace,policy=policy,tool_bundle=bundle,rules=rules,dependencies=dependencies,environment=env,report_root=tmp_path/"reports",runner=runner or Fake(),clock=lambda:op.created_at)
    return collector,op,calls,raw


def execute(collector,op,revalidate=lambda:True,cancellation=None):
    return collector.execute(op,deadline=op.expires_at,cancellation=cancellation or SimpleNamespace(is_cancelled=lambda:False),revalidate=revalidate)


def test_single_fake_capture_normalizes_actual_raw_and_fixed_vector(tmp_path):
    collector,op,calls,raw=fixture(tmp_path)
    result=execute(collector,op)
    assert result.status=="SUCCEEDED"
    assert len(calls)==1 and calls[0][0][1:6]==("pcb","drc","--format","json","--output")
    context=json.loads(result.artifact_content)
    assert len(context["violations"])==1
    pairs=(*result.additional_artifacts,(result.artifact,result.artifact_content))
    assert len(set(a.artifact_id for a,_ in pairs))==len(pairs)
    assert set(result.evidence_references)=={a.artifact_id for a,_ in pairs}
    assert all(a.build_operation_id==op.operation_id for a,_ in pairs)
    assert collector.target_current(op)


@pytest.mark.parametrize("change",("main.c","board.kicad_prl","board.kicad_pcb"))
def test_complete_workspace_or_prl_change_blocks_dispatch(tmp_path,change):
    collector,op,calls,_=fixture(tmp_path)
    (tmp_path/"project"/change).write_bytes(b"changed")
    result=execute(collector,op)
    assert result.status=="FAILED" and calls==[]


@pytest.mark.parametrize("error",(TimeoutError,InterruptedError,ValueError))
def test_runner_failure_preserves_readable_unique_raw_and_after_snapshot(tmp_path,error):
    class Failing:
        def run_drc_baseline(self,argv,**kwargs):
            Path(argv[6]).write_bytes(b"readable-partial-raw");raise error("synthetic")
    collector,op,_,_=fixture(tmp_path,Failing())
    result=execute(collector,op)
    assert result.status=={TimeoutError:"TIMED_OUT",InterruptedError:"CANCELLED",ValueError:"FAILED"}[error]
    pairs={a.artifact_id:blob for a,blob in result.additional_artifacts}
    assert pairs[op.operation_id+"-raw-report"]==b"readable-partial-raw"
    assert op.operation_id+"-workspace-after" in pairs and op.operation_id+"-prl-after" in pairs
    assert result.artifact is None


@pytest.mark.parametrize("drift",("authority","cancel","deadline"))
def test_actual_runner_preflight_after_slow_closure_io_never_spawns(tmp_path,monkeypatch,drift):
    collector,op,_,_=fixture(tmp_path,SafeProcessRunner())
    live=[True];cancel=[False];now=[op.created_at];collector._clock=lambda:now[0]
    original=collector._policy_inputs;count=[0]
    def slow():
        values=original();count[0]+=1
        # Initial values + prior-dispatch read, then actual SafeRunner preflight.
        if count[0]>=3:
            if drift=="authority":live[0]=False
            if drift=="cancel":cancel[0]=True
            if drift=="deadline":now[0]=op.expires_at
        return values
    collector._policy_inputs=slow
    calls=[]
    def forbidden(*args,**kwargs):calls.append((args,kwargs));raise AssertionError("unexpected actual spawn")
    monkeypatch.setattr("embedded_copilot.execution_worker.adapters.subprocess.Popen",forbidden)
    result=execute(collector,op,revalidate=lambda:live[0],cancellation=SimpleNamespace(is_cancelled=lambda:cancel[0]))
    assert calls==[] and count[0]>=3 and result.status=={"authority":"FAILED","cancel":"CANCELLED","deadline":"TIMED_OUT"}[drift]


def test_runner_return_workspace_side_effect_preserves_report_but_fails(tmp_path):
    class Mutating:
        def run_drc_baseline(self,argv,**kwargs):
            Path(argv[6]).write_bytes(b"raw");(tmp_path/"project"/"main.c").write_bytes(b"changed");return (0,b"",b"")
    collector,op,_,_=fixture(tmp_path,Mutating())
    result=execute(collector,op)
    assert result.status=="FAILED" and result.artifact is None
    assert any(a.artifact_id.endswith("raw-report") for a,_ in result.additional_artifacts)



def test_unsafe_operation_output_identity_never_dispatches(tmp_path):
    collector,op,calls,_=fixture(tmp_path)
    data=op.model_dump(mode="python",exclude={"fingerprint"});data["operation_id"]="escape/other"
    data["proposal_fingerprint"]=drc_baseline_proposal_fingerprint(data)
    result=execute(collector,PcbDrcBaselineOperation(**data))
    assert result.status=="FAILED" and calls==[]

@pytest.mark.parametrize("remaining_ms", (0, 1, 500, 999))
def test_subsecond_deadline_after_actual_runner_slow_preflight_never_spawns(tmp_path, monkeypatch, remaining_ms):
    collector,op,_,_=fixture(tmp_path,SafeProcessRunner())
    now=[op.created_at];collector._clock=lambda:now[0]
    original=collector._policy_inputs;reads=[0];spawns=[]
    def slow():
        value=original();reads[0]+=1
        if reads[0]>=3:now[0]=op.expires_at-timedelta(milliseconds=remaining_ms)
        return value
    collector._policy_inputs=slow
    def forbidden(*args,**kwargs):
        spawns.append(True)
        raise OSError("synthetic no-real-process guard")
    monkeypatch.setattr("embedded_copilot.execution_worker.adapters.subprocess.Popen",forbidden)
    result=execute(collector,op)
    assert reads[0]>=3 and spawns==[] and result.status=="TIMED_OUT"
