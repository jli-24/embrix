"""G-A records in SQLiteModelGatewayStore's DB; never an execution ledger.

No constructor creates a database. Composition initializes these three tables
on the existing store connection. Phase validation must share the caller's
BEGIN IMMEDIATE transaction with the existing budget intent write.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from embedded_copilot.model_gateway.governance import EngineeringStage
from embedded_copilot.model_gateway.invocation.bundle_contracts import (
    SingleInvocationBudgetEnvelopeV2,
)
from embedded_copilot.model_gateway.invocation.consumption_contracts import (
    AccountAuthorityPremiseV1,
    ConsumptionGrantEventV1,
    ConsumptionGrantV1,
    ConsumptionPreviewV1,
    ConsumptionScopeV1,
)
from embedded_copilot.model_gateway.invocation.consumption_store import (
    ConsumptionRejected,
    _account,
    _checked,
    _now,
    _reject,
)

_Record = TypeVar("_Record", bound=BaseModel)








def _encode(value: BaseModel) -> str:
    from embedded_copilot.model_gateway_storage import _assert_secret_free

    payload = value.model_dump(mode="json")
    _assert_secret_free(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )




def _decode(kind: type[_Record], payload: str) -> _Record:
    try:
        # Duplicate JSON keys are rejected, rather than normalizing tampering.
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        parsed = json.loads(
            payload, object_pairs_hook=pairs, parse_constant=lambda _: _reject()
        )
        result = kind.model_validate_json(payload)
        # Persisted records contain the complete signed shape. Never treat a
        # zero fingerprint/default-omitted payload as a newly constructed DTO.
        if parsed != result.model_dump(mode="json"):
            _reject()
        return result
    except Exception as error:
        raise ConsumptionRejected("CONSUMPTION_RECORD_INVALID") from error


def initialize_consumption_schema(connection: sqlite3.Connection) -> None:
    """Only add G-A tables; never touch historical state/budget payloads."""
    required = {
        "model_gateway_state",
        "model_invocation_receipts",
        "model_invocation_attempts",
        "model_invocation_budget_claims",
        "model_invocation_budget_outcomes",
    }
    present = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not required <= present:
        _reject("GRANT_STORE_IDENTITY_INVALID")
    connection.execute("SAVEPOINT consumption_schema_v1")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS model_consumption_previews (preview_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS model_consumption_grants (grant_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, request_id TEXT NOT NULL, logical_scope_fingerprint TEXT NOT NULL UNIQUE, envelope_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, UNIQUE(tenant_id,project_id,request_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS model_consumption_grant_events (grant_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(grant_id,revision))"
        )
    except sqlite3.Error:
        connection.execute("ROLLBACK TO consumption_schema_v1")
        connection.execute("RELEASE consumption_schema_v1")
        raise
    connection.execute("RELEASE consumption_schema_v1")


def _owner(
    scope: ConsumptionScopeV1,
    tenant_id: str,
    principal_id: str,
    project_id: str,
    conversation_id: str,
) -> None:
    if (
        scope.tenant_id,
        scope.principal_id,
        scope.project_id,
        scope.conversation_id,
    ) != (tenant_id, principal_id, project_id, conversation_id):
        _reject("CONSUMPTION_NOT_FOUND")


def _preview(connection: sqlite3.Connection, preview_id: str) -> ConsumptionPreviewV1:
    row = connection.execute(
        "SELECT payload FROM model_consumption_previews WHERE preview_id=?",
        (preview_id,),
    ).fetchone()
    if row is None:
        _reject("CONSUMPTION_NOT_FOUND")
    result = _decode(ConsumptionPreviewV1, row[0])
    if result.preview_id != preview_id:
        _reject()
    return result


def _grant(connection: sqlite3.Connection, grant_id: str) -> ConsumptionGrantV1:
    row = connection.execute(
        "SELECT preview_id,tenant_id,project_id,request_id,logical_scope_fingerprint,envelope_id,payload FROM model_consumption_grants WHERE grant_id=?",
        (grant_id,),
    ).fetchone()
    if row is None:
        _reject("CONSUMPTION_NOT_FOUND")
    result = _decode(ConsumptionGrantV1, row[6])
    if result.grant_id != grant_id or tuple(row[:6]) != (
        result.preview_id,
        result.scope.tenant_id,
        result.scope.project_id,
        result.scope.request_id,
        result.scope.logical_scope_fingerprint,
        result.envelope.envelope_id,
    ):
        _reject()
    preview = _preview(connection, result.preview_id)
    if (
        preview.fingerprint != result.preview_fingerprint
        or preview.scope != result.scope
        or result.not_after > preview.not_after
    ):
        _reject()
    rows = connection.execute(
        "SELECT revision,payload FROM model_consumption_grant_events WHERE grant_id=? ORDER BY revision",
        (grant_id,),
    ).fetchall()
    if len(rows) != result.revision:
        _reject()
    previous = None
    issued = None
    for index, (revision, payload) in enumerate(rows, start=1):
        event = _decode(ConsumptionGrantEventV1, payload)
        if (
            revision != index
            or event.revision != index
            or event.grant_id != grant_id
            or event.previous_event_fingerprint
            != (None if previous is None else previous.fingerprint)
        ):
            _reject()
        if index == 1:
            issued = event.grant
        else:
            immutable = {
                "status",
                "revision",
                "revoked_at",
                "revoked_by",
                "revocation_reason",
                "fingerprint",
            }
            if event.grant.model_dump(exclude=immutable) != cast(ConsumptionGrantV1, issued).model_dump(
                exclude=immutable
            ):
                _reject()
        previous = event
    if previous is None or previous.grant != result:
        _reject()
    return result




def _event(
    connection: sqlite3.Connection,
    grant: ConsumptionGrantV1,
    previous: str | None = None,
) -> None:
    issued = grant.status == "ACTIVE"
    event = ConsumptionGrantEventV1(
        grant_id=grant.grant_id,
        revision=grant.revision,
        event_type="ISSUED" if issued else "REVOKED",
        actor=grant.scope.principal_id if issued else grant.revoked_by,
        tenant_id=grant.scope.tenant_id,
        created_at=grant.issued_at if issued else grant.revoked_at,
        previous_event_fingerprint=None if previous is None else previous,
        grant=grant,
    )
    connection.execute(
        "INSERT INTO model_consumption_grant_events(grant_id,revision,payload) VALUES(?,?,?)",
        (grant.grant_id, grant.revision, _encode(event)),
    )


def validate_phase_grant(
    connection: sqlite3.Connection,
    *,
    envelope: SingleInvocationBudgetEnvelopeV2,
    scope: ConsumptionScopeV1,
    account_authority: AccountAuthorityPremiseV1 | None,
    phase: str,
    now: datetime,
) -> ConsumptionGrantV1:
    """Validate within the SAME existing intent transaction; no commit or send.

    Caller must use BEGIN IMMEDIATE and write its phase intent before committing.
    External account currentness is a separate final checkpoint, never an atomic
    promise across SQLite and a provider/HTTP system.
    """
    if not connection.in_transaction:
        _reject("GRANT_TRANSACTION_REQUIRED")
    _now(now)
    if phase not in {"COUNTER", "GENERATION"}:
        _reject("GRANT_PHASE_INVALID")
    envelope = _checked(SingleInvocationBudgetEnvelopeV2, envelope)
    scope = _checked(ConsumptionScopeV1, scope)
    row = connection.execute(
        "SELECT grant_id FROM model_consumption_grants WHERE envelope_id=?",
        (envelope.envelope_id,),
    ).fetchone()
    if row is None:
        _reject("GRANT_UNAVAILABLE")
    grant = _grant(connection, row[0])
    if grant.status != "ACTIVE":
        _reject("GRANT_REVOKED")
    if not grant.issued_at <= now < grant.not_after:
        _reject("GRANT_EXPIRED")
    if grant.envelope != envelope or grant.scope != scope:
        _reject("GRANT_SCOPE_STALE")
    current = _account(scope, account_authority, now)
    if current != grant.account_authority:
        _reject("ACCOUNT_AUTHORITY_STALE")
    return grant


class ConsumptionStoreMixin:
    """Mixin for the existing SQLiteModelGatewayStore; has no database constructor."""

    _path: Path

    def invalidate_consumption_grant(
        self,
        grant_id: str,
        *,
        observed_authority: AccountAuthorityPremiseV1,
        now: datetime,
    ) -> ConsumptionGrantV1:
        """Host-only durable invalidation after a definite current account change.

        Missing/failed/expired sources are not proof of revocation. This method
        never reactivates a grant and never releases an existing budget claim.
        """
        _now(now)
        observed = _checked(AccountAuthorityPremiseV1, observed_authority)
        if not observed.observed_at <= now < observed.valid_until:
            _reject("ACCOUNT_AUTHORITY_UNAVAILABLE")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _grant(connection, grant_id)
            scope, initial = before.scope, before.account_authority
            if (
                observed.authority_id,
                observed.tenant_id,
                observed.principal_id,
                observed.provider_id,
                observed.connection_id,
                observed.scope_fingerprint,
            ) != (
                initial.authority_id,
                scope.tenant_id,
                scope.principal_id,
                initial.provider_id,
                scope.connection_id,
                scope.fingerprint,
            ):
                _reject("ACCOUNT_AUTHORITY_STALE")
            if before.status == "REVOKED":
                return before
            if observed.status == "ACTIVE" and observed == initial:
                _reject("ACCOUNT_AUTHORITY_UNCHANGED")
            if now < before.issued_at:
                _reject("CONSUMPTION_TIME_INVALID")
            revoked = ConsumptionGrantV1.model_validate(
                {
                    **before.model_dump(mode="python", exclude={"fingerprint"}),
                    "status": "REVOKED",
                    "revision": 2,
                    "revoked_at": now,
                    "revoked_by": observed.authority_id,
                    "revocation_reason": "ACCOUNT_AUTHORITY_INVALIDATED",
                }
            )
            old_payload = connection.execute(
                "SELECT payload FROM model_consumption_grants WHERE grant_id=?",
                (grant_id,),
            ).fetchone()[0]
            previous_payload = connection.execute(
                "SELECT payload FROM model_consumption_grant_events WHERE grant_id=? AND revision=1",
                (grant_id,),
            ).fetchone()[0]
            previous = _decode(ConsumptionGrantEventV1, previous_payload)
            updated = connection.execute(
                "UPDATE model_consumption_grants SET payload=? WHERE grant_id=? AND payload=?",
                (_encode(revoked), grant_id, old_payload),
            )
            if updated.rowcount != 1:
                _reject("GRANT_REVISION_CONFLICT")
            _event(connection, revoked, previous.fingerprint)
            return revoked

    def consumption_grant_for_request(
        self, *, tenant_id: str, project_id: str, request_id: str
    ) -> ConsumptionGrantV1 | None:
        """Host-only source lookup; API callers must use the Owner-bound get."""
        with sqlite3.connect(f"{self._path.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT grant_id FROM model_consumption_grants WHERE tenant_id=? AND project_id=? AND request_id=?",
                (tenant_id, project_id, request_id),
            ).fetchone()
            return None if row is None else _grant(connection, row[0])

    def save_consumption_preview(
        self, preview: ConsumptionPreviewV1, *, now: datetime
    ) -> ConsumptionPreviewV1:
        _now(now)
        preview = _checked(ConsumptionPreviewV1, preview)
        if not preview.created_at <= now < preview.not_after:
            _reject("PREVIEW_EXPIRED")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM model_consumption_previews WHERE preview_id=?",
                (preview.preview_id,),
            ).fetchone()
            if row is not None:
                if _decode(ConsumptionPreviewV1, row[0]) != preview:
                    _reject("PREVIEW_STALE")
            else:
                connection.execute(
                    "INSERT INTO model_consumption_previews(preview_id,payload) VALUES(?,?)",
                    (preview.preview_id, _encode(preview)),
                )
        return preview

    def get_consumption_preview(
        self,
        preview_id: str,
        *,
        tenant_id: str,
        principal_id: str,
        project_id: str,
        conversation_id: str,
    ) -> ConsumptionPreviewV1:
        with sqlite3.connect(f"{self._path.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            result = _preview(connection, preview_id)
            _owner(result.scope, tenant_id, principal_id, project_id, conversation_id)
            return result

    def get_consumption_grant(
        self,
        grant_id: str,
        *,
        tenant_id: str,
        principal_id: str,
        project_id: str,
        conversation_id: str,
    ) -> ConsumptionGrantV1:
        with sqlite3.connect(f"{self._path.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            result = _grant(connection, grant_id)
            _owner(result.scope, tenant_id, principal_id, project_id, conversation_id)
            return result

    def find_consumption_grant(
        self, *, scope: ConsumptionScopeV1
    ) -> ConsumptionGrantV1 | None:
        scope = _checked(ConsumptionScopeV1, scope)
        with sqlite3.connect(f"{self._path.as_uri()}?mode=ro", uri=True) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT grant_id FROM model_consumption_grants WHERE tenant_id=? AND project_id=? AND request_id=?",
                (scope.tenant_id, scope.project_id, scope.request_id),
            ).fetchone()
            if row is None:
                return None
            result = _grant(connection, row[0])
            if result.scope != scope:
                _reject("GRANT_SCOPE_STALE")
            return result

    def confirm_consumption_grant(
        self,
        *,
        preview_id: str,
        preview_fingerprint: str,
        current_scope: ConsumptionScopeV1,
        account_authority: AccountAuthorityPremiseV1 | None,
        now: datetime,
        consent: bool,
        tenant_id: str,
        principal_id: str,
        project_id: str,
        conversation_id: str,
    ) -> ConsumptionGrantV1:
        _now(now)
        if consent is not True:
            _reject("CONSENT_REQUIRED")
        current_scope = _checked(ConsumptionScopeV1, current_scope)
        _owner(current_scope, tenant_id, principal_id, project_id, conversation_id)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            preview = _preview(connection, preview_id)
            _owner(preview.scope, tenant_id, principal_id, project_id, conversation_id)
            if (
                preview.fingerprint != preview_fingerprint
                or preview.scope != current_scope
            ):
                _reject("PREVIEW_STALE")
            row = connection.execute(
                "SELECT grant_id FROM model_consumption_grants WHERE preview_id=?",
                (preview_id,),
            ).fetchone()
            if row is not None:
                # A lost confirmation response is recovered without renewing,
                # unrevoking or minting any new execution authority.
                return _grant(connection, row[0])
            if not preview.created_at <= now < preview.not_after:
                _reject("PREVIEW_EXPIRED")
            premise = _account(current_scope, account_authority, now)
            not_after = min(
                preview.not_after, now + timedelta(minutes=10), premise.valid_until
            )
            envelope = SingleInvocationBudgetEnvelopeV2(
                envelope_id="consumption-envelope-" + uuid4().hex,
                tenant_id=tenant_id,
                project_id=project_id,
                conversation_id=conversation_id,
                task_id=current_scope.engineering_task_id,
                task_authority_fingerprint=current_scope.task_authority_fingerprint,
                engineering_stage=EngineeringStage.HARDWARE_SELECTION,
                not_after=not_after,
            )
            grant = ConsumptionGrantV1(
                grant_id="consumption-grant-" + uuid4().hex,
                preview_id=preview.preview_id,
                preview_fingerprint=preview.fingerprint,
                scope=current_scope,
                account_authority=premise,
                envelope=envelope,
                issued_at=now,
                not_after=not_after,
            )
            try:
                connection.execute(
                    "INSERT INTO model_consumption_grants(grant_id,preview_id,tenant_id,project_id,request_id,logical_scope_fingerprint,envelope_id,payload) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        grant.grant_id,
                        preview_id,
                        tenant_id,
                        project_id,
                        current_scope.request_id,
                        current_scope.logical_scope_fingerprint,
                        envelope.envelope_id,
                        _encode(grant),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConsumptionRejected("GRANT_SCOPE_CONFLICT") from error
            _event(connection, grant)
            return grant

    def revoke_consumption_grant(
        self,
        grant_id: str,
        *,
        expected_revision: int,
        now: datetime,
        tenant_id: str,
        principal_id: str,
        project_id: str,
        conversation_id: str,
    ) -> ConsumptionGrantV1:
        _now(now)
        if type(expected_revision) is not int or expected_revision < 1:
            _reject("GRANT_REVISION_CONFLICT")
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = _grant(connection, grant_id)
            _owner(before.scope, tenant_id, principal_id, project_id, conversation_id)
            if (
                before.status == "REVOKED"
                and expected_revision == 1
                and before.revoked_by == principal_id
                and before.revocation_reason == "USER_REVOKED"
            ):
                return before
            if before.status != "ACTIVE" or before.revision != expected_revision:
                _reject("GRANT_REVISION_CONFLICT")
            if now < before.issued_at:
                _reject("CONSUMPTION_TIME_INVALID")
            revoked = ConsumptionGrantV1.model_validate(
                {
                    **before.model_dump(mode="python", exclude={"fingerprint"}),
                    "status": "REVOKED",
                    "revision": 2,
                    "revoked_at": now,
                    "revoked_by": principal_id,
                    "revocation_reason": "USER_REVOKED",
                }
            )
            old_payload = connection.execute(
                "SELECT payload FROM model_consumption_grants WHERE grant_id=?",
                (grant_id,),
            ).fetchone()[0]
            previous = connection.execute(
                "SELECT payload FROM model_consumption_grant_events WHERE grant_id=? AND revision=1",
                (grant_id,),
            ).fetchone()[0]
            event = _decode(ConsumptionGrantEventV1, previous)
            result = connection.execute(
                "UPDATE model_consumption_grants SET payload=? WHERE grant_id=? AND payload=?",
                (_encode(revoked), grant_id, old_payload),
            )
            if result.rowcount != 1:
                _reject("GRANT_REVISION_CONFLICT")
            _event(connection, revoked, event.fingerprint)
            return revoked
