"""Offline actual Service -> held Gateway -> native adapter -> SQLite bundle chain."""

import asyncio
import base64
import json

import httpx
import pytest

from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelInvocationService,
    ModelSelectionResult,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.invocation.budget import digest
from embedded_copilot.model_gateway.invocation.contracts import ModelInvocationRequest
from embedded_copilot.model_gateway.invocation.qualified_bundle_producer import (
    QualifiedCounterBundleProducer,
)
from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
from tests.model_gateway.test_budget_gate import CurrentAuthority
from tests.model_gateway.test_counter_bundle_core import _case
from tests.model_gateway.test_qualified_bundle_producer import _producer


def chain(
    tmp_path,
    monkeypatch,
    *,
    count=8192,
    counter_status=200,
    invalid=False,
    fail_role=None,
    hook=None,
    qualified=True,
    generation_status=200,
    cancel_role=None,
):
    inputs, _ = _case()
    request = inputs["request"]
    material = request.model_dump(mode="python")
    material["payload"].update(messages=(), fingerprint="sha256:" + "0" * 64)
    material["generation_options"].update(
        structured_output_schema=(("type", "object"),)
    )
    request = ModelInvocationRequest.model_validate(material)
    snapshot = type(inputs["snapshot"]).model_validate(
        {
            **inputs["snapshot"].model_dump(exclude={"fingerprint"}),
            "policy_source": "STAGE_POLICY",
        }
    )
    identity = snapshot.selected_model
    bound = ModelInvocationService._build_request(request, identity)
    adapter = ProtocolHTTPProviderAdapter(
        provider="openai",
        endpoint_id=identity.endpoint_id,
        base_url="https://api.openai.com/v1",
        protocol_family=inputs["protocol"],
        api_key="offline-placeholder",
    )
    generation = adapter._prepare_transport_bytes(bound)
    counter = json.dumps(
        {
            k: v
            for k, v in json.loads(generation).items()
            if k not in {"max_output_tokens", "service_tier"}
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    inputs["admission"] = inputs["admission"].model_copy(
        update={"count_request_sha256": digest(counter)}
    )
    producer, sources = _producer(inputs)
    if not qualified:
        producer = QualifiedCounterBundleProducer()
    authority = CurrentAuthority()
    events = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            assert kwargs.get("transport") is None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, path, *, headers, content):
            assert content == (
                counter if path == "responses/input_tokens" else generation
            )
            events.append((path, content))
            if cancel_role == path:
                raise asyncio.CancelledError()
            if fail_role == path:
                raise httpx.ReadTimeout("offline timeout")
            if path == "responses/input_tokens":
                if hook:
                    hook(sources, authority)
                raw = (
                    b"invalid"
                    if invalid
                    else json.dumps(
                        {"object": "response.input_tokens", "input_tokens": count}
                    ).encode()
                )
                return httpx.Response(counter_status, content=raw)
            assert path == "responses"
            if generation_status != 200:
                return httpx.Response(
                    generation_status, content=b'{"error":"offline-generation-http"}'
                )
            return httpx.Response(
                200,
                json={
                    "id": "offline-generation",
                    "model": identity.model_id,
                    "output_text": "answer",
                    "usage": {
                        "input_tokens": min(count, 8192),
                        "output_tokens": 3,
                        "total_tokens": min(count, 8192) + 3,
                    },
                },
            )

    class AsyncClient(Client):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, *args, **kwargs):
            return super().post(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
    entry = ResolvedTransportEntry(
        provider_id=identity.provider_id,
        endpoint_id=identity.endpoint_id,
        credential_ref=identity.credential_ref,
        adapter=adapter,
        connection_id=snapshot.connection_id,
        credential_revision=snapshot.credential_revision,
        endpoint_revision=snapshot.endpoint_revision,
    )
    gateway = ModelGatewayService(
        providers={"openai": adapter}, transport_entries=(entry,)
    )

    class Selection:
        def select_governed_model(self, *_args, **_kwargs):
            return ModelSelectionResult(
                selected_model=identity, selection_reason="STAGE_FIXED_MODEL"
            )

        def execution_snapshot(self, *_args):
            return snapshot

        def credential_revision(self, *_args):
            return snapshot.credential_revision

        def policy_source(self, *_args):
            return snapshot.policy_source

    store = SQLiteModelGatewayStore(tmp_path / "bundle.db")
    service = ModelInvocationService(
        selection_resolver=Selection(),
        gateway=gateway,
        receipt_store=store,
        trusted_budget_bundle_source=producer,
        clock=lambda: inputs["now"],
    )
    return service, store, request, authority, events


def run(service, request, authority, asynchronous=False):
    return (
        asyncio.run(service.ainvoke(request, authority_source=authority))
        if asynchronous
        else service.invoke(request, authority_source=authority)
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_chain_two_roles_and_idempotent_restart(
    tmp_path, monkeypatch, asynchronous
):
    service, store, request, authority, events = chain(tmp_path, monkeypatch)
    result = run(service, request, authority, asynchronous)
    assert result.status.value == "SUCCESS", result
    receipt = result.receipt
    assert receipt.schema_version == 7
    assert (
        receipt.counter_transport_send_count,
        receipt.generation_transport_send_count,
        receipt.total_external_request_count,
    ) == (1, 1, 2)
    assert receipt.counter_status == "COUNTED" and receipt.counter_input_tokens == 8192
    assert receipt.budget_reconciliation_state == "UNKNOWN"
    assert receipt.provider_request_count == 0 and receipt.transport_send_count == 1
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    assert attempt.attempt_schema_version == 4 and attempt.budget_phase == "TERMINAL"
    _envelope, admission, observation, evidence = store.bundle_binding_for_attempt(
        attempt
    )
    assert evidence.input_token_bound == observation.input_tokens == 8192
    assert base64.b64decode(observation.raw_response_base64)
    assert digest(events[0][1]) == admission.count_request_sha256
    assert digest(events[1][1]) == admission.final_generation_bytes_sha256
    # New store instance is the same durable history; replay makes no HTTP call.
    service._receipt_store = SQLiteModelGatewayStore(tmp_path / "bundle.db")
    restored = run(service, request, authority, asynchronous)
    assert restored.receipt == receipt
    assert len(events) == 2


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"count": 8193}, "COUNTED"),
        ({"counter_status": 429}, "HTTP_ERROR"),
        ({"invalid": True}, "INVALID_RESPONSE"),
    ],
)
def test_counter_failure_observed_preserved_and_generation_zero(
    tmp_path, monkeypatch, kwargs, expected
):
    service, store, request, authority, events = chain(tmp_path, monkeypatch, **kwargs)
    result = run(service, request, authority)
    assert result.status.value != "SUCCESS"
    assert result.receipt is not None, result
    assert result.receipt.counter_status == expected
    assert result.receipt.counter_response_observed
    assert result.receipt.counter_transport_send_count == 1
    assert result.receipt.generation_transport_send_count == 0
    assert result.receipt.total_external_request_count == 1
    assert len(events) == 1
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    assert store.bundle_binding_for_attempt(attempt)[2].raw_response_base64
    assert run(service, request, authority).receipt == result.receipt
    assert len(events) == 1


