"""Offline consent lifecycle; fixture authority is never real account authority."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from pydantic import ValidationError

from embedded_copilot.model_gateway.governance import BoundModelIdentity
from embedded_copilot.model_gateway.invocation.consumption_contracts import (
    AccountAuthorityPremiseV1,
    ConsumptionCandidateSelectorV1,
    ConsumptionPreviewV1,
    ConsumptionScopeV1,
)
from embedded_copilot.model_gateway.invocation.consumption_store import (
    ConsumptionRejected,
    ConsumptionStoreMixin,
    initialize_consumption_schema,
    validate_phase_grant,
)
from embedded_copilot.model_gateway_storage import SQLiteModelGatewayStore

NOW = datetime(2026, 10, 17, tzinfo=UTC)
SHA = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64
OWNER = {
    "tenant_id": "tenant",
    "principal_id": "owner",
    "project_id": "project",
    "conversation_id": "conversation",
}


_StoreBase = (
    SQLiteModelGatewayStore
    if issubclass(SQLiteModelGatewayStore, ConsumptionStoreMixin)
    else type(
        "ConsumptionTestBase", (ConsumptionStoreMixin, SQLiteModelGatewayStore), {}
    )
)


class Store(_StoreBase):
    def __init__(self, path):
        super().__init__(path)
        with sqlite3.connect(self._path) as connection:
            initialize_consumption_schema(connection)


def scope(**changes):
    return ConsumptionScopeV1(
        **{
            **OWNER,
            "engineering_task_id": "task",
            "request_id": "request",
            "request_fingerprint": SHA,
            "snapshot_fingerprint": SHA,
            "task_authority_fingerprint": SHA,
            "candidate_context_fingerprint": SHA,
            "policy_fingerprint": SHA,
            "selected_model": BoundModelIdentity(
                provider_id="openai",
                endpoint_id="endpoint",
                credential_ref="credential:offline",
                model_profile_id="profile",
                model_id="gpt-5.6-luna",
            ),
            "connection_id": "connection",
            "connection_revision": "revision-1",
            "credential_revision": "revision-1",
            "endpoint_revision": "revision-1",
            "generation_bytes_sha256": SHA,
            "count_bytes_sha256": SHA,
            "generation_wire_target_sha256": SHA,
            "counter_wire_target_sha256": SHA,
            **changes,
        }
    )


def authority(current=None, **changes):
    current = current or scope()
    return AccountAuthorityPremiseV1(
        **{
            "authority_id": "offline-host",
            "association_id": "offline-association",
            "revision": "revision-1",
            "tenant_id": current.tenant_id,
            "principal_id": current.principal_id,
            "provider_id": "openai",
            "account_ref": "offline-account",
            "provider_project_ref": "offline-provider-project",
            "relationship": "OWNER",
            "connection_id": current.connection_id,
            "connection_revision": current.connection_revision,
            "credential_revision": current.credential_revision,
            "endpoint_revision": current.endpoint_revision,
            "scope_fingerprint": current.fingerprint,
            "source_head_fingerprint": SHA,
            "evidence_identity": "offline-evidence",
            "raw_sha256": SHA,
            "verified_by": "offline-verifier",
            "observed_at": NOW - timedelta(minutes=1),
            "valid_until": NOW + timedelta(minutes=30),
            **changes,
        }
    )


def preview(current=None, **changes):
    current = current or scope()
    return ConsumptionPreviewV1(
        **{
            "preview_id": "preview",
            "scope": current,
            "candidate_selector": ConsumptionCandidateSelectorV1(
                request_id=current.request_id,
                plan_id="plan",
                phase_id="phase",
                plan_task_id="plan-task",
            ),
            "created_at": NOW,
            "not_after": NOW + timedelta(minutes=10),
            "generation_bytes_length": 100,
            "count_bytes_length": 80,
            **changes,
        }
    )


def confirm(store, item=None, current=None, premise="default", **changes):
    item = item or preview()
    current = current or item.scope
    premise = authority(current) if premise == "default" else premise
    return store.confirm_consumption_grant(
        preview_id=item.preview_id,
        preview_fingerprint=item.fingerprint,
        current_scope=current,
        account_authority=premise,
        now=NOW,
        consent=True,
        **OWNER,
        **changes,
    )


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "model-gateway.sqlite3")


def test_exact_fixed_scope_and_immutable_collections():
    item = preview()
    assert item.scope.model_id == "gpt-5.6-luna"
    with pytest.raises(ValidationError):
        item.preview_id = "different"
    for extra in (
        {"max_cost_decimal": "1.00"},
        {"max_requests": 3},
        {"service_tier": "flex"},
        {"raw_prompt": "private"},
    ):
        with pytest.raises(ValidationError):
            scope(**extra)


def test_ttl_naive_and_fingerprint_tamper_rejected():
    for changes in (
        {"not_after": NOW + timedelta(seconds=601)},
        {"created_at": NOW.replace(tzinfo=None)},
        {"not_after": NOW},
        {"fingerprint": OTHER},
    ):
        with pytest.raises((ValidationError, ValueError)):
            preview(**changes)


def test_roundtrip_idempotence_and_no_budget_side_effects(store):
    item = preview()
    store.save_consumption_preview(item, now=NOW)
    grant = confirm(store, item)
    assert confirm(store, item) == grant
    assert store.get_consumption_grant(grant.grant_id, **OWNER) == grant
    assert grant.not_after == item.not_after
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grant_events"
            ).fetchone()[0]
            == 1
        )
    assert Store(store._path).get_consumption_grant(grant.grant_id, **OWNER) == grant


@pytest.mark.parametrize(
    "kind", ["missing", "wrong-owner", "expired", "wrong-scope", "wrong-revision"]
)
def test_missing_or_stale_authority_never_issues(store, kind):
    store.save_consumption_preview(preview(), now=NOW)
    premise = (
        None
        if kind == "missing"
        else authority(
            **{
                "wrong-owner": {"principal_id": "foreign"},
                "expired": {"valid_until": NOW},
                "wrong-scope": {"scope_fingerprint": OTHER},
                "wrong-revision": {"connection_revision": "revision-2"},
            }.get(kind, {})
        )
    )
    with pytest.raises(ConsumptionRejected):
        confirm(store, premise=premise)
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grants"
            ).fetchone()[0]
            == 0
        )


def test_false_consent_and_scope_changes_reject(store):
    store.save_consumption_preview(preview(), now=NOW)
    for current in (
        scope(policy_fingerprint=OTHER),
        scope(candidate_context_fingerprint=OTHER),
        scope(request_id="foreign"),
    ):
        with pytest.raises(ConsumptionRejected):
            confirm(store, current=current)
    kwargs = dict(
        preview_id="preview",
        preview_fingerprint=preview().fingerprint,
        current_scope=scope(),
        account_authority=authority(),
        now=NOW,
        consent=False,
        **OWNER,
    )
    with pytest.raises(ConsumptionRejected):
        store.confirm_consumption_grant(**kwargs)


def test_logical_scope_blocks_renamed_request_and_envelope(store):
    first = preview()
    renamed = scope(
        request_id="request-2", request_fingerprint=OTHER, snapshot_fingerprint=OTHER
    )
    assert renamed.logical_scope_fingerprint == first.scope.logical_scope_fingerprint
    second = preview(renamed, preview_id="preview-2")
    store.save_consumption_preview(first, now=NOW)
    store.save_consumption_preview(second, now=NOW)
    confirm(store, first)
    with pytest.raises(ConsumptionRejected, match="GRANT_SCOPE_CONFLICT"):
        confirm(store, second)


def test_revoke_cas_idempotence_and_history(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    revoked = store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=NOW + timedelta(seconds=2), **OWNER
    )
    assert revoked.status == "REVOKED" and revoked.revision == 2
    assert (
        store.revoke_consumption_grant(
            grant.grant_id, expected_revision=1, now=NOW + timedelta(seconds=3), **OWNER
        )
        == revoked
    )
    with pytest.raises(ConsumptionRejected, match="GRANT_REVISION_CONFLICT"):
        store.revoke_consumption_grant(
            grant.grant_id, expected_revision=3, now=NOW, **OWNER
        )
    assert confirm(store) == revoked


def test_owner_isolation_read_and_revoke(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    foreign = {**OWNER, "principal_id": "foreign"}
    for action in (
        lambda: store.get_consumption_grant(grant.grant_id, **foreign),
        lambda: store.get_consumption_preview("preview", **foreign),
        lambda: store.revoke_consumption_grant(
            grant.grant_id, expected_revision=1, now=NOW, **foreign
        ),
    ):
        with pytest.raises(ConsumptionRejected, match="CONSUMPTION_NOT_FOUND"):
            action()


def test_phase_requires_caller_transaction_and_current_head(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    with sqlite3.connect(store._path) as connection:
        with pytest.raises(ConsumptionRejected, match="GRANT_TRANSACTION_REQUIRED"):
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="COUNTER",
                now=NOW,
            )
        connection.execute("BEGIN IMMEDIATE")
        assert (
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="COUNTER",
                now=NOW,
            )
            == grant
        )
    store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=NOW, **OWNER
    )
    with sqlite3.connect(store._path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="GENERATION",
                now=NOW,
            )


def test_concurrent_confirm_only_one_immutable_issue(store):
    store.save_consumption_preview(preview(), now=NOW)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: confirm(store), range(8)))
    assert len({item.grant_id for item in results}) == 1
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grant_events"
            ).fetchone()[0]
            == 1
        )


def test_tamper_rejected_without_repair_or_rewrite(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    with sqlite3.connect(store._path) as connection:
        before = connection.execute(
            "SELECT payload FROM model_consumption_grants"
        ).fetchone()[0]
        changed = json.loads(before)
        changed["revision"] = 3
        connection.execute(
            "UPDATE model_consumption_grants SET payload=?", (json.dumps(changed),)
        )
    with pytest.raises(ConsumptionRejected, match="CONSUMPTION_RECORD_INVALID"):
        store.get_consumption_grant(grant.grant_id, **OWNER)


@pytest.mark.parametrize("field", ["account", "expiry", "scope", "role"])
def test_phase_currentness_is_required_for_each_role(store, field):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    kwargs = {
        "envelope": grant.envelope,
        "scope": scope(),
        "account_authority": authority(),
        "phase": "GENERATION",
        "now": NOW,
    }
    if field == "account":
        kwargs["account_authority"] = authority(source_head_fingerprint=OTHER)
    elif field == "expiry":
        kwargs["now"] = grant.not_after
    elif field == "scope":
        kwargs["scope"] = scope(candidate_context_fingerprint=OTHER)
    else:
        kwargs["phase"] = "CHAT"
    with sqlite3.connect(store._path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ConsumptionRejected):
            validate_phase_grant(connection, **kwargs)


def test_authority_interval_caps_grant_without_extending_preview(store):
    item = preview()
    store.save_consumption_preview(item, now=NOW)
    grant = confirm(store, premise=authority(valid_until=NOW + timedelta(seconds=30)))
    assert grant.not_after == NOW + timedelta(seconds=30)
    assert grant.envelope.not_after == grant.not_after


def test_delegate_requires_actual_reference_and_fixed_roles():
    with pytest.raises(ValidationError):
        authority(relationship="DELEGATE")
    assert (
        authority(
            relationship="DELEGATE", delegation_ref="offline-mandate"
        ).delegation_ref
        == "offline-mandate"
    )
    for change in (
        {"status": "SELF_DECLARED"},
        {"roles": ("RESPONSES_GENERATION",)},
        {"max_cost_decimal": "0.20"},
    ):
        with pytest.raises(ValidationError):
            authority(**change)


def test_preview_immutable_and_confirm_expiry(store):
    item = preview()
    store.save_consumption_preview(item, now=NOW)
    with pytest.raises(ConsumptionRejected, match="PREVIEW_STALE"):
        store.save_consumption_preview(preview(generation_bytes_length=101), now=NOW)
    with pytest.raises(ConsumptionRejected, match="PREVIEW_EXPIRED"):
        store.confirm_consumption_grant(
            preview_id="preview",
            preview_fingerprint=item.fingerprint,
            current_scope=item.scope,
            account_authority=authority(),
            now=item.not_after,
            consent=True,
            **OWNER,
        )


def test_issue_event_failure_rolls_back_grant(store):
    store.save_consumption_preview(preview(), now=NOW)
    with sqlite3.connect(store._path) as connection:
        connection.execute(
            "CREATE TRIGGER offline_fail_issue BEFORE INSERT ON model_consumption_grant_events BEGIN SELECT RAISE(ABORT,'offline fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        confirm(store)
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grants"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grant_events"
            ).fetchone()[0]
            == 0
        )


def test_revoke_event_failure_preserves_active_head(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    with sqlite3.connect(store._path) as connection:
        connection.execute(
            "CREATE TRIGGER offline_fail_revoke BEFORE INSERT ON model_consumption_grant_events WHEN NEW.revision=2 BEGIN SELECT RAISE(ABORT,'offline fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.revoke_consumption_grant(
            grant.grant_id, expected_revision=1, now=NOW, **OWNER
        )
    assert store.get_consumption_grant(grant.grant_id, **OWNER) == grant


@pytest.mark.parametrize(
    "tamper", ["delete-event", "zero-fingerprint", "duplicate-key", "changed-event"]
)
def test_event_and_raw_integrity_no_default_repair(store, tamper):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    with sqlite3.connect(store._path) as connection:
        if tamper == "delete-event":
            connection.execute("DELETE FROM model_consumption_grant_events")
        elif tamper == "zero-fingerprint":
            raw = json.loads(
                connection.execute(
                    "SELECT payload FROM model_consumption_grants"
                ).fetchone()[0]
            )
            raw["fingerprint"] = "sha256:" + "0" * 64
            connection.execute(
                "UPDATE model_consumption_grants SET payload=?", (json.dumps(raw),)
            )
        elif tamper == "duplicate-key":
            raw = connection.execute(
                "SELECT payload FROM model_consumption_grants"
            ).fetchone()[0]
            raw = '{"revision":1,' + raw[1:]
            connection.execute("UPDATE model_consumption_grants SET payload=?", (raw,))
        else:
            raw = json.loads(
                connection.execute(
                    "SELECT payload FROM model_consumption_grant_events"
                ).fetchone()[0]
            )
            raw["actor"] = "foreign"
            connection.execute(
                "UPDATE model_consumption_grant_events SET payload=?",
                (json.dumps(raw),),
            )
    with pytest.raises(ConsumptionRejected, match="CONSUMPTION_RECORD_INVALID"):
        store.get_consumption_grant(grant.grant_id, **OWNER)


def test_new_tables_preserve_old_rows_and_state_schema(store):
    old = ' { "historical": true } '
    with sqlite3.connect(store._path) as connection:
        connection.execute(
            "INSERT INTO model_gateway_state(id,schema_version,fingerprint,payload) VALUES(1,2,?,?)",
            (SHA, old),
        )
        connection.execute(
            "INSERT INTO model_invocation_attempts(project_id,request_id,payload) VALUES('legacy','unknown',?)",
            (old,),
        )
        before = connection.execute("SELECT * FROM model_gateway_state").fetchall()
    reopened = Store(store._path)
    reopened.save_consumption_preview(preview(), now=NOW)
    confirm(reopened)
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute("SELECT * FROM model_gateway_state").fetchall() == before
        )
        assert (
            connection.execute(
                "SELECT payload FROM model_invocation_attempts WHERE project_id='legacy'"
            ).fetchone()[0]
            == old
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_initializer_refuses_separate_blank_database():
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises(ConsumptionRejected, match="GRANT_STORE_IDENTITY_INVALID"):
            initialize_consumption_schema(connection)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            == []
        )


def test_schema_failure_rolls_back_only_new_ddl_and_keeps_outer_transaction():
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("CREATE TABLE IF NOT EXISTS model_consumption_grants"):
                raise sqlite3.OperationalError("offline migration fault")
            return super().execute(sql, parameters)

    with sqlite3.connect(":memory:", factory=FailingConnection) as connection:
        for name in (
            "model_gateway_state",
            "model_invocation_receipts",
            "model_invocation_attempts",
            "model_invocation_budget_claims",
            "model_invocation_budget_outcomes",
        ):
            connection.execute(f"CREATE TABLE {name} (value TEXT)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO model_gateway_state VALUES('legacy-outer-write')"
        )
        with pytest.raises(sqlite3.OperationalError, match="offline migration fault"):
            initialize_consumption_schema(connection)
        assert connection.in_transaction
        assert (
            connection.execute("SELECT value FROM model_gateway_state").fetchone()[0]
            == "legacy-outer-write"
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'model_consumption_%'"
            ).fetchall()
            == []
        )
        connection.rollback()
        assert (
            connection.execute("SELECT count(*) FROM model_gateway_state").fetchone()[0]
            == 0
        )


def test_intent_first_then_revoke_serializes_and_next_phase_rejects(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    intent_holds_lock, release_intent, revoke_entered = Event(), Event(), Event()

    def intent():
        with sqlite3.connect(store._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="COUNTER",
                now=NOW,
            )
            intent_holds_lock.set()
            assert release_intent.wait(3)
            # Isolated caller marker in the EXISTING claim table, not a new
            # role ledger. Root integration tests cover actual typed intents.
            connection.execute(
                "INSERT INTO model_invocation_budget_claims(envelope_id,tenant_id,project_id,request_id,reservation_id,envelope,evidence) VALUES(?,?,?,?,?,?,?)",
                (
                    grant.envelope.envelope_id,
                    "tenant",
                    "project",
                    "request",
                    "offline-reservation",
                    grant.envelope.model_dump_json(),
                    "{}",
                ),
            )

    def revoke():
        revoke_entered.set()
        return store.revoke_consumption_grant(
            grant.grant_id, expected_revision=1, now=NOW, **OWNER
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        intent_future = pool.submit(intent)
        assert intent_holds_lock.wait(3)
        revoke_future = pool.submit(revoke)
        assert revoke_entered.wait(3)
        try:
            with pytest.raises(TimeoutError):
                revoke_future.result(timeout=0.05)
        finally:
            release_intent.set()
        intent_future.result(timeout=3)
        assert revoke_future.result(timeout=3).status == "REVOKED"
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 1
        )
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="GENERATION",
                now=NOW,
            )


def test_revoke_first_prevents_caller_intent_write(store):
    store.save_consumption_preview(preview(), now=NOW)
    grant = confirm(store)
    store.revoke_consumption_grant(
        grant.grant_id, expected_revision=1, now=NOW, **OWNER
    )
    with sqlite3.connect(store._path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ConsumptionRejected, match="GRANT_REVOKED"):
            validate_phase_grant(
                connection,
                envelope=grant.envelope,
                scope=scope(),
                account_authority=authority(),
                phase="COUNTER",
                now=NOW,
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 0
        )
