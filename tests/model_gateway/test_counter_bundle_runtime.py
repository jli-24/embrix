"""Offline sequencing checks for the private two-operation dispatch guard."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.model_gateway.exceptions import ModelGatewayRejected
from embedded_copilot.model_gateway.invocation import bundle_runtime
from embedded_copilot.model_gateway.invocation.budget import wire_target_digest
from embedded_copilot.model_gateway.invocation.contracts import InvocationMode

_NOW = datetime(2026, 10, 15, tzinfo=UTC)
_RAW = b'{"model":"gpt-5.6-luna"}'
_TARGET = (("path", "responses/input_tokens"),)
_FP = "sha256:" + "a" * 64


def _harness(monkeypatch, *, status="COUNTED", tokens=8192, stale_at=None):
    events = []
    snapshot = SimpleNamespace(
        connection_id="connection",
        credential_revision="credential",
        endpoint_revision="endpoint",
    )
    attempt = SimpleNamespace(
        snapshot=snapshot,
        authority_reference="authority",
        attempt_schema_version=4,
        budget_phase="PREPARED",
    )
    attempt_state = [attempt]
    request = SimpleNamespace(
        invocation_mode=InvocationMode.TASK, authority_reference="authority"
    )
    entry = SimpleNamespace(
        connection_id="connection",
        credential_revision="credential",
        endpoint_revision="endpoint",
    )
    envelope = SimpleNamespace(input_admission_limit=8192)
    admission = SimpleNamespace(
        count_request_base64=base64.b64encode(_RAW).decode("ascii"),
        counter_wire_target_sha256=wire_target_digest(_TARGET),
    )
    observation = SimpleNamespace(observation_status=status, input_tokens=tokens)
    evidence = object()

    class Selection:
        def execution_snapshot(self, _profile, _selection):
            events.append("snapshot")
            return snapshot

    class Authority:
        def revalidate(self, _reference):
            events.append("authority")
            return "authority"

    class Source:
        def __init__(self):
            self.counter_qualifications = 0
            self.generation_qualifications = 0

        def prepare_counter(self, **_kwargs):
            events.append("prepare_counter")
            return envelope, admission

        def qualify_counter(self, **_kwargs):
            events.append("qualify_counter")
            self.counter_qualifications += 1
            if stale_at == ("counter", self.counter_qualifications):
                raise ValueError("stale counter witness")
            return object()

        def prepare_generation(self, **_kwargs):
            events.append("prepare_generation")
            return evidence

        def qualify_generation(self, **_kwargs):
            events.append("qualify_generation")
            self.generation_qualifications += 1
            if stale_at == ("generation", self.generation_qualifications):
                raise ValueError("stale generation witness")
            return object()

    class Store:
        def claim_counter_intent(self, previous, _envelope, _admission):
            events.append("claim_counter")
            return SimpleNamespace(
                **{**vars(previous), "budget_phase": "COUNTER_INTENT"}
            )

        def record_counter_observation(self, previous, _observation):
            events.append("record_observation")
            return SimpleNamespace(
                **{**vars(previous), "budget_phase": "COUNTER_OBSERVED"}
            )

        def claim_generation_intent(self, previous, _evidence):
            events.append("claim_generation")
            return SimpleNamespace(
                **{**vars(previous), "budget_phase": "GENERATION_INTENT"}
            )

    class Gateway:
        def __init__(self):
            self.posts = 0

        def _count_bound_with_metadata(self, actual_entry, payload, *, dispatch_guard):
            events.append("gateway_count")
            assert actual_entry is entry and payload == _RAW
            dispatch_guard(payload, _TARGET)
            self.posts += 1
            return object()

        async def _acount_bound_with_metadata(
            self, actual_entry, payload, *, dispatch_guard
        ):
            events.append("gateway_count")
            assert actual_entry is entry and payload == _RAW
            await dispatch_guard(payload, _TARGET)
            self.posts += 1
            return object()

    monkeypatch.setattr(
        bundle_runtime,
        "verify_counter_admission",
        lambda **_kwargs: (events.append("verify_counter"), (envelope, admission))[1],
    )
    monkeypatch.setattr(
        bundle_runtime,
        "observe_counter",
        lambda **_kwargs: (events.append("observe_counter"), observation)[1],
    )
    monkeypatch.setattr(
        bundle_runtime,
        "verify_generation_bundle",
        lambda **_kwargs: (events.append("verify_generation"), (envelope, evidence))[1],
    )

    class Service(bundle_runtime.BundleInvocationMixin):
        pass

    service = Service()
    service._budget_bundle_source = Source()
    service._receipt_store = Store()
    service._gateway = Gateway()
    service._selection = Selection()
    service._clock = lambda: _NOW
    args = (object(), entry, object(), b"generation", "max_output_tokens", object())
    return service, request, Authority(), attempt_state, args, events


@pytest.mark.parametrize("asynchronous", [False, True])
def test_guard_claims_both_roles_and_rechecks_before_each_send(
    monkeypatch, asynchronous
):
    service, request, authority, state, args, events = _harness(monkeypatch)
    guard = service._bundle_guard(
        request, object(), object(), authority, state, asynchronous=asynchronous
    )
    if asynchronous:
        asyncio.run(guard(*args))
    else:
        guard(*args)
    assert service._gateway.posts == 1
    assert state[0].budget_phase == "GENERATION_INTENT"
    assert events.index("claim_counter") < events.index("gateway_count")
    assert events.index("gateway_count") < events.index("record_observation")
    assert events.index("record_observation") < events.index("prepare_generation")
    assert events.index("prepare_generation") < events.index("claim_generation")
    assert events.count("qualify_counter") == 2
    assert events.count("qualify_generation") == 2


@pytest.mark.parametrize(
    "status,tokens",
    [
        ("HTTP_ERROR", None),
        ("INVALID_RESPONSE", None),
        ("COUNTED", 8193),
    ],
)
def test_error_or_overlimit_counter_is_recorded_before_generation_rejection(
    monkeypatch, status, tokens
):
    service, request, authority, state, args, events = _harness(
        monkeypatch, status=status, tokens=tokens
    )
    guard = service._bundle_guard(request, object(), object(), authority, state)
    with pytest.raises(ModelGatewayRejected):
        guard(*args)
    assert service._gateway.posts == 1
    assert state[0].budget_phase == "COUNTER_OBSERVED"
    assert "record_observation" in events
    assert "prepare_generation" not in events
    assert "claim_generation" not in events


def test_changed_counter_witness_after_durable_intent_blocks_count_post(monkeypatch):
    service, request, authority, state, args, events = _harness(
        monkeypatch, stale_at=("counter", 2)
    )
    guard = service._bundle_guard(request, object(), object(), authority, state)
    with pytest.raises(ModelGatewayRejected):
        guard(*args)
    assert state[0].budget_phase == "COUNTER_INTENT"
    assert service._gateway.posts == 0
    assert "record_observation" not in events


def test_changed_generation_witness_after_durable_intent_blocks_generation(
    monkeypatch,
):
    service, request, authority, state, args, events = _harness(
        monkeypatch, stale_at=("generation", 2)
    )
    guard = service._bundle_guard(request, object(), object(), authority, state)
    with pytest.raises(ModelGatewayRejected):
        guard(*args)
    assert state[0].budget_phase == "GENERATION_INTENT"
    assert service._gateway.posts == 1
    assert events.count("qualify_generation") == 2


def test_projection_propagates_unknown_and_counts_only_observed_generation():
    project = bundle_runtime.BundleInvocationMixin._bundle_role_projection
    attempt = SimpleNamespace(
        attempt_schema_version=4,
        budget_phase="COUNTER_INTENT",
        counter_admission_fingerprint=_FP,
        counter_observation_fingerprint=None,
        counter_status="DISPATCH_INTENT",
        counter_transport_send_count=None,
        counter_response_observed=False,
        generation_transport_send_count=0,
        counter_http_status=None,
        counter_input_tokens=None,
    )
    intent = project(attempt, None, None, 0)
    assert intent["total_external_request_count"] is None
    assert intent["generation_transport_send_count"] == 0
    assert intent["counter_status"] == "OUTCOME_UNKNOWN"
    attempt.budget_phase = "COUNTER_OBSERVED"
    attempt.counter_transport_send_count = 1
    attempt.counter_status = "COUNTED"
    attempt.counter_response_observed = True
    attempt.counter_observation_fingerprint = _FP
    attempt.counter_http_status = 200
    attempt.counter_input_tokens = 8192
    counted = project(attempt, None, None, 0)
    assert counted["total_external_request_count"] == 1
    attempt.budget_phase = "GENERATION_INTENT"
    unknown = project(attempt, None, None, None)
    assert unknown["generation_transport_send_count"] is None
    assert unknown["total_external_request_count"] is None
    pre_send_rejection = project(attempt, None, None, 0)
    assert pre_send_rejection["generation_transport_send_count"] == 0
    assert pre_send_rejection["total_external_request_count"] == 1
    success = project(attempt, object(), 200, 1)
    assert success["generation_transport_send_count"] == 1
    assert success["total_external_request_count"] == 2
