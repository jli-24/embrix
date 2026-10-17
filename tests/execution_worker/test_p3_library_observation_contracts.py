"""P3 raw observation excludes self-hashing identity and rejects ambiguous JSON."""
import hashlib,json
from datetime import UTC, datetime
import pytest
from embedded_copilot.execution_worker.contracts import ArtifactIdentity, IndependentLibraryMapObservationV2, parse_library_observation_payload
from tests.project_catalog.test_p3_library_terminal_map_contracts import _evidence,FP


def _payload():
    evidence=_evidence()
    body=dict(schema_version=2,source_evidence_id=evidence.evidence_id,source_evidence_fingerprint=evidence.fingerprint,
        symbol_sha256=evidence.symbol_sha256,footprint_sha256=evidence.footprint_sha256,
        observed_symbol_pins=["1","2","3","4","5"],observed_physical_pad_ids=["1","2","3","4","5"],
        observed_net_bindings=[item.model_dump(mode="json") for item in evidence.terminal_bindings])
    return body


def _identity(raw,**changes):
    body=dict(artifact_id="library-observation",tenant_id="tenant",project_id="project",target_id="target",
        workspace_fingerprint=FP,build_operation_id="readback",toolchain_id="KICAD_XML_NETLIST",toolchain_version="9.0",
        build_configuration="READ_ONLY_XML",artifact_type="MANIFEST",artifact_sha256="sha256:"+hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),created_at=datetime(2026,10,9,tzinfo=UTC))
    body.update(changes)
    return ArtifactIdentity(**body)


def test_library_observation_reconstructs_strict_dto_from_actual_raw_bytes_and_external_identity():
    raw=json.dumps(_payload(),sort_keys=True,separators=(",",":")).encode()
    observation=parse_library_observation_payload(raw,_identity(raw))
    assert observation.artifact.artifact_sha256=="sha256:"+hashlib.sha256(raw).hexdigest()
    assert IndependentLibraryMapObservationV2.model_validate_json(observation.model_dump_json())==observation
    assert "artifact" not in json.loads(raw) and "fingerprint" not in json.loads(raw)


@pytest.mark.parametrize("fault",("identity_in_raw","fingerprint_in_raw","extra","duplicate","size","sha","kind","schema","target_duplicates"))
def test_library_observation_rejects_raw_or_identity_fault(fault):
    body=_payload()
    if fault in {"identity_in_raw","fingerprint_in_raw","extra"}:
        body[{"identity_in_raw":"artifact","fingerprint_in_raw":"fingerprint","extra":"unapproved"}[fault]]={}
    elif fault=="schema": body["schema_version"]=3
    elif fault=="target_duplicates":body["observed_symbol_pins"].append("1")
    raw=json.dumps(body,separators=(",",":")).encode()
    if fault=="duplicate":raw=raw[:-1]+b',"schema_version":2}'
    changes={"size":{"size_bytes":len(raw)+1},"sha":{"artifact_sha256":FP},"kind":{"artifact_type":"NETLIST"}}.get(fault,{})
    with pytest.raises(ValueError):parse_library_observation_payload(raw,_identity(raw,**changes))


def test_readback_library_pair_is_explicit_and_legacy_fingerprint_unchanged():
    from tests.execution_worker.test_s02_evidence_contracts import operation
    from embedded_copilot.execution_worker.contracts import SchematicReadBackOperation, SchematicParseOperation, OperationRequest
    legacy=operation(SchematicReadBackOperation)
    assert "library_map_evidence_id" not in legacy.model_dump(mode="json")
    assert SchematicReadBackOperation.model_validate_json(legacy.model_dump_json()).fingerprint==legacy.fingerprint
    selected=operation(SchematicReadBackOperation,library_map_evidence_id="map",library_map_evidence_fingerprint=FP)
    assert selected.fingerprint!=legacy.fingerprint
    assert OperationRequest.model_validate_json(OperationRequest(operation=selected).model_dump_json()).operation==selected
    for model,fields in ((SchematicParseOperation,dict(library_map_evidence_id="map",library_map_evidence_fingerprint=FP)),
                         (SchematicReadBackOperation,dict(library_map_evidence_id="map"))):
        with pytest.raises(ValueError):operation(model,**fields)


def test_readback_api_accepts_only_complete_approved_library_selector():
    from embedded_copilot.api.project_catalog_models import SchematicEvidenceOperationInput
    selected = SchematicEvidenceOperationInput(kind="READBACK", library_map_evidence_id="map", library_map_evidence_fingerprint=FP)
    assert selected.library_map_evidence_id == "map"
    assert SchematicEvidenceOperationInput(kind="PARSE").library_map_evidence_id is None
    for fields in (dict(kind="PARSE", library_map_evidence_id="map", library_map_evidence_fingerprint=FP),
                   dict(kind="READBACK", library_map_evidence_id="map"),
                   dict(kind="READBACK", library_map_evidence_fingerprint=FP)):
        with pytest.raises(ValueError):
            SchematicEvidenceOperationInput(**fields)
