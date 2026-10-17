"""Synthetic reviewed facts and independent XML; no vendor values asserted."""
import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_copilot.schematic_evidence import (
    LocalParameterSnapshotPort,
    ParameterCandidate,
    ParameterConditions,
    ParameterLocator,
    compare_plan_xml,
    finite_rc_decoupling,
    parse_xml_netlist,
)


def test_independent_xml_reads_physical_pin_nets():
    data = b'<export><components><comp ref="U1"><libsource lib="Fixture" part="TEST"/><value>TEST</value><footprint>Fixture:DFN4</footprint><property name="Manufacturer" value="Fixture"/><property name="MPN" value="TEST"/><property name="Package" value="DFN4"/></comp></components><libparts><libpart lib="Fixture" part="TEST"><pins><pin num="2" name="SCL"/></pins></libpart></libparts><nets><net name="SCL"><node ref="U1" pin="2"/></net></nets></export>'
    observed = parse_xml_netlist(data)
    assert observed.components[0].reference == "U1"
    assert observed.pin_nets == (("U1", "2", "SCL"),)


@pytest.mark.parametrize("data", [b'<export/>', b'<!DOCTYPE export [<!ENTITY x "boom">]><export/>',
    b'<export><components><comp ref="U1"/><comp ref="U1"/></components><nets/></export>'])
def test_xml_unknown_or_duplicate_never_proves_topology(data):
    with pytest.raises(ValueError):
        parse_xml_netlist(data)


def test_conditions_unknown_is_explicit_and_nonfinite_rejected():
    assert ParameterConditions().mode == "UNKNOWN"
    with pytest.raises(ValueError):
        ParameterConditions(voltage_min="NaN", voltage_max="3.4")
    with pytest.raises(ValueError):
        ParameterConditions(voltage_min="4", voltage_max="3")
    with pytest.raises(ValueError):
        ParameterLocator(page=0, table="x", row="y")


def candidate(source, project_id, conversation_id, key="SUPPLY_MIN", value="3", original=b"synthetic original"):
    return ParameterCandidate(evidence_id="fixture-" + key, tenant_id="tenant-1", project_id=project_id, conversation_id=conversation_id,
        source_record_id=source.source_id, source_record_fingerprint=source.fingerprint, manufacturer=source.manufacturer,
        mpn=source.mpn, package=source.package, document_uri="fixture://original", document_revision="fixture-1",
        content_sha256="sha256:" + hashlib.sha256(original).hexdigest(), locator=ParameterLocator(page=1, table="fixture", row=key),
        parameter_key=key, bound_kind="MIN" if key == "SUPPLY_MIN" else "MAX", value_decimal=value, unit="V",
        conditions=ParameterConditions(voltage_min="3", voltage_max="4", temperature_min="0", temperature_max="50", mode="I2C_STANDARD", package=source.package))


def test_local_snapshot_rechecks_original_digest_and_extracted_value():
    source = SimpleNamespace(source_id="source-1", fingerprint="sha256:" + "1" * 64, manufacturer="Fixture", mpn="Fixture", package="Fixture")
    value = candidate(source, "project-1", "conversation-1")
    content = [b"synthetic original"]
    port = LocalParameterSnapshotPort(read_document=lambda uri, rev: content[0], read_locator=lambda data, locator: value.model_dump(mode="python"))
    assert port.read_parameter(value) == value
    content[0] = b"replaced original"
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        port.read_parameter(value)


