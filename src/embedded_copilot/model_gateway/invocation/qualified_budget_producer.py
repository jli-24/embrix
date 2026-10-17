"""Host-held, fail-closed producer for a single qualified invocation budget.

Each source is an independently configured host authority. Its ``read(context)``
must return a current, externally established witness; constructing these private
records or hashing bytes does not itself establish a provider qualification.
No source, grant, model, token bound, or tariff is installed by default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from ..contracts import canonical_fingerprint, fingerprint, identifier
from ..governance import EngineeringStage
from ..profiles import ProtocolFamily
from .budget import (
    TrustedBudgetQualification,
    cost_components,
    digest,
    reject,
    wire_target_digest,
)
from .contracts import InvocationBudgetEvidenceV1, SingleInvocationBudgetEnvelopeV1


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class QualificationContext:
    """Bounded facts supplied to each independent host source."""

    bound_request: object
    execution_snapshot: object
    selected_model: object
    task_authority: object
    final_transport_bytes: bytes
    wire_target: tuple
    request_fingerprint: str
    snapshot_fingerprint: str
    selected_model_fingerprint: str
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    protocol_family: ProtocolFamily
    wire_target_sha256: str
    final_transport_sha256: str
    provider_parameter: str
    output_token_limit: int
    tenant_id: str
    project_id: str
    conversation_id: str
    task_id: str
    task_authority_fingerprint: str
    engineering_stage: EngineeringStage
    now: datetime
    scope_fingerprint: str


@dataclass(frozen=True)
class WitnessHeader:
    authority_id: str
    evidence_identity: str
    revision: str
    raw_witness: bytes
    raw_sha256: str
    witness_fingerprint: str
    scope_fingerprint: str
    observed_at: datetime
    valid_until: datetime


@dataclass(frozen=True)
class GrantedBudget:
    witness: WitnessHeader
    envelope: SingleInvocationBudgetEnvelopeV1


@dataclass(frozen=True)
class CountPremise:
    witness: WitnessHeader
    count_kind: str
    input_token_bound: int
    covers_all_billable_input: bool


@dataclass(frozen=True)
class CapPremise:
    witness: WitnessHeader
    output_token_limit: int
    provider_parameter: str
    covers_all_billable_output: bool
    includes_reasoning: bool


@dataclass(frozen=True)
class TariffPremise:
    witness: WitnessHeader
    currency: str
    unit: str
    input_price_decimal: str
    output_price_decimal: str
    cache_read_price_decimal: str | None
    cache_write_price_decimal: str | None
    cache_semantics: str
    additional_fees_state: str
    additional_cost_bound_decimal: str
    pricing_conditions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TransportPremise:
    witness: WitnessHeader
    final_transport_sha256: str
    wire_target_sha256: str
    protocol_family: ProtocolFamily
    provider_parameter: str
    output_token_limit: int
    single_send_semantics: bool


@dataclass
class _Prepared:
    heads: tuple[WitnessHeader, ...]
    checks: int = 0


class QualifiedInvocationBudgetProducer:
    """TrustedInvocationBudgetPort implementation; sources remain host-owned."""

    def __init__(
        self,
        *,
        trusted_host_identity="desktop-qualified-budget-host-v1",
        grant_source=None,
        count_source=None,
        cap_source=None,
        tariff_source=None,
        transport_source=None,
    ):
        identifier(trusted_host_identity, field="trusted_host_identity")
        self.trusted_host_identity = trusted_host_identity
        self._sources = (
            grant_source,
            count_source,
            cap_source,
            tariff_source,
            transport_source,
        )
        self._lock = Lock()
        self._prepared: dict[str, _Prepared] = {}

    @staticmethod
    def _context(
        *, request, snapshot, bound, protocol, payload, parameter, wire_target, now
    ):
        if (
            type(payload) is not bytes
            or type(protocol) is not ProtocolFamily
            or protocol is ProtocolFamily.CUSTOM
            or type(parameter) is not str
            or type(now) is not datetime
            or now.utcoffset() is None
            or snapshot.schema_version != 2
            or request.authority_reference is None
            or bound.model_policy.fallback_providers
        ):
            reject()
        authority = request.authority_reference
        fields = {
            "request_fingerprint": bound.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
            "selected_model_fingerprint": canonical_fingerprint(
                snapshot.selected_model, exclude=set()
            ),
            "connection_id": snapshot.connection_id,
            "connection_revision": snapshot.connection_revision,
            "credential_revision": snapshot.credential_revision,
            "endpoint_revision": snapshot.endpoint_revision,
            "protocol_family": protocol.value,
            "wire_target_sha256": wire_target_digest(wire_target),
            "final_transport_sha256": digest(payload),
            "provider_parameter": parameter,
            "output_token_limit": bound.model_policy.max_output_tokens,
            "tenant_id": authority.tenant_id,
            "project_id": request.project_id,
            "conversation_id": request.payload.conversation_id,
            "task_id": authority.engineering_task_id,
            "task_authority_fingerprint": canonical_fingerprint(
                authority, exclude=set()
            ),
            "engineering_stage": request.stage_id.value,
        }
        return QualificationContext(
            **{
                **fields,
                "protocol_family": protocol,
                "now": now,
                "bound_request": bound,
                "execution_snapshot": snapshot,
                "selected_model": snapshot.selected_model,
                "task_authority": authority,
                "final_transport_bytes": payload,
                "wire_target": wire_target,
                "engineering_stage": request.stage_id,
                "scope_fingerprint": digest(_canonical(fields)),
            }
        )

    @staticmethod
    def _read(source, context, expected_type):
        if source is None or not callable(getattr(source, "read", None)):
            reject()
        authority_id = getattr(source, "authority_id", None)
        identifier(authority_id, field="authority_id")
        value = source.read(context)
        if type(value) is not expected_type or type(value.witness) is not WitnessHeader:
            reject()
        head = value.witness
        for name in ("authority_id", "evidence_identity", "revision"):
            identifier(getattr(head, name), field=name)
        if (
            head.authority_id != authority_id
            or type(head.raw_witness) is not bytes
            or not head.raw_witness
        ):
            reject()
        fingerprint(head.raw_sha256)
        fingerprint(head.witness_fingerprint)
        fingerprint(head.scope_fingerprint)
        if (
            head.raw_sha256 != digest(head.raw_witness)
            or head.scope_fingerprint != context.scope_fingerprint
        ):
            reject("BUDGET_BINDING_STALE")
        material = (
            head.authority_id,
            head.evidence_identity,
            head.revision,
            head.raw_sha256,
            head.scope_fingerprint,
            head.observed_at.isoformat(),
            head.valid_until.isoformat(),
        )
        if head.witness_fingerprint != digest(_canonical(material)):
            reject()
        if (
            head.observed_at.utcoffset() is None
            or head.valid_until.utcoffset() is None
            or not head.observed_at <= context.now < head.valid_until
        ):
            reject("BUDGET_BINDING_STALE")
        return value

    def _collect(self, context):
        types = (
            GrantedBudget,
            CountPremise,
            CapPremise,
            TariffPremise,
            TransportPremise,
        )
        values = tuple(
            self._read(source, context, kind)
            for source, kind in zip(self._sources, types)
        )
        # The five premises must have separately identifiable current authorities.
        if len({value.witness.authority_id for value in values}) != len(values):
            reject()
        grant, count, cap, tariff, transport = values
        if type(grant.envelope) is not SingleInvocationBudgetEnvelopeV1:
            reject()
        envelope = SingleInvocationBudgetEnvelopeV1.model_validate(
            grant.envelope.model_dump()
        )
        if (
            envelope.tenant_id != context.tenant_id
            or envelope.project_id != context.project_id
            or envelope.conversation_id != context.conversation_id
            or envelope.task_id != context.task_id
            or envelope.task_authority_fingerprint != context.task_authority_fingerprint
            or envelope.engineering_stage is not context.engineering_stage
            or context.now >= envelope.not_after
        ):
            reject("BUDGET_BINDING_STALE")
        if (
            count.count_kind not in {"EXACT", "PROVEN_UPPER_BOUND"}
            or type(count.input_token_bound) is not int
            or count.input_token_bound < 0
            or count.covers_all_billable_input is not True
            or type(cap.output_token_limit) is not int
            or cap.output_token_limit != context.output_token_limit
            or cap.provider_parameter != context.provider_parameter
            or cap.covers_all_billable_output is not True
            or cap.includes_reasoning is not True
            or transport.final_transport_sha256 != context.final_transport_sha256
            or transport.wire_target_sha256 != context.wire_target_sha256
            or transport.protocol_family is not context.protocol_family
            or transport.provider_parameter != context.provider_parameter
            or transport.output_token_limit != context.output_token_limit
            or transport.single_send_semantics is not True
        ):
            reject()
        if type(tariff.pricing_conditions) is not tuple or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(item) is not str for item in pair)
            for pair in tariff.pricing_conditions
        ):
            reject()
        conditions = tariff.pricing_conditions + (
            ("grant_witness_fingerprint", grant.witness.witness_fingerprint),
            ("tariff_witness_fingerprint", tariff.witness.witness_fingerprint),
            ("transport_witness_fingerprint", transport.witness.witness_fingerprint),
        )
        values = {
            "request_fingerprint": context.request_fingerprint,
            "execution_snapshot_fingerprint": context.snapshot_fingerprint,
            "final_transport_sha256": context.final_transport_sha256,
            "selected_model": None,  # replaced by caller's already validated snapshot
            "connection_id": context.connection_id,
            "connection_revision": context.connection_revision,
            "credential_revision": context.credential_revision,
            "endpoint_revision": context.endpoint_revision,
            "protocol_family": context.protocol_family,
            "pricing_conditions": conditions,
            "count_kind": count.count_kind,
            "input_token_bound": count.input_token_bound,
            "counter_identity": count.witness.evidence_identity,
            "counter_revision": count.witness.revision,
            "qualification_evidence_fingerprint": count.witness.witness_fingerprint,
            "count_not_after": count.witness.valid_until,
            "output_token_limit": cap.output_token_limit,
            "provider_parameter": cap.provider_parameter,
            "enforcement_evidence_fingerprint": cap.witness.witness_fingerprint,
            "covers_all_billable_input": True,
            "covers_all_billable_output": True,
            "includes_reasoning": True,
            "output_not_after": cap.witness.valid_until,
            "pricing_identity": tariff.witness.evidence_identity,
            "pricing_revision": tariff.witness.revision,
            "source_sha256": tariff.witness.raw_sha256,
            "currency": tariff.currency,
            "unit": tariff.unit,
            "input_price_decimal": tariff.input_price_decimal,
            "output_price_decimal": tariff.output_price_decimal,
            "cache_read_price_decimal": tariff.cache_read_price_decimal,
            "cache_write_price_decimal": tariff.cache_write_price_decimal,
            "cache_semantics": tariff.cache_semantics,
            "additional_fees_state": tariff.additional_fees_state,
            "additional_cost_bound_decimal": tariff.additional_cost_bound_decimal,
            "pricing_not_after": tariff.witness.valid_until,
            "transport_not_after": transport.witness.valid_until,
            "transport_evidence_fingerprint": context.wire_target_sha256,
            "envelope_fingerprint": envelope.fingerprint,
            "input_cost_upper_fraction": "0",
            "output_cost_upper_fraction": "0",
            "additional_cost_upper_fraction": "0",
            "total_cost_upper_fraction": "0",
        }
        return (
            envelope,
            values,
            tuple(value.witness for value in (grant, count, cap, tariff, transport)),
        )

    def _build(
        self,
        *,
        request,
        snapshot,
        bound,
        protocol,
        payload,
        parameter,
        wire_target,
        now,
    ):
        context = self._context(
            request=request,
            snapshot=snapshot,
            bound=bound,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
        )
        envelope, values, heads = self._collect(context)
        values["selected_model"] = snapshot.selected_model
        provisional = InvocationBudgetEvidenceV1(**values)
        costs = cost_components(provisional)
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
        evidence = InvocationBudgetEvidenceV1(**values)
        return envelope, evidence, heads

    def prepare(self, **kwargs):
        try:
            envelope, evidence, heads = self._build(**kwargs)
            with self._lock:
                # Bounded in-memory checkpoint; persistent envelope occupancy is
                # enforced by the existing store, never by this cache.
                self._prepared = {
                    key: value
                    for key, value in self._prepared.items()
                    if all(kwargs["now"] < head.valid_until for head in value.heads)
                }
                if len(self._prepared) >= 1024:
                    reject()
                self._prepared[evidence.fingerprint] = _Prepared(heads)
            return envelope, evidence
        except Exception as error:
            from ..exceptions import ModelGatewayRejected

            if isinstance(error, ModelGatewayRejected):
                raise
            reject()

    async def aprepare(self, **kwargs):
        return self.prepare(**kwargs)

    def qualify(self, *, envelope, evidence, **kwargs):
        try:
            current_envelope, current_evidence, heads = self._build(**kwargs)
            if (
                type(envelope) is not SingleInvocationBudgetEnvelopeV1
                or type(evidence) is not InvocationBudgetEvidenceV1
                or current_envelope != envelope
                or current_evidence != evidence
            ):
                reject("BUDGET_BINDING_STALE")
            with self._lock:
                prepared = self._prepared.get(evidence.fingerprint)
                if prepared is None or prepared.heads != heads:
                    reject("BUDGET_BINDING_STALE")
                prepared.checks += 1
                if prepared.checks >= 2:
                    del self._prepared[evidence.fingerprint]
            return TrustedBudgetQualification(True, True, True, True)
        except Exception as error:
            from ..exceptions import ModelGatewayRejected

            if isinstance(error, ModelGatewayRejected):
                raise
            reject()
