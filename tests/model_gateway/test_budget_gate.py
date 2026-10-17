import asyncio

import pytest

from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelInvocationRequest,
    ModelInvocationService,
    ModelSelectionResult,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.invocation.contracts import InvocationMode
from tests.model_gateway.test_invocation_service import (
    IDENTITY,
    _Gateway,
    _request,
    _Selection,
    _task_authority,
)


@pytest.mark.parametrize("async_execution", [False, True])
def test_task_without_trusted_budget_source_has_zero_send(tmp_path, async_execution):
    class Authority:
        def revalidate(self, reference): return reference
    request = ModelInvocationRequest.from_model_request(_request(), stage_id=EngineeringStage.HARDWARE_SELECTION).model_copy(update={"invocation_mode":InvocationMode.TASK,"authority_reference":_task_authority()})
    gateway=_Gateway()
    service=ModelInvocationService(selection_resolver=_Selection(ModelSelectionResult(selected_model=IDENTITY,selection_reason="STAGE_FIXED_MODEL")),gateway=gateway,receipt_store=SQLiteModelGatewayStore(tmp_path/"state.db"))
    result=asyncio.run(service.ainvoke(request,authority_source=Authority())) if async_execution else service.invoke(request,authority_source=Authority())
    assert result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
    assert gateway.calls == 0
    assert result.receipt.transport_send_count == 0


def test_known_token_overflow_is_not_hidden_by_unknown_currency():
    from types import SimpleNamespace

    from embedded_copilot.model_gateway import ModelUsage
    from embedded_copilot.model_gateway.invocation.budget import reconcile_budget
    evidence = SimpleNamespace(input_token_bound=2, output_token_limit=3)
    usage = ModelUsage(input_tokens=2, output_tokens=4, total_tokens=6)
    assert reconcile_budget(evidence, usage, SimpleNamespace(max_cost_decimal="0.10")) == "BOUND_VIOLATION"


def test_reported_cost_must_not_exceed_the_proven_upper_bound():
    from types import SimpleNamespace

    from embedded_copilot.model_gateway import ModelUsage
    from embedded_copilot.model_gateway.invocation.budget import reconcile_budget
    evidence = SimpleNamespace(input_token_bound=2, output_token_limit=3,
        unit="PER_TOKEN", currency="USD", input_price_decimal="0.001",
        output_price_decimal="0.001", cache_read_price_decimal=None,
        cache_write_price_decimal=None, cache_semantics="NONE",
        additional_cost_bound_decimal="0", additional_fees_state="KNOWN_NONE")
    usage = ModelUsage(input_tokens=2, output_tokens=3, total_tokens=5,
        currency="USD", provider_reported_cost=0.02, usage_source="PROVIDER_REPORTED")
    assert reconcile_budget(evidence, usage, SimpleNamespace(max_cost_decimal="0.10")) == "BOUND_VIOLATION"


import json
import sqlite3
from datetime import timedelta

import httpx

from embedded_copilot.model_gateway import ModelGatewayService
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.contracts import canonical_fingerprint
from embedded_copilot.model_gateway.invocation.budget import (
    TrustedBudgetQualification,
    cost_components,
    digest,
    wire_target_digest,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    InvocationBudgetEvidenceV1,
    ModelExecutionSnapshot,
    SingleInvocationBudgetEnvelopeV1,
)
from embedded_copilot.model_gateway.profiles import ProtocolFamily
from embedded_copilot.model_gateway.transport import ResolvedTransportEntry

ZERO = "sha256:" + "0" * 64
FP = "sha256:" + "3" * 64


class BudgetSelection(_Selection):
    def execution_snapshot(self, profile, selection):
        base = super().execution_snapshot(profile, selection)
        return ModelExecutionSnapshot.model_validate({
            **base.model_dump(mode="python", exclude={"fingerprint"}),
            "schema_version": 2, "connection_revision": FP,
        })


