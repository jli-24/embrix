"""Host-held current consent source; no default account premise or network I/O.

The source's preliminary read uses the persisted principal to reconstruct scope.
The phase hook additionally requires the *current* trusted caller principal;
that check must occur before each durable phase intent in the same store DB.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol, cast

from ..contracts import ModelRequest, identifier
from .budget import digest, wire_target_digest
from .consumption_contracts import (
    AccountAuthorityPremiseV1,
    ConsumptionGrantV1,
    ConsumptionScopeV1,
)
from .consumption_store import (
    ConsumptionRejected,
    _account,
    _checked,
)
from .contracts import ModelExecutionSnapshot
from .openai_responses_qualification import (
    _native_wire_target,
    count_request_from_final,
)
from .qualified_budget_producer import (
    QualificationContext,
    QualifiedInvocationBudgetProducer,
    WitnessHeader,
    _canonical,
)
from .qualified_bundle_producer import GrantedBundle


class AccountAuthorityPort(Protocol):
    """Private trusted host source; the product does not accept public premises."""

    authority_id: str

    def read(self, scope: ConsumptionScopeV1) -> AccountAuthorityPremiseV1 | None: ...


def scope_from_context(
    context: QualificationContext, *, principal_id: str
) -> tuple[ConsumptionScopeV1, bytes]:
    """Same exact count projection as the existing qualifier, without a grant."""
    identifier(principal_id, field="consumption principal")
    if type(context) is not QualificationContext:
        raise ConsumptionRejected("GRANT_SCOPE_STALE")
    counter = count_request_from_final(context)
    # V1 qualification accepts an absent tier for historical reads. New G-A is
    # strictly the approved V2 default-tier profile; do not auto-upgrade V1.
    if (
        json.loads(context.final_transport_bytes).get("service_tier") != "default"
        or cast(ModelRequest, context.bound_request).model_policy.service_tier != "default"
    ):
        raise ConsumptionRejected("GRANT_SCOPE_STALE")
    scope = ConsumptionScopeV1(
        tenant_id=context.tenant_id,
        principal_id=principal_id,
        project_id=context.project_id,
        conversation_id=context.conversation_id,
        engineering_task_id=context.task_id,
        request_id=cast(ModelRequest, context.bound_request).request_id,
        request_fingerprint=context.request_fingerprint,
        snapshot_fingerprint=context.snapshot_fingerprint,
        task_authority_fingerprint=context.task_authority_fingerprint,
        candidate_context_fingerprint=digest(
            cast(ModelRequest, context.bound_request).task.encode("utf-8")
        ),
        policy_fingerprint=cast(ModelExecutionSnapshot, context.execution_snapshot).policy_fingerprint,
        selected_model=context.selected_model,
        connection_id=context.connection_id,
        connection_revision=context.connection_revision,
        credential_revision=context.credential_revision,
        endpoint_revision=context.endpoint_revision,
        generation_bytes_sha256=context.final_transport_sha256,
        count_bytes_sha256=digest(counter),
        generation_wire_target_sha256=context.wire_target_sha256,
        counter_wire_target_sha256=wire_target_digest(
            _native_wire_target(context, "responses/input_tokens")
        ),
    )
    return scope, counter


class _ConsumptionStorePort(Protocol):
    def consumption_grant_for_request(self, *, tenant_id: str, project_id: str, request_id: str) -> ConsumptionGrantV1 | None: ...
    def invalidate_consumption_grant(self, grant_id: str, *, observed_authority: AccountAuthorityPremiseV1, now: datetime) -> ConsumptionGrantV1: ...


class ConsumptionGrantSource:
    """Maps authenticated durable consent into the existing GrantedBundle seam."""

    def __init__(
        self,
        store: _ConsumptionStorePort,
        account_authority_source: AccountAuthorityPort | None = None,
        *,
        authority_id: str = "desktop-consumption-grant-v1",
    ) -> None:
        try:
            valid = all(callable(getattr(store, name, None)) for name in ("consumption_grant_for_request", "invalidate_consumption_grant"))
        except Exception:  # noqa: BLE001 - untrusted injected property must map to a fixed boundary error.
            raise ConsumptionRejected("GRANT_STORE_IDENTITY_INVALID") from None
        if not valid:
            raise ConsumptionRejected("GRANT_STORE_IDENTITY_INVALID")
        self._store = store
        self._account_source = account_authority_source
        self.authority_id = identifier(authority_id, field="consumption authority")

    @property
    def consumption_store(self) -> _ConsumptionStorePort:
        """Composition compares this exact store object with the claim store."""
        return self._store

    def _read_current(
        self, context: QualificationContext, principal_id: str | None = None
    ) -> tuple[ConsumptionGrantV1, ConsumptionScopeV1, AccountAuthorityPremiseV1]:
        grant = self._store.consumption_grant_for_request(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            request_id=cast(ModelRequest, context.bound_request).request_id,
        )
        if grant is None:
            raise ConsumptionRejected("GRANT_UNAVAILABLE")
        grant = _checked(ConsumptionGrantV1, grant)
        if principal_id is not None and principal_id != grant.scope.principal_id:
            raise ConsumptionRejected("GRANT_SCOPE_STALE")
        scope, _ = scope_from_context(context, principal_id=grant.scope.principal_id)
        if scope != grant.scope:
            raise ConsumptionRejected("GRANT_SCOPE_STALE")
        if grant.status != "ACTIVE":
            raise ConsumptionRejected("GRANT_REVOKED")
        if not grant.issued_at <= context.now < grant.not_after:
            raise ConsumptionRejected("GRANT_EXPIRED")
        reader = getattr(self._account_source, "read", None)
        if not callable(reader):
            raise ConsumptionRejected("ACCOUNT_AUTHORITY_UNAVAILABLE")
        try:
            observed = _checked(AccountAuthorityPremiseV1, reader(scope))
            if observed.authority_id != getattr(
                self._account_source, "authority_id", None
            ):
                raise ConsumptionRejected("ACCOUNT_AUTHORITY_UNAVAILABLE")
            if observed.observed_at <= context.now < observed.valid_until and (
                observed.status == "REVOKED" or observed != grant.account_authority
            ):
                self._store.invalidate_consumption_grant(
                    grant.grant_id, observed_authority=observed, now=context.now
                )
                raise ConsumptionRejected("ACCOUNT_AUTHORITY_STALE")
            premise = _account(scope, observed, context.now)
        except ConsumptionRejected:
            raise
        except Exception as error:  # untrusted host failure remains unavailable
            raise ConsumptionRejected("ACCOUNT_AUTHORITY_UNAVAILABLE") from error
        if premise != grant.account_authority:
            raise ConsumptionRejected("ACCOUNT_AUTHORITY_STALE")
        return grant, scope, premise

    def read(self, context: QualificationContext) -> GrantedBundle:
        grant, _, _ = self._read_current(context)
        raw = grant.model_dump_json().encode("utf-8")
        raw_sha = digest(raw)
        material = (
            self.authority_id,
            grant.grant_id,
            str(grant.revision),
            raw_sha,
            context.scope_fingerprint,
            grant.issued_at.isoformat(),
            grant.not_after.isoformat(),
        )
        witness = WitnessHeader(
            self.authority_id,
            grant.grant_id,
            str(grant.revision),
            raw,
            raw_sha,
            digest(_canonical(material)),
            context.scope_fingerprint,
            grant.issued_at,
            grant.not_after,
        )
        return GrantedBundle(witness, grant.envelope)

    def phase_authorization(
        self, envelope, *, principal_id: str | None, **contextinputs
    ) -> tuple[ConsumptionScopeV1, AccountAuthorityPremiseV1]:
        if principal_id is None:
            raise ConsumptionRejected("GRANT_PRINCIPAL_UNAVAILABLE")
        context = QualifiedInvocationBudgetProducer._context(**contextinputs)
        grant, scope, premise = self._read_current(context, principal_id)
        if grant.envelope != envelope:
            raise ConsumptionRejected("GRANT_SCOPE_STALE")
        return scope, premise
