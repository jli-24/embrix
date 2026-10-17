"""Offline transaction tests for the approved two-role budget claim."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from embedded_copilot.model_gateway import SQLiteModelGatewayStore
from embedded_copilot.model_gateway.contracts import (
    ModelResponse,
    canonical_fingerprint,
)
from embedded_copilot.model_gateway.governance import SelectionMode
from embedded_copilot.model_gateway.invocation.bundle_store import (
    CounterBundleStoreMixin,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    AttemptStatus,
    GateStatus,
    InvocationStatus,
    ModelInvocationAttempt,
    ModelInvocationReceipt,
)
from tests.model_gateway.test_counter_bundle_core import _case, _evidence, _observed

Store = (
    SQLiteModelGatewayStore
    if issubclass(SQLiteModelGatewayStore, CounterBundleStoreMixin)
    else type("BundleTestStore", (CounterBundleStoreMixin, SQLiteModelGatewayStore), {})
)


def _prepared(inputs, *, request_id: str | None = None):
    request = inputs["request"]
    return ModelInvocationAttempt(
        attempt_schema_version=4,
        budget_phase="PREPARED",
        counter_status="NOT_ATTEMPTED",
        counter_transport_send_count=0,
        generation_transport_send_count=0,
        total_external_request_count=0,
        project_id=request.project_id,
        request_id=request.request_id if request_id is None else request_id,
        request_fingerprint=canonical_fingerprint(request, exclude=set()),
        snapshot=inputs["snapshot"],
        invocation_mode=request.invocation_mode,
        authority_reference=request.authority_reference,
    )


def _receipt(
    attempt,
    *,
    generation: int | None,
    result=InvocationStatus.FAILED,
    reconciliation="UNKNOWN",
):
    now = attempt.snapshot  # only its bound identity is used below
    from datetime import UTC, datetime

    started = datetime(2026, 10, 15, tzinfo=UTC)
    counter_count = (
        0
        if attempt.budget_phase == "PREPARED"
        else attempt.counter_transport_send_count
    )
    counter_status = (
        "OUTCOME_UNKNOWN"
        if attempt.budget_phase == "COUNTER_INTENT"
        else attempt.counter_status
    )
    total = (
        None
        if counter_count is None or generation is None
        else counter_count + generation
    )
    return ModelInvocationReceipt(
        schema_version=7,
        budget_phase="TERMINAL",
        budget_envelope_id=attempt.budget_envelope_id,
        budget_reservation_id=attempt.budget_reservation_id,
        budget_evidence_fingerprint=attempt.budget_evidence_fingerprint,
        budget_reconciliation_state=reconciliation,
        counter_admission_fingerprint=attempt.counter_admission_fingerprint,
        counter_observation_fingerprint=attempt.counter_observation_fingerprint,
        counter_status=counter_status,
        counter_transport_send_count=counter_count,
        counter_response_observed=attempt.counter_response_observed,
        generation_transport_send_count=generation,
        total_external_request_count=total,
        counter_http_status=attempt.counter_http_status,
        counter_input_tokens=attempt.counter_input_tokens,
        request_id=attempt.request_id,
        project_id=attempt.project_id,
        trace_context={"trace_id": "trace-bundle"},
        stage_id=now.stage_id,
        invocation_mode=attempt.invocation_mode,
        authority_reference=attempt.authority_reference,
        task_type="HARDWARE_SELECTION",
        policy_source=now.policy_source,
        selection_mode=SelectionMode.FIXED_MODEL,
        selection_reason="STAGE_FIXED_MODEL",
        selected_model=now.selected_model,
        connection_id=now.connection_id,
        gates=tuple(
            {"name": name, "status": GateStatus.PASS}
            for name in (
                "capability",
                "availability",
                "credential",
                "endpoint_health",
                "quota",
            )
        ),
        started_at=started,
        completed_at=started + timedelta(seconds=1),
        latency_ms=1.0,
        result=result,
        error_code=None if result is InvocationStatus.SUCCESS else "BUDGET_STOPPED",
        provider_request_count=0,
        gateway_call_count=1 if generation else 0,
        gateway_response_observed=bool(generation),
        transport_send_count=generation,
    )


def _setup(tmp_path):
    inputs, target = _case()
    store = Store(tmp_path / "bundle.db")
    prepared = _prepared(inputs)
    store.begin_attempt(prepared)
    return store, inputs, target, prepared


def test_claim_observe_generate_complete_and_new_process_reopen(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    observation = _observed(inputs, target)
    counted = store.record_counter_observation(intent, observation)
    evidence = _evidence(inputs, observation)
    generation = store.claim_generation_intent(counted, evidence)
    assert generation.budget_phase == "GENERATION_INTENT"
    assert generation.total_external_request_count is None
    assert store.bundle_binding_for_attempt(generation) == (
        inputs["envelope"],
        inputs["admission"],
        observation,
        evidence,
    )
    receipt = _receipt(generation, generation=1, result=InvocationStatus.SUCCESS)
    completed_receipt = store.complete_bundle_attempt(generation, receipt)
    reopened = Store(tmp_path / "bundle.db")
    terminal = reopened.get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.COMPLETED
    assert terminal.budget_phase == "TERMINAL"
    assert terminal.total_external_request_count == 2
    assert reopened.get_receipt(receipt.fingerprint) == completed_receipt
    assert reopened.bundle_binding_for_attempt(terminal)[2:] == (observation, evidence)
    with pytest.raises(ValueError, match="active bundle attempt required"):
        reopened.mark_bundle_unknown(terminal)


def test_prepared_rejection_persists_zero_operation_terminal_without_claim(tmp_path):
    store, _, _, prepared = _setup(tmp_path)
    receipt = _receipt(prepared, generation=0, result=InvocationStatus.REJECTED)
    store.complete_bundle_attempt(prepared, receipt)
    terminal = Store(tmp_path / "bundle.db").get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.COMPLETED
    assert terminal.counter_transport_send_count == 0
    assert terminal.generation_transport_send_count == 0
    assert terminal.total_external_request_count == 0
    with sqlite3.connect(tmp_path / "bundle.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 0
        )


def test_counter_intent_timeout_persists_unknown_receipt_and_permanent_claim(tmp_path):
    store, inputs, _, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    receipt = _receipt(intent, generation=0)
    store.complete_bundle_attempt(intent, receipt)
    reopened = Store(tmp_path / "bundle.db")
    terminal = reopened.get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.OUTCOME_UNKNOWN
    assert terminal.counter_status == "OUTCOME_UNKNOWN"
    assert terminal.counter_transport_send_count is None
    assert terminal.generation_transport_send_count == 0
    assert reopened.bundle_binding_for_attempt(terminal)[2:] == (None, None)
    assert reopened.get_receipt(receipt.fingerprint) == receipt


def test_generation_intent_timeout_persists_counter_fact_and_unknown_total(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    observation = _observed(inputs, target)
    counted = store.record_counter_observation(intent, observation)
    evidence = _evidence(inputs, observation)
    generation = store.claim_generation_intent(counted, evidence)
    receipt = _receipt(generation, generation=None)
    store.complete_bundle_attempt(generation, receipt)
    reopened = Store(tmp_path / "bundle.db")
    terminal = reopened.get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.OUTCOME_UNKNOWN
    assert terminal.counter_status == "COUNTED"
    assert terminal.counter_transport_send_count == 1
    assert terminal.generation_transport_send_count is None
    assert terminal.total_external_request_count is None
    assert reopened.bundle_binding_for_attempt(terminal)[2:] == (observation, evidence)


def test_claim_is_permanent_unique_and_stale_attempt_cannot_advance(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    rival = _prepared(inputs, request_id="request-rival")
    store.begin_attempt(rival)
    with pytest.raises(ValueError, match="BUDGET_ENVELOPE_CLAIMED"):
        store.claim_counter_intent(rival, inputs["envelope"], inputs["admission"])
    assert store.get_attempt_for_project(rival.project_id, rival.request_id) == rival
    with pytest.raises(ValueError, match="bundle counter observation invalid"):
        store.record_counter_observation(prepared, _observed(inputs, target))
    unknown = Store(tmp_path / "bundle.db").mark_bundle_unknown(intent)
    assert unknown.counter_status == "OUTCOME_UNKNOWN"
    assert unknown.counter_transport_send_count is None
    assert unknown.total_external_request_count is None
    assert Store(tmp_path / "bundle.db").bundle_binding_for_attempt(unknown)[2:] == (
        None,
        None,
    )
    with pytest.raises(ValueError):
        store.record_counter_observation(intent, _observed(inputs, target))


def test_observation_is_append_only_and_counter_only_terminal_keeps_raw(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    observation = _observed(inputs, target, b'{"error":"offline"}', status=500)
    counted = store.record_counter_observation(intent, observation)
    with pytest.raises(ValueError):
        store.record_counter_observation(intent, observation)
    with pytest.raises(ValueError, match="bundle generation intent invalid"):
        store.claim_generation_intent(
            counted, _evidence(inputs, _observed(inputs, target))
        )
    receipt = _receipt(counted, generation=0)
    store.complete_bundle_attempt(counted, receipt)
    reopened = Store(tmp_path / "bundle.db")
    terminal = reopened.get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.COMPLETED
    assert terminal.counter_status == "HTTP_ERROR"
    assert terminal.generation_transport_send_count == 0
    assert reopened.bundle_binding_for_attempt(terminal)[2] == observation


def test_generation_intent_unknown_retains_observation_and_never_reclaims(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    observation = _observed(inputs, target)
    counted = store.record_counter_observation(intent, observation)
    evidence = _evidence(inputs, observation)
    generation = store.claim_generation_intent(counted, evidence)
    unknown = Store(tmp_path / "bundle.db").mark_bundle_unknown(generation)
    assert unknown.status is AttemptStatus.OUTCOME_UNKNOWN
    assert unknown.counter_status == "COUNTED"
    assert unknown.counter_transport_send_count == 1
    assert unknown.generation_transport_send_count is None
    assert unknown.total_external_request_count is None
    assert store.bundle_binding_for_attempt(unknown)[2:] == (observation, evidence)
    with pytest.raises(ValueError):
        store.claim_generation_intent(counted, evidence)
    receipt = _receipt(unknown, generation=None)
    reopened = Store(tmp_path / "bundle.db")
    reopened.complete_bundle_attempt(unknown, receipt)
    with pytest.raises(ValueError):
        reopened.complete_bundle_attempt(unknown, receipt)
    assert reopened.get_receipt(receipt.fingerprint) == receipt
    with sqlite3.connect(tmp_path / "bundle.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 1
        )


def test_bound_violation_receipt_and_original_response_are_one_transaction(tmp_path):
    store, inputs, target, prepared = _setup(tmp_path)
    intent = store.claim_counter_intent(
        prepared, inputs["envelope"], inputs["admission"]
    )
    observation = _observed(inputs, target)
    counted = store.record_counter_observation(intent, observation)
    generation = store.claim_generation_intent(counted, _evidence(inputs, observation))
    receipt = _receipt(generation, generation=1, reconciliation="BOUND_VIOLATION")
    response = ModelResponse(
        content='{"candidate":"offline"}',
        model_name="gpt-5.6-luna",
        provider="openai",
        provenance=(),
        request_id=prepared.request_id,
    )
    with pytest.raises(ValueError, match="original missing"):
        store.complete_bundle_attempt(generation, receipt)
    assert (
        store.get_attempt_for_project(prepared.project_id, prepared.request_id)
        == generation
    )
    store.complete_bundle_attempt(generation, receipt, response)
    restored = Store(tmp_path / "bundle.db")
    terminal = restored.get_attempt_for_project(
        prepared.project_id, prepared.request_id
    )
    assert terminal.status is AttemptStatus.OUTCOME_UNKNOWN
    with sqlite3.connect(tmp_path / "bundle.db") as connection:
        outcome = connection.execute(
            "SELECT response_fingerprint,response FROM model_invocation_budget_outcomes "
            "WHERE project_id=? AND request_id=?",
            (prepared.project_id, prepared.request_id),
        ).fetchone()
    assert outcome is not None and outcome[0] == response.fingerprint


def test_two_concurrent_requests_cannot_occupy_same_envelope(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, inputs, _, first = _setup(tmp_path)
    second = _prepared(inputs, request_id="concurrent-second")
    store.begin_attempt(second)
    barrier = Barrier(2)

    def claim(attempt):
        barrier.wait(timeout=5)
        try:
            store.claim_counter_intent(attempt, inputs["envelope"], inputs["admission"])
            return "CLAIMED"
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(claim, item) for item in (first, second)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == ["BUDGET_ENVELOPE_CLAIMED", "CLAIMED"]
    with sqlite3.connect(tmp_path / "bundle.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 1
        )