def test_parameter_owner_review_is_immutable_and_stale(tmp_path):
    from tests.engineering_copilot.test_schematic_production_g3 import (
        _runtime_with_approved_plan,
    )
    catalog, project, session, detail = _runtime_with_approved_plan(tmp_path)
    source = detail.hardware_selection.proposal.selected_components[0].source
    value = candidate(source, project.project_id, session.conversation.conversation_id)
    content = [b"synthetic original"]
    port = LocalParameterSnapshotPort(read_document=lambda uri, rev: content[0], read_locator=lambda data, locator: value.model_dump(mode="python"))
    catalog._schematic_parameter_port = port
    assert catalog.propose_schematic_parameter(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1", candidate=value) == value
    review = catalog.review_schematic_parameter(project.project_id, value.evidence_id, tenant_id="tenant-1", requester="engineer-1", evidence_fingerprint=value.fingerprint, decision="APPROVED")
    assert review.decision == "APPROVED"
    assert catalog.review_schematic_parameter(project.project_id, value.evidence_id, tenant_id="tenant-1", requester="engineer-1", evidence_fingerprint=value.fingerprint, decision="APPROVED") == review
    from embedded_copilot.project_catalog import ProjectCatalogConflict
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_schematic_parameter(project.project_id, value.evidence_id, tenant_id="tenant-1", requester="engineer-1", evidence_fingerprint=value.fingerprint, decision="REJECTED")
    evidence, current = catalog.schematic_parameter(project.project_id, value.evidence_id, tenant_id="tenant-1", requester="engineer-1")
    assert current == "CURRENT"
    content[0] = b"replaced original"
    assert catalog.schematic_parameter(project.project_id, value.evidence_id, tenant_id="tenant-1", requester="engineer-1") == (evidence, "STALE")


def test_full_xml_compared_against_parts_and_physical_endpoints():
    source = SimpleNamespace(package="DFN4")
    component = SimpleNamespace(instance_id="part-1", hardware_slot_id="slot-1", manufacturer="Fixture", mpn="TEST", footprint="Fixture:DFN4", symbol="Fixture:TEST", quantity=1)
    plan = SimpleNamespace(component_instances=(component,), pin_bindings=(SimpleNamespace(component_id="part-1", pin_number="2", pin_name="SCL"),), nets=(SimpleNamespace(name="SCL", endpoints=(SimpleNamespace(component_id="part-1", pin_number="2"),)),))
    production = SimpleNamespace(proposal=SimpleNamespace(components=(SimpleNamespace(component_id="part-1", reference="U1"),)))
    selection = SimpleNamespace(proposal=SimpleNamespace(selected_components=(SimpleNamespace(slot_id="slot-1", source=source),)))
    data = b'<export><components><comp ref="U1"><libsource lib="Fixture" part="TEST"/><footprint>Fixture:DFN4</footprint><property name="Manufacturer" value="Fixture"/><property name="MPN" value="TEST"/><property name="Package" value="DFN4"/></comp></components><libparts><libpart lib="Fixture" part="TEST"><pins><pin num="2" name="SCL"/></pins></libpart></libparts><nets><net name="SCL"><node ref="U1" pin="2"/></net></nets></export>'
    assert compare_plan_xml(plan, production, selection, parse_xml_netlist(data))
    for altered in (data.replace(b'pin="2"', b'pin="3"'), data.replace(b'value="TEST"', b'value="OTHER"'), data.replace(b'name="SCL"', b'name="SDA"')):
        assert not compare_plan_xml(plan, production, selection, parse_xml_netlist(altered))


def test_finite_rc_decoupling_recomputes_worst_case():
    values = {key: Decimal(value) for key, value in {"r_ohm": "10000", "r_tolerance": ".01", "cbus_f": "0.00000000005", "rise_limit_s": ".000001", "frequency_hz": "100000", "frequency_limit_hz": "100000", "c_effective_f": ".0000001", "c_required_f": ".00000008", "vol_max_v": ".4", "iol_min_a": ".003", "leakage_max_a": ".000001", "vil_max_v": ".8", "vmax_v": "3.4"}.items()}
    assert finite_rc_decoupling(values)
    assert not finite_rc_decoupling({**values, "cbus_f": Decimal(".000000001")})
    assert not finite_rc_decoupling({**values, "c_effective_f": Decimal(".00000001")})
    assert not finite_rc_decoupling({**values, "iol_min_a": Decimal(".00001")})
    assert not finite_rc_decoupling({**values, "leakage_max_a": Decimal("NaN")})
