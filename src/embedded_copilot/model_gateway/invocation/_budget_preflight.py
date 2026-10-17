"""Private conditional arithmetic only: NOT_WIRED, never spending authorization.

USD 0.10 is a mathematical comparison threshold. A CONDITIONAL result proves an
inequality *assuming* externally qualified tariffs, billable token bounds, and
transport/cap semantics. This module cannot establish those real-world premises,
enforce a provider cap, count a wire request, or authorize execution. It has no
network, dispatch, persistence, public export, or provider serializer integration.
Existing PricingMetadata float prices are deliberately not exact tariff inputs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Literal

from ..contracts import ModelRequest, ModelUsage, fingerprint
from ..profiles import ProtocolFamily
from .contracts import ModelExecutionSnapshot

_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_UNIT_TOKENS = {"PER_TOKEN": 1, "PER_1K_TOKENS": 1000, "PER_1M_TOKENS": 1000000}
_THRESHOLD_USD = Fraction(1, 10)


def _exact_rate(value: str) -> Fraction:
    # Fraction parses the decimal string directly; Decimal context is irrelevant.
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        raise ValueError("EXACT_TARIFF_INVALID")
    return Fraction(value)


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("EVIDENCE_TIME_INVALID")


def _integer(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("TOKEN_BOUND_INVALID")


def _transport_digest(payload: bytes) -> str:
    if type(payload) is not bytes or not payload:
        raise ValueError("TRANSPORT_BYTES_INVALID")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _ProofBinding:
    """Integrity binding, not an attestation of truth or trust."""

    request_fingerprint: str
    snapshot: ModelExecutionSnapshot
    transport_sha256: str
    protocol: ProtocolFamily
    output_cap_field: str

    def __post_init__(self) -> None:
        fingerprint(self.request_fingerprint)
        fingerprint(self.transport_sha256)
        if not isinstance(self.snapshot, ModelExecutionSnapshot):
            raise TypeError("EVIDENCE_BINDING_INVALID")
        ModelExecutionSnapshot.model_validate(self.snapshot.model_dump())
        if (
            type(self.protocol) is not ProtocolFamily
            or self.protocol is ProtocolFamily.CUSTOM
        ):
            raise ValueError("EVIDENCE_PROTOCOL_INVALID")
        if type(self.output_cap_field) is not str or not re.fullmatch(
            r"[A-Za-z_][A-Za-z_0-9]{0,127}", self.output_cap_field
        ):
            raise ValueError("EVIDENCE_CAP_FIELD_INVALID")


def _evidence_window(
    binding: _ProofBinding, observed_at: datetime, valid_until: datetime
) -> None:
    if type(binding) is not _ProofBinding:
        raise ValueError("EVIDENCE_BINDING_INVALID")
    binding.__post_init__()
    _aware(observed_at)
    _aware(valid_until)
    if observed_at >= valid_until:
        raise ValueError("EVIDENCE_TIME_INVALID")


@dataclass(frozen=True)
class _ExactTariff:
    """Externally supplied decimal tariffs, independent of float metadata.

    All billable input tokens are conservatively charged at the highest known
    input/cache tariff only when cache prices explicitly replace the base rate.
    Unknown/additive cache charging is outside this mathematical scope. The
    qualified premise must cover every billable input category; unknown extra
    charges invalidate this proof.
    """

    binding: _ProofBinding
    observed_at: datetime
    valid_until: datetime
    input_rate: str
    output_rate: str
    currency: str
    pricing_unit: str
    additional_fees_absent: bool
    cache_read_rate: str | None = None
    cache_write_rate: str | None = None
    cache_rates_replace_input: bool = False

    def __post_init__(self) -> None:
        _evidence_window(self.binding, self.observed_at, self.valid_until)
        _exact_rate(self.input_rate)
        _exact_rate(self.output_rate)
        for rate in (self.cache_read_rate, self.cache_write_rate):
            if rate is not None:
                _exact_rate(rate)
        if type(self.cache_rates_replace_input) is not bool or (
            (self.cache_read_rate is not None or self.cache_write_rate is not None)
            and self.cache_rates_replace_input is not True
        ):
            raise ValueError("CACHE_TARIFF_COVERAGE_INVALID")
        if (
            self.currency != "USD"
            or type(self.currency) is not str
            or type(self.pricing_unit) is not str
            or self.pricing_unit not in _UNIT_TOKENS
            or self.additional_fees_absent is not True
        ):
            raise ValueError("TARIFF_COVERAGE_INVALID")

    def _rates_per_token(self) -> tuple[Fraction, Fraction]:
        input_rates = [self.input_rate]
        input_rates.extend(
            rate
            for rate in (self.cache_read_rate, self.cache_write_rate)
            if rate is not None
        )
        unit = _UNIT_TOKENS[self.pricing_unit]
        return (
            max(_exact_rate(rate) for rate in input_rates) / unit,
            _exact_rate(self.output_rate) / unit,
        )


@dataclass(frozen=True)
class _BillableInputCount:
    binding: _ProofBinding
    observed_at: datetime
    valid_until: datetime
    input_tokens: int
    basis: Literal["EXACT", "CONSERVATIVE_UPPER_BOUND"]
    covers_all_billable_input: bool

    def __post_init__(self) -> None:
        _evidence_window(self.binding, self.observed_at, self.valid_until)
        _integer(self.input_tokens)
        if (
            type(self.basis) is not str
            or self.basis not in {"EXACT", "CONSERVATIVE_UPPER_BOUND"}
            or self.covers_all_billable_input is not True
        ):
            raise ValueError("INPUT_COUNT_COVERAGE_INVALID")


@dataclass(frozen=True)
class _OutputCap:
    """External evidence that this exact field caps all billed output.

    includes_reasoning is explicit: a visible-response-only limit cannot provide
    the bound. Actual provider support/enforcement remains an external premise.
    """

    binding: _ProofBinding
    observed_at: datetime
    valid_until: datetime
    output_tokens: int
    covers_all_billable_output: bool
    includes_reasoning: bool

    def __post_init__(self) -> None:
        _evidence_window(self.binding, self.observed_at, self.valid_until)
        _integer(self.output_tokens, minimum=1)
        if (
            self.covers_all_billable_output is not True
            or self.includes_reasoning is not True
        ):
            raise ValueError("OUTPUT_CAP_COVERAGE_INVALID")


@dataclass(frozen=True)
class _ExternalPremises:
    """Caller assertions; no field, label, fingerprint, or constructor proves them."""

    tariff_qualified: bool = False
    count_qualified: bool = False
    cap_qualified: bool = False
    transport_semantics_qualified: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.tariff_qualified,
                self.count_qualified,
                self.cap_qualified,
                self.transport_semantics_qualified,
            )
        ):
            raise ValueError("EXTERNAL_PREMISE_INVALID")


@dataclass(frozen=True)
class _RequestScope:
    """Assumed one wire request; cannot enforce retry/fallback or send counts."""

    max_requests: int = 1
    retry_count: int = 0
    fallback_used: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.max_requests) is not int
            or self.max_requests != 1
            or type(self.retry_count) is not int
            or self.retry_count != 0
            or self.fallback_used is not False
        ):
            raise ValueError("REQUEST_SCOPE_INVALID")


@dataclass(frozen=True)
class _ConditionalProof:
    status: Literal["CONDITIONAL", "REJECTED"]
    reason: str
    upper_bound_usd: Fraction | None = None
    binding: _ProofBinding | None = None
    mathematical_threshold_usd: Fraction = field(default=_THRESHOLD_USD, init=False)
    wiring: Literal["NOT_WIRED"] = field(default="NOT_WIRED", init=False)


def _prove_one_request(
    *,
    request: ModelRequest,
    snapshot: ModelExecutionSnapshot,
    final_transport_bytes: bytes,
    protocol: ProtocolFamily,
    output_cap_field: str,
    tariff: _ExactTariff | None,
    input_count: _BillableInputCount | None,
    output_cap: _OutputCap | None,
    premises: _ExternalPremises | None,
    scope: _RequestScope | None,
    now: datetime,
) -> _ConditionalProof:
    """Return only a conditional inequality, never a dispatch or spending permit."""
    if (
        tariff is None
        or input_count is None
        or output_cap is None
        or premises is None
        or scope is None
    ):
        return _ConditionalProof("REJECTED", "REQUIRED_EVIDENCE_MISSING")
    try:
        # Revalidate existing immutable contracts; model_copy or object mutation
        # must not bypass fingerprint/identity validation at this private boundary.
        request = ModelRequest.model_validate(request.model_dump())
        snapshot = ModelExecutionSnapshot.model_validate(snapshot.model_dump())
        _aware(now)
        if type(premises) is not _ExternalPremises or type(scope) is not _RequestScope:
            raise ValueError("PROOF_INPUT_INVALID")
        premises.__post_init__()
        scope.__post_init__()
        if request.model_policy.fallback_providers:
            return _ConditionalProof("REJECTED", "REQUEST_SCOPE_INVALID")
        if (
            request.project_id != snapshot.project_id
            or request.engineering_context.project_id != snapshot.project_id
        ):
            return _ConditionalProof("REJECTED", "REQUEST_IDENTITY_MISMATCH")
        identity = snapshot.selected_model
        if (
            request.model_policy.primary_provider != identity.provider_id
            or request.model_policy.model_name != identity.model_id
            or (
                request.endpoint_id is not None
                and request.endpoint_id != identity.endpoint_id
            )
            or (
                request.credential_ref is not None
                and request.credential_ref != identity.credential_ref
            )
        ):
            return _ConditionalProof("REJECTED", "REQUEST_IDENTITY_MISMATCH")
        if output_cap.output_tokens != request.model_policy.max_output_tokens:
            return _ConditionalProof("REJECTED", "OUTPUT_CAP_POLICY_MISMATCH")
        # snapshot.policy_fingerprint identifies governance policy, not the
        # request's generation ModelPolicy. Their two fingerprints are bound
        # independently through snapshot and request, not compared for equality.
        if not all(
            (
                premises.tariff_qualified,
                premises.count_qualified,
                premises.cap_qualified,
                premises.transport_semantics_qualified,
            )
        ):
            return _ConditionalProof("REJECTED", "EXTERNAL_PREMISE_MISSING")
        expected = _ProofBinding(
            request_fingerprint=request.fingerprint,
            snapshot=snapshot,
            transport_sha256=_transport_digest(final_transport_bytes),
            protocol=protocol,
            output_cap_field=output_cap_field,
        )
        for evidence, expected_type in (
            (tariff, _ExactTariff),
            (input_count, _BillableInputCount),
            (output_cap, _OutputCap),
        ):
            if type(evidence) is not expected_type:
                raise ValueError("PROOF_INPUT_INVALID")
            evidence.__post_init__()
            if evidence.binding != expected:
                return _ConditionalProof("REJECTED", "EVIDENCE_BINDING_MISMATCH")
            if not evidence.observed_at <= now < evidence.valid_until:
                return _ConditionalProof("REJECTED", "EVIDENCE_NOT_CURRENT")
        input_rate, output_rate = tariff._rates_per_token()
        bound = (
            input_count.input_tokens * input_rate
            + output_cap.output_tokens * output_rate
        )
    except (ValueError, TypeError, AttributeError, OverflowError):
        return _ConditionalProof("REJECTED", "PROOF_INPUT_INVALID")
    if bound > _THRESHOLD_USD:
        return _ConditionalProof("REJECTED", "THRESHOLD_EXCEEDED", bound, expected)
    return _ConditionalProof(
        "CONDITIONAL", "EXTERNAL_PREMISES_ASSUMED", bound, expected
    )


@dataclass(frozen=True)
class _PostactualCost:
    """Observed float usage costs; never used in the exact preflight inequality."""

    status: Literal["REPORTED", "ESTIMATED", "UNKNOWN"]
    reported_cost: float | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    wiring: Literal["NOT_WIRED"] = field(default="NOT_WIRED", init=False)


def _postactual_cost(usage: ModelUsage | None) -> _PostactualCost:
    if usage is None:
        return _PostactualCost("UNKNOWN")
    try:
        usage = ModelUsage.model_validate(usage.model_dump())
    except (ValueError, TypeError, AttributeError):
        return _PostactualCost("UNKNOWN")
    status: Literal["REPORTED", "ESTIMATED", "UNKNOWN"] = "UNKNOWN"
    if usage.currency == "USD":
        if usage.provider_reported_cost is not None:
            status = "REPORTED"
        elif usage.estimated_cost is not None:
            status = "ESTIMATED"
    return _PostactualCost(
        status, usage.provider_reported_cost, usage.estimated_cost, usage.currency
    )
