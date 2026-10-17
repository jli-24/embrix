"""G-A consent records. These typed records do not establish account authority.

Only a separately configured trusted host may supply an account premise. There
is no public importer, credential, raw prompt, network client or execution here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, cast

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    ModelGatewayContract,
    canonical_fingerprint,
    fingerprint,
    identifier,
)
from ..governance import BoundModelIdentity, EngineeringStage
from .bundle_contracts import SingleInvocationBudgetEnvelopeV2

_AUTO = "sha256:" + "0" * 64
ROLES: tuple[Literal["RESPONSES_INPUT_TOKENS"], Literal["RESPONSES_GENERATION"]] = ("RESPONSES_INPUT_TOKENS", "RESPONSES_GENERATION")


class _ConsumptionRecord(ModelGatewayContract):
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    fingerprint: str = _AUTO

    @model_validator(mode="after")
    def _integrity(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is None or name == "fingerprint":
                continue
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("consumption time must be timezone aware")
            if isinstance(value, str):
                if name.endswith(("_fingerprint", "_sha256")):
                    fingerprint(value, field=name)
                else:
                    identifier(value, field=name)
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("consumption fingerprint mismatch")
        return self


class ConsumptionScopeV1(_ConsumptionRecord):
    tenant_id: str
    principal_id: str
    project_id: str
    conversation_id: str
    engineering_task_id: str
    request_id: str
    request_fingerprint: str
    snapshot_fingerprint: str
    task_authority_fingerprint: str
    candidate_context_fingerprint: str
    policy_fingerprint: str
    selected_model: BoundModelIdentity
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    generation_bytes_sha256: str
    count_bytes_sha256: str
    generation_wire_target_sha256: str
    counter_wire_target_sha256: str
    operation_id: Literal["HARDWARE_SELECTION_PROPOSAL"] = "HARDWARE_SELECTION_PROPOSAL"
    engineering_stage: Literal["HARDWARE_SELECTION"] = "HARDWARE_SELECTION"
    protocol_family: Literal["OPENAI_RESPONSES"] = "OPENAI_RESPONSES"
    service_tier: Literal["default"] = "default"
    currency: Literal["USD"] = "USD"
    max_cost_decimal: Literal["0.10"] = "0.10"
    max_requests: int = Field(default=2, strict=True, ge=2, le=2)
    max_counter_requests: int = Field(default=1, strict=True, ge=1, le=1)
    max_generation_requests: int = Field(default=1, strict=True, ge=1, le=1)
    input_admission_limit: int = Field(default=8192, strict=True, ge=8192, le=8192)
    total_output_limit: int = Field(default=2048, strict=True, ge=2048, le=2048)

    @model_validator(mode="after")
    def _profile(self):
        if (
            self.selected_model.provider_id != "openai"
            or self.selected_model.model_id != "gpt-5.6-luna"
        ):
            raise ValueError("consumption profile unsupported")
        return self

    @property
    def model_id(self) -> str:
        return self.selected_model.model_id

    @property
    def logical_scope_fingerprint(self) -> str:
        # Selection/execution snapshots and request fingerprints include request
        # IDs. Their independently bound model/policy/source fields remain above.
        return canonical_fingerprint(
            self,
            exclude={
                "fingerprint",
                "request_id",
                "request_fingerprint",
                "snapshot_fingerprint",
            },
        )


class AccountAuthorityPremiseV1(_ConsumptionRecord):
    """Host-only current mapping; constructing/hashing it proves no external fact."""

    authority_id: str
    association_id: str
    revision: str
    tenant_id: str
    principal_id: str
    provider_id: Literal["openai"] = "openai"
    account_ref: str
    provider_project_ref: str
    relationship: Literal["OWNER", "DELEGATE"]
    delegation_ref: str | None = None
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    scope_fingerprint: str
    source_head_fingerprint: str
    evidence_identity: str
    raw_sha256: str
    verified_by: str
    observed_at: datetime
    valid_until: datetime
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    roles: tuple[Literal["RESPONSES_INPUT_TOKENS"], Literal["RESPONSES_GENERATION"]] = (
        ROLES
    )
    currency: Literal["USD"] = "USD"
    max_cost_decimal: Literal["0.10"] = "0.10"

    @model_validator(mode="after")
    def _relationship(self):
        if self.observed_at >= self.valid_until:
            raise ValueError("account authority interval invalid")
        if self.relationship == "DELEGATE" and self.delegation_ref is None:
            raise ValueError("account delegation reference required")
        return self


class ConsumptionCandidateSelectorV1(ModelGatewayContract):
    """Safe durable selector sufficient to rebuild the current request after restart."""

    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    selection_schema_version: Literal[1, 2, 3] = 1
    request_id: str
    plan_id: str
    phase_id: str
    plan_task_id: str
    operation_id: Literal["HARDWARE_SELECTION_PROPOSAL"] = "HARDWARE_SELECTION_PROPOSAL"
    expected_head_revision_id: str | None = None
    expected_head_selection_id: str | None = None
    expected_head_selection_fingerprint: str | None = None
    selection_preview_fingerprint: str | None = None
    parent_choices: tuple[tuple[str, str, str], ...] = ()

    @field_validator("selection_schema_version", mode="before")
    @classmethod
    def _strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("consumption selector version invalid")
        return value

    @model_validator(mode="after")
    def _selector(self):
        for name in (
            "request_id",
            "plan_id",
            "phase_id",
            "plan_task_id",
            "expected_head_revision_id",
            "expected_head_selection_id",
        ):
            if getattr(self, name) is not None:
                identifier(getattr(self, name), field=name)
        for name in (
            "expected_head_selection_fingerprint",
            "selection_preview_fingerprint",
        ):
            if getattr(self, name) is not None:
                fingerprint(getattr(self, name), field=name)
        heads = (
            self.expected_head_revision_id,
            self.expected_head_selection_id,
            self.expected_head_selection_fingerprint,
        )
        if self.selection_schema_version == 1:
            if (
                any(value is not None for value in heads)
                or self.selection_preview_fingerprint is not None
                or self.parent_choices
            ):
                raise ValueError(
                    "legacy consumption selector cannot carry revision data"
                )
        else:
            if (
                self.selection_preview_fingerprint is None
                or (self.expected_head_selection_id is None)
                != (self.expected_head_selection_fingerprint is None)
                or (
                    self.expected_head_revision_id is not None
                    and self.expected_head_selection_id is None
                )
            ):
                raise ValueError("consumption selector head invalid")
            if self.selection_schema_version == 2 and self.parent_choices:
                raise ValueError("v2 consumption selector cannot carry parents")
            if (
                self.selection_schema_version == 3
                and not 1 <= len(self.parent_choices) <= 64
            ):
                raise ValueError("v3 consumption selector requires bounded parents")
        for slot_id, source_id, source_fingerprint in self.parent_choices:
            identifier(slot_id, field="parent slot")
            identifier(source_id, field="parent source")
            fingerprint(source_fingerprint, field="parent source fingerprint")
        if len({item[0] for item in self.parent_choices}) != len(self.parent_choices):
            raise ValueError("consumption selector parents repeated")
        return self


class ConsumptionPreviewV1(_ConsumptionRecord):
    preview_id: str
    scope: ConsumptionScopeV1
    candidate_selector: ConsumptionCandidateSelectorV1
    created_at: datetime
    not_after: datetime
    generation_bytes_length: int = Field(strict=True, gt=0)
    count_bytes_length: int = Field(strict=True, gt=0)
    data_categories: tuple[
        Literal["ENGINEERING_CANDIDATE_CONTEXT"], Literal["SYSTEM_INSTRUCTION"]
    ] = ("ENGINEERING_CANDIDATE_CONTEXT", "SYSTEM_INSTRUCTION")

    @model_validator(mode="after")
    def _expiry(self):
        if (
            self.candidate_selector.request_id != self.scope.request_id
            or self.candidate_selector.operation_id != self.scope.operation_id
        ):
            raise ValueError("consumption selector scope invalid")
        if not timedelta(0) < self.not_after - self.created_at <= timedelta(minutes=10):
            raise ValueError("consumption preview interval invalid")
        return self


class ConsumptionGrantV1(_ConsumptionRecord):
    grant_id: str
    preview_id: str
    preview_fingerprint: str
    scope: ConsumptionScopeV1
    account_authority: AccountAuthorityPremiseV1
    envelope: SingleInvocationBudgetEnvelopeV2
    issued_at: datetime
    not_after: datetime
    status: Literal["ACTIVE", "REVOKED"] = "ACTIVE"
    revision: int = Field(default=1, strict=True, ge=1, le=2)
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revocation_reason: (
        Literal["USER_REVOKED", "ACCOUNT_AUTHORITY_INVALIDATED"] | None
    ) = None

    @model_validator(mode="after")
    def _binding(self):
        scope, envelope, account = self.scope, self.envelope, self.account_authority
        if (
            not timedelta(0) < self.not_after - self.issued_at <= timedelta(minutes=10)
            or self.not_after > account.valid_until
        ):
            raise ValueError("consumption grant interval invalid")
        if (
            envelope.tenant_id,
            envelope.project_id,
            envelope.conversation_id,
            envelope.task_id,
            envelope.task_authority_fingerprint,
            envelope.engineering_stage,
            envelope.not_after,
        ) != (
            scope.tenant_id,
            scope.project_id,
            scope.conversation_id,
            scope.engineering_task_id,
            scope.task_authority_fingerprint,
            EngineeringStage.HARDWARE_SELECTION,
            self.not_after,
        ):
            raise ValueError("consumption envelope binding invalid")
        if account.scope_fingerprint != scope.fingerprint or (
            account.tenant_id,
            account.principal_id,
            account.connection_id,
            account.connection_revision,
            account.credential_revision,
            account.endpoint_revision,
        ) != (
            scope.tenant_id,
            scope.principal_id,
            scope.connection_id,
            scope.connection_revision,
            scope.credential_revision,
            scope.endpoint_revision,
        ):
            raise ValueError("consumption account binding invalid")
        if (
            account.status != "ACTIVE"
            or not account.observed_at <= self.issued_at < account.valid_until
        ):
            raise ValueError("consumption account unavailable at issuance")
        revoked = (self.revoked_at, self.revoked_by, self.revocation_reason)
        if self.status == "ACTIVE":
            if self.revision != 1 or any(value is not None for value in revoked):
                raise ValueError("active consumption head invalid")
        elif (
            self.revision != 2
            or any(value is None for value in revoked)
            or cast(datetime, self.revoked_at) < self.issued_at
        ):
            raise ValueError("revoked consumption head invalid")
        return self


class ConsumptionGrantEventV1(_ConsumptionRecord):
    grant_id: str
    revision: int = Field(strict=True, ge=1, le=2)
    event_type: Literal["ISSUED", "REVOKED"]
    actor: str
    tenant_id: str
    created_at: datetime
    previous_event_fingerprint: str | None
    grant: ConsumptionGrantV1

    @model_validator(mode="after")
    def _event(self):
        grant = self.grant
        if (self.grant_id, self.revision, self.tenant_id) != (
            grant.grant_id,
            grant.revision,
            grant.scope.tenant_id,
        ):
            raise ValueError("consumption event binding invalid")
        if self.event_type == "ISSUED":
            if (
                grant.status != "ACTIVE"
                or self.previous_event_fingerprint is not None
                or (self.actor, self.created_at)
                != (grant.scope.principal_id, grant.issued_at)
            ):
                raise ValueError("consumption issuance event invalid")
        elif (
            grant.status != "REVOKED"
            or self.previous_event_fingerprint is None
            or (self.actor, self.created_at) != (grant.revoked_by, grant.revoked_at)
        ):
            raise ValueError("consumption revocation event invalid")
        return self