@pytest.mark.parametrize(
    "role,counts",
    [("responses/input_tokens", (None, 0, None)), ("responses", (1, None, None))],
)
def test_role_timeout_preserves_unknown_without_retry(
    tmp_path, monkeypatch, role, counts
):
    service, _store, request, authority, events = chain(
        tmp_path, monkeypatch, fail_role=role
    )
    result = run(service, request, authority)
    assert result.receipt is not None, result
    assert (
        result.receipt.counter_transport_send_count,
        result.receipt.generation_transport_send_count,
        result.receipt.total_external_request_count,
    ) == counts
    prior = len(events)
    assert run(service, request, authority).receipt == result.receipt
    assert len(events) == prior


@pytest.mark.parametrize("which", ["source", "authority"])
def test_change_after_counter_preserves_raw_and_blocks_generation(
    tmp_path, monkeypatch, which
):
    def hook(sources, authority):
        if which == "source":
            sources["tariff"].revision = "changed"
        else:
            authority.stale = True

    service, _store, request, authority, events = chain(
        tmp_path, monkeypatch, hook=hook
    )
    result = run(service, request, authority)
    assert result.receipt is not None, result
    assert result.receipt.counter_response_observed
    assert result.receipt.generation_transport_send_count == 0
    assert len(events) == 1