class OfflineQualifiedBudget:
    """Explicit injected fixture; supplies no production qualification."""
    trusted_host_identity = "offline-test-host"

    def __init__(self, *, mutation=None, qualified=True, price="0.000001", hook=None):
        self.mutation = mutation
        self.qualified = qualified
        self.price = price
        self.hook = hook
        self.payloads = []

    def prepare(self, *, request, snapshot, bound, protocol, payload, parameter, wire_target, now):
        self.payloads.append(payload)
        if self.hook:
            self.hook()
        expiry = now + timedelta(hours=1)
        envelope = SingleInvocationBudgetEnvelopeV1(
            envelope_id="offline-one-envelope", tenant_id=request.authority_reference.tenant_id,
            project_id=request.project_id, conversation_id=request.payload.conversation_id,
            task_id=request.authority_reference.engineering_task_id,
            task_authority_fingerprint=canonical_fingerprint(request.authority_reference, exclude=set()),
            engineering_stage=request.stage_id, not_after=expiry,
        )
        values = {
            "request_fingerprint": bound.fingerprint, "execution_snapshot_fingerprint": snapshot.fingerprint,
            "final_transport_sha256": digest(payload), "selected_model": snapshot.selected_model,
            "connection_id": snapshot.connection_id, "connection_revision": snapshot.connection_revision,
            "credential_revision": snapshot.credential_revision, "endpoint_revision": snapshot.endpoint_revision,
            "protocol_family": protocol, "pricing_conditions": (("normalized_usage_scope", "ALL_BILLABLE"),), "count_kind": "PROVEN_UPPER_BOUND", "input_token_bound": 12,
            "counter_identity": "offline-qualified-counter", "counter_revision": "offline-counter-r1",
            "qualification_evidence_fingerprint": FP, "count_not_after": expiry,
            "output_token_limit": bound.model_policy.max_output_tokens, "provider_parameter": parameter,
            "enforcement_evidence_fingerprint": FP, "covers_all_billable_input": True,
            "covers_all_billable_output": True, "includes_reasoning": True, "output_not_after": expiry,
            "pricing_identity": "offline-fixed-tariff", "pricing_revision": "offline-price-r1",
            "source_sha256": FP, "currency": "USD", "unit": "PER_TOKEN",
            "input_price_decimal": self.price, "output_price_decimal": self.price,
            "additional_fees_state": "KNOWN_NONE", "additional_cost_bound_decimal": "0",
            "pricing_not_after": expiry, "transport_not_after": expiry, "transport_evidence_fingerprint": wire_target_digest(wire_target),
            "envelope_fingerprint": envelope.fingerprint,
            "input_cost_upper_fraction": "0", "output_cost_upper_fraction": "0",
            "additional_cost_upper_fraction": "0", "total_cost_upper_fraction": "0",
        }
        if self.mutation:
            values.update(self.mutation)
        provisional = InvocationBudgetEvidenceV1(**values)
        costs = cost_components(provisional)
        values.update(zip(("input_cost_upper_fraction", "output_cost_upper_fraction",
            "additional_cost_upper_fraction", "total_cost_upper_fraction"), map(str, costs)))
        return envelope, InvocationBudgetEvidenceV1(**values)

    async def aprepare(self, **kwargs):
        return self.prepare(**kwargs)

    def qualify(self, **kwargs):
        return TrustedBudgetQualification(*(self.qualified and dict(kwargs["wire_target"])["base_url"] == "https://offline.invalid/v1/" for _ in range(4)))


class CurrentAuthority:
    def __init__(self): self.stale = False
    def revalidate(self, reference):
        if self.stale:
            return reference.model_copy(update={"review_fingerprint": "sha256:" + "4" * 64})
        return reference


def task_request(request_id="request-1"):
    request = ModelInvocationRequest.from_model_request(_request(), stage_id=EngineeringStage.HARDWARE_SELECTION)
    return request.model_copy(update={
        "request_id": request_id, "invocation_mode": InvocationMode.TASK,
        "authority_reference": _task_authority(),
        "payload": type(request.payload).model_validate({**request.payload.model_dump(mode="python", exclude={"fingerprint"}), "conversation_id": "conversation-budget"}),
        "trace_context": type(request.trace_context)(trace_id=request_id),
    })


