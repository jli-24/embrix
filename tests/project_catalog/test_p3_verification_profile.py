"""Approved profile2 record selectors; same eight claims, legacy identity unchanged."""
from datetime import UTC,datetime

import pytest

from embedded_copilot.engineering_runtime.common import canonical_fingerprint
from embedded_copilot.engineering_copilot.schematic_evidence import PROFILE_ID,PROFILE_FINGERPRINT,PROFILE_FINGERPRINT_V2,REQUIRED_CLAIM_IDS
from embedded_copilot.project_catalog.models import SchematicVerificationRecord,SchematicClaimResolution
from embedded_copilot.api.project_catalog_models import SchematicVerificationInput

FP="sha256:"+"1"*64


def _record(version=1,**changes):
    data=dict(verification_id="verification",tenant_id="tenant",project_id="project",conversation_id="conversation",
        intent_id="circuit",intent_revision="r1",intent_fingerprint=FP,plan_id="plan",plan_fingerprint=FP,
        artifact_fingerprint=FP,generation_operation_id="write",generation_receipt_fingerprint=FP,
        parse_validation_fingerprint=FP,erc_status="BLOCKED",claims=tuple(SchematicClaimResolution(claim_id=claim,status="BLOCKED",
            reason="EVIDENCE_UNAVAILABLE",proof=None) for claim in REQUIRED_CLAIM_IDS),profile_id=PROFILE_ID,profile_version=version,
        profile_fingerprint=PROFILE_FINGERPRINT_V2 if version==2 else PROFILE_FINGERPRINT,required_claim_ids=REQUIRED_CLAIM_IDS,
        requirement_review_fingerprint=FP,architecture_review_fingerprint=FP,selection_approval_fingerprint=FP,context_fingerprint=FP,
        aggregate_status="BLOCKED",created_at=datetime(2026,10,9,tzinfo=UTC),readback_operation_id="readback")
    if version==2:data.update(library_map_evidence_ids=("map",),library_map_evidence_fingerprints=(FP,))
    data.update(changes)
    draft=SchematicVerificationRecord.model_construct(**data,fingerprint="sha256:"+"0"*64)
    return SchematicVerificationRecord.model_validate({**draft.model_dump(mode="python"),"fingerprint":canonical_fingerprint(draft,exclude={"fingerprint"})})


def test_profile2_strict_roundtrip_and_eight_claims_with_legacy_fields_omitted():
    legacy=_record()
    current=_record(2)
    assert "library_map_evidence_ids" not in legacy.model_dump(mode="json")
    assert SchematicVerificationRecord.model_validate_json(legacy.model_dump_json())==legacy
    assert SchematicVerificationRecord.model_validate_json(current.model_dump_json())==current
    assert tuple(item.claim_id for item in current.claims)==REQUIRED_CLAIM_IDS
    assert current.fingerprint!=legacy.fingerprint


@pytest.mark.parametrize("changes",(dict(library_map_evidence_ids=None),dict(library_map_evidence_ids=()),
    dict(library_map_evidence_fingerprints=("bad",)),dict(readback_operation_id=None),dict(profile_fingerprint=PROFILE_FINGERPRINT),
    dict(library_map_evidence_ids=("a","b"),library_map_evidence_fingerprints=(FP,FP))))
def test_profile2_rejects_incomplete_or_noncanonical_selectors(changes):
    with pytest.raises(ValueError):_record(2,**changes)


def test_legacy_record_cannot_carry_or_implicitly_upgrade_library_map_selectors():
    with pytest.raises(ValueError):_record(library_map_evidence_ids=("map",),library_map_evidence_fingerprints=(FP,))


def test_formal_verification_input_requires_explicit_readback_and_single_map_pair():
    fields=dict(intent_id="circuit",intent_revision="r1",intent_fingerprint=FP,plan_id="plan",plan_fingerprint=FP,
        artifact_fingerprint=FP,generation_operation_id="write",readback_operation_id="readback",
        library_map_evidence_ids=["map"],library_map_evidence_fingerprints=[FP])
    assert SchematicVerificationInput.model_validate(fields).library_map_evidence_ids==("map",)
    for changes in (dict(readback_operation_id=None),dict(library_map_evidence_ids=None),dict(library_map_evidence_fingerprints=["bad"]),
                    dict(library_map_evidence_ids=["map","other"],library_map_evidence_fingerprints=[FP,FP])):
        with pytest.raises(ValueError):SchematicVerificationInput.model_validate(fields|changes)


def test_approved_profile_record_canonical_fixed_vectors():
    assert _record().fingerprint=="sha256:2c0168e558038bbd52fd3b489c742741452d8f29745a49dde688d78f902ede76"
    assert _record(2).fingerprint=="sha256:290d3dc0d5e0607dcb8d76b63bc31752f6a09d92ae6f6a1050d62684a60a6aa1"
