"""Single-envelope TASK budget verification. Qualification comes only from injected host ports."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction

from ..contracts import canonical_fingerprint
from ..exceptions import ModelGatewayRejected
from ..profiles import ProtocolFamily
from ._budget_preflight import _UNIT_TOKENS, _exact_rate
from .contracts import (
    InvocationBudgetEvidenceV1,
    SingleInvocationBudgetEnvelopeV1,
    SingleInvocationBudgetEnvelopeV2,
)


@dataclass(frozen=True)
class TrustedBudgetQualification:
    count_qualified: bool = False
    tariff_qualified: bool = False
    cap_qualified: bool = False
    transport_semantics_qualified: bool = False


def reject(code="BUDGET_EVIDENCE_UNAVAILABLE"):
    raise ModelGatewayRejected(reason_code=code)


def digest(payload: bytes) -> str:
    if type(payload) is not bytes or not payload:
        reject()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def wire_target_digest(target: tuple) -> str:
    return digest(json.dumps(target, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def cost_components(evidence: InvocationBudgetEvidenceV1):
    unit = _UNIT_TOKENS.get(evidence.unit)
    if unit is None or evidence.currency != "USD":
        reject()
    base = _exact_rate(evidence.input_price_decimal)
    output = _exact_rate(evidence.output_price_decimal)
    cache = [v for v in (evidence.cache_read_price_decimal, evidence.cache_write_price_decimal) if v is not None]
    rates = [_exact_rate(v) for v in cache]
    if evidence.cache_semantics == "NONE" and not rates:
        rate = base
    elif evidence.cache_semantics == "EXCLUSIVE_REPLACEMENT" and rates:
        rate = max([base, *rates])
    elif evidence.cache_semantics == "ADDITIVE" and rates:
        rate = base + sum(rates, Fraction(0))
    else:
        reject()
    extra = _exact_rate(evidence.additional_cost_bound_decimal)
    if evidence.additional_fees_state == "KNOWN_NONE":
        if extra != 0:
            reject()
    elif evidence.additional_fees_state != "QUALIFIED_BOUND":
        reject()
    input_cost = evidence.input_token_bound * rate / unit
    output_cost = evidence.output_token_limit * output / unit
    return input_cost, output_cost, extra, input_cost + output_cost + extra


def verify_budget(*, request, bound, snapshot, envelope, evidence, protocol, payload, parameter, wire_target, now, qualification):
    try:
        if type(envelope) not in (SingleInvocationBudgetEnvelopeV1, SingleInvocationBudgetEnvelopeV2) or type(evidence) is not InvocationBudgetEvidenceV1:
            reject()
        envelope = type(envelope).model_validate(envelope.model_dump())
        evidence = InvocationBudgetEvidenceV1.model_validate(evidence.model_dump())
        if type(qualification) is not TrustedBudgetQualification or any(v is not True for v in (qualification.count_qualified, qualification.tariff_qualified, qualification.cap_qualified, qualification.transport_semantics_qualified)):
            reject()
        authority = request.authority_reference
        if authority is None or (
            envelope.tenant_id != authority.tenant_id
            or envelope.project_id != request.project_id
            or envelope.conversation_id != request.payload.conversation_id
            or envelope.task_id != authority.engineering_task_id
            or envelope.task_authority_fingerprint != canonical_fingerprint(authority, exclude=set())
            or envelope.engineering_stage != request.stage_id
            or evidence.request_fingerprint != bound.fingerprint
            or evidence.execution_snapshot_fingerprint != snapshot.fingerprint
            or evidence.final_transport_sha256 != digest(payload)
            or evidence.selected_model != snapshot.selected_model
            or evidence.connection_id != snapshot.connection_id
            or evidence.connection_revision != snapshot.connection_revision
            or evidence.credential_revision != snapshot.credential_revision
            or evidence.endpoint_revision != snapshot.endpoint_revision
            or evidence.protocol_family != protocol
            or evidence.transport_evidence_fingerprint != wire_target_digest(wire_target)
            or evidence.envelope_fingerprint != envelope.fingerprint
        ):
            reject("BUDGET_BINDING_STALE")
        if now.utcoffset() is None or not all(now < expiry for expiry in (envelope.not_after, evidence.count_not_after, evidence.output_not_after, evidence.pricing_not_after, evidence.transport_not_after)):
            reject("BUDGET_BINDING_STALE")
        if evidence.count_kind not in {"EXACT", "PROVEN_UPPER_BOUND"} or evidence.covers_all_billable_input is not True or evidence.covers_all_billable_output is not True or evidence.includes_reasoning is not True:
            reject()
        if evidence.provider_parameter != parameter or evidence.output_token_limit != bound.model_policy.max_output_tokens:
            reject("BUDGET_BINDING_STALE")
        target = dict(wire_target)
        if target.get("provider_id") != snapshot.selected_model.provider_id or target.get("endpoint_id") != snapshot.selected_model.endpoint_id:
            reject("BUDGET_BINDING_STALE")
        actual = json.loads(payload)
        if protocol is ProtocolFamily.GOOGLE_GENAI:
            if target.get("path") != "models/" + snapshot.selected_model.model_id + ":generateContent":
                reject("BUDGET_BINDING_STALE")
            cap_value = actual.get("generationConfig", {}).get("maxOutputTokens")
        else:
            if actual.get("model") != snapshot.selected_model.model_id:
                reject("BUDGET_BINDING_STALE")
            cap_value = actual.get(parameter)
        if type(cap_value) is not int or cap_value != evidence.output_token_limit:
            reject("BUDGET_BINDING_STALE")
        costs = cost_components(evidence)
        claimed = (evidence.input_cost_upper_fraction, evidence.output_cost_upper_fraction, evidence.additional_cost_upper_fraction, evidence.total_cost_upper_fraction)
        if tuple(str(value) for value in costs) != claimed:
            reject("BUDGET_BINDING_STALE")
        if costs[-1] > _exact_rate(envelope.max_cost_decimal):
            reject("BUDGET_LIMIT_EXCEEDED")
        return envelope, evidence
    except ModelGatewayRejected:
        raise
    except (TypeError, ValueError, AttributeError, OverflowError):
        reject()


def reconcile_budget(evidence, usage, envelope):
    if usage is None:
        return "UNKNOWN"
    # Token facts remain meaningful even if billing currency is unavailable.
    if usage.input_tokens > evidence.input_token_bound or usage.output_tokens > evidence.output_token_limit or (usage.reasoning_tokens is not None and usage.reasoning_tokens > usage.output_tokens):
        return "BOUND_VIOLATION"
    if usage.currency not in (None, "USD"):
        return "UNKNOWN"
    upper = cost_components(evidence)[-1]
    threshold = min(upper, _exact_rate(envelope.max_cost_decimal))
    if usage.provider_reported_cost is not None:
        if usage.currency != "USD":
            return "UNKNOWN"
        # The legacy normalized field is binary float: original decimal billing
        # precision cannot be reconstructed. Only a lower rounding bound above
        # the limit proves an overrun; otherwise retain UNKNOWN, never REPORTED.
        import math
        reported = usage.provider_reported_cost
        lower = (Fraction.from_float(reported) + Fraction.from_float(math.nextafter(reported, -math.inf))) / 2
        return "BOUND_VIOLATION" if lower > threshold else "UNKNOWN"
    if usage.usage_source != "PROVIDER_REPORTED" or ("normalized_usage_scope", "ALL_BILLABLE") not in evidence.pricing_conditions:
        return "UNKNOWN"
    estimate = estimate_budget_cost(evidence, usage)
    return "BOUND_VIOLATION" if estimate > threshold else "ESTIMATED"


def estimate_budget_cost(evidence, usage):
    """Exact conservative estimate using the same qualified pre-send tariff."""
    costs = cost_components(evidence)
    return (costs[0] * usage.input_tokens / evidence.input_token_bound if evidence.input_token_bound else Fraction(0)) + costs[1] * usage.output_tokens / evidence.output_token_limit + costs[2]


def reconciled_usage(evidence, usage, state):
    if usage is None or state != "ESTIMATED":
        return usage
    import math

    from ..contracts import ModelUsage
    estimate = estimate_budget_cost(evidence, usage)
    display = float(estimate)
    if Fraction.from_float(display) < estimate:
        display = math.nextafter(display, math.inf)
    return ModelUsage.model_validate({
        **usage.model_dump(mode="python", exclude={"fingerprint"}),
        "estimated_cost": display,
        "currency": evidence.currency,
    })