def real_bound_fixture(tmp_path, source, *, output_tokens=3, failing=False, family=ProtocolFamily.OPENAI_RESPONSES):
    sent = []
    def handler(request):
        sent.append(request.content)
        if failing:
            raise httpx.ReadTimeout("offline timeout")
        body = {"id":"offline-response", "model":IDENTITY.model_id, "output_text":"answer", "usage":{"input_tokens":2,"output_tokens":output_tokens,"total_tokens":2+output_tokens}}
        if family in (ProtocolFamily.OPENAI_CHAT, ProtocolFamily.LOCAL_OPENAI_COMPATIBLE):
            body = {"id":"offline-response", "model":IDENTITY.model_id, "choices":[{"message":{"content":"answer"}}], "usage":{"prompt_tokens":2,"completion_tokens":output_tokens,"total_tokens":2+output_tokens}}
        elif family is ProtocolFamily.ANTHROPIC_MESSAGES:
            body = {"id":"offline-response", "model":IDENTITY.model_id, "content":[{"type":"text","text":"answer"}], "usage":{"input_tokens":2,"output_tokens":output_tokens}}
        elif family is ProtocolFamily.GOOGLE_GENAI:
            body = {"candidates":[{"content":{"parts":[{"text":"answer"}]}}], "usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":output_tokens,"totalTokenCount":2+output_tokens}}
        return httpx.Response(200, json=body)
    transport = httpx.MockTransport(handler)
    adapter = ProtocolHTTPProviderAdapter(provider=IDENTITY.provider_id,
        endpoint_id=IDENTITY.endpoint_id, base_url="https://offline.invalid/v1",
        protocol_family=family,
        api_key="offline-fixture-placeholder", http_transport=transport, async_transport=transport)
    entry = ResolvedTransportEntry(provider_id=IDENTITY.provider_id, endpoint_id=IDENTITY.endpoint_id,
        credential_ref=IDENTITY.credential_ref, adapter=adapter, connection_id="fake-connection",
        credential_revision="fake-credential-revision", endpoint_revision="fake-endpoint-revision")
    gateway = ModelGatewayService(providers={IDENTITY.provider_id:adapter}, transport_entries=(entry,))
    selection = BudgetSelection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL"))
    store = SQLiteModelGatewayStore(tmp_path/"qualified-budget.db")
    service = ModelInvocationService(selection_resolver=selection, gateway=gateway,
        receipt_store=store, trusted_budget_source=source)
    return service, store, sent, adapter


@pytest.mark.parametrize("asynchronous", [False, True])
def test_qualified_final_bytes_are_exactly_the_bytes_sent_and_reserved(tmp_path, asynchronous):
    source = OfflineQualifiedBudget()
    service, store, sent, _ = real_bound_fixture(tmp_path, source)
    request = task_request()
    result = asyncio.run(service.ainvoke(request, authority_source=CurrentAuthority())) if asynchronous else service.invoke(request, authority_source=CurrentAuthority())
    assert result.status.value == "SUCCESS", result
    assert sent == source.payloads and len(sent) == 1
    assert json.loads(sent[0])["max_output_tokens"] == 1024
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    assert attempt.attempt_schema_version == 3 and attempt.status.value == "COMPLETED"
    assert result.receipt.schema_version == 6 and result.receipt.transport_send_count == 1
    envelope, evidence = store.budget_binding_for_attempt(attempt)
    assert evidence.final_transport_sha256 == digest(sent[0])
    assert envelope.max_requests == 1


