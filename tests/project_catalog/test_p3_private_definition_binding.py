"""Private definition join: real mapped originals; no EDA/provider/write execution."""
from pathlib import Path
import pytest
from embedded_copilot.engineering_copilot import SchematicProductionRequest,generate_schematic_proposal,create_schematic_production_state
from embedded_copilot.execution_worker.schematic_erc import SchematicSymbolLibraryValidator
from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
from tests.project_catalog.test_p3_library_terminal_maps import _runtime,NOW,ZERO


def _context(tmp_path,monkeypatch,review=True):
    catalog,detail,values,root=_runtime(tmp_path,monkeypatch)
    state=catalog.create_library_terminal_map(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester,values=values)
    if review: state=catalog.review_library_terminal_map(detail.project_id,state.evidence.evidence_id,tenant_id=detail.tenant_id,requester=detail.requester,evidence_fingerprint=state.evidence.fingerprint,decision="APPROVED")
    detail=catalog.conversation(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester)
    from tests.engineering_copilot.test_schematic_production_g3 import _Answer
    session=catalog.resume_conversation(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester)
    catalog.begin_turn(session.session_id,request_id="private-context",trace_id="private-context-trace",question="准备工程执行",created_at=NOW)
    catalog.complete_turn(session.session_id,"private-context",_Answer(),NOW)
    detail=catalog.conversation(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester)
    task=next(item for item in detail.implementation_spec.tasks if "SCHEMATIC" in item.expected_artifacts or any(a.artifact_type.value=="SCHEMATIC" and a.artifact_id in item.expected_artifacts for a in detail.implementation_spec.expected_artifacts))
    intent=next(item for item in detail.execution_intents if item.source_task_refs==(task.task_id,))
    authorization=next(item for item in detail.execution_authorization.requests if item.intent_id==intent.intent_id and item.capability_id=="ARTIFACT_CREATION")
    catalog.decide_execution_authorization(detail.project_id,detail.conversation_id,authorization.request_id,"APPROVED",tenant_id=detail.tenant_id,requester=detail.requester,decided_at=NOW)
    catalog.begin_turn(session.session_id,request_id="private-generation",trace_id="private-generation-trace",question="生成原理图",created_at=NOW)
    catalog.complete_turn(session.session_id,"private-generation",_Answer(),NOW)
    detail=catalog.conversation(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester)
    assert detail.schematic_production is not None
    request=detail.schematic_production.request
    plan=detail.schematic_planning.approved_plan
    text=(root/"symbol.kicad_sym").read_text(encoding="utf-8")
    definition=SchematicSymbolLibraryValidator._extract_symbol_definition(text,"SHTC3_EP")
    expected=SchematicSymbolLibraryValidator._expand_symbol_definition(text,definition,binding=values["symbol_library_id"],symbol="SHTC3_EP")
    kwargs=dict(production_request=request,approved_schematic_plan=plan,hardware_selection=detail.hardware_selection,
        tenant_id=detail.tenant_id,symbol_definitions={values["symbol_library_id"]:expected})
    return catalog,kwargs,state,root,values


def test_exact_original_definition_is_bound_to_request_local_scoped_review_token(tmp_path,monkeypatch):
    catalog,kwargs,state,root,_=_context(tmp_path,monkeypatch)
    token=catalog._validate_private_schematic_definitions(**kwargs)
    assert token==((state.evidence.component_instance_id,state.evidence.fingerprint,state.review.fingerprint),)
    assert catalog._validate_private_schematic_definitions(**kwargs,expected_binding=token)==token
    # Same LibraryID was previously accepted by the provider alone; join rejects changed bytes/definition.
    bad={key:value.replace('"VSS"','"UNAPPROVED_VSS"') for key,value in kwargs["symbol_definitions"].items()}
    assert bad!=kwargs["symbol_definitions"]
    with pytest.raises(ProjectCatalogError):catalog._validate_private_schematic_definitions(**(kwargs|{"symbol_definitions":bad}))


