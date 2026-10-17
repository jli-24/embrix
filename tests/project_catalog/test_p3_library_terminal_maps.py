"""P3 Catalog lifecycle with real project-mapped private-library/PDF originals."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from embedded_copilot.component_recommendation.catalog import StaticComponentSource
from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord, ComponentInterfaceFact, ComponentPowerFact
from embedded_copilot.engineering_copilot.schematic_evidence import LibraryTerminalBindingV2
from embedded_copilot.engineering_copilot.schematic_parameter_readers import ProjectMappedParameterPort
from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanDecision
from embedded_copilot.project_catalog import ProjectCatalogConflict
from tests.engineering_copilot import test_schematic_planning_g2 as golden
from tests.engineering_copilot.test_p3_library_readers import _project, SYMBOL_ID, FOOTPRINT_ID
from tests.project_catalog.test_p1_project_supply import _approved_runtime, NOW, ZERO


def _runtime(tmp_path, monkeypatch):
    base = golden._catalog()
    old = next(item for item in base.records if "SENSOR" in item.supported_roles)
    values = old.model_dump(mode="python")
    values.update(manufacturer="Sensirion", mpn="SHTC3", package="DFN-4 2x2 mm", symbol=SYMBOL_ID, footprint=FOOTPRINT_ID,
        pin_facts=(golden._pin("1", "VDD", "supply", power=True), golden._pin("2", "SCL", "I2C_SCL", protocols=("I2C",)),
            golden._pin("3", "SDA", "I2C_SDA", protocols=("I2C",)), golden._pin("4", "VSS", "ground", power=True), golden._pin("5", "VSS", "ground", power=True)),
        interface_facts=(ComponentInterfaceFact.create(protocol="I2C",pin_names=("2","3")),),
        power_facts=(golden._power("consumer", "3V3", "1", minimum=1.62, maximum=3.6),
            ComponentPowerFact.create(**{**golden._power("ground", "GND", "4", nominal=0).model_dump(mode="python"),"pin_numbers":("4","5")})), fingerprint=ZERO)
    source = ComponentSourceRecord.create(**values)
    records = StaticComponentSource(tuple(source if item.source_id == old.source_id else item for item in base.records))
    monkeypatch.setattr(golden, "_catalog", lambda **kwargs: records)
    catalog, detail, relation, selectors, circuit, _source = _approved_runtime(tmp_path, monkeypatch, full_s02=True)
    candidate = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id, candidate.candidate.candidate_id,
        candidate.candidate.fingerprint, "APPROVED", tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester)
    current = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id, requester=detail.requester, decided_at=NOW)
    original_root = tmp_path / "mapped-originals"; original_root.mkdir()
    kwargs, libraries, parameters = _project(original_root)
    for mapping in (libraries, parameters):
        mapping.update(tenant_id=detail.tenant_id,project_id=detail.project_id)
        for item in mapping.get("libraries",mapping.get("documents",())):
            item.update(source_record_id=source.source_id,source_record_fingerprint=source.fingerprint)
    (original_root/".embrix"/"library-documents.json").write_text(json.dumps(libraries),encoding="utf-8")
    (original_root/".embrix"/"parameter-documents.json").write_text(json.dumps(parameters),encoding="utf-8")
    catalog._schematic_parameter_port = ProjectMappedParameterPort(lambda _t,_p: original_root)
    plan = current.schematic_planning.approved_plan
    instance = next(item for item in plan.component_instances if item.mpn == "SHTC3")
    originals = catalog._schematic_parameter_port.read_library_originals(source,
        **{key:value for key,value in kwargs.items() if key not in {"root","source","tenant_id","project_id"}},
        tenant_id=detail.tenant_id,project_id=detail.project_id)
    bindings=[]
    for number in ("1","2","3","4","EP"):
        logical="5" if number=="EP" else number
        terminal=next((item for item in originals.manufacturer_terminals if item.number==number),None)
        locator=originals.manufacturer_ep.locator if terminal is None else terminal.locator
        net=next(net for net in plan.nets if any(node.component_id==instance.instance_id and node.pin_number==logical for node in net.endpoints))
        bindings.append(LibraryTerminalBindingV2(manufacturer_terminal=number,manufacturer_locator=locator,
            logical_symbol_pin=logical,physical_pad_ids=(logical,),net_id=net.net_id))
    values={key:value for key,value in kwargs.items() if key not in {"root","source","tenant_id","project_id"}}
    values.update(component_instance_id=instance.instance_id, source_record_id=source.source_id,
        source_record_fingerprint=source.fingerprint,plan_id=plan.plan_id,plan_fingerprint=plan.fingerprint,terminal_bindings=tuple(bindings))
    return catalog, current, values, original_root


def test_library_original_candidate_review_restart_and_changed_bytes(tmp_path,monkeypatch):
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
    from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
    from embedded_copilot.project_catalog.models import ConversationDetail
    catalog,detail,values,root=_runtime(tmp_path,monkeypatch)
    assert "library_terminal_maps" not in detail.model_dump(mode="json")
    candidate=catalog.create_library_terminal_map(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester,values=values)
    assert candidate.review is None
    reviewed=catalog.review_library_terminal_map(detail.project_id,candidate.evidence.evidence_id,
        tenant_id=detail.tenant_id,requester=detail.requester,evidence_fingerprint=candidate.evidence.fingerprint,decision="APPROVED")
    assert reviewed.review.reviewer_id==detail.requester
    catalog._repository=SQLiteProjectCatalogRepository(catalog._repository._database)
    current=catalog.conversation(detail.project_id,detail.conversation_id,tenant_id=detail.tenant_id,requester=detail.requester)
    assert ConversationDetail.model_validate_json(current.model_dump_json())==current
    assert catalog.library_terminal_maps(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)==((reviewed,"STALE"),)
    catalog.set_hardware_component_source(PublishedComponentSourcePort(
        catalog._repository, tenant_id=detail.tenant_id, publisher_id="p1-fixture-publisher"))
    assert catalog.library_terminal_maps(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)==((reviewed,"APPROVED"),)
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_library_terminal_map(detail.project_id,candidate.evidence.evidence_id,
            tenant_id=detail.tenant_id,requester=detail.requester,evidence_fingerprint=candidate.evidence.fingerprint,decision="REJECTED")
    (root/"symbol.kicad_sym").write_bytes((root/"symbol.kicad_sym").read_bytes()+b"changed")
    assert catalog.library_terminal_maps(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)[0][1]=="STALE"


@pytest.mark.parametrize("fault",("plan","source","net","locator","partial","wrong_owner","io_owner"))
def test_library_wrong_current_bindings_never_persist(tmp_path,monkeypatch,fault):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
    catalog,detail,values,_root=_runtime(tmp_path,monkeypatch)
    scope=dict(tenant_id=detail.tenant_id,requester=detail.requester)
    if fault in {"plan","source"}: values["plan_fingerprint" if fault=="plan" else "source_record_fingerprint"]=ZERO
    elif fault in {"net","locator"}:
        binding=values["terminal_bindings"][0]
        update={"net_id":"wrong-net"} if fault=="net" else {"manufacturer_locator":values["terminal_bindings"][1].manufacturer_locator}
        values["terminal_bindings"]=(binding.model_copy(update=update),*values["terminal_bindings"][1:])
    elif fault=="partial": values["terminal_bindings"]=values["terminal_bindings"][:-1]
    elif fault=="wrong_owner": scope["requester"]="other"
    else:
        from embedded_copilot.project_catalog.models import ConversationDetail
        from datetime import timedelta
        original=catalog._schematic_parameter_port.read_library_originals
        def drift(*args,**kwargs):
            observed=original(*args,**kwargs)
            payload=detail.model_dump(mode="python");payload.update(updated_at=detail.updated_at+timedelta(seconds=1),fingerprint=ZERO)
            catalog.persist_conversation_detail(ConversationDetail.model_validate(payload))
            return observed
        monkeypatch.setattr(catalog._schematic_parameter_port,"read_library_originals",drift)
    with pytest.raises(ProjectCatalogError):
        catalog.create_library_terminal_map(detail.project_id,detail.conversation_id,values=values,**scope)
    assert catalog.conversation(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester).library_terminal_maps is None