@pytest.mark.parametrize("mutation", [
    {"final_transport_sha256": "sha256:" + "7"*64},
    {"connection_revision": "sha256:" + "7"*64},
    {"credential_revision":"wrong-credential"},
    {"endpoint_revision":"wrong-endpoint"},
    {"includes_reasoning":False}, {"covers_all_billable_input":False},
    {"currency":"UNKNOWN"}, {"unit":"UNKNOWN"},
    {"additional_fees_state":"UNKNOWN"}, {"count_kind":"CHARACTERS"},
    {"output_token_limit":1},
])
def test_missing_or_wrong_qualification_has_zero_actual_http_send(tmp_path, mutation):
    service, _, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget(mutation=mutation))
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert result.status.value != "SUCCESS"
    assert sent == [] and result.receipt.transport_send_count == 0


def test_ordinary_evidence_labels_do_not_qualify_a_provider(tmp_path):
    service, _, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget(qualified=False))
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"


def test_budget_violation_is_failed_and_original_response_survives_restart(tmp_path):
    service, _store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget(), output_tokens=1025)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert len(sent) == 1 and result.status.value == "FAILED"
    assert result.receipt.budget_reconciliation_state == "BOUND_VIOLATION"
    assert result.error_code == "BUDGET_BOUND_VIOLATION"
    restored = SQLiteModelGatewayStore(tmp_path/"qualified-budget.db")
    attempt = restored.get_attempt_for_project("project-1", "request-1")
    assert attempt.status.value == "OUTCOME_UNKNOWN"
    original = restored.budget_violation_response_for_attempt(attempt)
    assert original.fingerprint == result.response.fingerprint and original.content == "answer"
    assert restored.get_receipt(result.receipt.fingerprint) == result.receipt


def test_final_authority_change_after_intent_has_confirmed_zero_send(tmp_path):
    authority = CurrentAuthority()
    service, store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    claim = store.claim_budget_dispatch_intent
    def changed(*args):
        outcome = claim(*args)
        authority.stale = True
        return outcome
    store.claim_budget_dispatch_intent = changed
    result = service.invoke(task_request(), authority_source=authority)
    assert sent == [] and result.receipt.transport_send_count == 0
    assert store.get_attempt_for_project("project-1", "request-1").status.value == "OUTCOME_UNKNOWN"


def test_same_envelope_for_different_request_ids_permits_only_one_send(tmp_path):
    service, _store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    first = service.invoke(task_request("request-1"), authority_source=CurrentAuthority())
    second = service.invoke(task_request("request-2"), authority_source=CurrentAuthority())
    assert first.status.value == "SUCCESS" and len(sent) == 1
    assert second.error_code == "BUDGET_ENVELOPE_CLAIMED"
    assert second.receipt.transport_send_count == 0


def test_timeout_consumes_the_envelope_without_retry(tmp_path):
    service, store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget(), failing=True)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert result.status.value == "FAILED" and len(sent) == 1
    service.invoke(task_request("request-2"), authority_source=CurrentAuthority())
    assert len(sent) == 1
    assert store.get_attempt_for_project("project-1", "request-1").status.value == "OUTCOME_UNKNOWN"


def test_same_request_restores_identical_receipt_without_reexecution(tmp_path):
    service, _store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    request = task_request()
    first = service.invoke(request, authority_source=CurrentAuthority())
    second = service.invoke(request, authority_source=CurrentAuthority())
    assert second.receipt == first.receipt and second.detail == "MODEL_INVOCATION_HISTORY_ONLY"
    assert second.response is None and len(sent) == 1
    restored = SQLiteModelGatewayStore(tmp_path/"qualified-budget.db")
    assert restored.get_receipt(first.receipt.fingerprint) == first.receipt


def test_common_envelope_concurrent_requests_have_only_one_send(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service, _store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: service.invoke(task_request(f"request-{i}"),
            authority_source=CurrentAuthority()), [1, 2]))
    assert len(sent) == 1
    assert sum(r.status.value == "SUCCESS" for r in results) == 1
    assert any(r.error_code == "BUDGET_ENVELOPE_CLAIMED" for r in results)
    with sqlite3.connect(tmp_path/"qualified-budget.db") as db:
        assert db.execute("SELECT COUNT(*) FROM model_invocation_budget_claims").fetchone()[0] == 1