@pytest.mark.parametrize("fault",("unreviewed","missing","duplicate","scope","plan","selection","request",
    "definition_missing","expected_token","original_drift","review_generation","owner_during_read"))
def test_private_definition_join_rejects_noncurrent_context_and_slow_io_drift(tmp_path,monkeypatch,fault):
    catalog,kwargs,state,root,values=_context(tmp_path,monkeypatch,review=fault!="unreviewed")
    request=kwargs["production_request"]
    if fault=="missing":
        detail=catalog.conversation(request.project_id,request.conversation_id,tenant_id=kwargs["tenant_id"],requester=request.requester)
        catalog.persist_conversation_detail(detail.model_copy(update={"library_terminal_maps":(),"fingerprint":ZERO}))
    elif fault=="duplicate":
        extra=catalog.create_library_terminal_map(request.project_id,request.conversation_id,tenant_id=kwargs["tenant_id"],requester=request.requester,values=values)
        catalog.review_library_terminal_map(request.project_id,extra.evidence.evidence_id,tenant_id=kwargs["tenant_id"],requester=request.requester,evidence_fingerprint=extra.evidence.fingerprint,decision="APPROVED")
    elif fault=="scope":kwargs["tenant_id"]="other"
    elif fault in {"plan","selection"}:kwargs["approved_schematic_plan" if fault=="plan" else "hardware_selection"]=None
    elif fault=="request":
        data=request.model_dump(mode="python",exclude={"fingerprint"});data["request_id"]="other-request";kwargs["production_request"]=SchematicProductionRequest.create(**data)
    elif fault=="definition_missing":kwargs["symbol_definitions"]={}
    elif fault=="expected_token":kwargs["expected_binding"]=(('other',state.evidence.fingerprint,state.review.fingerprint),)
    elif fault=="original_drift":(root/"symbol.kicad_sym").write_bytes((root/"symbol.kicad_sym").read_bytes()+b"changed")
    elif fault=="review_generation":
        kwargs["expected_binding"]=catalog._validate_private_schematic_definitions(**kwargs)
        detail=catalog.conversation(request.project_id,request.conversation_id,tenant_id=kwargs["tenant_id"],requester=request.requester)
        from embedded_copilot.project_catalog.models import LibraryTerminalMapReviewV2,ReviewedLibraryTerminalMapV2
        review=LibraryTerminalMapReviewV2(**(state.review.model_dump(mode="python",exclude={"fingerprint"})|{"review_id":"new-review"}))
        current=ReviewedLibraryTerminalMapV2(evidence=state.evidence,review=review)
        catalog.persist_conversation_detail(detail.model_copy(update={"library_terminal_maps":(current,),"fingerprint":ZERO}))
    elif fault=="owner_during_read":
        original=catalog._library_terminal_map_checked
        def drift(detail,evidence):
            result=original(detail,evidence)
            catalog.persist_conversation_detail(detail.model_copy(update={"title":"changed during I/O","fingerprint":ZERO}))
            return result
        monkeypatch.setattr(catalog,"_library_terminal_map_checked",drift)
    with pytest.raises(ProjectCatalogError):catalog._validate_private_schematic_definitions(**kwargs)