def test_default_unqualified_bundle_has_zero_requests(tmp_path, monkeypatch):
    service, _store, request, authority, events = chain(
        tmp_path, monkeypatch, qualified=False
    )
    result = run(service, request, authority)
    assert result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
    assert result.receipt is not None, result
    assert result.receipt.total_external_request_count == 0
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", [400, 429, 500])
def test_generation_http_error_raw_survives_restart(
    tmp_path, monkeypatch, asynchronous, status
):
    service, _store, request, authority, events = chain(
        tmp_path, monkeypatch, generation_status=status
    )
    result = run(service, request, authority, asynchronous)
    assert result.status.value == "FAILED" and result.receipt is not None, result
    assert result.receipt.http_status == status
    assert result.receipt.generation_transport_send_count == 1
    assert result.receipt.total_external_request_count == 2
    reopened = SQLiteModelGatewayStore(tmp_path / "bundle.db")
    attempt = reopened.get_attempt_for_project(request.project_id, request.request_id)
    assert attempt.status.value == "COMPLETED"
    original = reopened.generation_http_failure_for_attempt(attempt)
    assert original.http_status == status
    assert original.raw_response == b'{"error":"offline-generation-http"}'
    assert original.request_sha256 == digest(events[1][1])
    assert run(service, request, authority, asynchronous).receipt == result.receipt
    assert len(events) == 2


@pytest.mark.parametrize(
    "role,counts",
    [("responses/input_tokens", (None, 0, None)), ("responses", (1, None, None))],
)
def test_effective_async_cancellation_then_recovery_never_replays(
    tmp_path, monkeypatch, role, counts
):
    service, _store, request, authority, events = chain(
        tmp_path, monkeypatch, cancel_role=role
    )
    with pytest.raises(asyncio.CancelledError):
        run(service, request, authority, True)
    # Cancellation is injected inside the actual mocked HTTP post, with no SQLite lock held.
    reopened = SQLiteModelGatewayStore(tmp_path / "bundle.db")
    attempt = reopened.get_attempt_for_project(request.project_id, request.request_id)
    assert attempt.status.value == "OUTCOME_UNKNOWN"
    service._receipt_store = reopened
    before = len(events)
    recovered = run(service, request, authority, True)
    assert recovered.receipt is not None, recovered
    assert (
        recovered.receipt.counter_transport_send_count,
        recovered.receipt.generation_transport_send_count,
        recovered.receipt.total_external_request_count,
    ) == counts
    assert len(events) == before


