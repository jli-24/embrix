"""Formal source/phase wiring with synthetic account and provider evidence only."""

import sqlite3
from datetime import timedelta

import pytest

from embedded_copilot.model_gateway.invocation.consumption_contracts import (
    ConsumptionCandidateSelectorV1,
    ConsumptionPreviewV1,
)
from embedded_copilot.model_gateway.invocation.consumption_source import (
    ConsumptionGrantSource,
    scope_from_context,
)
from embedded_copilot.model_gateway.invocation.consumption_store import (
    ConsumptionRejected,
)

if __name__.startswith("tests.model_gateway."):
    from tests.model_gateway.test_consumption_core import Store, authority, scope
else:
    # Isolated candidate directory; the installed repository uses the package
    # import above. This fallback is only a test harness seam.
    from test_consumption_core import Store, authority, scope

from embedded_copilot.model_gateway.invocation.budget import digest
from embedded_copilot.model_gateway.invocation.bundle_store import (
    CounterBundleStoreMixin,
)
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    QualifiedInvocationBudgetProducer,
)
from tests.model_gateway.test_counter_bundle_core import _case, _evidence, _observed
from tests.model_gateway.test_counter_bundle_store import _prepared
from tests.model_gateway.test_qualified_bundle_producer import _context, _producer

PhaseStore = (
    Store
    if issubclass(Store, CounterBundleStoreMixin)
    else type("PhaseStore", (CounterBundleStoreMixin, Store), {})
)


class Account:
    def __init__(self, premise):
        self.premise = premise
        self.authority_id = premise.authority_id
        self.reads = 0

    def read(self, _scope):
        self.reads += 1
        return self.premise


def prepared(tmp_path):
    inputs, target = _case()
    kwargs = _context(inputs)
    context = QualifiedInvocationBudgetProducer._context(**kwargs)
    current, _ = scope_from_context(context, principal_id="owner")
    owner = {
        name: getattr(current, name)
        for name in ("tenant_id", "principal_id", "project_id", "conversation_id")
    }
    item = ConsumptionPreviewV1(
        preview_id="preview-formal",
        scope=current,
        candidate_selector=ConsumptionCandidateSelectorV1(
            request_id=current.request_id,
            plan_id="plan",
            phase_id="phase",
            plan_task_id="plan-task",
        ),
        created_at=context.now,
        not_after=context.now + timedelta(minutes=10),
        count_bytes_length=80,
        generation_bytes_length=len(context.final_transport_bytes),
    )
    store = PhaseStore(tmp_path / "model-gateway.sqlite3")
    store.save_consumption_preview(item, now=context.now)
    account = Account(
        authority(
            current,
            observed_at=context.now - timedelta(seconds=1),
            valid_until=context.now + timedelta(hours=1),
        )
    )
    grant = store.confirm_consumption_grant(
        preview_id=item.preview_id,
        preview_fingerprint=item.fingerprint,
        current_scope=current,
        account_authority=account.premise,
        now=context.now,
        consent=True,
        **owner,
    )
    inputs["envelope"] = grant.envelope
    producer, _sources = _producer(inputs)
    formal = ConsumptionGrantSource(store, account)
    producer._sources = (formal, *producer._sources[1:])
    envelope, admission = producer.prepare_counter(**kwargs)
    inputs.update(envelope=envelope, admission=admission)
    return (
        store,
        inputs,
        target,
        kwargs,
        context,
        current,
        owner,
        account,
        grant,
        producer,
        formal,
    )


