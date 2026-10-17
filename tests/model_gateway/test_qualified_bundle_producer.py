"""Synthetic independent sources for the host-held bundle producer."""

from datetime import timedelta

import pytest

from embedded_copilot.model_gateway import ModelGatewayRejected
from embedded_copilot.model_gateway.invocation.budget import digest, wire_target_digest
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    CapPremise,
    TariffPremise,
    TransportPremise,
    WitnessHeader,
    _canonical,
)
from embedded_copilot.model_gateway.invocation.qualified_bundle_producer import (
    CounterFeePremise,
    GrantedBundle,
    QualifiedCounterBundleProducer,
)
from tests.model_gateway.test_counter_bundle_core import CONDITIONS, _case, _observed


class OfflineSource:
    def __init__(self, kind, envelope, count_sha):
        self.kind = kind
        self.authority_id = "offline-" + kind
        self.envelope = envelope
        self.count_sha = count_sha
        self.revision = "revision-1"
        self.price = "0.000001"
        self.raise_on_read = False

    def read(self, context):
        if self.raise_on_read:
            raise RuntimeError("offline source unavailable")
        observed = context.now - timedelta(seconds=1)
        # Stable source heads across the three phases for a fixed fixture clock.
        observed = context.now.replace(minute=0, second=0, microsecond=0) - timedelta(
            seconds=1
        )
        expiry = context.now.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
        raw = (self.authority_id + self.revision).encode()
        raw_sha = digest(raw)
        material = (
            self.authority_id,
            self.kind + "-identity",
            self.revision,
            raw_sha,
            context.scope_fingerprint,
            observed.isoformat(),
            expiry.isoformat(),
        )
        head = WitnessHeader(
            self.authority_id,
            self.kind + "-identity",
            self.revision,
            raw,
            raw_sha,
            digest(_canonical(material)),
            context.scope_fingerprint,
            observed,
            expiry,
        )
        if self.kind == "grant":
            return GrantedBundle(head, self.envelope)
        if self.kind == "counter_fee":
            return CounterFeePremise(head, self.count_sha, "0.01")
        if self.kind == "cap":
            return CapPremise(head, 2048, "max_output_tokens", True, True)
        if self.kind == "tariff":
            return TariffPremise(
                head,
                "USD",
                "PER_TOKEN",
                self.price,
                self.price,
                None,
                None,
                "NONE",
                "KNOWN_NONE",
                "0",
                CONDITIONS[:-3],
            )
        return TransportPremise(
            head,
            digest(context.final_transport_bytes),
            wire_target_digest(context.wire_target),
            context.protocol_family,
            context.provider_parameter,
            2048,
            True,
        )


def _producer(inputs):
    sources = {
        kind: OfflineSource(
            kind, inputs["envelope"], inputs["admission"].count_request_sha256
        )
        for kind in ("grant", "counter_fee", "cap", "tariff", "transport")
    }
    return QualifiedCounterBundleProducer(
        **{kind + "_source": source for kind, source in sources.items()}
    ), sources


def _context(inputs):
    return {
        key: value
        for key, value in inputs.items()
        if key not in {"envelope", "admission", "qualification"}
    }


def test_current_independent_sources_build_and_recheck_both_roles():
    inputs, target = _case()
    producer, _ = _producer(inputs)
    context = _context(inputs)
    envelope, admission = producer.prepare_counter(**context)
    assert producer.qualify_counter(
        envelope=envelope, admission=admission, **context
    ).grant_qualified
    inputs.update(envelope=envelope, admission=admission)
    observation = _observed(inputs, target)
    context["now"] += timedelta(seconds=2)
    evidence = producer.prepare_generation(
        envelope=envelope, admission=admission, observation=observation, **context
    )
    assert evidence.count_kind == "EXACT" and evidence.input_token_bound == 8192
    assert producer.qualify_generation(
        envelope=envelope,
        admission=admission,
        observation=observation,
        evidence=evidence,
        **context,
    ).counter_fee_qualified


@pytest.mark.parametrize(
    "missing", ["grant", "counter_fee", "cap", "tariff", "transport"]
)
def test_missing_independent_source_rejects(missing):
    inputs, _ = _case()
    producer, _ = _producer(inputs)
    sources = dict(
        zip(("grant", "counter_fee", "cap", "tariff", "transport"), producer._sources)
    )
    sources[missing] = None
    producer = QualifiedCounterBundleProducer(
        **{kind + "_source": value for kind, value in sources.items()}
    )
    with pytest.raises(ModelGatewayRejected):
        producer.prepare_counter(**_context(inputs))


@pytest.mark.parametrize(
    "changed", ["grant", "counter_fee", "cap", "tariff", "transport"]
)
def test_changed_current_source_head_rejects(changed):
    inputs, _ = _case()
    producer, sources = _producer(inputs)
    context = _context(inputs)
    envelope, admission = producer.prepare_counter(**context)
    sources[changed].revision = "revision-2"
    with pytest.raises(ModelGatewayRejected):
        producer.qualify_counter(envelope=envelope, admission=admission, **context)


def test_counter_fee_applies_to_entire_count_request_before_count():
    inputs, _ = _case()
    producer, sources = _producer(inputs)
    sources["counter_fee"].count_sha = digest(b"different-request")
    with pytest.raises(ModelGatewayRejected):
        producer.prepare_counter(**_context(inputs))


def test_source_exception_fails_closed():
    inputs, _ = _case()
    producer, sources = _producer(inputs)
    sources["tariff"].raise_on_read = True
    with pytest.raises(ModelGatewayRejected):
        producer.prepare_counter(**_context(inputs))


def test_generation_rechecks_current_source_head_after_observation():
    inputs, target = _case()
    producer, sources = _producer(inputs)
    context = _context(inputs)
    envelope, admission = producer.prepare_counter(**context)
    inputs.update(envelope=envelope, admission=admission)
    observation = _observed(inputs, target)
    context["now"] += timedelta(seconds=2)
    sources["tariff"].revision = "revision-2"
    with pytest.raises(ModelGatewayRejected):
        producer.prepare_generation(
            envelope=envelope, admission=admission, observation=observation, **context
        )