def test_budget_claim_persistence_failure_prevents_send(tmp_path):
    service, store, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    def failed(*args): raise OSError("offline persistence failure")
    store.claim_budget_dispatch_intent = failed
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.receipt.transport_send_count == 0
    assert store.get_attempt_for_project("project-1", "request-1").status.value == "PREPARED"


def test_mutating_adapter_during_slow_qualification_rejects_zero_send(tmp_path):
    source = OfflineQualifiedBudget()
    service, _store, sent, adapter = real_bound_fixture(tmp_path, source)
    source.hook = lambda: setattr(adapter, "_base_url", "https://changed.invalid/")
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert sent == [] and result.error_code == "BUDGET_BINDING_STALE"
    assert result.receipt.transport_send_count == 0


@pytest.mark.parametrize("price,should_send", [
    ("0.00009765625", True),
    ("0.000097656250000000000000000001", False),
])
def test_exact_usd_boundary_is_not_rounded_by_decimal_context(tmp_path, price, should_send):
    from decimal import localcontext
    with localcontext() as context:
        context.prec = 2
        source = OfflineQualifiedBudget(price=price, mutation={"input_price_decimal":"0"})
        service, _, sent, _ = real_bound_fixture(tmp_path, source)
        result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert bool(sent) == should_send
    if should_send:
        assert result.status.value == "SUCCESS"
    else:
        assert result.error_code == "BUDGET_LIMIT_EXCEEDED" and result.receipt.transport_send_count == 0


@pytest.mark.parametrize("flag", ["count_qualified", "tariff_qualified", "cap_qualified", "transport_semantics_qualified"])
def test_each_missing_independent_host_qualification_refuses_send(tmp_path, flag):
    source = OfflineQualifiedBudget()
    values = {name:True for name in ("count_qualified", "tariff_qualified", "cap_qualified", "transport_semantics_qualified")}
    values[flag] = False
    source.qualify = lambda **kwargs: TrustedBudgetQualification(**values)
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert not sent and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"


def test_receipt_fee_estimate_uses_claimed_exact_tariff_and_api_projects_it(tmp_path):
    from embedded_copilot.api.model_gateway_models import (
        InvocationAttemptView,
        InvocationReceiptView,
    )
    service, store, _, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert result.receipt.budget_reconciliation_state == "ESTIMATED"
    assert result.response.usage.currency is None and result.response.usage.estimated_cost is None
    assert result.receipt.usage.currency == "USD"
    assert result.receipt.usage.estimated_cost >= 0.000005
    view = InvocationReceiptView.from_domain(result.receipt).model_dump(mode="json")
    assert view["budget_reconciliation_state"] == "ESTIMATED"
    assert view["usage"]["estimated_cost"] == result.receipt.usage.estimated_cost
    assert "provider_reported_cost" not in view["usage"]
    assert "content" not in view and "credential_ref" not in view["selected_model"]
    attempt = store.get_attempt_for_project("project-1", "request-1")
    assert InvocationAttemptView.from_domain(attempt).budget_envelope_id == result.receipt.budget_envelope_id


def test_cancel_during_async_count_has_no_claim_and_no_send(tmp_path):
    source = OfflineQualifiedBudget()
    async def cancelled(**kwargs): raise asyncio.CancelledError()
    source.aprepare = cancelled
    service, store, sent, _ = real_bound_fixture(tmp_path, source)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.ainvoke(task_request(), authority_source=CurrentAuthority()))
    assert sent == []
    assert store.get_attempt_for_project("project-1", "request-1").status.value == "PREPARED"
    with sqlite3.connect(tmp_path/"qualified-budget.db") as db:
        assert db.execute("SELECT COUNT(*) FROM model_invocation_budget_claims").fetchone()[0] == 0