def test_scope_projection_matches_existing_bundle_and_roundtrip(tmp_path):
    (
        store,
        inputs,
        _,
        kwargs,
        context,
        current,
        owner,
        account,
        grant,
        producer,
        formal,
    ) = prepared(tmp_path)
    import base64

    assert current.count_bytes_sha256 == inputs["admission"].count_request_sha256
    assert (
        digest(base64.b64decode(inputs["admission"].count_request_base64))
        == current.count_bytes_sha256
    )
    assert formal.read(context).envelope == grant.envelope
    scope, premise = producer.phase_authorization(
        grant.envelope, principal_id="owner", **kwargs
    )
    assert (scope, premise) == (current, account.premise)
    assert producer.consumption_store is store
    assert account.reads >= 3
    restored = Store(store._path).get_consumption_preview(grant.preview_id, **owner)
    assert restored.candidate_selector.request_id == current.request_id


def test_absent_current_source_or_principal_and_drift_close(tmp_path):
    store, _, _, kwargs, context, current, _, account, grant, _, formal = prepared(
        tmp_path
    )
    with pytest.raises(ConsumptionRejected, match="ACCOUNT_AUTHORITY_UNAVAILABLE"):
        ConsumptionGrantSource(store).read(context)
    for principal in (None, "foreign"):
        with pytest.raises(ConsumptionRejected):
            formal.phase_authorization(grant.envelope, principal_id=principal, **kwargs)
    account.premise = authority(
        current,
        revision="changed",
        observed_at=context.now - timedelta(seconds=1),
        valid_until=context.now + timedelta(hours=1),
    )
    with pytest.raises(ConsumptionRejected, match="ACCOUNT_AUTHORITY_STALE"):
        formal.read(context)


def test_real_claim_transaction_rechecks_revocation_after_source_read(tmp_path):
    store, inputs, _, kwargs, _, _, owner, _, grant, producer, _ = prepared(tmp_path)
    attempt = _prepared(inputs)
    store.begin_attempt(attempt)
    auth = producer.phase_authorization(grant.envelope, principal_id="owner", **kwargs)
    store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=inputs["now"], **owner
    )
    with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
        store.claim_counter_intent(
            attempt,
            grant.envelope,
            inputs["admission"],
            _consumption_authorization=auth,
            _consumption_now=inputs["now"],
        )
    assert (
        store.get_attempt_for_project(attempt.project_id, attempt.request_id) == attempt
    )
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 0
        )


def test_count_observed_then_revoke_keeps_claim_and_blocks_generation(tmp_path):
    store, inputs, target, kwargs, _, _, owner, _, grant, producer, _ = prepared(
        tmp_path
    )
    attempt = _prepared(inputs)
    store.begin_attempt(attempt)
    auth = producer.phase_authorization(grant.envelope, principal_id="owner", **kwargs)
    attempt = store.claim_counter_intent(
        attempt,
        grant.envelope,
        inputs["admission"],
        _consumption_authorization=auth,
        _consumption_now=inputs["now"],
    )
    observation = _observed(inputs, target)
    observed = store.record_counter_observation(attempt, observation)
    evidence = _evidence(inputs, observation)
    store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=inputs["now"], **owner
    )
    with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
        store.claim_generation_intent(
            observed,
            evidence,
            _consumption_authorization=auth,
            _consumption_now=inputs["now"],
        )
    assert (
        store.get_attempt_for_project(attempt.project_id, attempt.request_id)
        == observed
    )
    assert observed.generation_transport_send_count == 0
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 1
        )


