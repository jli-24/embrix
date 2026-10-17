"""P3 reviewed library mapping preserves manufacturer EP identity and legacy JSON."""
from datetime import UTC, datetime
import pytest
from embedded_copilot.engineering_copilot.schematic_evidence import LibraryTerminalBindingV2, ParameterLocator
from embedded_copilot.project_catalog.models import LibraryTerminalMapEvidenceV2, LibraryTerminalMapReviewV2, ReviewedLibraryTerminalMapV2

FP="sha256:"+"a"*64
NOW=datetime(2026,10,9,tzinfo=UTC)


def _evidence():
    bindings=tuple(LibraryTerminalBindingV2(manufacturer_terminal=number,
        manufacturer_locator=ParameterLocator(page=6,table="Table 7",row=number) if number!="EP" else ParameterLocator(section="4 Interface Specifications",clause="CENTER_PAD_INTERNAL_VSS_AND_OPTIONAL_PCB_GND"),
        logical_symbol_pin=str(index), physical_pad_ids=(str(index),), net_id="gnd" if index in (4,5) else "net-"+str(index))
        for index,number in enumerate(("1","2","3","4","EP"),1))
    return LibraryTerminalMapEvidenceV2(schema_version=2,evidence_id="library-map",tenant_id="tenant",project_id="project",conversation_id="conversation",
        component_instance_id="sensor",source_record_id="source",source_record_fingerprint=FP,plan_id="plan",plan_fingerprint=FP,
        symbol_library_id="SHTC3_EP:SHTC3_EP",symbol_uri="local:symbol",symbol_sha256=FP,
        footprint_library_id="SHTC3_EP:footprint",footprint_uri="local:footprint",footprint_sha256=FP,
        terminal_bindings=bindings,created_at=NOW)


def test_library_map_json_roundtrip_and_ep_is_not_manufacturer_pin_five():
    evidence=_evidence()
    review=LibraryTerminalMapReviewV2(schema_version=2,evidence_id=evidence.evidence_id,evidence_fingerprint=evidence.fingerprint,
        review_id="review",tenant_id="tenant",project_id="project",conversation_id="conversation",decision="APPROVED",reviewer_id="owner",decided_at=NOW)
    state=ReviewedLibraryTerminalMapV2(evidence=evidence,review=review)
    assert ReviewedLibraryTerminalMapV2.model_validate_json(state.model_dump_json())==state
    assert evidence.terminal_bindings[-1].manufacturer_terminal=="EP"
    assert evidence.terminal_bindings[-1].logical_symbol_pin=="5"
    assert "fingerprint" not in state.model_dump()


@pytest.mark.parametrize("field,value", (("schema_version",3),("unapproved_field","extra"),("symbol_sha256",FP.replace("a","b")),("terminal_bindings",())))
def test_library_map_rejects_tamper_unknown_schema_or_empty_bindings(field,value):
    body=_evidence().model_dump(mode="json");body[field]=value
    with pytest.raises(ValueError): LibraryTerminalMapEvidenceV2.model_validate(body)


@pytest.mark.parametrize("field,value", (("manufacturer_terminal","5"),("logical_symbol_pin",""),("physical_pad_ids",("1","1")),("net_id","")))
def test_library_binding_rejects_invalid_identity(field,value):
    body=_evidence().terminal_bindings[-1].model_dump(mode="python");body[field]=value
    with pytest.raises(ValueError): LibraryTerminalBindingV2.model_validate(body)