def test_misassembled_adapter_cannot_qualify_by_endpoint_revision_label(tmp_path):
    service, _, sent, adapter = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    adapter._base_url = "https://wrong-unqualified-endpoint.invalid/"
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert not sent and result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
    assert result.receipt.transport_send_count == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_default_reloadable_gateway_preserves_budget_guard_to_wire(tmp_path, asynchronous):
    from embedded_copilot.production_release.provider import ReloadableModelGateway
    service, _, sent, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget())
    gateway = service._gateway
    wrapper = ReloadableModelGateway()
    wrapper.install_transport(gateway, gateway.transport_entries)
    service._gateway = wrapper
    request = task_request()
    result = asyncio.run(service.ainvoke(request, authority_source=CurrentAuthority())) if asynchronous else service.invoke(request, authority_source=CurrentAuthority())
    assert result.status.value == "SUCCESS", result.error_code
    assert len(sent) == 1


def test_reloadable_binding_change_during_count_never_sends_old_adapter(tmp_path):
    from embedded_copilot.production_release.provider import ReloadableModelGateway
    source = OfflineQualifiedBudget()
    service, _, sent, _ = real_bound_fixture(tmp_path, source)
    gateway = service._gateway
    wrapper = ReloadableModelGateway()
    wrapper.install_transport(gateway, gateway.transport_entries)
    service._gateway = wrapper
    source.hook = lambda: wrapper.install_transport(gateway, gateway.transport_entries)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert not sent and result.error_code == "BUDGET_BINDING_STALE"
    assert result.receipt.transport_send_count == 0


@pytest.mark.parametrize("family", [ProtocolFamily.OPENAI_RESPONSES, ProtocolFamily.OPENAI_CHAT, ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, ProtocolFamily.ANTHROPIC_MESSAGES, ProtocolFamily.GOOGLE_GENAI])
def test_fixed_http_profiles_bind_their_actual_output_parameter(tmp_path, family):
    source = OfflineQualifiedBudget()
    service, _, sent, _ = real_bound_fixture(tmp_path, source, family=family)
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert result.status.value == "SUCCESS", result.error_code
    assert sent == source.payloads and len(sent) == 1
    body = json.loads(sent[0])
    cap = body["generationConfig"]["maxOutputTokens"] if family is ProtocolFamily.GOOGLE_GENAI else body["max_output_tokens"] if family is ProtocolFamily.OPENAI_RESPONSES else body["max_tokens"]
    assert cap == 1024


def test_unqualified_normalized_meter_scope_keeps_fee_unknown(tmp_path):
    service, _, _, _ = real_bound_fixture(tmp_path, OfflineQualifiedBudget(mutation={"pricing_conditions":()}))
    result = service.invoke(task_request(), authority_source=CurrentAuthority())
    assert result.receipt.budget_reconciliation_state == "UNKNOWN"
    assert result.receipt.usage.estimated_cost is None and result.receipt.usage.currency is None


@pytest.mark.parametrize("amount",[0.1,float("0.10000000000000001"),0.0])
def test_float_reported_cost_cannot_prove_original_decimal_exactness(amount):
    from types import SimpleNamespace

    from embedded_copilot.model_gateway.contracts import ModelUsage
    from embedded_copilot.model_gateway.invocation.budget import reconcile_budget
    evidence=SimpleNamespace(input_token_bound=1,output_token_limit=1,input_price_decimal="0.05",output_price_decimal="0.05",cache_read_price_decimal=None,cache_write_price_decimal=None,cache_semantics="NONE",additional_cost_bound_decimal="0",additional_fees_state="KNOWN_NONE",currency="USD",unit="PER_TOKEN")
    usage=ModelUsage(input_tokens=1,output_tokens=1,total_tokens=2,currency="USD",provider_reported_cost=amount,usage_source="PROVIDER_REPORTED")
    assert reconcile_budget(evidence,usage,SimpleNamespace(max_cost_decimal="0.10"))=="UNKNOWN"
