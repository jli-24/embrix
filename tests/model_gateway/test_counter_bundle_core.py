"""Offline synthetic evidence for the pure counter bundle verifier."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    EngineeringStage,
    ModelGatewayRejected,
    ModelInvocationRequest,
    ModelPolicy,
    ModelRequest,
    ProtocolFamily,
)
from embedded_copilot.model_gateway.contracts import canonical_fingerprint
from embedded_copilot.model_gateway.invocation.budget import (
    cost_components,
    digest,
    wire_target_digest,
)
from embedded_copilot.model_gateway.invocation.budget_bundle import (
    TrustedCounterQualification,
    observe_counter,
    verify_counter_admission,
    verify_generation_bundle,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    ConditionalGenerationCeilingV1,
    CounterAdmissionV1,
    InvocationBudgetEvidenceV1,
    InvocationMode,
    ModelExecutionSnapshot,
    SingleInvocationBudgetEnvelopeV2,
)
from embedded_copilot.model_gateway.invocation.openai_count_collector import (
    CounterHTTPResponse,
)
from tests.model_gateway.test_invocation_service import _request, _task_authority

FP = "sha256:" + "3" * 64
QUALIFIED = TrustedCounterQualification(True, True, True, True, True)
CONDITIONS = (
    ("provider", "OPENAI"),
    ("model", "gpt-5.6-luna"),
    ("region", "STANDARD"),
    ("service_tier", "DEFAULT"),
    ("input_token_band", "LE_8192"),
    ("normalized_usage_scope", "ALL_BILLABLE"),
    ("effective_conditions_authority_id", "offline-settings"),
    ("effective_conditions_identity", "offline-region-mode"),
    ("effective_conditions_revision", "revision-1"),
    ("effective_conditions_witness_fingerprint", FP),
    ("effective_conditions_source_sha256", FP),
    ("effective_conditions_not_after", "2026-10-15T01:00:00+00:00"),
    ("actual_processing_region", "offline-region"),
    ("processing_mode", "STANDARD"),
    ("tariff_witness_fingerprint", FP),
    ("transport_witness_fingerprint", FP),
    ("grant_witness_fingerprint", FP),
)


def _target(identity, path):
    return (
        ("provider_id", "openai"),
        ("endpoint_id", identity.endpoint_id),
        ("base_url", "https://api.openai.com/v1/"),
        ("path", path),
        ("protocol", ProtocolFamily.OPENAI_RESPONSES.value),
        ("noncredential_headers", (("content-type", "application/json"),)),
        ("credential_header_names", ("authorization",)),
    )


def _case(*, fee="0.01", price="0.000001"):
    now = datetime(2026, 10, 15, tzinfo=UTC)
    expiry = now + timedelta(hours=1)
    original = _request()
    bound = ModelRequest.model_validate(
        {
            **original.model_dump(mode="python", exclude={"fingerprint"}),
            "model_policy": ModelPolicy(
                primary_provider="openai",
                model_name="gpt-5.6-luna",
                max_output_tokens=2048,
                service_tier="default",
            ),
            "max_tokens": 2048,
        }
    )
    request = ModelInvocationRequest.from_model_request(
        bound, stage_id=EngineeringStage.HARDWARE_SELECTION
    ).model_copy(
        update={
            "invocation_mode": InvocationMode.TASK,
            "authority_reference": _task_authority(),
            "payload": ModelInvocationRequest.from_model_request(
                bound, stage_id=EngineeringStage.HARDWARE_SELECTION
            ).payload.model_copy(update={"conversation_id": "conversation-bundle"}),
        }
    )
    identity = BoundModelIdentity(
        provider_id="openai",
        endpoint_id="endpoint-openai",
        credential_ref="credential:openai",
        model_profile_id="model-luna",
        model_id="gpt-5.6-luna",
    )
    snapshot = ModelExecutionSnapshot(
        schema_version=2,
        project_id=request.project_id,
        stage_id=request.stage_id,
        selected_model=identity,
        connection_id="connection-openai",
        connection_revision=FP,
        credential_revision="credential-r1",
        endpoint_revision="endpoint-r1",
        policy_source="offline-policy",
        policy_fingerprint=FP,
        selection_fingerprint=FP,
    )
    envelope = SingleInvocationBudgetEnvelopeV2(
        envelope_id="bundle-1",
        tenant_id=request.authority_reference.tenant_id,
        project_id=request.project_id,
        conversation_id=request.payload.conversation_id,
        task_id=request.authority_reference.engineering_task_id,
        task_authority_fingerprint=canonical_fingerprint(
            request.authority_reference, exclude=set()
        ),
        engineering_stage=request.stage_id,
        not_after=expiry,
    )
    payload = json.dumps(
        {
            "model": "gpt-5.6-luna",
            "instructions": bound.system_instruction,
            "input": bound.task,
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": 2048,
            "service_tier": "default",
        },
        separators=(",", ":"),
    ).encode()
    count_bytes = json.dumps(
        {
            "model": "gpt-5.6-luna",
            "instructions": bound.system_instruction,
            "input": bound.task,
            "text": {"format": {"type": "json_object"}},
        },
        separators=(",", ":"),
    ).encode()
    generation_target = _target(identity, "responses")
    counter_target = _target(identity, "responses/input_tokens")
    ceiling = ConditionalGenerationCeilingV1(
        unit="PER_TOKEN",
        input_price_decimal=price,
        output_price_decimal=price,
        cache_semantics="NONE",
        additional_fees_state="KNOWN_NONE",
        additional_cost_bound_decimal="0",
        pricing_identity="price-identity",
        pricing_revision="price-r1",
        pricing_source_sha256=FP,
        pricing_conditions=CONDITIONS,
        pricing_not_after=expiry,
        output_not_after=expiry,
        transport_not_after=expiry,
        cap_evidence_fingerprint=FP,
        transport_evidence_fingerprint=wire_target_digest(generation_target),
        grant_evidence_fingerprint=FP,
        covers_all_billable_input=True,
        covers_all_billable_output=True,
        includes_reasoning=True,
    )
    from fractions import Fraction

    upper = cost_components(
        type(
            "Cost",
            (),
            {
                "currency": "USD",
                "unit": "PER_TOKEN",
                "input_token_bound": 8192,
                "output_token_limit": 2048,
                "input_price_decimal": price,
                "output_price_decimal": price,
                "cache_read_price_decimal": None,
                "cache_write_price_decimal": None,
                "cache_semantics": "NONE",
                "additional_fees_state": "KNOWN_NONE",
                "additional_cost_bound_decimal": "0",
            },
        )()
    )[-1]
    admission = CounterAdmissionV1(
        envelope_fingerprint=envelope.fingerprint,
        request_fingerprint=bound.fingerprint,
        execution_snapshot_fingerprint=snapshot.fingerprint,
        task_authority_fingerprint=envelope.task_authority_fingerprint,
        policy_fingerprint=snapshot.policy_fingerprint,
        selected_model=identity,
        connection_id=snapshot.connection_id,
        connection_revision=snapshot.connection_revision,
        credential_revision=snapshot.credential_revision,
        endpoint_revision=snapshot.endpoint_revision,
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        final_generation_bytes_sha256=digest(payload),
        count_request_base64=base64.b64encode(count_bytes).decode(),
        count_request_sha256=digest(count_bytes),
        counter_wire_target_sha256=wire_target_digest(counter_target),
        generation_wire_target_sha256=wire_target_digest(generation_target),
        counter_fee_identity="counter-fee",
        counter_fee_revision="fee-r1",
        counter_fee_source_sha256=FP,
        counter_fee_upper_decimal=fee,
        counter_fee_evidence_fingerprint=FP,
        counter_fee_not_after=expiry,
        generation_ceiling=ceiling,
        generation_cost_upper_fraction=str(upper),
        combined_cost_upper_fraction=str(Fraction(fee) + upper),
        not_after=expiry,
    )
    inputs = {
        "request": request,
        "snapshot": snapshot,
        "bound": bound,
        "envelope": envelope,
        "admission": admission,
        "protocol": ProtocolFamily.OPENAI_RESPONSES,
        "payload": payload,
        "parameter": "max_output_tokens",
        "wire_target": generation_target,
        "now": now,
        "qualification": QUALIFIED,
    }
    return inputs, counter_target


def _change(model, **values):
    return type(model).model_validate(
        {**model.model_dump(mode="python", exclude={"fingerprint"}), **values}
    )


def _observed(
    inputs,
    target,
    raw=b'{"object":"response.input_tokens","input_tokens":8192}',
    status=200,
):
    response = CounterHTTPResponse(
        status_code=status,
        response_bytes=raw,
        request_sha256=inputs["admission"].count_request_sha256,
        wire_target=target,
    )
    return observe_counter(
        request=inputs["request"],
        snapshot=inputs["snapshot"],
        envelope=inputs["envelope"],
        admission=inputs["admission"],
        response=response,
        now=inputs["now"] + timedelta(seconds=1),
    )


def test_admission_exact_total_and_projection():
    inputs, _ = _case()
    assert verify_counter_admission(**inputs) == (
        inputs["envelope"],
        inputs["admission"],
    )
    projected = json.loads(base64.b64decode(inputs["admission"].count_request_base64))
    assert set(projected) == {"model", "instructions", "input", "text"}


@pytest.mark.parametrize(
    "change",
    [
        {"counter_fee_upper_decimal": "0"},
        {"combined_cost_upper_fraction": "0"},
        {"policy_fingerprint": FP.replace("3", "4")},
        {"generation_wire_target_sha256": FP},
    ],
)
def test_admission_rejects_stale_or_unproven_claim(change):
    inputs, _ = _case()
    inputs["admission"] = _change(inputs["admission"], **change)
    with pytest.raises(ModelGatewayRejected):
        verify_counter_admission(**inputs)


def test_admission_rejects_tiny_budget_excess_and_missing_qualification():
    inputs, _ = _case(fee="0.089760001")  # 8192+2048 at 0.000001 = 0.01024.
    with pytest.raises(ModelGatewayRejected) as failure:
        verify_counter_admission(**inputs)
    assert failure.value.reason_code == "BUDGET_LIMIT_EXCEEDED"
    inputs, _ = _case()
    inputs["qualification"] = TrustedCounterQualification(True, True, True, True, False)
    with pytest.raises(ModelGatewayRejected):
        verify_counter_admission(**inputs)


def test_exact_tenth_usd_boundary_is_admitted():
    inputs, _ = _case(fee="0.08976")
    assert inputs["admission"].combined_cost_upper_fraction == "1/10"
    verify_counter_admission(**inputs)


@pytest.mark.parametrize(
    "raw,status,expected,count",
    [
        (
            b'{"object":"response.input_tokens","input_tokens":8192}',
            200,
            "COUNTED",
            8192,
        ),
        (
            b'{"object":"response.input_tokens","input_tokens":8193}',
            200,
            "COUNTED",
            8193,
        ),
        (
            b'{"object":"response.input_tokens","input_tokens":true}',
            200,
            "INVALID_RESPONSE",
            None,
        ),
        (
            b'{"object":"response.input_tokens","input_tokens":1,"input_tokens":2}',
            200,
            "INVALID_RESPONSE",
            None,
        ),
        (
            b'{"object":"response.input_tokens","input_tokens":NaN}',
            200,
            "INVALID_RESPONSE",
            None,
        ),
        (b"", 200, "INVALID_RESPONSE", None),
        (b"backend failure", 503, "HTTP_ERROR", None),
    ],
)
def test_observation_preserves_every_raw_response(raw, status, expected, count):
    inputs, target = _case()
    observed = _observed(inputs, target, raw, status)
    assert observed.observation_status == expected
    assert observed.input_tokens == count
    assert base64.b64decode(observed.raw_response_base64) == raw


def test_observation_requires_actual_request_and_target():
    inputs, target = _case()
    response = CounterHTTPResponse(200, b"{}", FP, target)
    with pytest.raises(ModelGatewayRejected):
        observe_counter(
            request=inputs["request"],
            snapshot=inputs["snapshot"],
            envelope=inputs["envelope"],
            admission=inputs["admission"],
            response=response,
            now=inputs["now"],
        )


def test_late_http_response_keeps_raw_without_extending_evidence_lifetime():
    inputs, target = _case()
    raw = b'{"object":"response.input_tokens","input_tokens":7}'
    response = CounterHTTPResponse(
        200, raw, inputs["admission"].count_request_sha256, target
    )
    observed = observe_counter(
        request=inputs["request"],
        snapshot=inputs["snapshot"],
        envelope=inputs["envelope"],
        admission=inputs["admission"],
        response=response,
        now=inputs["envelope"].not_after + timedelta(seconds=1),
    )
    assert observed.observation_status == "COUNTED"
    assert base64.b64decode(observed.raw_response_base64) == raw
    assert observed.not_after == inputs["envelope"].not_after < observed.observed_at


def test_generation_rejects_over_limit_count_before_budget_verifier():
    inputs, target = _case()
    observation = _observed(
        inputs, target, b'{"object":"response.input_tokens","input_tokens":8193}'
    )
    with pytest.raises(ModelGatewayRejected):
        verify_generation_bundle(**inputs, observation=observation, evidence=None)


def test_generation_reparses_raw_count_with_strict_integer_type():
    inputs, target = _case()
    observation = _observed(
        inputs, target, b'{"object":"response.input_tokens","input_tokens":1}'
    )
    fake_raw = b'{"object":"response.input_tokens","input_tokens":true}'
    observation = _change(
        observation,
        raw_response_base64=base64.b64encode(fake_raw).decode("ascii"),
        response_sha256=digest(fake_raw),
    )
    inputs["now"] += timedelta(seconds=2)
    with pytest.raises(ModelGatewayRejected):
        verify_generation_bundle(
            **inputs, observation=observation, evidence=_evidence(inputs, observation)
        )


def _evidence(inputs, observation):
    admission = inputs["admission"]
    ceiling = admission.generation_ceiling
    values = dict(  # noqa: C408
        request_fingerprint=admission.request_fingerprint,
        execution_snapshot_fingerprint=admission.execution_snapshot_fingerprint,
        final_transport_sha256=admission.final_generation_bytes_sha256,
        selected_model=admission.selected_model,
        connection_id=admission.connection_id,
        connection_revision=admission.connection_revision,
        credential_revision=admission.credential_revision,
        endpoint_revision=admission.endpoint_revision,
        protocol_family=admission.protocol_family,
        pricing_conditions=ceiling.pricing_conditions,
        count_kind="EXACT",
        input_token_bound=observation.input_tokens,
        counter_identity="native-input-token-counter",
        counter_revision="counter-r1",
        qualification_evidence_fingerprint=observation.fingerprint,
        count_not_after=observation.not_after,
        output_token_limit=ceiling.output_token_limit,
        provider_parameter="max_output_tokens",
        enforcement_evidence_fingerprint=ceiling.cap_evidence_fingerprint,
        covers_all_billable_input=True,
        covers_all_billable_output=True,
        includes_reasoning=True,
        output_not_after=ceiling.output_not_after,
        pricing_identity=ceiling.pricing_identity,
        pricing_revision=ceiling.pricing_revision,
        source_sha256=ceiling.pricing_source_sha256,
        currency=ceiling.currency,
        unit=ceiling.unit,
        input_price_decimal=ceiling.input_price_decimal,
        output_price_decimal=ceiling.output_price_decimal,
        cache_read_price_decimal=ceiling.cache_read_price_decimal,
        cache_write_price_decimal=ceiling.cache_write_price_decimal,
        cache_semantics=ceiling.cache_semantics,
        additional_fees_state=ceiling.additional_fees_state,
        additional_cost_bound_decimal=ceiling.additional_cost_bound_decimal,
        pricing_not_after=ceiling.pricing_not_after,
        transport_not_after=ceiling.transport_not_after,
        transport_evidence_fingerprint=ceiling.transport_evidence_fingerprint,
        envelope_fingerprint=inputs["envelope"].fingerprint,
        input_cost_upper_fraction="0",
        output_cost_upper_fraction="0",
        additional_cost_upper_fraction="0",
        total_cost_upper_fraction="0",
    )
    costs = cost_components(InvocationBudgetEvidenceV1(**values))
    values.update(
        zip(
            (
                "input_cost_upper_fraction",
                "output_cost_upper_fraction",
                "additional_cost_upper_fraction",
                "total_cost_upper_fraction",
            ),
            map(str, costs),
        )
    )
    return InvocationBudgetEvidenceV1(**values)


def test_generation_accepts_exact_observation_and_bound_evidence():
    inputs, target = _case()
    observation = _observed(inputs, target)
    evidence = _evidence(inputs, observation)
    inputs["now"] += timedelta(seconds=2)
    assert verify_generation_bundle(
        **inputs, observation=observation, evidence=evidence
    ) == (inputs["envelope"], evidence)


@pytest.mark.parametrize(
    "change",
    [
        {"input_token_bound": 8191},
        {"count_kind": "PROVEN_UPPER_BOUND"},
        {"qualification_evidence_fingerprint": FP},
        {"input_price_decimal": "0.000002"},
    ],
)
def test_generation_rejects_evidence_drift(change):
    inputs, target = _case()
    observation = _observed(inputs, target)
    evidence = _change(_evidence(inputs, observation), **change)
    inputs["now"] += timedelta(seconds=2)
    with pytest.raises(ModelGatewayRejected):
        verify_generation_bundle(**inputs, observation=observation, evidence=evidence)


def test_region_original_sha_and_unambiguous_conditions_required():
    inputs, _ = _case()
    ceiling = inputs["admission"].generation_ceiling
    for conditions in (
        tuple(
            pair
            for pair in ceiling.pricing_conditions
            if pair[0] != "effective_conditions_source_sha256"
        ),
        ceiling.pricing_conditions + (("processing_mode", "STANDARD"),),
    ):
        changed_ceiling = _change(ceiling, pricing_conditions=conditions)
        changed_admission = _change(
            inputs["admission"], generation_ceiling=changed_ceiling
        )
        with pytest.raises(ModelGatewayRejected):
            verify_counter_admission(**{**inputs, "admission": changed_admission})
