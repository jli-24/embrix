"""Offline checks of the private CONDITIONAL / NOT_WIRED mathematical core."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import localcontext
from fractions import Fraction

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    EngineeringStage,
    ModelPolicy,
    ModelRequest,
    ModelUsage,
    PricingMetadata,
    ProtocolFamily,
)
from embedded_copilot.model_gateway.invocation._budget_preflight import (
    _BillableInputCount,
    _ConditionalProof,
    _ExactTariff,
    _ExternalPremises,
    _OutputCap,
    _postactual_cost,
    _ProofBinding,
    _prove_one_request,
    _RequestScope,
    _transport_digest,
)
from embedded_copilot.model_gateway.invocation.contracts import ModelExecutionSnapshot

NOW = datetime(2026, 10, 14, tzinfo=UTC)
PAST = NOW - timedelta(minutes=5)
FUTURE = NOW + timedelta(minutes=5)
BODY = b'{"model":"model-1","max_completion_tokens":1000,"messages":[]}'


@pytest.fixture
def inputs():
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="offline proof",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    request = ModelRequest(
        request_id="request-1",
        project_id="project-1",
        task="offline proof",
        engineering_context=context,
        system_instruction="Use supplied evidence.",
        model_policy=ModelPolicy(
            primary_provider="provider-1",
            model_name="model-1",
            max_output_tokens=1000,
        ),
    )
    snapshot = ModelExecutionSnapshot(
        project_id="project-1",
        stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
        selected_model=BoundModelIdentity(
            provider_id="provider-1",
            endpoint_id="endpoint-1",
            credential_ref="credential:provider-1",
            model_profile_id="profile-1",
            model_id="model-1",
        ),
        connection_id="connection-1",
        credential_revision="credential-r1",
        endpoint_revision="endpoint-r1",
        policy_source="project",
        policy_fingerprint=request.model_policy.fingerprint,
        selection_fingerprint="sha256:" + "a" * 64,
    )
    binding = _ProofBinding(
        request_fingerprint=request.fingerprint,
        snapshot=snapshot,
        transport_sha256=_transport_digest(BODY),
        protocol=ProtocolFamily.OPENAI_CHAT,
        output_cap_field="max_completion_tokens",
    )
    return {
        "request": request,
        "snapshot": snapshot,
        "final_transport_bytes": BODY,
        "protocol": ProtocolFamily.OPENAI_CHAT,
        "output_cap_field": "max_completion_tokens",
        "tariff": _ExactTariff(
            binding=binding,
            observed_at=PAST,
            valid_until=FUTURE,
            input_rate="0.00005",
            output_rate="0.00005",
            currency="USD",
            pricing_unit="PER_TOKEN",
            additional_fees_absent=True,
        ),
        "input_count": _BillableInputCount(
            binding=binding,
            observed_at=PAST,
            valid_until=FUTURE,
            input_tokens=1000,
            basis="EXACT",
            covers_all_billable_input=True,
        ),
        "output_cap": _OutputCap(
            binding=binding,
            observed_at=PAST,
            valid_until=FUTURE,
            output_tokens=1000,
            covers_all_billable_output=True,
            includes_reasoning=True,
        ),
        "premises": _ExternalPremises(
            tariff_qualified=True,
            count_qualified=True,
            cap_qualified=True,
            transport_semantics_qualified=True,
        ),
        "scope": _RequestScope(),
        "now": NOW,
    }


def test_exact_threshold_is_conditional_and_has_no_execution_authority(inputs):
    proof = _prove_one_request(**inputs)
    assert proof.status == "CONDITIONAL"
    assert proof.wiring == "NOT_WIRED"
    assert proof.upper_bound_usd == Fraction(1, 10)
    assert proof.binding == inputs["tariff"].binding
    assert proof.mathematical_threshold_usd == Fraction(1, 10)
    assert not hasattr(proof, "execution_permitted")
    assert not hasattr(proof, "approved")
    assert isinstance(proof, _ConditionalProof)


@pytest.mark.parametrize("delta", ["0.000000000000000000001", "0.00000001", "0.01"])
def test_every_exact_amount_above_threshold_is_rejected(inputs, delta):
    rate = Fraction("0.00005") + Fraction(delta)
    # Terminating exact decimal strings; no ambient Decimal arithmetic.
    exponent = len(delta.partition(".")[2])
    numerator = int(rate * 10**exponent)
    rate_text = f"{numerator // 10**exponent}.{numerator % 10**exponent:0{exponent}d}"
    inputs["tariff"] = replace(inputs["tariff"], output_rate=rate_text)
    proof = _prove_one_request(**inputs)
    assert proof.status == "REJECTED"
    assert proof.reason == "THRESHOLD_EXCEEDED"
    assert proof.upper_bound_usd > Fraction(1, 10)


def test_decimal_context_does_not_round_an_overrun_to_threshold(inputs):
    inputs["tariff"] = replace(
        inputs["tariff"], output_rate="0.000050000000000000000000001"
    )
    with localcontext() as context:
        context.prec = 2
        proof = _prove_one_request(**inputs)
    assert proof.status == "REJECTED"
    assert proof.upper_bound_usd == Fraction("0.100000000000000000000001")


@pytest.mark.parametrize(
    "unit,rate",
    [
        ("PER_TOKEN", "0.00005"),
        ("PER_1K_TOKENS", "0.05"),
        ("PER_1M_TOKENS", "50"),
    ],
)
def test_explicit_supported_units_are_exact(inputs, unit, rate):
    inputs["tariff"] = replace(
        inputs["tariff"], pricing_unit=unit, input_rate=rate, output_rate=rate
    )
    assert _prove_one_request(**inputs).upper_bound_usd == Fraction(1, 10)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        0.0,
        0.1,
        True,
        False,
        -1,
        "NaN",
        "Infinity",
        "-Infinity",
        "-0.01",
        "",
        " 0.1",
        "0.1 ",
        "1e-3",
    ],
)
def test_missing_or_nonexact_tariff_is_rejected(inputs, bad):
    with pytest.raises(ValueError, match="EXACT_TARIFF_INVALID"):
        replace(inputs["tariff"], input_rate=bad)


def test_float_pricing_metadata_is_not_an_exact_tariff_source(inputs):
    metadata = PricingMetadata(input_token_price=0.0, output_token_price=0.0)
    with pytest.raises(ValueError):
        replace(inputs["tariff"], input_rate=metadata.input_token_price)


def test_qualified_explicit_zero_is_valid_but_missing_price_is_not(inputs):
    inputs["tariff"] = replace(inputs["tariff"], input_rate="0", output_rate="0")
    assert _prove_one_request(**inputs).upper_bound_usd == 0
    inputs["tariff"] = None
    proof = _prove_one_request(**inputs)
    assert proof.status == "REJECTED"
    assert proof.upper_bound_usd is None


@pytest.mark.parametrize(
    "name,value",
    [
        ("currency", None),
        ("currency", "CNY"),
        ("currency", "usd"),
        ("pricing_unit", None),
        ("pricing_unit", "USD"),
        ("additional_fees_absent", False),
        ("additional_fees_absent", 1),
    ],
)
def test_unknown_currency_unit_or_additional_fees_fail_closed(inputs, name, value):
    with pytest.raises(ValueError):
        replace(inputs["tariff"], **{name: value})


def test_cache_categories_use_the_conservative_highest_input_price(inputs):
    inputs["tariff"] = replace(
        inputs["tariff"], cache_write_rate="0.00006", cache_rates_replace_input=True
    )
    proof = _prove_one_request(**inputs)
    assert proof.status == "REJECTED"
    assert proof.upper_bound_usd == Fraction("0.11")


@pytest.mark.parametrize(
    "name",
    [
        "tariff_qualified",
        "count_qualified",
        "cap_qualified",
        "transport_semantics_qualified",
    ],
)
def test_qualification_is_an_external_premise_not_inferred_from_hash(inputs, name):
    inputs["premises"] = replace(inputs["premises"], **{name: False})
    proof = _prove_one_request(**inputs)
    assert proof.status == "REJECTED"
    assert proof.reason == "EXTERNAL_PREMISE_MISSING"
    assert proof.upper_bound_usd is None


@pytest.mark.parametrize(
    "name", ["tariff", "input_count", "output_cap", "premises", "scope"]
)
def test_missing_required_evidence_fails_closed(inputs, name):
    inputs[name] = None
    assert _prove_one_request(**inputs).status == "REJECTED"


@pytest.mark.parametrize("evidence", ["tariff", "input_count", "output_cap"])
@pytest.mark.parametrize(
    "change", ["expired", "future", "request", "hash", "field", "protocol"]
)
def test_evidence_validity_and_binding_are_checked(inputs, evidence, change):
    original = inputs[evidence]
    if change == "expired":
        altered = replace(original, valid_until=NOW)
    elif change == "future":
        altered = replace(original, observed_at=NOW + timedelta(seconds=1))
    else:
        changes = {
            "request": {"request_fingerprint": "sha256:" + "b" * 64},
            "hash": {"transport_sha256": "sha256:" + "b" * 64},
            "field": {"output_cap_field": "max_tokens"},
            "protocol": {"protocol": ProtocolFamily.OPENAI_RESPONSES},
        }
        altered = replace(
            original, binding=replace(original.binding, **changes[change])
        )
    inputs[evidence] = altered
    assert _prove_one_request(**inputs).status == "REJECTED"


def test_final_bytes_mutation_rejects_even_when_abstract_request_is_unchanged(inputs):
    inputs["final_transport_bytes"] += b" "
    assert _prove_one_request(**inputs).reason == "EVIDENCE_BINDING_MISMATCH"


@pytest.mark.parametrize(
    "name",
    [
        "connection_id",
        "credential_revision",
        "endpoint_revision",
        "policy_source",
    ],
)
def test_current_snapshot_revision_mutation_rejects_old_evidence(inputs, name):
    data = inputs["snapshot"].model_dump()
    data[name] += "-changed"
    data["fingerprint"] = "sha256:" + "0" * 64
    inputs["snapshot"] = ModelExecutionSnapshot.model_validate(data)
    assert _prove_one_request(**inputs).reason == "EVIDENCE_BINDING_MISMATCH"


@pytest.mark.parametrize(
    "name",
    [
        "provider_id",
        "endpoint_id",
        "credential_ref",
        "model_profile_id",
        "model_id",
    ],
)
def test_exact_model_identity_mutation_rejects(inputs, name):
    data = inputs["snapshot"].model_dump()
    data["selected_model"][name] += "-changed"
    data["fingerprint"] = "sha256:" + "0" * 64
    inputs["snapshot"] = ModelExecutionSnapshot.model_validate(data)
    assert _prove_one_request(**inputs).status == "REJECTED"


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("input_count", "input_tokens", True),
        ("input_count", "input_tokens", -1),
        ("input_count", "input_tokens", 1.0),
        ("input_count", "basis", "ESTIMATE"),
        ("input_count", "covers_all_billable_input", False),
        ("output_cap", "output_tokens", True),
        ("output_cap", "output_tokens", -1),
        ("output_cap", "covers_all_billable_output", False),
        ("output_cap", "includes_reasoning", False),
    ],
)
def test_incomplete_or_invalid_token_bound_is_rejected(inputs, target, field, value):
    with pytest.raises(ValueError):
        replace(inputs[target], **{field: value})


def test_qualified_conservative_input_upper_bound_is_supported(inputs):
    inputs["input_count"] = replace(
        inputs["input_count"], basis="CONSERVATIVE_UPPER_BOUND"
    )
    assert _prove_one_request(**inputs).status == "CONDITIONAL"


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_requests", 2),
        ("max_requests", True),
        ("retry_count", 1),
        ("retry_count", False),
        ("fallback_used", True),
    ],
)
def test_only_one_request_without_retry_or_fallback_is_in_scope(field, value):
    with pytest.raises(ValueError):
        _RequestScope(**{field: value})


def test_request_fallback_policy_is_rejected(inputs):
    data = inputs["request"].model_dump()
    data["model_policy"]["fallback_providers"] = ("fallback-1",)
    data["model_policy"]["fingerprint"] = "sha256:" + "0" * 64
    data["fingerprint"] = "sha256:" + "0" * 64
    inputs["request"] = ModelRequest.model_validate(data)
    assert _prove_one_request(**inputs).reason == "REQUEST_SCOPE_INVALID"


def test_naive_clock_fails_closed(inputs):
    inputs["now"] = NOW.replace(tzinfo=None)
    assert _prove_one_request(**inputs).status == "REJECTED"


def test_tampered_existing_request_fingerprint_fails_revalidation(inputs):
    object.__setattr__(inputs["request"], "task", "tampered")
    assert _prove_one_request(**inputs).reason == "PROOF_INPUT_INVALID"


@pytest.mark.parametrize(
    "usage,expected,reported,estimated",
    [
        (None, "UNKNOWN", None, None),
        (
            ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            "UNKNOWN",
            None,
            None,
        ),
        (
            ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                provider_reported_cost=0.0,
            ),
            "UNKNOWN",
            0.0,
            None,
        ),
        (
            ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2, currency="USD"),
            "UNKNOWN",
            None,
            None,
        ),
        (
            ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                estimated_cost=0.04,
                currency="USD",
            ),
            "ESTIMATED",
            None,
            0.04,
        ),
        (
            ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                provider_reported_cost=0.0,
                estimated_cost=0.04,
                currency="USD",
            ),
            "REPORTED",
            0.0,
            0.04,
        ),
        (
            ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                provider_reported_cost=0.2,
                currency="CNY",
            ),
            "UNKNOWN",
            0.2,
            None,
        ),
    ],
)
def test_postactual_preserves_reported_estimated_and_unknown(
    usage, expected, reported, estimated
):
    result = _postactual_cost(usage)
    assert result.status == expected
    assert result.reported_cost == reported
    assert result.estimated_cost == estimated
    assert result.wiring == "NOT_WIRED"


@pytest.mark.parametrize("bad", [None, True, b"", bytearray(BODY), "serialized string"])
def test_only_nonempty_final_immutable_bytes_are_supported(inputs, bad):
    inputs["final_transport_bytes"] = bad
    assert _prove_one_request(**inputs).status == "REJECTED"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("tariff_qualified", 1),
        ("count_qualified", "trusted"),
        ("cap_qualified", None),
        ("transport_semantics_qualified", object()),
    ],
)
def test_caller_premises_reject_non_boolean_values(field, bad):
    with pytest.raises(ValueError):
        _ExternalPremises(**{field: bad})


def test_default_premises_do_not_qualify_evidence(inputs):
    inputs["premises"] = _ExternalPremises()
    assert _prove_one_request(**inputs).reason == "EXTERNAL_PREMISE_MISSING"


def test_new_valid_request_fingerprint_cannot_reuse_old_evidence(inputs):
    data = inputs["request"].model_dump()
    data["task"] = "different task"
    data["prompt"] = "different task"
    data["fingerprint"] = "sha256:" + "0" * 64
    inputs["request"] = ModelRequest.model_validate(data)
    assert _prove_one_request(**inputs).reason == "EVIDENCE_BINDING_MISMATCH"


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("tariff", "input_rate", 0.0),
        ("input_count", "input_tokens", True),
        ("output_cap", "includes_reasoning", False),
    ],
)
def test_object_mutation_cannot_bypass_private_evidence_validation(
    inputs, target, field, value
):
    object.__setattr__(inputs[target], field, value)
    assert _prove_one_request(**inputs).reason == "PROOF_INPUT_INVALID"


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", ProtocolFamily.CUSTOM),
        ("protocol", "OPENAI_CHAT"),
        ("output_cap_field", ""),
        ("output_cap_field", "field.with.path"),
        ("transport_sha256", "not-a-hash"),
    ],
)
def test_unknown_protocol_field_and_malformed_hash_are_rejected(inputs, field, value):
    with pytest.raises(ValueError):
        replace(inputs["tariff"].binding, **{field: value})


def test_snapshot_hash_mutation_cannot_bypass_revalidation(inputs):
    object.__setattr__(inputs["snapshot"], "connection_id", "different-connection")
    assert _prove_one_request(**inputs).reason == "PROOF_INPUT_INVALID"


def test_missing_currency_preserves_observation_without_zero_cost_claim():
    usage = ModelUsage(
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        provider_reported_cost=0.07,
        estimated_cost=0.05,
    )
    result = _postactual_cost(usage)
    assert result.status == "UNKNOWN"
    assert result.currency is None
    assert result.reported_cost == 0.07
    assert result.estimated_cost == 0.05


@pytest.mark.parametrize("field", ["provider_id", "model_id"])
def test_self_consistent_snapshot_and_all_evidence_cannot_replace_request_model(
    inputs, field
):
    snapshot_data = inputs["snapshot"].model_dump()
    snapshot_data["selected_model"][field] += "-different"
    snapshot_data["fingerprint"] = "sha256:" + "0" * 64
    inputs["snapshot"] = ModelExecutionSnapshot.model_validate(snapshot_data)
    for evidence_name in ("tariff", "input_count", "output_cap"):
        original = inputs[evidence_name]
        inputs[evidence_name] = replace(
            original, binding=replace(original.binding, snapshot=inputs["snapshot"])
        )
    assert _prove_one_request(**inputs).reason == "REQUEST_IDENTITY_MISMATCH"


@pytest.mark.parametrize("cap", [1, 999, 1001])
def test_output_cap_must_match_request_generation_policy(inputs, cap):
    inputs["output_cap"] = replace(inputs["output_cap"], output_tokens=cap)
    assert _prove_one_request(**inputs).reason == "OUTPUT_CAP_POLICY_MISMATCH"


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint_id", "different-endpoint"),
        ("credential_ref", "credential:different"),
    ],
)
def test_declared_request_endpoint_and_credential_must_match_current_snapshot(
    inputs, field, value
):
    request_data = inputs["request"].model_dump()
    request_data[field] = value
    request_data["fingerprint"] = "sha256:" + "0" * 64
    inputs["request"] = ModelRequest.model_validate(request_data)
    for evidence_name in ("tariff", "input_count", "output_cap"):
        original = inputs[evidence_name]
        inputs[evidence_name] = replace(
            original,
            binding=replace(
                original.binding,
                request_fingerprint=inputs["request"].fingerprint,
            ),
        )
    assert _prove_one_request(**inputs).reason == "REQUEST_IDENTITY_MISMATCH"


def test_governance_policy_and_generation_policy_are_independent_bindings(inputs):
    snapshot_data = inputs["snapshot"].model_dump()
    snapshot_data["policy_fingerprint"] = "sha256:" + "c" * 64
    snapshot_data["fingerprint"] = "sha256:" + "0" * 64
    inputs["snapshot"] = ModelExecutionSnapshot.model_validate(snapshot_data)
    assert (
        inputs["snapshot"].policy_fingerprint
        != inputs["request"].model_policy.fingerprint
    )
    for evidence_name in ("tariff", "input_count", "output_cap"):
        original = inputs[evidence_name]
        inputs[evidence_name] = replace(
            original,
            binding=replace(original.binding, snapshot=inputs["snapshot"]),
        )
    assert _prove_one_request(**inputs).status == "CONDITIONAL"


@pytest.mark.parametrize("cache_field", ["cache_read_rate", "cache_write_rate"])
@pytest.mark.parametrize("semantics", ["UNKNOWN", "ADDITIVE"])
def test_unknown_or_additive_cache_prices_cannot_use_maximum_base_rate(
    inputs, cache_field, semantics
):
    changes = {cache_field: "0.00005"}
    if semantics == "ADDITIVE":
        changes["cache_rates_replace_input"] = False
    with pytest.raises(ValueError, match="CACHE_TARIFF_COVERAGE_INVALID"):
        replace(inputs["tariff"], **changes)


@pytest.mark.parametrize("cache_field", ["cache_read_rate", "cache_write_rate"])
def test_explicit_replacement_cache_price_is_conditionally_supported(
    inputs, cache_field
):
    inputs["tariff"] = replace(
        inputs["tariff"],
        **{
            cache_field: "0.00005",
            "cache_rates_replace_input": True,
        },
    )
    assert _prove_one_request(**inputs).status == "CONDITIONAL"


@pytest.mark.parametrize("bad", [None, 1, "REPLACES"])
def test_cache_replacement_premise_requires_boolean(inputs, bad):
    with pytest.raises(ValueError):
        replace(
            inputs["tariff"],
            cache_read_rate="0.00005",
            cache_rates_replace_input=bad,
        )


def test_cache_replacement_object_mutation_is_rechecked_at_proof_boundary(inputs):
    tariff = replace(
        inputs["tariff"],
        cache_write_rate="0.00005",
        cache_rates_replace_input=True,
    )
    object.__setattr__(tariff, "cache_rates_replace_input", False)
    inputs["tariff"] = tariff
    assert _prove_one_request(**inputs).reason == "PROOF_INPUT_INVALID"