def test_selector_versions_validation_and_json_recovery(tmp_path):
    base = {
        "request_id": "request",
        "plan_id": "plan",
        "phase_id": "phase",
        "plan_task_id": "task",
    }
    for changes in (
        {"selection_schema_version": True},
        {"parent_choices": (("slot", "source", "sha256:" + "1" * 64),)},
        {"selection_schema_version": 2},
        {
            "selection_schema_version": 3,
            "selection_preview_fingerprint": "sha256:" + "1" * 64,
        },
    ):
        with pytest.raises(ValueError):
            ConsumptionCandidateSelectorV1(**base, **changes)
    selector = ConsumptionCandidateSelectorV1(
        **base,
        selection_schema_version=3,
        selection_preview_fingerprint="sha256:" + "1" * 64,
        parent_choices=(("slot", "source", "sha256:" + "1" * 64),),
    )
    assert (
        ConsumptionCandidateSelectorV1.model_validate_json(selector.model_dump_json())
        == selector
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_runtime_passes_consent_and_current_clock_to_both_claims(
    monkeypatch, asynchronous
):
    import asyncio

    from tests.model_gateway.test_counter_bundle_runtime import _harness

    service, request, caller, state, args, _ = _harness(monkeypatch)
    caller.requester = "owner"
    source, store = service._budget_bundle_source, service._receipt_store
    source.consumption_store = store
    reads, claims = [], []

    def current(_envelope, *, principal_id, **kwargs):
        assert principal_id == "owner"
        reads.append(kwargs["now"])
        return scope(), authority()

    source.phase_authorization = current
    count, generation = store.claim_counter_intent, store.claim_generation_intent

    def claim_count(*args, **kwargs):
        claims.append(kwargs)
        return count(*args)

    def claim_generation(*args, **kwargs):
        claims.append(kwargs)
        return generation(*args)

    store.claim_counter_intent, store.claim_generation_intent = (
        claim_count,
        claim_generation,
    )
    guard = service._bundle_guard(
        request, object(), object(), caller, state, asynchronous=asynchronous
    )
    asyncio.run(guard(*args)) if asynchronous else guard(*args)
    assert len(reads) == len(claims) == 2
    for kwargs in claims:
        assert kwargs["_consumption_authorization"] == (scope(), authority())
        assert kwargs["_consumption_now"] == service._clock()


@pytest.mark.parametrize(
    "failure",
    ["foreign-store", "missing-principal", "foreign-principal", "source-failure"],
)
def test_runtime_consent_hook_failure_precedes_intent_and_count(monkeypatch, failure):

    from embedded_copilot.model_gateway.exceptions import ModelGatewayRejected
    from tests.model_gateway.test_counter_bundle_runtime import _harness

    service, request, caller, state, args, events = _harness(monkeypatch)
    caller.requester = (
        None
        if failure == "missing-principal"
        else "foreign"
        if failure == "foreign-principal"
        else "owner"
    )
    source = service._budget_bundle_source
    source.consumption_store = (
        object() if failure == "foreign-store" else service._receipt_store
    )

    def current(_envelope, *, principal_id, **_kwargs):
        if principal_id != "owner" or failure == "source-failure":
            raise ConsumptionRejected("GRANT_SCOPE_STALE")
        return scope(), authority()

    source.phase_authorization = current
    guard = service._bundle_guard(request, object(), object(), caller, state)
    with pytest.raises(ModelGatewayRejected):
        guard(*args)
    assert "claim_counter" not in events
    assert service._gateway.posts == 0


def formal_chain(tmp_path, monkeypatch, *, revoke_after_count=False):
    from embedded_copilot.model_gateway.invocation.contracts import (
        ModelInvocationRequest,
    )
    from tests.model_gateway.test_counter_bundle_integration import chain

    state = {}

    def hook(_sources, _authority):
        if revoke_after_count:
            store.revoke_consumption_grant(
                state["grant"].grant_id,
                expected_revision=1,
                now=service._clock(),
                **state["owner"],
            )

    service, store, request, caller, events = chain(tmp_path, monkeypatch, hook=hook)
    # Stage harness may have imported the repository store before the isolated
    # new mixin was loaded. Use the same DB through the candidate-compatible
    # store, also valid when run as normal installed repository tests.
    store = Store(store._path)
    service._receipt_store = store
    request = ModelInvocationRequest.model_validate(
        {
            **request.model_dump(mode="python", exclude={"fingerprint"}),
            "task_type": "HARDWARE_SELECTION_PROPOSAL",
        }
    )
    caller.requester = "owner"
    context = service.prepare_consumption_context(request, authority_source=caller)
    current, counted = scope_from_context(context, principal_id="owner")
    owner = {
        name: getattr(current, name)
        for name in ("tenant_id", "principal_id", "project_id", "conversation_id")
    }
    item = ConsumptionPreviewV1(
        preview_id="preview-service",
        scope=current,
        candidate_selector=ConsumptionCandidateSelectorV1(
            request_id=current.request_id,
            plan_id="plan",
            phase_id="phase",
            plan_task_id="plan-task",
        ),
        created_at=context.now,
        not_after=context.now + timedelta(minutes=10),
        generation_bytes_length=len(context.final_transport_bytes),
        count_bytes_length=len(counted),
    )
    store.save_consumption_preview(item, now=context.now)
    account = Account(
        authority(
            current,
            observed_at=context.now - timedelta(seconds=1),
            valid_until=context.now + timedelta(hours=1),
        )
    )
    grant = store.confirm_consumption_grant(
        preview_id=item.preview_id,
        preview_fingerprint=item.fingerprint,
        current_scope=current,
        account_authority=account.premise,
        now=context.now,
        consent=True,
        **owner,
    )
    source = ConsumptionGrantSource(store, account)
    service._budget_bundle_source._sources = (
        source,
        *service._budget_bundle_source._sources[1:],
    )
    state.update(grant=grant, owner=owner)
    assert events == []
    return service, store, request, caller, events, grant, account


@pytest.mark.parametrize("asynchronous", [False, True])
def test_formal_service_chain_and_historical_restart_no_new_posts(
    tmp_path, monkeypatch, asynchronous
):
    from tests.model_gateway.test_counter_bundle_integration import run

    service, store, request, caller, events, grant, _ = formal_chain(
        tmp_path, monkeypatch
    )
    result = run(service, request, caller, asynchronous)
    assert result.status.value == "SUCCESS", result
    assert (
        result.receipt.counter_transport_send_count,
        result.receipt.generation_transport_send_count,
        result.receipt.total_external_request_count,
    ) == (1, 1, 2)
    assert len(events) == 2
    # Historical receipt recovery happens before current qualification; it does
    # not renew/replay a consumed grant even after current revocation.
    owner = {
        name: getattr(grant.scope, name)
        for name in ("tenant_id", "principal_id", "project_id", "conversation_id")
    }
    store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=service._clock(), **owner
    )
    service._receipt_store = type(store)(store._path)
    assert run(service, request, caller, asynchronous).receipt == result.receipt
    assert len(events) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_formal_service_count_then_revoke_preserves_count_and_stops_generation(
    tmp_path, monkeypatch, asynchronous
):
    from tests.model_gateway.test_counter_bundle_integration import run

    service, store, request, caller, events, _, _ = formal_chain(
        tmp_path, monkeypatch, revoke_after_count=True
    )
    result = run(service, request, caller, asynchronous)
    assert result.status.value != "SUCCESS"
    assert len(events) == 1
    assert result.receipt.counter_transport_send_count == 1
    assert result.receipt.generation_transport_send_count == 0
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    assert store.bundle_binding_for_attempt(attempt)[2].raw_response_base64
    assert run(service, request, caller, asynchronous).receipt == result.receipt
    assert len(events) == 1


