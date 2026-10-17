"""Pure, fail-closed verification for the approved two-role budget bundle.

Qualification flags must come from a current trusted host port. These checks
never establish a provider tariff, grant, transport property, or count source.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

from ..contracts import canonical_fingerprint, fingerprint, identifier
from ..exceptions import ModelGatewayRejected
from ..profiles import ProtocolFamily
from ._budget_preflight import _exact_rate
from .budget import (
    TrustedBudgetQualification,
    cost_components,
    digest,
    reject,
    verify_budget,
    wire_target_digest,
)
from .contracts import (
    CounterAdmissionV1,
    CounterObservationV1,
    InvocationBudgetEvidenceV1,
    SingleInvocationBudgetEnvelopeV2,
)
from .openai_count_collector import CounterHTTPResponse

_MODEL = "gpt-5.6-luna"
_CAP = 2048
_LIMIT = 8192
_COUNT_FIELDS = {"model", "instructions", "input", "text"}
_GENERATION_FIELDS = _COUNT_FIELDS | {"max_output_tokens", "service_tier"}
_TEXT = {"format": {"type": "json_object"}}
_REQUIRED_CONDITIONS = {
    ("provider", "OPENAI"),
    ("model", _MODEL),
    ("region", "STANDARD"),
    ("service_tier", "DEFAULT"),
    ("input_token_band", "LE_8192"),
    ("normalized_usage_scope", "ALL_BILLABLE"),
}


@dataclass(frozen=True)
class TrustedCounterQualification:
    counter_fee_qualified: bool = False
    generation_tariff_qualified: bool = False
    cap_qualified: bool = False
    transport_semantics_qualified: bool = False
    grant_qualified: bool = False


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            reject()
        value[key] = item
    return value


def _nonfinite(_value):
    reject()


def _strict_object(raw):
    if type(raw) is not bytes or not raw:
        reject()
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeError, ValueError, TypeError):
        reject()
    if type(value) is not dict:
        reject()
    return value


def _native_target(identity, path):
    return (
        ("provider_id", "openai"),
        ("endpoint_id", identity.endpoint_id),
        ("base_url", "https://api.openai.com/v1/"),
        ("path", path),
        ("protocol", ProtocolFamily.OPENAI_RESPONSES.value),
        ("noncredential_headers", (("content-type", "application/json"),)),
        ("credential_header_names", ("authorization",)),
    )


def _scope(
    *,
    request,
    snapshot,
    bound,
    envelope,
    protocol,
    payload,
    parameter,
    wire_target,
    now,
):
    if (
        type(envelope) is not SingleInvocationBudgetEnvelopeV2
        or type(now) is not datetime
        or now.utcoffset() is None
        or type(payload) is not bytes
        or type(wire_target) is not tuple
        or protocol is not ProtocolFamily.OPENAI_RESPONSES
        or parameter != "max_output_tokens"
        or snapshot.schema_version != 2
        or snapshot.selected_model.provider_id != "openai"
        or snapshot.selected_model.model_id != _MODEL
        or bound.model_policy.primary_provider != "openai"
        or bound.model_policy.model_name != _MODEL
        or bound.model_policy.service_tier != "default"
        or bound.model_policy.max_output_tokens != _CAP
        or bound.model_policy.fallback_providers
        or (
            bound.endpoint_id is not None
            and bound.endpoint_id != snapshot.selected_model.endpoint_id
        )
        or (
            bound.credential_ref is not None
            and bound.credential_ref != snapshot.selected_model.credential_ref
        )
        or request.payload.task != bound.task
        or request.payload.system_instruction != bound.system_instruction
        or wire_target != _native_target(snapshot.selected_model, "responses")
    ):
        reject()
    authority = request.authority_reference
    if authority is None or (
        envelope.tenant_id != authority.tenant_id
        or envelope.project_id != request.project_id
        or envelope.conversation_id != request.payload.conversation_id
        or envelope.task_id != authority.engineering_task_id
        or envelope.task_authority_fingerprint
        != canonical_fingerprint(authority, exclude=set())
        or envelope.engineering_stage != request.stage_id
        or snapshot.project_id != request.project_id
        or snapshot.stage_id != request.stage_id
        or now >= envelope.not_after
    ):
        reject("BUDGET_BINDING_STALE")
    final = _strict_object(payload)
    if (
        set(final) != _GENERATION_FIELDS
        or final["model"] != _MODEL
        or type(final["instructions"]) is not str
        or type(final["input"]) is not str
        or final["instructions"] != bound.system_instruction
        or final["input"] != bound.task
        or final["text"] != _TEXT
        or type(final["max_output_tokens"]) is not int
        or final["max_output_tokens"] != _CAP
        or final["service_tier"] != "default"
    ):
        reject("BUDGET_BINDING_STALE")
    projected = {key: final[key] for key in final if key in _COUNT_FIELDS}
    counter_bytes = json.dumps(
        projected, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return counter_bytes, _native_target(
        snapshot.selected_model, "responses/input_tokens"
    )


def _qualified(value):
    if type(value) is not TrustedCounterQualification or any(
        item is not True
        for item in (
            value.counter_fee_qualified,
            value.generation_tariff_qualified,
            value.cap_qualified,
            value.transport_semantics_qualified,
            value.grant_qualified,
        )
    ):
        reject()


def _ceiling_cost(ceiling, now):
    conditions = dict(ceiling.pricing_conditions)
    if len(conditions) != len(ceiling.pricing_conditions):
        reject()
    if len(conditions) != len(ceiling.pricing_conditions):
        reject()
    try:
        effective_expiry = datetime.fromisoformat(
            conditions["effective_conditions_not_after"]
        )
        for key in (
            "effective_conditions_authority_id",
            "effective_conditions_identity",
            "effective_conditions_revision",
            "actual_processing_region",
        ):
            identifier(conditions[key], field=key)
        fingerprint(conditions["effective_conditions_witness_fingerprint"])
        fingerprint(conditions["effective_conditions_source_sha256"])
        for key in (
            "tariff_witness_fingerprint",
            "transport_witness_fingerprint",
            "grant_witness_fingerprint",
        ):
            fingerprint(conditions[key])
    except (KeyError, TypeError, ValueError):
        reject()
    if (
        ceiling.input_admission_limit != _LIMIT
        or ceiling.output_token_limit != _CAP
        or ceiling.covers_all_billable_input is not True
        or ceiling.covers_all_billable_output is not True
        or ceiling.includes_reasoning is not True
        or not _REQUIRED_CONDITIONS.issubset(set(ceiling.pricing_conditions))
        or conditions.get("processing_mode") != "STANDARD"
        or conditions["grant_witness_fingerprint"] != ceiling.grant_evidence_fingerprint
        or effective_expiry.utcoffset() is None
        or now >= effective_expiry
    ):
        reject()
    return cost_components(
        SimpleNamespace(
            currency=ceiling.currency,
            unit=ceiling.unit,
            input_token_bound=_LIMIT,
            output_token_limit=_CAP,
            input_price_decimal=ceiling.input_price_decimal,
            output_price_decimal=ceiling.output_price_decimal,
            cache_read_price_decimal=ceiling.cache_read_price_decimal,
            cache_write_price_decimal=ceiling.cache_write_price_decimal,
            cache_semantics=ceiling.cache_semantics,
            additional_fees_state=ceiling.additional_fees_state,
            additional_cost_bound_decimal=ceiling.additional_cost_bound_decimal,
        )
    )[-1]


def _current_witnesses(admission):
    ceiling = admission.generation_ceiling
    conditions = dict(ceiling.pricing_conditions)
    return (
        admission.counter_fee_evidence_fingerprint,
        conditions["tariff_witness_fingerprint"],
        ceiling.cap_evidence_fingerprint,
        conditions["transport_witness_fingerprint"],
        ceiling.grant_evidence_fingerprint,
        conditions["effective_conditions_witness_fingerprint"],
    )


def _admission_checks(
    *,
    request,
    snapshot,
    bound,
    envelope,
    admission,
    protocol,
    payload,
    parameter,
    wire_target,
    now,
    qualification,
):
    counter_bytes, counter_target = _scope(
        request=request,
        snapshot=snapshot,
        bound=bound,
        envelope=envelope,
        protocol=protocol,
        payload=payload,
        parameter=parameter,
        wire_target=wire_target,
        now=now,
    )
    _qualified(qualification)
    if type(admission) is not CounterAdmissionV1:
        reject()
    admission = CounterAdmissionV1.model_validate(admission.model_dump())
    ceiling = admission.generation_ceiling
    if (
        admission.envelope_fingerprint != envelope.fingerprint
        or admission.request_fingerprint != bound.fingerprint
        or admission.execution_snapshot_fingerprint != snapshot.fingerprint
        or admission.task_authority_fingerprint != envelope.task_authority_fingerprint
        or admission.policy_fingerprint != snapshot.policy_fingerprint
        or admission.selected_model != snapshot.selected_model
        or admission.connection_id != snapshot.connection_id
        or admission.connection_revision != snapshot.connection_revision
        or admission.credential_revision != snapshot.credential_revision
        or admission.endpoint_revision != snapshot.endpoint_revision
        or admission.protocol_family is not protocol
        or admission.final_generation_bytes_sha256 != digest(payload)
        or admission.count_request_base64
        != base64.b64encode(counter_bytes).decode("ascii")
        or admission.count_request_sha256 != digest(counter_bytes)
        or admission.counter_wire_target_sha256 != wire_target_digest(counter_target)
        or admission.generation_wire_target_sha256 != wire_target_digest(wire_target)
        or admission.input_admission_limit != envelope.input_admission_limit
        or admission.total_output_limit != envelope.total_output_limit
        or now
        >= min(
            admission.not_after,
            admission.counter_fee_not_after,
            ceiling.pricing_not_after,
            ceiling.output_not_after,
            ceiling.transport_not_after,
        )
    ):
        reject("BUDGET_BINDING_STALE")
    if (
        admission.counter_fee_currency != "USD"
        or admission.counter_fee_unit != "ENTIRE_COUNT_REQUEST_BOUND"
        or ceiling.currency != "USD"
        or not ceiling.pricing_identity
        or not ceiling.pricing_revision
    ):
        reject()
    fee = _exact_rate(admission.counter_fee_upper_decimal)
    generation = _ceiling_cost(ceiling, now)
    if admission.generation_cost_upper_fraction != str(
        generation
    ) or admission.combined_cost_upper_fraction != str(fee + generation):
        reject("BUDGET_BINDING_STALE")
    if fee + generation > _exact_rate(envelope.max_cost_decimal):
        reject("BUDGET_LIMIT_EXCEEDED")
    return admission, counter_bytes, counter_target, fee


def verify_counter_admission(
    *,
    request,
    snapshot,
    bound,
    envelope,
    admission,
    protocol,
    payload,
    parameter,
    wire_target,
    now,
    qualification,
):
    try:
        envelope = SingleInvocationBudgetEnvelopeV2.model_validate(
            envelope.model_dump()
        )
        admission, _, _, _ = _admission_checks(
            request=request,
            snapshot=snapshot,
            bound=bound,
            envelope=envelope,
            admission=admission,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
            qualification=qualification,
        )
        return envelope, admission
    except Exception as error:
        if isinstance(error, ModelGatewayRejected):
            raise
        reject()


def observe_counter(*, request, snapshot, envelope, admission, response, now):
    try:
        if (
            type(envelope) is not SingleInvocationBudgetEnvelopeV2
            or type(admission) is not CounterAdmissionV1
            or type(response) is not CounterHTTPResponse
            or type(now) is not datetime
            or now.utcoffset() is None
        ):
            reject()
        envelope = SingleInvocationBudgetEnvelopeV2.model_validate(
            envelope.model_dump()
        )
        admission = CounterAdmissionV1.model_validate(admission.model_dump())
        if (
            response.request_sha256 != admission.count_request_sha256
            or wire_target_digest(response.wire_target)
            != admission.counter_wire_target_sha256
            or response.wire_target
            != _native_target(admission.selected_model, "responses/input_tokens")
            or admission.envelope_fingerprint != envelope.fingerprint
            or admission.execution_snapshot_fingerprint != snapshot.fingerprint
            or admission.selected_model != snapshot.selected_model
            or admission.connection_id != snapshot.connection_id
            or admission.connection_revision != snapshot.connection_revision
            or admission.credential_revision != snapshot.credential_revision
            or admission.endpoint_revision != snapshot.endpoint_revision
            or admission.task_authority_fingerprint
            != envelope.task_authority_fingerprint
            or request.project_id != envelope.project_id
            or request.stage_id != envelope.engineering_stage
            or request.payload.conversation_id != envelope.conversation_id
            or request.authority_reference is None
            or request.authority_reference.engineering_task_id != envelope.task_id
            or canonical_fingerprint(request.authority_reference, exclude=set())
            != admission.task_authority_fingerprint
        ):
            reject("BUDGET_BINDING_STALE")
        count = None
        status = "HTTP_ERROR" if response.status_code != 200 else "INVALID_RESPONSE"
        if response.status_code == 200:
            try:
                parsed = _strict_object(response.response_bytes)
                if (
                    set(parsed) == {"object", "input_tokens"}
                    and parsed["object"] == "response.input_tokens"
                    and type(parsed["input_tokens"]) is int
                    and parsed["input_tokens"] >= 0
                ):
                    count = parsed["input_tokens"]
                    status = "COUNTED"
            except ModelGatewayRejected:
                pass  # Invalid provider data is preserved below, never converted to a count.
        return CounterObservationV1(
            envelope_id=envelope.envelope_id,
            project_id=request.project_id,
            request_id=request.request_id,
            counter_admission_fingerprint=admission.fingerprint,
            execution_snapshot_fingerprint=snapshot.fingerprint,
            task_authority_fingerprint=admission.task_authority_fingerprint,
            selected_model=admission.selected_model,
            connection_id=admission.connection_id,
            connection_revision=admission.connection_revision,
            credential_revision=admission.credential_revision,
            endpoint_revision=admission.endpoint_revision,
            final_generation_bytes_sha256=admission.final_generation_bytes_sha256,
            count_request_sha256=admission.count_request_sha256,
            counter_wire_target_sha256=admission.counter_wire_target_sha256,
            count_request_base64=admission.count_request_base64,
            raw_response_base64=base64.b64encode(response.response_bytes).decode(
                "ascii"
            ),
            response_sha256="sha256:"
            + hashlib.sha256(response.response_bytes).hexdigest(),
            http_status=response.status_code,
            observation_status=status,
            input_tokens=count,
            provider_request_id=response.response_request_id,
            current_witness_fingerprints=_current_witnesses(admission),
            observed_at=now,
            not_after=min(
                envelope.not_after,
                admission.not_after,
                admission.counter_fee_not_after,
                admission.generation_ceiling.pricing_not_after,
                admission.generation_ceiling.output_not_after,
                admission.generation_ceiling.transport_not_after,
            ),
        )
    except Exception as error:
        if isinstance(error, ModelGatewayRejected):
            raise
        reject()


def verify_generation_bundle(
    *,
    request,
    snapshot,
    bound,
    envelope,
    admission,
    observation,
    evidence,
    protocol,
    payload,
    parameter,
    wire_target,
    now,
    qualification,
):
    try:
        envelope = SingleInvocationBudgetEnvelopeV2.model_validate(
            envelope.model_dump()
        )
        admission, _, _, fee = _admission_checks(
            request=request,
            snapshot=snapshot,
            bound=bound,
            envelope=envelope,
            admission=admission,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
            qualification=qualification,
        )
        if (
            type(observation) is not CounterObservationV1
            or type(evidence) is not InvocationBudgetEvidenceV1
        ):
            reject()
        observation = CounterObservationV1.model_validate(observation.model_dump())
        evidence = InvocationBudgetEvidenceV1.model_validate(evidence.model_dump())
        ceiling = admission.generation_ceiling
        parsed_response = _strict_object(
            base64.b64decode(observation.raw_response_base64, validate=True)
        )
        expected_witnesses = _current_witnesses(admission)
        if (
            observation.counter_admission_fingerprint != admission.fingerprint
            or observation.envelope_id != envelope.envelope_id
            or observation.project_id != request.project_id
            or observation.request_id != request.request_id
            or observation.execution_snapshot_fingerprint != snapshot.fingerprint
            or observation.task_authority_fingerprint
            != admission.task_authority_fingerprint
            or observation.selected_model != snapshot.selected_model
            or observation.connection_id != snapshot.connection_id
            or observation.connection_revision != snapshot.connection_revision
            or observation.credential_revision != snapshot.credential_revision
            or observation.endpoint_revision != snapshot.endpoint_revision
            or observation.final_generation_bytes_sha256
            != admission.final_generation_bytes_sha256
            or observation.count_request_sha256 != admission.count_request_sha256
            or observation.count_request_base64 != admission.count_request_base64
            or observation.counter_wire_target_sha256
            != admission.counter_wire_target_sha256
            or observation.current_witness_fingerprints != expected_witnesses
            or observation.observation_status != "COUNTED"
            or observation.http_status != 200
            or type(observation.input_tokens) is not int
            or observation.input_tokens > _LIMIT
            or set(parsed_response) != {"object", "input_tokens"}
            or parsed_response["object"] != "response.input_tokens"
            or type(parsed_response["input_tokens"]) is not int
            or parsed_response["input_tokens"] != observation.input_tokens
            or now < observation.observed_at
            or now >= observation.not_after
            or evidence.count_kind != "EXACT"
            or evidence.input_token_bound != observation.input_tokens
            or evidence.qualification_evidence_fingerprint != observation.fingerprint
            or evidence.count_not_after != observation.not_after
            or evidence.currency != ceiling.currency
            or evidence.unit != ceiling.unit
            or evidence.input_price_decimal != ceiling.input_price_decimal
            or evidence.output_price_decimal != ceiling.output_price_decimal
            or evidence.cache_read_price_decimal != ceiling.cache_read_price_decimal
            or evidence.cache_write_price_decimal != ceiling.cache_write_price_decimal
            or evidence.cache_semantics != ceiling.cache_semantics
            or evidence.additional_fees_state != ceiling.additional_fees_state
            or evidence.additional_cost_bound_decimal
            != ceiling.additional_cost_bound_decimal
            or evidence.pricing_identity != ceiling.pricing_identity
            or evidence.pricing_revision != ceiling.pricing_revision
            or evidence.source_sha256 != ceiling.pricing_source_sha256
            or evidence.pricing_conditions != ceiling.pricing_conditions
            or evidence.pricing_not_after != ceiling.pricing_not_after
            or evidence.output_not_after != ceiling.output_not_after
            or evidence.transport_not_after != ceiling.transport_not_after
            or evidence.transport_evidence_fingerprint
            != ceiling.transport_evidence_fingerprint
            or evidence.enforcement_evidence_fingerprint
            != ceiling.cap_evidence_fingerprint
            or evidence.covers_all_billable_input
            is not ceiling.covers_all_billable_input
            or evidence.covers_all_billable_output
            is not ceiling.covers_all_billable_output
            or evidence.includes_reasoning is not ceiling.includes_reasoning
        ):
            reject("BUDGET_BINDING_STALE")
        actual_cost = cost_components(evidence)[-1]
        if fee + actual_cost > _exact_rate(envelope.max_cost_decimal):
            reject("BUDGET_LIMIT_EXCEEDED")
        verify_budget(
            request=request,
            snapshot=snapshot,
            bound=bound,
            envelope=envelope,
            evidence=evidence,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
            qualification=TrustedBudgetQualification(True, True, True, True),
        )
        return envelope, evidence
    except Exception as error:
        if isinstance(error, ModelGatewayRejected):
            raise
        reject()