@pytest.mark.parametrize(
    "phase", ["PREPARED", "COUNTER_INTENT", "COUNTER_OBSERVED", "GENERATION_INTENT"]
)
def test_new_process_recovers_each_interrupted_role_without_http(
    tmp_path, monkeypatch, phase
):
    import subprocess
    import sys

    from embedded_copilot.model_gateway.invocation.openai_count_collector import (
        CounterHTTPResponse,
    )

    service, store, request, authority, events = chain(tmp_path, monkeypatch)
    profile = service._task_profile(request)
    selection = service._selection.select_governed_model(profile)
    attempt = service._prepare_attempt(request, profile, selection, authority)
    bound = service._build_request(request, selection.selected_model)
    adapter = service._gateway.transport_entries[0].adapter
    payload = adapter._prepare_transport_bytes(bound)
    target = adapter._budget_wire_target("responses", adapter._headers())
    ctx = {
        "request": request,
        "snapshot": attempt.snapshot,
        "bound": bound,
        "protocol": adapter._family,
        "payload": payload,
        "parameter": "max_output_tokens",
        "wire_target": target,
        "now": service._clock(),
    }
    envelope, admission = service._budget_bundle_source.prepare_counter(**ctx)
    if phase != "PREPARED":
        attempt = store.claim_counter_intent(attempt, envelope, admission)
    if phase not in {"PREPARED", "COUNTER_INTENT"}:
        from embedded_copilot.model_gateway.invocation.budget_bundle import (
            observe_counter,
        )

        count_target = tuple(
            (key, "responses/input_tokens" if key == "path" else value)
            for key, value in target
        )
        raw = CounterHTTPResponse(
            200,
            b'{"object":"response.input_tokens","input_tokens":7}',
            admission.count_request_sha256,
            count_target,
        )
        observation = observe_counter(
            request=request,
            snapshot=attempt.snapshot,
            envelope=envelope,
            admission=admission,
            response=raw,
            now=service._clock(),
        )
        attempt = store.record_counter_observation(attempt, observation)
        if phase == "GENERATION_INTENT":
            evidence = service._budget_bundle_source.prepare_generation(
                envelope=envelope, admission=admission, observation=observation, **ctx
            )
            store.claim_generation_intent(attempt, evidence)
    code = """import sys,json
sys.path[:0]=["src","."]
from pathlib import Path
from pytest import MonkeyPatch
from tests.model_gateway.test_counter_bundle_integration import chain,run
with MonkeyPatch.context() as mp:
    service,store,request,authority,events=chain(Path(sys.argv[1]),mp)
    class UnavailableSelection:
        def select_governed_model(self,*args,**kwargs): raise AssertionError("historical selection forbidden")
        def policy_source(self,*args): raise AssertionError("historical policy read forbidden")
    service._selection=UnavailableSelection()
    result=run(service,request,authority)
    assert result.receipt is not None,result
    assert events==[],events
    print(json.dumps({'phase':result.receipt.budget_phase,'counter':result.receipt.counter_transport_send_count,'generation':result.receipt.generation_transport_send_count,'total':result.receipt.total_external_request_count,'error':result.error_code}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    facts = json.loads(completed.stdout.strip())
    assert facts["phase"] == "TERMINAL"
    assert facts["counter"] == (
        0 if phase == "PREPARED" else None if phase == "COUNTER_INTENT" else 1
    )
    assert facts["generation"] == (None if phase == "GENERATION_INTENT" else 0)
    assert facts["total"] == (
        0 if phase == "PREPARED" else 1 if phase == "COUNTER_OBSERVED" else None
    )
    if phase == "PREPARED":
        assert facts["error"] != "MODEL_INVOCATION_OUTCOME_UNKNOWN"
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kwargs", [{}, {"fail_role": "responses/input_tokens"}])
def test_history_precedes_new_model_governance_without_receipt_duplication(
    tmp_path, monkeypatch, asynchronous, kwargs
):
    service, store, request, authority, events = chain(tmp_path, monkeypatch, **kwargs)
    first = run(service, request, authority, asynchronous)
    assert first.receipt is not None, first

    class RejectingSelection:
        def select_governed_model(self, *args, **kwargs):
            raise AssertionError("history must not select a model again")

        def policy_source(self, *args):
            raise AssertionError("history must not consult current policy")

    service._selection = RejectingSelection()
    before = len(events)
    restored = run(service, request, authority, asynchronous)
    assert restored.receipt == first.receipt
    assert restored.context.policy_source == first.receipt.policy_source
    assert (
        store.count_receipts_for_project(
            request.project_id, request_id=request.request_id
        )
        == 1
    )
    assert len(events) == before
    conflict = request.model_copy(update={"task_type": "different-task"})
    rejected = run(service, conflict, authority, asynchronous)
    assert rejected.error_code == "MODEL_INVOCATION_REQUEST_CONFLICT"
    assert (
        store.count_receipts_for_project(
            request.project_id, request_id=request.request_id
        )
        == 1
    )
    assert len(events) == before