def test_formal_service_foreign_current_principal_stops_before_count(
    tmp_path, monkeypatch
):
    from tests.model_gateway.test_counter_bundle_integration import run

    service, _, request, caller, events, _, _ = formal_chain(tmp_path, monkeypatch)
    caller.requester = "foreign"
    result = run(service, request, caller)
    assert result.status.value != "SUCCESS"
    assert events == []


def test_formal_prepare_renamed_request_cannot_mint_second_scope_grant(
    tmp_path, monkeypatch
):
    from embedded_copilot.model_gateway.invocation.contracts import (
        ModelInvocationRequest,
    )

    service, store, request, caller, events, grant, _ = formal_chain(
        tmp_path, monkeypatch
    )
    renamed = ModelInvocationRequest.model_validate(
        {
            **request.model_dump(mode="python", exclude={"fingerprint"}),
            "request_id": "renamed-request",
            "trace_context": type(request.trace_context)(trace_id="renamed-trace"),
        }
    )
    context = service.prepare_consumption_context(renamed, authority_source=caller)
    current, counter = scope_from_context(context, principal_id="owner")
    assert current.logical_scope_fingerprint == grant.scope.logical_scope_fingerprint
    owner = {
        name: getattr(current, name)
        for name in ("tenant_id", "principal_id", "project_id", "conversation_id")
    }
    item = ConsumptionPreviewV1(
        preview_id="renamed-preview",
        scope=current,
        candidate_selector=ConsumptionCandidateSelectorV1(
            request_id=current.request_id,
            plan_id="plan",
            phase_id="phase",
            plan_task_id="plan-task",
        ),
        created_at=context.now,
        not_after=context.now + timedelta(minutes=10),
        generation_bytes_length=len(context.final_transport_bytes),
        count_bytes_length=len(counter),
    )
    store.save_consumption_preview(item, now=context.now)
    premise = authority(
        current,
        observed_at=context.now - timedelta(seconds=1),
        valid_until=context.now + timedelta(hours=1),
    )
    with pytest.raises(ConsumptionRejected, match="GRANT_SCOPE_CONFLICT"):
        store.confirm_consumption_grant(
            preview_id=item.preview_id,
            preview_fingerprint=item.fingerprint,
            current_scope=current,
            account_authority=premise,
            now=context.now,
            consent=True,
            **owner,
        )
    assert events == []


