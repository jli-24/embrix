"""Offline witness suppliers exercise the production producer through the real seam."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.model_gateway import SQLiteModelGatewayStore
from embedded_copilot.model_gateway.invocation.budget import digest
from embedded_copilot.model_gateway.invocation.contracts import (
    SingleInvocationBudgetEnvelopeV1,
)
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    CapPremise,
    CountPremise,
    GrantedBudget,
    QualifiedInvocationBudgetProducer,
    TariffPremise,
    TransportPremise,
    WitnessHeader,
    _canonical,
)
from tests.model_gateway.test_budget_gate import (
    CurrentAuthority,
    real_bound_fixture,
    task_request,
)


class OfflineWitnessSource:
    """Test-only stand-in for an independent, host-held qualification authority."""

    def __init__(self, name, kind):
        self.authority_id = "offline-" + name
        self.kind = kind
        self.revision = "revision-1"
        self.observed = None
        self.expiry = None
        self.scope_override = None
        self.mutate = None
        self.value_override = None

    def read(self, context):
        if self.observed is None:
            self.observed = context.now - timedelta(seconds=1)
            self.expiry = context.now + timedelta(hours=1)
        raw = (
            self.authority_id + ":" + self.revision + ":qualified-source-document"
        ).encode()
        raw_sha = digest(raw)
        scope = self.scope_override or context.scope_fingerprint
        material = (
            self.authority_id,
            self.kind + "-identity",
            self.revision,
            raw_sha,
            scope,
            self.observed.isoformat(),
            self.expiry.isoformat(),
        )
        witness = WitnessHeader(
            self.authority_id,
            self.kind + "-identity",
            self.revision,
            raw,
            raw_sha,
            digest(_canonical(material)),
            scope,
            self.observed,
            self.expiry,
        )
        if self.kind == "grant":
            envelope = SingleInvocationBudgetEnvelopeV1(
                envelope_id="offline-one-envelope",
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                conversation_id=context.conversation_id,
                task_id=context.task_id,
                task_authority_fingerprint=context.task_authority_fingerprint,
                engineering_stage=context.engineering_stage,
                not_after=self.expiry,
            )
            result = GrantedBudget(witness, envelope)
        elif self.kind == "count":
            result = CountPremise(witness, "PROVEN_UPPER_BOUND", 12, True)
        elif self.kind == "cap":
            result = CapPremise(
                witness,
                context.output_token_limit,
                context.provider_parameter,
                True,
                True,
            )
        elif self.kind == "tariff":
            result = TariffPremise(
                witness,
                "USD",
                "PER_TOKEN",
                "0.000001",
                "0.000001",
                None,
                None,
                "NONE",
                "KNOWN_NONE",
                "0",
                (("normalized_usage_scope", "ALL_BILLABLE"),),
            )
        else:
            result = TransportPremise(
                witness,
                context.final_transport_sha256,
                context.wire_target_sha256,
                context.protocol_family,
                context.provider_parameter,
                context.output_token_limit,
                True,
            )
        if self.value_override is not None:
            result = self.value_override(result)
        if self.mutate is not None:
            self.mutate()
        return result


def producer():
    sources = {
        kind: OfflineWitnessSource(kind, kind)
        for kind in ("grant", "count", "cap", "tariff", "transport")
    }
    qualified = QualifiedInvocationBudgetProducer(
        **{kind + "_source": source for kind, source in sources.items()}
    )
    return qualified, sources


@pytest.mark.parametrize("asynchronous", [False, True])
def test_exact_final_bytes_persisted_and_restored(tmp_path, asynchronous):
    source, _ = producer()
    service, store, sent, _ = real_bound_fixture(tmp_path, source)
    request = task_request()
    result = (
        asyncio.run(service.ainvoke(request, authority_source=CurrentAuthority()))
        if asynchronous
        else service.invoke(request, authority_source=CurrentAuthority())
    )
    assert result.status.value == "SUCCESS", result.error_code
    assert len(sent) == 1 and result.receipt.transport_send_count == 1
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    envelope, evidence = store.budget_binding_for_attempt(attempt)
    assert evidence.final_transport_sha256 == digest(sent[0])
    assert evidence.input_cost_upper_fraction == "3/250000"
    assert evidence.output_cost_upper_fraction == "16/15625"
    assert evidence.envelope_fingerprint == envelope.fingerprint
    restored = SQLiteModelGatewayStore(tmp_path / "qualified-budget.db")
    assert restored.budget_binding_for_attempt(
        restored.get_attempt_for_project(request.project_id, request.request_id)
    ) == (envelope, evidence)
    second = service.invoke(request, authority_source=CurrentAuthority())
    assert second.receipt == result.receipt and len(sent) == 1


@pytest.mark.parametrize("absent", ["grant", "count", "cap", "tariff", "transport"])
def test_each_missing_host_source_has_zero_send(tmp_path, absent):
    source, sources = producer()
    source._sources = tuple(
        None if kind == absent else sources[kind]
        for kind in ("grant", "count", "cap", "tariff", "transport")
    )
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
    assert result.receipt.transport_send_count == 0


@pytest.mark.parametrize(
    "kind,override",
    [
        ("count", lambda value: replace(value, covers_all_billable_input=False)),
        ("cap", lambda value: replace(value, includes_reasoning=False)),
        ("cap", lambda value: replace(value, output_token_limit=1)),
        ("tariff", lambda value: replace(value, currency="UNKNOWN")),
        ("tariff", lambda value: replace(value, additional_fees_state="UNKNOWN")),
        ("tariff", lambda value: replace(value, cache_semantics="UNKNOWN")),
        ("transport", lambda value: replace(value, single_send_semantics=False)),
        (
            "transport",
            lambda value: replace(value, final_transport_sha256="sha256:" + "4" * 64),
        ),
        (
            "transport",
            lambda value: replace(value, wire_target_sha256="sha256:" + "4" * 64),
        ),
    ],
)
def test_unqualified_or_wrong_scope_premise_has_zero_send(tmp_path, kind, override):
    source, sources = producer()
    sources[kind].value_override = override
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
    assert result.receipt.transport_send_count == 0


@pytest.mark.parametrize("kind", ["grant", "count", "cap", "tariff", "transport"])
def test_wrong_model_endpoint_or_owner_bound_witness_has_zero_send(tmp_path, kind):
    source, sources = producer()
    sources[kind].scope_override = "sha256:" + "7" * 64
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_BINDING_STALE"


def test_expired_witness_has_zero_send(tmp_path):
    source, sources = producer()
    sources["tariff"].observed = datetime.now(UTC) - timedelta(hours=2)
    sources["tariff"].expiry = datetime.now(UTC) - timedelta(hours=1)
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.receipt.transport_send_count == 0
    assert result.error_code == "BUDGET_BINDING_STALE"


@pytest.mark.parametrize("after_claim", [False, True])
def test_changed_current_source_head_before_or_after_claim_has_zero_send(
    tmp_path, after_claim
):
    source, sources = producer()
    service, store, sent, _ = real_bound_fixture(tmp_path, source)
    if after_claim:
        claim = store.claim_budget_dispatch_intent

        def changed(*args):
            result = claim(*args)
            sources["count"].revision = "revision-2"
            return result

        store.claim_budget_dispatch_intent = changed
    else:
        reads = 0
        count = sources["count"]
        original = count.read

        def changed(context):
            nonlocal reads
            reads += 1
            if reads == 2:
                count.revision = "revision-2"
            return original(context)

        count.read = changed
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.receipt.transport_send_count == 0
    assert result.error_code == "BUDGET_BINDING_STALE"
    assert store.get_attempt_for_project("project-1", "request-1").status.value == (
        "OUTCOME_UNKNOWN" if after_claim else "PREPARED"
    )


def test_default_unconfigured_producer_refuses_transport(tmp_path):
    source = QualifiedInvocationBudgetProducer()
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"


def test_unknown_witness_origin_and_raw_document_mismatch_have_zero_send(tmp_path):
    for index, (override, code) in enumerate(
        (
            (
                lambda value: replace(
                    value, witness=replace(value.witness, authority_id="unknown-origin")
                ),
                "BUDGET_EVIDENCE_UNAVAILABLE",
            ),
            (
                lambda value: replace(
                    value,
                    witness=replace(value.witness, raw_witness=b"different-document"),
                ),
                "BUDGET_BINDING_STALE",
            ),
        ),
        start=1,
    ):
        source, sources = producer()
        sources["count"].value_override = override
        case_path = tmp_path / str(index)
        case_path.mkdir()
        service, _, sent, _ = real_bound_fixture(case_path, source)
        result = service.invoke(
            task_request(f"request-{index}"), authority_source=CurrentAuthority()
        )
        assert sent == [] and result.error_code == code


def test_qualified_additional_fee_above_threshold_has_zero_send(tmp_path):
    source, sources = producer()
    sources["tariff"].value_override = lambda value: replace(
        value,
        additional_fees_state="QUALIFIED_BOUND",
        additional_cost_bound_decimal="0.10",
    )
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_LIMIT_EXCEEDED"
    assert result.receipt.transport_send_count == 0


def test_timeout_consumes_envelope_and_never_retries(tmp_path):
    source, _ = producer()
    service, store, sent, _ = real_bound_fixture(tmp_path, source, failing=True)
    first = service.invoke(
        task_request("request-1"), authority_source=CurrentAuthority()
    )
    second = service.invoke(
        task_request("request-2"), authority_source=CurrentAuthority()
    )
    assert len(sent) == 1 and first.status.value == "FAILED"
    assert second.error_code == "BUDGET_ENVELOPE_CLAIMED"
    assert second.receipt.transport_send_count == 0
    assert (
        store.get_attempt_for_project("project-1", "request-1").status.value
        == "OUTCOME_UNKNOWN"
    )
