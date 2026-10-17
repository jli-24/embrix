"""P2 standard limits resolve only through their exact reviewed typed evidence."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_copilot.schematic_evidence import ParameterConditions, ParameterLocator, resolve_calculation_inputs
from embedded_copilot.engineering_runtime import CalculationRecord
from embedded_copilot.project_catalog.models import StandardApplicabilityEvidenceV2, StandardApplicabilityReviewV2, ReviewedStandardApplicabilityV2, StandardTargetBindingV2

FP = "sha256:" + "a" * 64
NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _inputs(field="frequency_max_hz", input_name="frequency_limit_hz", value="100000", unit="Hz"):
    corner = ParameterConditions(voltage_min="3.2", voltage_max="3.4", temperature_min="20", temperature_max="30", mode="I2C_STANDARD")
    evidence = StandardApplicabilityEvidenceV2(schema_version=2, evidence_id="standard-1",
        tenant_id="tenant", project_id="project", conversation_id="conversation",
        document_uri="isolated-test:um10204", document_revision="Rev7", content_sha256=FP,
        locator=ParameterLocator(page=44, table="Table 11", row=field), mode="I2C_STANDARD",
        **{field: value}, target_net_ids=("sda", "scl"), target_components=(StandardTargetBindingV2(
            component_instance_id="sensor", source_record_id="shtc3", source_record_fingerprint=FP,
            device_conditions=ParameterConditions(voltage_min="1.62", voltage_max="3.6", temperature_min="5", temperature_max="60", mode="I2C_STANDARD", package="DFN-4")),),
        requirement_id="requirement", requirement_fingerprint=FP, plan_id="engineering-plan", plan_fingerprint=FP, created_at=NOW)
    review = StandardApplicabilityReviewV2(schema_version=2, evidence_id=evidence.evidence_id,
        evidence_fingerprint=evidence.fingerprint, review_id="review", tenant_id="tenant",
        project_id="project", conversation_id="conversation", decision="APPROVED", reviewer_id="owner", decided_at=NOW)
    reviewed = ReviewedStandardApplicabilityV2(evidence=evidence, review=review)
    calculation = CalculationRecord(calculation_id="calculation", owner_ref="pullup", component_instance_id="resistor",
        equation="S02_RC_DECOUPLING_V1", inputs=((input_name, value),), corner=corner.model_dump_json(exclude={"package"}),
        input_sources=({"input_name":input_name, "value":value, "unit":unit, "source_kind":"ENGINEERING_EVIDENCE",
            "source_id": evidence.evidence_id, "source_fingerprint":evidence.fingerprint,
            "content_fingerprint":evidence.content_sha256, "locator":evidence.locator.model_dump_json(),
            "condition":corner.model_dump_json(exclude={"package"}), "component_instance_id":"sensor"},))
    requirement = SimpleNamespace(standard_fields=(), fingerprint=FP, conversation_id="conversation")
    return calculation, requirement, reviewed


@pytest.mark.parametrize("field,name,value,unit", (("frequency_max_hz", "frequency_limit_hz", "100000", "Hz"),
    ("rise_time_max_s", "rise_limit_s", "0.000001", "s")))
def test_standard_exact_reviewed_limit_resolves(field,name,value,unit):
    from decimal import Decimal
    calculation, requirement, reviewed = _inputs(field,name,value,unit)
    assert resolve_calculation_inputs(calculation, requirement, (), reviewed_standards=(reviewed,)) == {name:Decimal(value)}


@pytest.mark.parametrize("fault", ("missing", "no_review", "rejected", "fingerprint", "content", "locator", "unit", "target", "condition", "requirement", "value"))
def test_standard_unreviewed_or_wrong_exact_input_is_unresolved(fault):
    calculation, requirement, reviewed = _inputs()
    sources = list(calculation.input_sources)
    changes = {"fingerprint": {"source_fingerprint":FP}, "content":{"content_fingerprint":"sha256:"+"b"*64},
        "locator":{"locator":"wrong"}, "unit":{"unit":"kHz"}, "target":{"component_instance_id":"other"},
        "condition":{"condition":ParameterConditions(voltage_min="1.5", voltage_max="4", temperature_min="20",temperature_max="30", mode="I2C_STANDARD").model_dump_json(exclude={"package"})}, "value":{"value":"999"}}
    if fault in changes:
        sources[0] = sources[0].model_copy(update=changes[fault])
        calculation = calculation.model_copy(update={"input_sources":tuple(sources), "corner":sources[0].condition})
    elif fault == "no_review":
        reviewed = ReviewedStandardApplicabilityV2(evidence=reviewed.evidence)
    elif fault == "rejected":
        body = reviewed.review.model_dump(mode="python")
        body.update(decision="REJECTED", fingerprint="sha256:"+"0"*64)
        reviewed = ReviewedStandardApplicabilityV2(evidence=reviewed.evidence, review=StandardApplicabilityReviewV2(**body))
    elif fault == "requirement":
        requirement.fingerprint = "sha256:"+"b"*64
    assert resolve_calculation_inputs(calculation, requirement, (), reviewed_standards=() if fault=="missing" else (reviewed,)) is None


def test_bus_capacitance_is_not_resolved_from_standard_allowed_maximum():
    calculation, requirement, reviewed = _inputs("cbus_max_f", "cbus_f", "0.0000000004", "F")
    assert resolve_calculation_inputs(calculation, requirement, (), reviewed_standards=(reviewed,)) is None


@pytest.mark.parametrize("args,evidence_fp,review_fp", (((),
    "sha256:85074454a603460db699d5091027629c253a8eb0468d460ea6d8d27562841f0e",
    "sha256:5b513b3c5b72f649ab68cb735bb553d86ef0cc9a5569043c206c063ee5e51ce9"),
    (("rise_time_max_s", "rise_limit_s", "0.000001", "s"),
    "sha256:ea81e459504d83b4e95c26a2a413161ce24495d347e4538cd20bf06056a2e52d",
    "sha256:1eba1e05c749fe7ceb2efb700154bf3b3320e29c52ff352d04195c6b5b3729ce")))
def test_standard_canonical_fixed_vectors_and_json_roundtrip(args,evidence_fp,review_fp):
    _calculation, _requirement, reviewed = _inputs(*args)
    assert reviewed.evidence.fingerprint == evidence_fp
    assert reviewed.review.fingerprint == review_fp
    assert ReviewedStandardApplicabilityV2.model_validate_json(reviewed.model_dump_json()) == reviewed
