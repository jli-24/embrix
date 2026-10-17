"""Host-held evidence producer for the bounded native count/generation bundle.

All five readers are independently configured by the host. This module has no
default source, persistence, network call, retry, or provider price table.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from ..contracts import identifier
from ..exceptions import ModelGatewayRejected
from .budget import cost_components, digest, reject, wire_target_digest
from .budget_bundle import (
    TrustedCounterQualification,
    _ceiling_cost,
    _scope,
    verify_counter_admission,
    verify_generation_bundle,
)
from .contracts import (
    ConditionalGenerationCeilingV1,
    CounterAdmissionV1,
    InvocationBudgetEvidenceV1,
    SingleInvocationBudgetEnvelopeV2,
)
from .qualified_budget_producer import (
    CapPremise,
    QualificationContext,
    QualifiedInvocationBudgetProducer,
    TariffPremise,
    TransportPremise,
    WitnessHeader,
)


@dataclass(frozen=True)
class GrantedBundle:
    witness: WitnessHeader
    envelope: SingleInvocationBudgetEnvelopeV2


@dataclass(frozen=True)
class CounterFeePremise:
    witness: WitnessHeader
    count_request_sha256: str
    upper_decimal: str
    currency: str = "USD"
    unit: str = "ENTIRE_COUNT_REQUEST_BOUND"


class QualifiedCounterBundleProducer:
    """TrustedCounterBudgetPort; only current independent readers qualify facts."""

    def __init__(
        self,
        *,
        grant_source=None,
        counter_fee_source=None,
        cap_source=None,
        tariff_source=None,
        transport_source=None,
        trusted_host_identity="desktop-qualified-counter-bundle-host-v1",
    ):
        self.trusted_host_identity = identifier(
            trusted_host_identity, field="trusted_host_identity"
        )
        self._sources = (
            grant_source,
            counter_fee_source,
            cap_source,
            tariff_source,
            transport_source,
        )

    @property
    def phase_authorization(self) -> object | None:
        """Only a formal consent source opts into the same-DB phase gate."""
        hook = getattr(self._sources[0], "phase_authorization", None)
        if not callable(hook):
            return None
        return hook

    @property
    def consumption_store(self) -> object | None:
        return getattr(self._sources[0], "consumption_store", None)

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
        context: QualificationContext = QualifiedInvocationBudgetProducer._context(
            request=request,
            snapshot=snapshot,
            bound=bound,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
        )
        values = tuple(
            QualifiedInvocationBudgetProducer._read(source, context, kind)
            for source, kind in zip(
                self._sources,
                (
                    GrantedBundle,
                    CounterFeePremise,
                    CapPremise,
                    TariffPremise,
                    TransportPremise,
                ),
            )
        )
        if len({item.witness.authority_id for item in values}) != 5:
            reject()
        grant, fee, cap, tariff, transport = values
        if type(grant.envelope) is not SingleInvocationBudgetEnvelopeV2:
            reject()
        envelope = SingleInvocationBudgetEnvelopeV2.model_validate(
            grant.envelope.model_dump()
        )
        count_bytes, counter_target = _scope(
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
        if (
            fee.count_request_sha256 != digest(count_bytes)
            or fee.currency != "USD"
            or fee.unit != "ENTIRE_COUNT_REQUEST_BOUND"
            or cap.output_token_limit != envelope.total_output_limit
            or cap.output_token_limit != context.output_token_limit
            or cap.provider_parameter != parameter
            or cap.covers_all_billable_output is not True
            or cap.includes_reasoning is not True
            or transport.final_transport_sha256 != digest(payload)
            or transport.wire_target_sha256 != wire_target_digest(wire_target)
            or transport.protocol_family is not protocol
            or transport.provider_parameter != parameter
            or transport.output_token_limit != envelope.total_output_limit
            or transport.single_send_semantics is not True
            or type(tariff.pricing_conditions) is not tuple
            or any(
                type(pair) is not tuple
                or len(pair) != 2
                or any(type(part) is not str for part in pair)
                for pair in tariff.pricing_conditions
            )
        ):
            reject()
        conditions = tariff.pricing_conditions + (
            ("grant_witness_fingerprint", grant.witness.witness_fingerprint),
            ("tariff_witness_fingerprint", tariff.witness.witness_fingerprint),
            ("transport_witness_fingerprint", transport.witness.witness_fingerprint),
        )
        ceiling = ConditionalGenerationCeilingV1(
            unit=tariff.unit,
            input_price_decimal=tariff.input_price_decimal,
            output_price_decimal=tariff.output_price_decimal,
            cache_read_price_decimal=tariff.cache_read_price_decimal,
            cache_write_price_decimal=tariff.cache_write_price_decimal,
            cache_semantics=tariff.cache_semantics,
            additional_fees_state=tariff.additional_fees_state,
            additional_cost_bound_decimal=tariff.additional_cost_bound_decimal,
            pricing_identity=tariff.witness.evidence_identity,
            pricing_revision=tariff.witness.revision,
            pricing_source_sha256=tariff.witness.raw_sha256,
            pricing_conditions=conditions,
            pricing_not_after=tariff.witness.valid_until,
            output_not_after=cap.witness.valid_until,
            transport_not_after=transport.witness.valid_until,
            cap_evidence_fingerprint=cap.witness.witness_fingerprint,
            transport_evidence_fingerprint=wire_target_digest(wire_target),
            grant_evidence_fingerprint=grant.witness.witness_fingerprint,
            covers_all_billable_input=True,
            covers_all_billable_output=cap.covers_all_billable_output,
            includes_reasoning=cap.includes_reasoning,
        )
        generation_upper = _ceiling_cost(ceiling, now)
        from ._budget_preflight import _exact_rate

        counter_upper = _exact_rate(fee.upper_decimal)
        admission = CounterAdmissionV1(
            envelope_fingerprint=envelope.fingerprint,
            request_fingerprint=bound.fingerprint,
            execution_snapshot_fingerprint=snapshot.fingerprint,
            task_authority_fingerprint=envelope.task_authority_fingerprint,
            policy_fingerprint=snapshot.policy_fingerprint,
            selected_model=snapshot.selected_model,
            connection_id=snapshot.connection_id,
            connection_revision=snapshot.connection_revision,
            credential_revision=snapshot.credential_revision,
            endpoint_revision=snapshot.endpoint_revision,
            protocol_family=protocol,
            final_generation_bytes_sha256=digest(payload),
            count_request_base64=base64.b64encode(count_bytes).decode("ascii"),
            count_request_sha256=digest(count_bytes),
            counter_wire_target_sha256=wire_target_digest(counter_target),
            generation_wire_target_sha256=wire_target_digest(wire_target),
            counter_fee_identity=fee.witness.evidence_identity,
            counter_fee_revision=fee.witness.revision,
            counter_fee_source_sha256=fee.witness.raw_sha256,
            counter_fee_upper_decimal=fee.upper_decimal,
            counter_fee_evidence_fingerprint=fee.witness.witness_fingerprint,
            counter_fee_not_after=fee.witness.valid_until,
            generation_ceiling=ceiling,
            generation_cost_upper_fraction=str(generation_upper),
            combined_cost_upper_fraction=str(counter_upper + generation_upper),
            not_after=min(item.witness.valid_until for item in values),
        )
        kwargs = {
            "request": request,
            "snapshot": snapshot,
            "bound": bound,
            "envelope": envelope,
            "admission": admission,
            "protocol": protocol,
            "payload": payload,
            "parameter": parameter,
            "wire_target": wire_target,
            "now": now,
            "qualification": TrustedCounterQualification(True, True, True, True, True),
        }
        verify_counter_admission(**kwargs)
        return envelope, admission

    def prepare_counter(
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
        try:
            return self._build(
                request=request,
                snapshot=snapshot,
                bound=bound,
                protocol=protocol,
                payload=payload,
                parameter=parameter,
                wire_target=wire_target,
                now=now,
            )
        except ModelGatewayRejected:
            raise
        except Exception:  # noqa: BLE001 - an unavailable host source must fail closed
            reject()

    def qualify_counter(
        self,
        *,
        envelope,
        admission,
        request,
        snapshot,
        bound,
        protocol,
        payload,
        parameter,
        wire_target,
        now,
    ):
        current = self.prepare_counter(
            request=request,
            snapshot=snapshot,
            bound=bound,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
        )
        if (
            type(envelope) is not SingleInvocationBudgetEnvelopeV2
            or type(admission) is not CounterAdmissionV1
            or current != (envelope, admission)
        ):
            reject("BUDGET_BINDING_STALE")
        return TrustedCounterQualification(True, True, True, True, True)

    @staticmethod
    def _generation_evidence(*, envelope, admission, observation):
        ceiling = admission.generation_ceiling
        values = {
            "request_fingerprint": admission.request_fingerprint,
            "execution_snapshot_fingerprint": admission.execution_snapshot_fingerprint,
            "final_transport_sha256": admission.final_generation_bytes_sha256,
            "selected_model": admission.selected_model,
            "connection_id": admission.connection_id,
            "connection_revision": admission.connection_revision,
            "credential_revision": admission.credential_revision,
            "endpoint_revision": admission.endpoint_revision,
            "protocol_family": admission.protocol_family,
            "pricing_conditions": ceiling.pricing_conditions,
            "count_kind": "EXACT",
            "input_token_bound": observation.input_tokens,
            "counter_identity": "native-responses-input-tokens",
            "counter_revision": "native-response-v1",
            "qualification_evidence_fingerprint": observation.fingerprint,
            "count_not_after": observation.not_after,
            "output_token_limit": ceiling.output_token_limit,
            "provider_parameter": "max_output_tokens",
            "enforcement_evidence_fingerprint": ceiling.cap_evidence_fingerprint,
            "covers_all_billable_input": ceiling.covers_all_billable_input,
            "covers_all_billable_output": ceiling.covers_all_billable_output,
            "includes_reasoning": ceiling.includes_reasoning,
            "output_not_after": ceiling.output_not_after,
            "pricing_identity": ceiling.pricing_identity,
            "pricing_revision": ceiling.pricing_revision,
            "source_sha256": ceiling.pricing_source_sha256,
            "currency": ceiling.currency,
            "unit": ceiling.unit,
            "input_price_decimal": ceiling.input_price_decimal,
            "output_price_decimal": ceiling.output_price_decimal,
            "cache_read_price_decimal": ceiling.cache_read_price_decimal,
            "cache_write_price_decimal": ceiling.cache_write_price_decimal,
            "cache_semantics": ceiling.cache_semantics,
            "additional_fees_state": ceiling.additional_fees_state,
            "additional_cost_bound_decimal": ceiling.additional_cost_bound_decimal,
            "pricing_not_after": ceiling.pricing_not_after,
            "transport_not_after": ceiling.transport_not_after,
            "transport_evidence_fingerprint": ceiling.transport_evidence_fingerprint,
            "envelope_fingerprint": envelope.fingerprint,
            "input_cost_upper_fraction": "0",
            "output_cost_upper_fraction": "0",
            "additional_cost_upper_fraction": "0",
            "total_cost_upper_fraction": "0",
        }
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

    def prepare_generation(
        self,
        *,
        envelope,
        admission,
        observation,
        request,
        snapshot,
        bound,
        protocol,
        payload,
        parameter,
        wire_target,
        now,
    ):
        try:
            current = self._build(
                request=request,
                snapshot=snapshot,
                bound=bound,
                protocol=protocol,
                payload=payload,
                parameter=parameter,
                wire_target=wire_target,
                now=now,
            )
            if (
                type(envelope) is not SingleInvocationBudgetEnvelopeV2
                or type(admission) is not CounterAdmissionV1
                or current != (envelope, admission)
            ):
                reject("BUDGET_BINDING_STALE")
            evidence = self._generation_evidence(
                envelope=envelope, admission=admission, observation=observation
            )
            verify_generation_bundle(
                request=request,
                snapshot=snapshot,
                bound=bound,
                envelope=envelope,
                admission=admission,
                observation=observation,
                evidence=evidence,
                protocol=protocol,
                payload=payload,
                parameter=parameter,
                wire_target=wire_target,
                now=now,
                qualification=TrustedCounterQualification(True, True, True, True, True),
            )
            return evidence
        except ModelGatewayRejected:
            raise
        except Exception:  # noqa: BLE001 - an unavailable host source must fail closed
            reject()

    def qualify_generation(
        self,
        *,
        envelope,
        admission,
        observation,
        evidence,
        request,
        snapshot,
        bound,
        protocol,
        payload,
        parameter,
        wire_target,
        now,
    ):
        current = self.prepare_generation(
            envelope=envelope,
            admission=admission,
            observation=observation,
            request=request,
            snapshot=snapshot,
            bound=bound,
            protocol=protocol,
            payload=payload,
            parameter=parameter,
            wire_target=wire_target,
            now=now,
        )
        if type(evidence) is not InvocationBudgetEvidenceV1 or current != evidence:
            reject("BUDGET_BINDING_STALE")
        return TrustedCounterQualification(True, True, True, True, True)