def test_formal_account_reader_identity_cannot_impersonate_other_authority(tmp_path):
    _, _, _, _, context, _, _, account, _, _, formal = prepared(tmp_path)
    account.authority_id = "foreign-host"
    with pytest.raises(ConsumptionRejected, match="ACCOUNT_AUTHORITY_UNAVAILABLE"):
        formal.read(context)


@pytest.mark.parametrize("change", ["revoked", "revision"])
def test_definite_account_change_permanently_invalidates_even_after_aba(
    tmp_path, change
):
    store, _, _, _, context, current, owner, account, grant, _, formal = prepared(
        tmp_path
    )
    original = account.premise
    changes = (
        {"status": "REVOKED"} if change == "revoked" else {"revision": "revision-2"}
    )
    account.premise = authority(
        current,
        observed_at=original.observed_at,
        valid_until=original.valid_until,
        **changes,
    )
    with pytest.raises(ConsumptionRejected, match="ACCOUNT_AUTHORITY_STALE"):
        formal.read(context)
    revoked = store.get_consumption_grant(grant.grant_id, **owner)
    assert (
        revoked.status == "REVOKED"
        and revoked.revocation_reason == "ACCOUNT_AUTHORITY_INVALIDATED"
    )
    account.premise = original
    with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
        formal.read(context)
    assert Store(store._path).get_consumption_grant(grant.grant_id, **owner) == revoked
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grant_events"
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("failure", ["none", "exception", "expired", "foreign-source"])
def test_unavailability_is_not_fabricated_revocation(tmp_path, failure):
    store, _, _, _, context, current, owner, account, grant, _, formal = prepared(
        tmp_path
    )
    original = account.premise
    if failure == "none":
        account.premise = None
    elif failure == "exception":

        def broken(_scope):
            raise RuntimeError("offline temporary failure")

        account.read = broken
    elif failure == "expired":
        account.premise = authority(
            current, observed_at=original.observed_at, valid_until=context.now
        )
    else:
        account.authority_id = "foreign"
    with pytest.raises(ConsumptionRejected):
        formal.read(context)
    assert store.get_consumption_grant(grant.grant_id, **owner) == grant
