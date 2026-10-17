"""Bridge control-flow probes with synthetic callbacks; no formal library approval claims."""
from datetime import UTC,datetime
from pathlib import Path
import pytest
from embedded_copilot.engineering_copilot import SchematicComponentProposal,SchematicProposal,decide_schematic_proposal
from embedded_copilot.engineering_copilot.schematic_production import _library_symbol
from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver,SQLiteWorkspaceApplyStore,WorkspaceApplyService
from embedded_copilot.execution_worker import ExecutionOperationService,SQLiteExecutionStore
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from tests.engineering_copilot.test_schematic_production import _proposal,_request,_WorkspaceClock

FP="sha256:"+"a"*64
TOKEN=(("component",FP,FP),)


def _prepare(tmp_path,validator=None,provider_factory=None,private=True):
    proposal=_proposal()
    if private:
        component=SchematicComponentProposal.create(**(proposal.components[0].model_dump(mode="python",exclude={"fingerprint"})|{"symbol_binding":"SHTC3_EP:SHTC3_EP"}))
        proposal=SchematicProposal.create(**(proposal.model_dump(mode="python",exclude={"fingerprint"})|{"components":(component,*proposal.components[1:])}))
    request=_request(proposal)
    decision=decide_schematic_proposal(proposal,decision="APPROVED",reviewer="engineer-1",decided_at=datetime(2026,9,8,12,tzinfo=UTC))
    root=tmp_path/"project";root.mkdir();(root/"README.md").write_text("Isolated bridge fixture",encoding="utf-8")
    workspace=WorkspaceApplyService(resolver=FixedWorkspaceTargetResolver(root,tenant_id="tenant",project_id=proposal.project_id),store=SQLiteWorkspaceApplyStore(tmp_path/"workspace.sqlite3"),state_root=tmp_path/"state",clock=_WorkspaceClock())
    execution=ExecutionOperationService(store=SQLiteExecutionStore(tmp_path/"execution.sqlite3"),clock=lambda:datetime(2026,9,8,12,tzinfo=UTC))
    definitions={item.symbol_binding:"\n".join(_library_symbol(item,tuple(pin for pin in proposal.pins if pin.component_id==item.component_id))).strip() for item in proposal.components}
    provider=provider_factory(definitions) if provider_factory else lambda **_:definitions
    bridge=SchematicExecutionBridge(workspace=workspace,execution=execution,clock=lambda:datetime(2026,9,8,12,tzinfo=UTC),symbol_library_provider=provider,private_definition_validator=validator)
    kwargs=dict(production_request=request,proposal=proposal,decision=decision,tenant_id="tenant")
    return bridge,workspace,execution,kwargs,definitions,root


def test_private_missing_canonical_callback_fails_closed_and_legacy_stays_unchanged(tmp_path):
    bridge,_,execution,kwargs,_,root=_prepare(tmp_path)
    with pytest.raises(ValueError,match="validator is unavailable"):bridge.prepare_approved(**kwargs)
    assert not execution.list_operations(tenant_id="tenant",project_id="project-1")
    assert not (root/"hardware/main.kicad_sch").exists()
    legacy=tmp_path/"legacy";legacy.mkdir()
    bridge,_,execution,kwargs,_,root=_prepare(legacy,private=False)
    result=bridge.prepare_approved(**kwargs)
    assert execution.get_status(tenant_id="tenant",project_id="project-1",operation_id=result.operation.operation_id).status.value=="WAIT_APPROVAL"
    assert execution.get_receipt(tenant_id="tenant",project_id="project-1",operation_id=result.operation.operation_id) is None
    assert not (root/"hardware/main.kicad_sch").exists()


@pytest.mark.parametrize("late_call",(1,2,3,4))
def test_private_recheck_failure_prevents_operation_publication_after_slow_io(tmp_path,late_call):
    calls=[]
    def validate(**kwargs):
        calls.append(kwargs)
        if len(calls)==late_call:raise ValueError("current original/review drift")
        assert kwargs["expected_binding"] is (None if len(calls)==1 else TOKEN)
        return TOKEN
    bridge,workspace,execution,kwargs,_,root=_prepare(tmp_path,validate)
    with pytest.raises(ValueError,match="drift"):bridge.prepare_approved(**kwargs)
    assert len(calls)==late_call
    assert not execution.list_operations(tenant_id="tenant",project_id="project-1")
    assert not (root/"hardware/main.kicad_sch").exists()
    # Any changeset/apply grant prepared before failure remains historical; never applied.


def test_provider_mapping_is_copied_once_and_serializer_uses_the_validated_strings(tmp_path):
    calls=[];original=[]
    def factory(definitions):
        original.append(definitions)
        return lambda **_:definitions
    def validate(**kwargs):
        calls.append(kwargs["symbol_definitions"])
        if len(calls)==1:
            original[0]["SHTC3_EP:SHTC3_EP"]="INVALID MUTABLE PROVIDER VALUE"
        assert "INVALID MUTABLE" not in kwargs["symbol_definitions"]["SHTC3_EP:SHTC3_EP"]
        return TOKEN
    bridge,_,execution,kwargs,_,root=_prepare(tmp_path,validate,factory)
    result=bridge.prepare_approved(**kwargs)
    assert len(calls)==4 and all(item is calls[0] for item in calls)
    assert "INVALID MUTABLE" not in result.operation.content
    # Existing operation return still revalidates current authority, no implicit replay.
    original[0].update(calls[0])
    assert bridge.prepare_approved(**kwargs).operation.operation_id==result.operation.operation_id
    assert len(calls)==7
    assert not (root/"hardware/main.kicad_sch").exists()


def test_wrong_mapping_value_is_rejected_before_callback(tmp_path):
    calls=[]
    bridge,_,execution,kwargs,_,_=_prepare(tmp_path,lambda **kw:calls.append(kw) or TOKEN,lambda _defs:lambda **_:{"SHTC3_EP:SHTC3_EP":object()})
    with pytest.raises(ValueError,match="definitions are invalid"):bridge.prepare_approved(**kwargs)
    assert calls==[] and not execution.list_operations(tenant_id="tenant",project_id="project-1")