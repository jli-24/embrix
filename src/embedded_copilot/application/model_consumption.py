# ruff: noqa: BLE001
"""No-send consent application service; external qualifications remain independent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from uuid import uuid4

from embedded_copilot.model_gateway.invocation.consumption_contracts import (
    AccountAuthorityPremiseV1,
    ConsumptionCandidateSelectorV1,
    ConsumptionGrantV1,
    ConsumptionPreviewV1,
    ConsumptionScopeV1,
)
from embedded_copilot.model_gateway.invocation.consumption_source import (
    AccountAuthorityPort,
    scope_from_context,
)
from embedded_copilot.model_gateway.invocation.consumption_store import (
    ConsumptionRejected,
    ConsumptionStoreMixin,
)
from embedded_copilot.model_gateway.invocation.contracts import ModelInvocationAttempt
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    QualificationContext,
)


class _AttemptReader(Protocol):
    def get_attempt_for_project(self, project_id: str, request_id: str) -> ModelInvocationAttempt | None: ...


PrepareConsumption = Callable[[ConsumptionCandidateSelectorV1], QualificationContext]


class ModelConsumptionService:
    """Use the existing ledger, without an account or execution fallback."""

    def __init__(
        self,
        store: ConsumptionStoreMixin,
        *,
        account_authority_source: AccountAuthorityPort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._account_source = account_authority_source
        self._clock = clock or (lambda: datetime.now(UTC))

    def _account(self, scope: ConsumptionScopeV1) -> AccountAuthorityPremiseV1:
        source = self._account_source
        if source is None or not callable(getattr(source, "read", None)):
            raise ConsumptionRejected("ACCOUNT_AUTHORITY_UNAVAILABLE")
        try:
            value = source.read(scope)
            if type(value) is not AccountAuthorityPremiseV1:
                raise ValueError("account source type")
            checked = AccountAuthorityPremiseV1.model_validate(
                value.model_dump(mode="python")
            )
            if checked.authority_id != source.authority_id:
                raise ValueError("account source identity")
            return checked
        except Exception as error:
            raise ConsumptionRejected("ACCOUNT_AUTHORITY_UNAVAILABLE") from error

    def preview(
        self,
        selector: ConsumptionCandidateSelectorV1,
        *,
        owner: dict[str, str],
        prepare: PrepareConsumption,
    ) -> dict[str, object]:
        context = prepare(selector)
        scope, count_bytes = scope_from_context(
            context, principal_id=owner["principal_id"]
        )
        now = self._clock()
        preview = ConsumptionPreviewV1(
            preview_id="consumption-preview-" + str(uuid4()),
            scope=scope,
            candidate_selector=selector,
            created_at=now,
            not_after=now + timedelta(minutes=10),
            generation_bytes_length=len(context.final_transport_bytes),
            count_bytes_length=len(count_bytes),
        )
        preview = self._store.save_consumption_preview(preview, now=now)
        try:
            account = self._account(scope)
            # The store applies the same strict currentness checks at confirmation.
            from embedded_copilot.model_gateway.invocation.consumption_store import (
                _account,
            )

            _account(scope, account, now)
        except ConsumptionRejected:
            return {
                "schema_version": 1,
                "status": "BLOCKED",
                "preview": None,
                "blockers": ("ACCOUNT_AUTHORITY_UNAVAILABLE",),
                "dispatch_ready": False,
            }
        return {
            "schema_version": 1,
            "status": "READY",
            "preview": preview,
            "blockers": (),
            "dispatch_ready": False,
        }

    def confirm(
        self,
        *,
        preview_id: str,
        preview_fingerprint: str,
        owner: dict[str, str],
        prepare: PrepareConsumption,
    ) -> dict[str, object]:
        preview = self._store.get_consumption_preview(preview_id, **owner)
        if preview.fingerprint != preview_fingerprint:
            raise ConsumptionRejected("PREVIEW_STALE")
        existing = self._store.consumption_grant_for_request(
            tenant_id=owner["tenant_id"],
            project_id=owner["project_id"],
            request_id=preview.scope.request_id,
        )
        if existing is not None:
            existing = self._store.get_consumption_grant(existing.grant_id, **owner)
            if (
                existing.preview_id != preview_id
                or existing.preview_fingerprint != preview_fingerprint
            ):
                raise ConsumptionRejected("GRANT_SCOPE_CONFLICT")
            return self._view(existing, owner=owner, prepare=prepare)
        context = prepare(preview.candidate_selector)
        scope, _ = scope_from_context(context, principal_id=owner["principal_id"])
        grant = self._store.confirm_consumption_grant(
            preview_id=preview_id,
            preview_fingerprint=preview_fingerprint,
            current_scope=scope,
            account_authority=self._account(scope),
            now=self._clock(),
            consent=True,
            **owner,
        )
        return self._view(grant, owner=owner, prepare=prepare)

    def get(
        self, grant_id: str, *, owner: dict[str, str], prepare: PrepareConsumption
    ) -> dict[str, object]:
        grant = self._store.get_consumption_grant(grant_id, **owner)
        return self._view(grant, owner=owner, prepare=prepare, read_account=False)

    def revoke(
        self, grant_id: str, *, expected_revision: int, owner: dict[str, str]
    ) -> dict[str, object]:
        grant = self._store.revoke_consumption_grant(
            grant_id, expected_revision=expected_revision, now=self._clock(), **owner
        )
        return self._view(grant, owner=owner, prepare=None)

    def _view(
        self,
        grant: ConsumptionGrantV1,
        *,
        owner: dict[str, str],
        prepare: PrepareConsumption | None,
        read_account: bool = True,
    ) -> dict[str, object]:
        status: str = grant.status
        if status == "ACTIVE":
            if self._clock() >= grant.not_after:
                status = "EXPIRED"
            else:
                try:
                    preview = self._store.get_consumption_preview(
                        grant.preview_id, **owner
                    )
                    if prepare is None:
                        raise ValueError("no current preparation")
                    context = prepare(preview.candidate_selector)
                    scope, _ = scope_from_context(
                        context, principal_id=owner["principal_id"]
                    )
                    if scope != grant.scope:
                        raise ValueError("scope changed")
                    if read_account:
                        observed = self._account(scope)
                        if observed != grant.account_authority:
                            self._store.invalidate_consumption_grant(
                                grant.grant_id,
                                observed_authority=observed,
                                now=self._clock(),
                            )
                            status = "REVOKED"
                            grant = self._store.get_consumption_grant(
                                grant.grant_id, **owner
                            )
                except Exception:
                    status = "STALE"
        attempt = cast(_AttemptReader, self._store).get_attempt_for_project(
            grant.scope.project_id, grant.scope.request_id
        )
        if attempt is not None and attempt.budget_envelope_id not in {
            None,
            grant.envelope.envelope_id,
        }:
            raise ConsumptionRejected("GRANT_SCOPE_STALE")
        return {
            "schema_version": 1,
            "grant_id": grant.grant_id,
            "preview_id": grant.preview_id,
            "preview_fingerprint": grant.preview_fingerprint,
            "scope_fingerprint": grant.scope.fingerprint,
            "status": status,
            "revision": grant.revision,
            "not_after": grant.not_after.isoformat(),
            "counter_status": "NOT_STARTED"
            if attempt is None
            else (attempt.counter_status or "UNKNOWN"),
            "generation_status": "NOT_STARTED"
            if attempt is None
            else attempt.status.value,
            "total_external_request_count": 0
            if attempt is None
            else attempt.total_external_request_count,
            "dispatch_ready": False,
        }