def test_private_owner_cas_failure_preserves_prepared_operation_without_enqueue(tmp_path,monkeypatch):
    from embedded_copilot.engineering_copilot.schematic_production import _library_symbol
    from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver,SQLiteWorkspaceApplyStore,WorkspaceApplyService
    from embedded_copilot.execution_worker import ExecutionOperationService,SQLiteExecutionStore
    from embedded_copilot.schematic_execution import SchematicExecutionBridge
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogConflict
    catalog,kwargs,state,root,_=_context(tmp_path,monkeypatch)
    request=kwargs["production_request"]
    detail=catalog.conversation(request.project_id,request.conversation_id,tenant_id=kwargs["tenant_id"],requester=request.requester)
    proposal=detail.schematic_production.proposal
    project=tmp_path/"bridge-project";project.mkdir();(project/"README.md").write_text("Isolated CAS probe",encoding="utf-8")
    workspace=WorkspaceApplyService(resolver=FixedWorkspaceTargetResolver(project,tenant_id=kwargs["tenant_id"],project_id=request.project_id),store=SQLiteWorkspaceApplyStore(tmp_path/"workspace.sqlite3"),state_root=tmp_path/"workspace-state",clock=type("Clock",(),{"now":lambda self:NOW})())
    execution=ExecutionOperationService(store=SQLiteExecutionStore(tmp_path/"execution.sqlite3"),clock=lambda:NOW)
    definitions={item.symbol_binding:"\n".join(_library_symbol(item,tuple(pin for pin in proposal.pins if pin.component_id==item.component_id))).strip() for item in proposal.components}
    definitions.update(kwargs["symbol_definitions"])
    bridge=SchematicExecutionBridge(workspace=workspace,execution=execution,clock=lambda:NOW,symbol_library_provider=lambda **_:definitions,private_definition_validator=catalog._validate_private_schematic_definitions)
    original=catalog._repository.save_conversation_if_fingerprint
    def fail_cas(value,**keys):raise ProjectCatalogConflict("Owner changed before CAS")
    monkeypatch.setattr(catalog._repository,"save_conversation_if_fingerprint",fail_cas)
    with pytest.raises(ProjectCatalogConflict,match="CAS"):
        catalog.decide_schematic_production(request.project_id,request.conversation_id,"APPROVED",tenant_id=kwargs["tenant_id"],requester=request.requester,decided_at=NOW,bridge=bridge)
    operations=execution.list_operations(tenant_id=kwargs["tenant_id"],project_id=request.project_id)
    assert len(operations)==1
    assert execution.get_status(tenant_id=kwargs["tenant_id"],project_id=request.project_id,operation_id=operations[0].operation_id).status.value=="WAIT_APPROVAL"
    assert execution.get_receipt(tenant_id=kwargs["tenant_id"],project_id=request.project_id,operation_id=operations[0].operation_id) is None
    assert not (project/request.target_relative_path).exists()
    assert catalog.conversation(request.project_id,request.conversation_id,tenant_id=kwargs["tenant_id"],requester=request.requester).schematic_production.status.value=="AWAITING_APPROVAL"


def test_default_formal_composition_injects_only_canonical_catalog_validator(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from embedded_copilot.api import main
    from embedded_copilot.engineering_workspace_apply import ProjectCatalogWorkspaceApplyPort,ProjectCatalogWorkspaceTargetResolver,SQLiteWorkspaceApplyStore,WorkspaceApplyService
    from embedded_copilot.execution_worker import ExecutionOperationService,SQLiteExecutionStore
    from embedded_copilot.services.config import Settings
    catalog,kwargs,_,_,_=_context(tmp_path,monkeypatch)
    workspace=WorkspaceApplyService(resolver=ProjectCatalogWorkspaceTargetResolver(catalog),store=SQLiteWorkspaceApplyStore(tmp_path/"workspace.sqlite3"),state_root=tmp_path/"workspace-state",clock=type("Clock",(),{"now":lambda self:NOW})())
    execution=ExecutionOperationService(store=SQLiteExecutionStore(tmp_path/"execution.sqlite3"),clock=lambda:NOW)
    app=main.create_app(settings=Settings(environment="test",project_root=tmp_path/"projects",desktop_state_database=tmp_path/"app.sqlite3",_env_file=None),hardware_component_source=catalog._hardware_component_source,project_catalog_runtime=catalog,workspace_apply_port=ProjectCatalogWorkspaceApplyPort(service=workspace,proposals=None),execution_operation_port=execution)
    with TestClient(app):
        bridge=app.state.schematic_execution_bridge
        assert bridge._private_definition_validator.__self__ is catalog
        assert bridge._private_definition_validator.__func__ is type(catalog)._validate_private_schematic_definitions
        assert execution.list_operations(tenant_id=kwargs["tenant_id"],project_id=kwargs["production_request"].project_id)==()