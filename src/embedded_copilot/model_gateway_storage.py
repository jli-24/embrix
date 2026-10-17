"""Concrete persistence adapter for the secret-free model gateway state."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, field_validator, model_validator

from embedded_copilot.model_gateway.contracts import ModelGatewayContract
from embedded_copilot.model_gateway.contracts import fingerprint as validate_fingerprint
from embedded_copilot.model_gateway.governance import (
    AllowedModelPolicy,
    CandidateGroup,
    EngineeringStageDefinition,
    ProjectDefaultModelPolicy,
    SelectionMode,
    StageModelPolicy,
    default_engineering_stage_registry,
)
from embedded_copilot.model_gateway.invocation import ModelInvocationReceipt
from embedded_copilot.model_gateway.invocation.contracts import (
    AttemptStatus,
    ModelInvocationAttempt,
)
from embedded_copilot.model_gateway.metrics import (
    AccountUsageSnapshot,
    ModelReliability,
    ModelTaskMetrics,
)
from embedded_copilot.model_gateway.profiles import (
    CredentialProfile,
    EndpointProfile,
    GlobalModelPolicy,
    IdentityStatus,
    ModelProfile,
    ProjectModelPolicy,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    TaskProfile,
)
from embedded_copilot.model_gateway.registry import CapabilityProbeResult
from embedded_copilot.model_gateway.usage import ModelUsageAggregate
from embedded_copilot.model_gateway_persistence.bundle_store import (
    CounterBundleStoreMixin,
)
from embedded_copilot.model_gateway_persistence.consumption_store import (
    ConsumptionStoreMixin,
    initialize_consumption_schema,
)


class IdentityIntegrityIssue(ModelGatewayContract):
    """Secret-free quarantine summary for a rejected persisted binding."""

    record_type: str
    record_id: str
    provider_id: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    model_profile_id: str | None = None
    model_id: str | None = None
    identity_status: IdentityStatus = IdentityStatus.INVALID
    reason: str
    detected_at: datetime
    # D1 provenance metadata is deliberately optional so schema v3 payloads
    # from before the resolver remain readable without a migration rewrite.
    source_type: str | None = None
    record_reference: str | None = None
    source_fingerprint: str | None = None
    migration_version: int | None = Field(default=None, ge=0)
    migration_sub_version: int | None = Field(default=None, ge=1)

    @field_validator(
        "record_type",
        "record_id",
        "provider_id",
        "endpoint_id",
        "model_profile_id",
        "model_id",
        "reason",
        mode="before",
    )
    @classmethod
    def _safe_text(cls, value: object, info) -> str | None:
        if value is None and info.field_name not in {"record_type", "record_id", "reason"}:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            raise ValueError(f"{info.field_name} is invalid")
        if any(token in value.lower() for token in ("api_key", "authorization", "password", "secret", "token")):
            raise ValueError(f"{info.field_name} is sensitive")
        return value.strip()

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()
        ):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @field_validator("identity_status", mode="before")
    @classmethod
    def _identity_status(cls, value: object) -> IdentityStatus:
        return value if isinstance(value, IdentityStatus) else IdentityStatus(value)

    @field_validator("detected_at", mode="before")
    @classmethod
    def _detected_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("detected_at is invalid")
        return value.astimezone(UTC)

    @field_validator("source_type", "record_reference", mode="before")
    @classmethod
    def _source_metadata(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
            raise ValueError(f"{info.field_name} is invalid")
        if any(token in value.lower() for token in ("api_key", "authorization", "password", "secret", "token")):
            raise ValueError(f"{info.field_name} is sensitive")
        return value.strip()

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value: object) -> str | None:
        return None if value is None else validate_fingerprint(value, field="source_fingerprint")


class LegacyIdentityCorrelation(ModelGatewayContract):
    """A persisted mapping from one legacy record to a proven identity.

    This is migration evidence, not a sixth identity component and not a
    runtime selection record.  All fields are optional only for the audit
    metadata; a valid correlation requires the complete canonical five-tuple.
    """

    source_type: str
    record_reference: str
    source_fingerprint: str
    migration_version: int = Field(ge=1)
    migration_sub_version: int = Field(default=1, ge=1)
    status: str = "UNIQUE"
    provider_id: str
    endpoint_id: str
    credential_ref: str
    model_profile_id: str
    model_id: str
    connection_id: str | None = None
    source_semantics: str = "LEGACY_MODEL"
    endpoint_revision_source: str | None = None
    endpoint_revision_current: str | None = None
    endpoint_revision_currentness: bool | None = None
    credential_revision_source: str | None = None
    credential_revision_current: str | None = None
    credential_revision_currentness: bool | None = None
    model_revision_source: str | None = None
    model_revision_current: str | None = None
    model_revision_currentness: bool | None = None
    verification_current: bool = False
    current_eligible: bool = False
    historical_correlation: bool = False
    correlation_fingerprint: str = "sha256:" + "0" * 64

    @field_validator("source_type", "record_reference", "source_semantics", "status", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
            raise ValueError(f"{info.field_name} is invalid")
        if any(token in value.lower() for token in ("api_key", "authorization", "password", "secret", "token")):
            raise ValueError(f"{info.field_name} is sensitive")
        return value.strip()

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str:
        return validate_fingerprint(value, field="source_fingerprint")

    @field_validator(
        "provider_id", "endpoint_id", "model_profile_id", "model_id",
        "connection_id", "endpoint_revision_source", "endpoint_revision_current",
        "credential_revision_source", "credential_revision_current",
        "model_revision_source", "model_revision_current", mode="before",
    )
    @classmethod
    def _identifiers(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            raise ValueError(f"{info.field_name} is invalid")
        if any(token in value.lower() for token in ("api_key", "authorization", "password", "secret", "token")):
            raise ValueError(f"{info.field_name} is sensitive")
        return value.strip()

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential_ref(cls, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()
        ):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @model_validator(mode="after")
    def _verify(self) -> LegacyIdentityCorrelation:
        if self.status != "UNIQUE":
            raise ValueError("identity correlation status must be UNIQUE")
        material = self.model_dump(mode="json", exclude={"correlation_fingerprint"})
        expected = _canonical_payload_fingerprint(material)
        if self.correlation_fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "correlation_fingerprint", expected)
        elif self.correlation_fingerprint != expected:
            raise ValueError("identity correlation fingerprint mismatch")
        return self


def _integrity_issue_identity(
    issue: IdentityIntegrityIssue,
) -> tuple[str | None, ...]:
    """Return the stable identity of one quarantined source record.

    ``detected_at`` is deliberately excluded: the same source record can be
    observed again during restore.  The full source identity remains part of
    the key so equal remote model IDs from different providers or credentials
    are not merged.
    """

    return (
        issue.record_type,
        issue.record_id,
        issue.provider_id,
        issue.endpoint_id,
        issue.credential_ref,
        issue.model_profile_id,
        issue.model_id,
        issue.reason,
        issue.source_type,
        issue.record_reference,
        issue.source_fingerprint,
        issue.migration_version,
        issue.migration_sub_version,
    )


def _deduplicate_integrity_issues(
    issues: tuple[IdentityIntegrityIssue, ...] | list[IdentityIntegrityIssue],
) -> tuple[IdentityIntegrityIssue, ...]:
    unique: list[IdentityIntegrityIssue] = []
    seen: set[tuple[str | None, ...]] = set()
    for issue in issues:
        identity = _integrity_issue_identity(issue)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(issue)
    return tuple(unique)


class LegacyModelMigrationError(ValueError):
    """The old row does not contain enough trusted data for a unique binding."""


class ModelGatewayState(ModelGatewayContract):
    providers: tuple[ProviderProfile, ...] = ()
    endpoints: tuple[EndpointProfile, ...] = ()
    connections: tuple[ProviderConnection, ...] = ()
    credentials: tuple[CredentialProfile, ...] = ()
    models: tuple[ModelProfile, ...] = ()
    capability_probes: tuple[CapabilityProbeResult, ...] = ()
    task_profiles: tuple[TaskProfile, ...] = ()
    global_policy: GlobalModelPolicy = Field(default_factory=GlobalModelPolicy)
    project_policies: tuple[ProjectModelPolicy, ...] = ()
    reliability: tuple[ModelReliability, ...] = ()
    task_metrics: tuple[ModelTaskMetrics, ...] = ()
    usage_aggregates: tuple[ModelUsageAggregate, ...] = ()
    usage_snapshots: tuple[AccountUsageSnapshot, ...] = ()
    engineering_stages: tuple[EngineeringStageDefinition, ...] = Field(default_factory=default_engineering_stage_registry)
    allowed_model_policies: tuple[AllowedModelPolicy, ...] = ()
    stage_model_policies: tuple[StageModelPolicy, ...] = ()
    candidate_groups: tuple[CandidateGroup, ...] = ()
    project_default_model_policies: tuple[ProjectDefaultModelPolicy, ...] = ()
    integrity_issues: tuple[IdentityIntegrityIssue, ...] = ()
    identity_correlations: tuple[LegacyIdentityCorrelation, ...] = ()
    migration_version: int = Field(default=0, ge=0)
    migration_source_fingerprint: str | None = None
    model_identity_migration_version: int = Field(default=0, ge=0)
    model_identity_source_fingerprint: str | None = None
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _identity_bindings(self) -> ModelGatewayState:
        provider_ids = {item.provider_id for item in self.providers}
        if len(provider_ids) != len(self.providers):
            raise ValueError("model gateway providers are not unique")
        endpoint_by_id = {item.endpoint_id: item for item in self.endpoints}
        if len(endpoint_by_id) != len(self.endpoints) or any(item.provider_id not in provider_ids for item in self.endpoints):
            raise ValueError("model gateway endpoint identity is invalid")
        credential_by_secret = {item.secret_ref: item for item in self.credentials}
        connection_by_id = {item.connection_id: item for item in self.connections}
        if len(connection_by_id) != len(self.connections):
            raise ValueError("model gateway connections are not unique")
        owned_endpoint_ids: set[str] = set()
        for connection in self.connections:
            endpoint = endpoint_by_id.get(connection.endpoint_id)
            if (
                connection.provider_id not in provider_ids
                or endpoint is None
                or endpoint.provider_id != connection.provider_id
                or connection.endpoint_id in owned_endpoint_ids
                or (
                    endpoint.credential_ref is not None
                    and endpoint.credential_ref != connection.credential_ref
                )
                or (
                    connection.credential_ref is not None
                    and credential_by_secret.get(connection.credential_ref) is not None
                    and credential_by_secret[connection.credential_ref].provider_id
                    != connection.provider_id
                )
            ):
                raise ValueError("model gateway connection ownership is invalid")
            owned_endpoint_ids.add(connection.endpoint_id)
        if self.migration_source_fingerprint is not None:
            validate_fingerprint(
                self.migration_source_fingerprint,
                field="migration_source_fingerprint",
            )
        if self.model_identity_source_fingerprint is not None:
            validate_fingerprint(
                self.model_identity_source_fingerprint,
                field="model_identity_source_fingerprint",
            )
        correlation_keys = {
            (item.source_type, item.record_reference) for item in self.identity_correlations
        }
        if len(correlation_keys) != len(self.identity_correlations):
            raise ValueError("model identity correlations are not unique")
        credential_ids = {item.credential_id for item in self.credentials}
        if len(credential_ids) != len(self.credentials) or any(item.provider_id not in provider_ids for item in self.credentials):
            raise ValueError("model gateway credential identity is invalid")
        model_by_id = {item.model_profile_id: item for item in self.models}
        if len(model_by_id) != len(self.models) or any(item.provider_id not in provider_ids or endpoint_by_id.get(item.endpoint_id) is None or endpoint_by_id[item.endpoint_id].provider_id != item.provider_id for item in self.models):
            raise ValueError("model gateway model identity is invalid")
        if any(item.model_profile_id not in model_by_id or model_by_id[item.model_profile_id].endpoint_id != item.endpoint_id for item in self.capability_probes):
            raise ValueError("model gateway probe identity is invalid")
        task_types = {item.task_type for item in self.task_profiles}
        if len(task_types) != len(self.task_profiles):
            raise ValueError("model gateway task profiles are not unique")
        project_ids = {item.project_id for item in self.project_policies}
        if len(project_ids) != len(self.project_policies):
            raise ValueError("model gateway project policies are not unique")
        stage_ids = {item.stage_id for item in self.engineering_stages}
        if len(stage_ids) != len(self.engineering_stages):
            raise ValueError("engineering stages are not unique")
        allowed_ids = {item.id for item in self.allowed_model_policies}
        if len(allowed_ids) != len(self.allowed_model_policies):
            raise ValueError("allowed model policies are not unique")
        stage_policy_ids = {item.id for item in self.stage_model_policies}
        if len(stage_policy_ids) != len(self.stage_model_policies):
            raise ValueError("stage model policies are not unique")
        stage_keys = {(item.project_id, item.stage_id) for item in self.stage_model_policies}
        if len(stage_keys) != len(self.stage_model_policies):
            raise ValueError("stage model policy bindings are not unique")
        group_by_key = {(item.group_id, item.revision): item for item in self.candidate_groups}
        if len(group_by_key) != len(self.candidate_groups):
            raise ValueError("candidate group versions are not unique")
        for policy in self.stage_model_policies:
            if policy.selection_mode is SelectionMode.CANDIDATE_GROUP:
                group = group_by_key.get((policy.candidate_group_id, policy.candidate_group_revision))
                if group is None or group.stage is not policy.stage_id:
                    raise ValueError("stage candidate group binding is invalid")
        default_ids = {item.id for item in self.project_default_model_policies}
        if len(default_ids) != len(self.project_default_model_policies):
            raise ValueError("project default model policies are not unique")
        default_projects = {item.project_id for item in self.project_default_model_policies}
        if len(default_projects) != len(self.project_default_model_policies):
            raise ValueError("project default model policy bindings are not unique")
        policy_model_ids = {
            item.model_profile_id
            for item in self.allowed_model_policies
        }
        for policy in self.stage_model_policies:
            if policy.fixed_model is not None:
                policy_model_ids.add(policy.fixed_model.model_profile_id)
            policy_model_ids.update(item.model_profile_id for item in policy.candidate_models)
        for policy in self.project_default_model_policies:
            if policy.fixed_model is not None:
                policy_model_ids.add(policy.fixed_model.model_profile_id)
            policy_model_ids.update(item.model_profile_id for item in policy.candidate_models)
        if any(model_id not in model_by_id for model_id in policy_model_ids):
            raise ValueError("model governance policy model identity is invalid")
        credential_by_secret = {item.secret_ref: item for item in self.credentials if item.enabled}
        for policy in self.allowed_model_policies:
            model = model_by_id.get(policy.model_profile_id)
            endpoint = endpoint_by_id.get(policy.endpoint_id)
            if model is None or endpoint is None or model.provider_id != policy.provider_id or model.endpoint_id != policy.endpoint_id or model.remote_model_id != policy.model_id or endpoint.provider_id != policy.provider_id:
                raise ValueError("allowed model policy identity is invalid")
            if endpoint.credential_ref is not None and (endpoint.credential_ref != policy.credential_ref or policy.credential_ref not in credential_by_secret):
                raise ValueError("allowed model policy credential identity is invalid")
        for policy in (*self.stage_model_policies, *self.project_default_model_policies):
            identities = ((policy.fixed_model,) if policy.fixed_model is not None else ()) + tuple(policy.candidate_models)
            for identity in identities:
                model = model_by_id.get(identity.model_profile_id)
                endpoint = endpoint_by_id.get(identity.endpoint_id)
                if model is None or endpoint is None or model.provider_id != identity.provider_id or model.endpoint_id != identity.endpoint_id or model.remote_model_id != identity.model_id or endpoint.provider_id != identity.provider_id:
                    raise ValueError("model governance identity is invalid")
                if endpoint.credential_ref is not None and (endpoint.credential_ref != identity.credential_ref or identity.credential_ref not in credential_by_secret):
                    raise ValueError("model governance credential identity is invalid")
        return self

    @model_validator(mode="after")
    def _fingerprint(self) -> ModelGatewayState:
        expected = _canonical_payload_fingerprint(_state_payload(self, include_fingerprint=False))
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model gateway state fingerprint mismatch")
        return self


def _state_payload(state: ModelGatewayState, *, include_fingerprint: bool = True) -> dict[str, object]:
    payload = state.model_dump(mode="json", exclude={"fingerprint"} if not include_fingerprint else None)
    if not state.candidate_groups:
        payload.pop("candidate_groups", None)
    for policy in payload["stage_model_policies"]:
        if policy["candidate_group_id"] is None:
            policy.pop("candidate_group_id")
            policy.pop("candidate_group_revision")
    return payload


class SQLiteModelGatewayStore(ConsumptionStoreMixin, CounterBundleStoreMixin):
    """Persist one versioned state document without accepting provider secrets."""

    _SCHEMA_VERSION = 3
    # D1 model-identity migration markers share this existing store; this
    # constant is a sub-version for future consumers, not a second schema.
    MODEL_IDENTITY_MIGRATION_SUB_VERSION = 1
    _LEGACY_MODEL_PROFILE_FIELDS = frozenset(
        {
            "availability",
            "availability_status",
            "availability_source",
            "availability_provenance",
            "credential_revision",
            "endpoint_revision",
            "discovered_at",
            "verified_at",
        }
    )
    _MIGRATED_MODEL_PROFILE_FIELDS = frozenset(
        {
            "billing_provenance",
            "billing_source",
            "catalog_source",
            "lifecycle_status",
            "deprecated",
            "metadata_updated_at",
            "metadata_source",
        }
    )

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or path.name in {"", ".", ".."}:
            raise ValueError("model gateway state path is invalid")
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as connection:
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS model_gateway_state (id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS model_invocation_receipts (fingerprint TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS model_invocation_attempts (project_id TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, request_id));"
                "CREATE TABLE IF NOT EXISTS model_invocation_budget_claims (envelope_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, request_id TEXT NOT NULL, reservation_id TEXT NOT NULL UNIQUE, envelope TEXT NOT NULL, evidence TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS model_invocation_budget_outcomes (project_id TEXT NOT NULL, request_id TEXT NOT NULL, receipt_fingerprint TEXT NOT NULL UNIQUE, response_fingerprint TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(project_id, request_id));"
            )
            initialize_consumption_schema(connection)

    def begin_attempt(self, attempt: ModelInvocationAttempt) -> None:
        if type(attempt) is not ModelInvocationAttempt or attempt.status is not AttemptStatus.PREPARED:
            raise TypeError("prepared model invocation attempt is invalid")
        payload = attempt.model_dump(mode="json")
        _assert_secret_free(payload)
        with sqlite3.connect(self._path) as connection:
            try:
                connection.execute(
                    "INSERT INTO model_invocation_attempts(project_id, request_id, payload) VALUES (?, ?, ?)",
                    (attempt.project_id, attempt.request_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("model invocation request already attempted") from error

    def get_attempt_for_project(self, project_id: str, request_id: str) -> ModelInvocationAttempt | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload FROM model_invocation_attempts WHERE project_id = ? AND request_id = ?",
                (project_id, request_id),
            ).fetchone()
        return None if row is None else ModelInvocationAttempt.model_validate_json(row[0])

    def _advance_attempt(self, attempt: ModelInvocationAttempt, expected: AttemptStatus) -> None:
        payload = attempt.model_dump(mode="json")
        _assert_secret_free(payload)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM model_invocation_attempts WHERE project_id = ? AND request_id = ?",
                (attempt.project_id, attempt.request_id),
            ).fetchone()
            if row is None:
                raise ValueError("model invocation attempt is missing")
            previous = ModelInvocationAttempt.model_validate_json(row[0])
            if previous.status is not expected or previous.request_fingerprint != attempt.request_fingerprint or previous.snapshot != attempt.snapshot or previous.attempt_schema_version != attempt.attempt_schema_version or previous.invocation_mode != attempt.invocation_mode or previous.authority_reference != attempt.authority_reference:
                raise ValueError("model invocation attempt transition rejected")
            connection.execute(
                "UPDATE model_invocation_attempts SET payload = ? WHERE project_id = ? AND request_id = ?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), attempt.project_id, attempt.request_id),
            )

    def mark_dispatch_intent(self, attempt: ModelInvocationAttempt) -> ModelInvocationAttempt:
        intent = ModelInvocationAttempt.model_validate({
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": AttemptStatus.DISPATCH_INTENT,
        })
        self._advance_attempt(intent, AttemptStatus.PREPARED)
        return intent

    def claim_budget_dispatch_intent(self, attempt, envelope, evidence):
        from embedded_copilot.model_gateway.invocation.contracts import (
            InvocationBudgetEvidenceV1,
            SingleInvocationBudgetEnvelopeV1,
        )
        envelope = SingleInvocationBudgetEnvelopeV1.model_validate(envelope.model_dump())
        evidence = InvocationBudgetEvidenceV1.model_validate(evidence.model_dump())
        if attempt.attempt_schema_version != 3 or attempt.status is not AttemptStatus.PREPARED or evidence.execution_snapshot_fingerprint != attempt.snapshot.fingerprint or evidence.envelope_fingerprint != envelope.fingerprint or envelope.project_id != attempt.project_id:
            raise ValueError("budget dispatch binding invalid")
        reservation_id = "budget-" + evidence.fingerprint.removeprefix("sha256:")
        intent = ModelInvocationAttempt.model_validate({
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": AttemptStatus.DISPATCH_INTENT,
            "budget_envelope_id": envelope.envelope_id,
            "budget_evidence_fingerprint": evidence.fingerprint,
            "budget_reservation_id": reservation_id,
        })
        payload = intent.model_dump(mode="json")
        envelope_payload = envelope.model_dump(mode="json")
        evidence_payload = evidence.model_dump(mode="json")
        for value in (payload, envelope_payload, evidence_payload):
            _assert_secret_free(value)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM model_invocation_attempts WHERE project_id = ? AND request_id = ?", (attempt.project_id, attempt.request_id)).fetchone()
            if row is None or ModelInvocationAttempt.model_validate_json(row[0]) != attempt:
                raise ValueError("budget prepared attempt changed")
            try:
                connection.execute("INSERT INTO model_invocation_budget_claims(envelope_id, tenant_id, project_id, request_id, reservation_id, envelope, evidence) VALUES (?, ?, ?, ?, ?, ?, ?)", (envelope.envelope_id, envelope.tenant_id, envelope.project_id, attempt.request_id, reservation_id, json.dumps(envelope_payload, sort_keys=True, separators=(",", ":")), json.dumps(evidence_payload, sort_keys=True, separators=(",", ":"))))
            except sqlite3.IntegrityError as error:
                raise ValueError("BUDGET_ENVELOPE_CLAIMED") from error
            connection.execute("UPDATE model_invocation_attempts SET payload = ? WHERE project_id = ? AND request_id = ?", (json.dumps(payload, sort_keys=True, separators=(",", ":")), attempt.project_id, attempt.request_id))
        return intent

    def budget_binding_for_attempt(self, attempt):
        if attempt.attempt_schema_version == 4:
            envelope, _, _, evidence = self.bundle_binding_for_attempt(attempt)
            if evidence is None:
                raise ValueError("generation budget evidence missing")
            return envelope, evidence
        from embedded_copilot.model_gateway.invocation.contracts import (
            InvocationBudgetEvidenceV1,
            SingleInvocationBudgetEnvelopeV1,
        )
        with sqlite3.connect(self._path) as connection:
            row = connection.execute("SELECT envelope, evidence FROM model_invocation_budget_claims WHERE envelope_id = ? AND reservation_id = ? AND project_id = ? AND request_id = ?", (attempt.budget_envelope_id, attempt.budget_reservation_id, attempt.project_id, attempt.request_id)).fetchone()
        if row is None:
            raise ValueError("budget claim missing")
        envelope, evidence = SingleInvocationBudgetEnvelopeV1.model_validate_json(row[0]), InvocationBudgetEvidenceV1.model_validate_json(row[1])
        if evidence.fingerprint != attempt.budget_evidence_fingerprint:
            raise ValueError("budget evidence mismatch")
        return envelope, evidence

    def complete_budget_violation(self, attempt, receipt, response):
        from embedded_copilot.model_gateway import ModelResponse
        response = ModelResponse.model_validate(response.model_dump(mode="python"))
        if (attempt.attempt_schema_version != 3 or attempt.status is not AttemptStatus.DISPATCH_INTENT
                or receipt.schema_version != 6 or receipt.result.value != "FAILED"
                or receipt.budget_reconciliation_state != "BOUND_VIOLATION"
                or receipt.project_id != attempt.project_id or receipt.request_id != attempt.request_id
                or receipt.budget_envelope_id != attempt.budget_envelope_id
                or receipt.budget_reservation_id != attempt.budget_reservation_id
                or receipt.budget_evidence_fingerprint != attempt.budget_evidence_fingerprint):
            raise ValueError("budget violation binding invalid")
        self.budget_binding_for_attempt(attempt)
        if len(response.content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("budget outcome exceeds evidence bound")
        response_payload = response.model_dump(mode="json")
        receipt_payload = receipt.model_dump(mode="json")
        unknown = ModelInvocationAttempt.model_validate({
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": AttemptStatus.OUTCOME_UNKNOWN,
        })
        for payload in (response_payload, receipt_payload, unknown.model_dump(mode="json")):
            _assert_secret_free(payload)
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM model_invocation_attempts WHERE project_id=? AND request_id=?", (attempt.project_id, attempt.request_id)).fetchone()
            if row is None or ModelInvocationAttempt.model_validate_json(row[0]) != attempt:
                raise ValueError("budget violation attempt changed")
            connection.execute("INSERT INTO model_invocation_receipts(fingerprint,schema_version,payload,created_at) VALUES(?,?,?,?)", (receipt.fingerprint, receipt.schema_version, encode(receipt_payload), receipt.completed_at.isoformat()))
            connection.execute("INSERT INTO model_invocation_budget_outcomes(project_id,request_id,receipt_fingerprint,response_fingerprint,response) VALUES(?,?,?,?,?)", (attempt.project_id, attempt.request_id, receipt.fingerprint, response.fingerprint, encode(response_payload)))
            connection.execute("UPDATE model_invocation_attempts SET payload=? WHERE project_id=? AND request_id=?", (encode(unknown.model_dump(mode="json")), attempt.project_id, attempt.request_id))
        return receipt

    def budget_violation_response_for_attempt(self, attempt):
        from embedded_copilot.model_gateway import ModelResponse
        self.budget_binding_for_attempt(attempt)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute("SELECT response_fingerprint,response FROM model_invocation_budget_outcomes WHERE project_id=? AND request_id=?", (attempt.project_id, attempt.request_id)).fetchone()
        if row is None:
            return None
        if json.loads(row[1]).get("kind") == "GENERATION_HTTP_FAILURE":
            return None
        # ModelResponse requires tuple-only carriers; normalize JSON arrays
        # without rewriting the stored original JSON or its fingerprint.
        def restore_tuples(value):
            if isinstance(value, list):
                return tuple(restore_tuples(item) for item in value)
            if isinstance(value, dict):
                return {key: restore_tuples(item) for key, item in value.items()}
            return value
        response = ModelResponse.model_validate(restore_tuples(json.loads(row[1])))
        if response.fingerprint != row[0]:
            raise ValueError("budget outcome fingerprint mismatch")
        return response

    def mark_attempt_unknown(self, attempt: ModelInvocationAttempt) -> ModelInvocationAttempt:
        unknown = ModelInvocationAttempt.model_validate({
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": AttemptStatus.OUTCOME_UNKNOWN,
        })
        self._advance_attempt(unknown, AttemptStatus.DISPATCH_INTENT)
        return unknown

    def complete_attempt(self, attempt: ModelInvocationAttempt, receipt: ModelInvocationReceipt) -> ModelInvocationReceipt:
        if (
            receipt.project_id != attempt.project_id
            or receipt.request_id != attempt.request_id
            or receipt.stage_id != attempt.snapshot.stage_id
            or receipt.selected_model != attempt.snapshot.selected_model
            or receipt.policy_source != attempt.snapshot.policy_source
            or (receipt.connection_id is not None and receipt.connection_id != attempt.snapshot.connection_id)
            or (receipt.provider_request_count != 1 if attempt.attempt_schema_version == 1 else receipt.provider_request_count != 0)
            or (attempt.attempt_schema_version == 3 and (
                receipt.budget_envelope_id != attempt.budget_envelope_id
                or receipt.budget_evidence_fingerprint != attempt.budget_evidence_fingerprint
                or receipt.budget_reservation_id != attempt.budget_reservation_id
            ))
            or receipt.result.value != "SUCCESS"
            or (attempt.attempt_schema_version >= 2 and (
                receipt.schema_version != (6 if attempt.attempt_schema_version == 3 else 5)
                or receipt.invocation_mode != attempt.invocation_mode
                or receipt.authority_reference != attempt.authority_reference
                or receipt.gateway_call_count != 1
                or not receipt.gateway_response_observed
            ))
        ):
            raise ValueError("model invocation receipt attempt binding mismatch")
        payload = receipt.model_dump(mode="json", exclude={"candidate_selection_evidence"} if receipt.candidate_selection_evidence is None else None)
        _assert_secret_free(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        completed = ModelInvocationAttempt.model_validate({
            **attempt.model_dump(mode="python", exclude={"fingerprint"}),
            "status": AttemptStatus.COMPLETED,
            "confirmed_provider_request_count": 1 if attempt.attempt_schema_version == 1 else None,
            "gateway_call_count": 1 if attempt.attempt_schema_version >= 2 else None,
            "gateway_response_observed": attempt.attempt_schema_version >= 2,
            "receipt_fingerprint": receipt.fingerprint,
        })
        attempt_payload = completed.model_dump(mode="json")
        _assert_secret_free(attempt_payload)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM model_invocation_attempts WHERE project_id = ? AND request_id = ?", (attempt.project_id, attempt.request_id)).fetchone()
            if row is None:
                raise ValueError("model invocation attempt is missing")
            previous = ModelInvocationAttempt.model_validate_json(row[0])
            if previous.status is not AttemptStatus.DISPATCH_INTENT or previous.request_fingerprint != attempt.request_fingerprint or previous.snapshot != attempt.snapshot or previous.attempt_schema_version != attempt.attempt_schema_version or previous.invocation_mode != attempt.invocation_mode or previous.authority_reference != attempt.authority_reference:
                raise ValueError("model invocation attempt transition rejected")
            connection.execute("INSERT INTO model_invocation_receipts(fingerprint, schema_version, payload, created_at) VALUES (?, ?, ?, ?)", (receipt.fingerprint, receipt.schema_version, encoded, receipt.completed_at.isoformat()))
            connection.execute("UPDATE model_invocation_attempts SET payload = ? WHERE project_id = ? AND request_id = ?", (json.dumps(attempt_payload, sort_keys=True, separators=(",", ":")), attempt.project_id, attempt.request_id))
        return ModelInvocationReceipt.model_validate_json(encoded)

    def save(self, state: ModelGatewayState) -> None:
        if type(state) is not ModelGatewayState:
            raise TypeError("model gateway state is invalid")
        unique_issues = _deduplicate_integrity_issues(state.integrity_issues)
        state_to_save = state
        if unique_issues != state.integrity_issues:
            normalized_payload = state.model_dump(mode="json")
            normalized_payload["integrity_issues"] = [
                item.model_dump(mode="json") for item in unique_issues
            ]
            normalized_payload["fingerprint"] = "sha256:" + "0" * 64
            state_to_save = ModelGatewayState.model_validate(normalized_payload)
        payload = _state_payload(state_to_save)
        _assert_secret_free(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            connection.executemany(
                "INSERT INTO model_gateway_state(id, schema_version, fingerprint, payload) VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET schema_version=excluded.schema_version, fingerprint=excluded.fingerprint, payload=excluded.payload",
                ((self._SCHEMA_VERSION, state_to_save.fingerprint, encoded),),
            )

    def load(self) -> ModelGatewayState | None:
        with sqlite3.connect(self._path) as connection:
            cursor = connection.cursor()
            query = cursor.execute
            row = query(
                "SELECT schema_version, fingerprint, payload FROM model_gateway_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        schema_version, fingerprint, encoded = row
        if not isinstance(encoded, str):
            raise ValueError("model gateway state schema is invalid")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise ValueError("model gateway state payload is invalid")
        if payload.get("fingerprint") != fingerprint:
            raise ValueError("model gateway state receipt mismatch")
        # All supported schemas must authenticate the persisted source before
        # migration or quarantine can project a different recovery view.
        if _canonical_payload_fingerprint(payload) != fingerprint:
            raise ValueError("model gateway state fingerprint mismatch")
        if schema_version not in {1, 2, self._SCHEMA_VERSION}:
            raise ValueError("model gateway state schema is invalid")
        _assert_secret_free(payload)

        # Schema v1 fingerprints cover the payload as it was persisted at the
        # time, before fields introduced by the v2 contract were added.
        if schema_version == 1:
            state, _ = _load_state_with_quarantine(payload, legacy=True)
            return state

        state, quarantined = _load_state_with_quarantine(
            payload,
            legacy=schema_version == 1,
        )
        if quarantined:
            # The source row was fingerprint-verified above.  Returning a
            # filtered projection is a recovery view; the original SQLite row
            # is intentionally left untouched until an explicit save.
            return state
        if state.fingerprint != fingerprint and not _matches_known_legacy_fingerprint(
            state, payload, fingerprint
        ):
            raise ValueError("model gateway state fingerprint mismatch")
        return state

    def migrate_legacy_connections(
        self, payload: Mapping[str, object]
    ) -> ModelGatewayState:
        """Project a legacy state into the canonical connection contract.

        This helper is intentionally non-persisting.  Callers must explicitly
        save the returned canonical state after reviewing the migration view.
        """

        if not isinstance(payload, Mapping):
            raise TypeError("legacy model gateway payload is invalid")
        state, _ = _load_state_with_quarantine(dict(payload), legacy=True)
        return state

    def append_receipt(self, receipt: ModelInvocationReceipt) -> ModelInvocationReceipt:
        if type(receipt) is not ModelInvocationReceipt:
            raise TypeError("model invocation receipt is invalid")
        payload = receipt.model_dump(
            mode="json",
            exclude={"candidate_selection_evidence"} if receipt.candidate_selection_evidence is None else None,
        )
        _assert_secret_free(payload)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload FROM model_invocation_receipts WHERE fingerprint = ?",
                (receipt.fingerprint,),
            ).fetchone()
            if row is not None:
                if row[0] != encoded:
                    raise ValueError("model invocation receipt conflict")
                return ModelInvocationReceipt.model_validate_json(row[0])
            try:
                connection.execute(
                    "INSERT INTO model_invocation_receipts(fingerprint, schema_version, payload, created_at) VALUES (?, ?, ?, ?)",
                    (receipt.fingerprint, receipt.schema_version, encoded, receipt.completed_at.isoformat()),
                )
            except sqlite3.IntegrityError as error:
                raced = connection.execute(
                    "SELECT payload FROM model_invocation_receipts WHERE fingerprint = ?",
                    (receipt.fingerprint,),
                ).fetchone()
                if raced is not None and raced[0] == encoded:
                    return ModelInvocationReceipt.model_validate_json(raced[0])
                raise ValueError("model invocation receipt conflict") from error
        return ModelInvocationReceipt.model_validate_json(encoded)

    def get_receipt(self, fingerprint: str) -> ModelInvocationReceipt | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload FROM model_invocation_receipts WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        return None if row is None else ModelInvocationReceipt.model_validate_json(row[0])

    def get_receipt_for_project(self, project_id: str, fingerprint: str) -> ModelInvocationReceipt | None:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT payload FROM model_invocation_receipts WHERE fingerprint = ? AND json_extract(payload, '$.project_id') = ?",
                (fingerprint, project_id),
            ).fetchone()
        return None if row is None else ModelInvocationReceipt.model_validate_json(row[0])

    def list_receipts_for_project(
        self, project_id: str, *, request_id: str | None = None, limit: int = 100,
        offset: int = 0,
    ) -> tuple[ModelInvocationReceipt, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("receipt limit is invalid")
        if type(offset) is not int or offset < 0:
            raise ValueError("receipt offset is invalid")
        query = "SELECT payload FROM model_invocation_receipts WHERE json_extract(payload, '$.project_id') = ?"
        parameters: tuple[object, ...] = (project_id,)
        if request_id is not None:
            query += " AND json_extract(payload, '$.request_id') = ?"
            parameters += (request_id,)
        query += " ORDER BY created_at DESC, fingerprint DESC LIMIT ? OFFSET ?"
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(query, parameters + (limit, offset)).fetchall()
        return tuple(ModelInvocationReceipt.model_validate_json(row[0]) for row in rows)

    def count_receipts_for_project(self, project_id: str, *, request_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM model_invocation_receipts WHERE json_extract(payload, '$.project_id') = ?"
        parameters: tuple[str, ...] = (project_id,)
        if request_id is not None:
            query += " AND json_extract(payload, '$.request_id') = ?"
            parameters += (request_id,)
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(query, parameters).fetchone()
        return int(row[0])

    def list_receipts(self, limit: int = 100) -> tuple[ModelInvocationReceipt, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("receipt limit is invalid")
        with sqlite3.connect(self._path) as connection:
            rows = connection.execute(
                "SELECT payload FROM model_invocation_receipts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(ModelInvocationReceipt.model_validate_json(row[0]) for row in rows)


def migrate_legacy_connections(payload: Mapping[str, object]) -> ModelGatewayState:
    """Return a secret-free, fail-closed migration projection."""

    if not isinstance(payload, Mapping):
        raise TypeError("legacy model gateway payload is invalid")
    state, _ = _load_state_with_quarantine(dict(payload), legacy=True)
    return state


def _load_state_with_quarantine(
    payload: object,
    *,
    legacy: bool = False,
) -> tuple[ModelGatewayState, bool]:
    """Restore valid records while quarantining only broken identities.

    The database row is fingerprint-checked by ``load`` before this function
    runs.  Consequently this is an integrity projection, not an attempt to
    repair or rewrite the persisted source document.
    """

    if not isinstance(payload, dict):
        raise ValueError("model gateway state payload is invalid")
    current_payload = dict(payload)
    if legacy:
        current_payload.setdefault(
            "engineering_stages",
            [item.model_dump(mode="json") for item in default_engineering_stage_registry()],
        )
        current_payload.setdefault("allowed_model_policies", [])
        current_payload.setdefault("stage_model_policies", [])
        current_payload.setdefault("project_default_model_policies", [])
    detected_at = datetime.now(UTC)
    issues: list[IdentityIntegrityIssue] = []

    def source_rows(field: str) -> list[object]:
        value = current_payload.get(field, [])
        if not isinstance(value, list):
            raise ValueError(f"model gateway state {field} is invalid")
        return value

    def parse_item(model_type: type[object], item: object) -> object:
        encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        return model_type.model_validate_json(encoded)  # type: ignore[attr-defined]

    def safe_value(item: object, field: str) -> str | None:
        if not isinstance(item, dict):
            return None
        value = item.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            return None
        lowered = value.lower()
        if any(token in lowered for token in ("api_key", "authorization", "password", "secret", "token")):
            return None
        return value.strip()

    def issue(
        record_type: str,
        item: object,
        index: int,
        reason: str,
        *,
        identity_status: IdentityStatus = IdentityStatus.INVALID,
    ) -> None:
        record_id = (
            safe_value(item, "id")
            or safe_value(item, "connection_id")
            or safe_value(item, "model_profile_id")
            or f"row-{index}"
        )
        issues.append(
            IdentityIntegrityIssue(
                record_type=record_type,
                record_id=record_id,
                provider_id=safe_value(item, "provider_id"),
                endpoint_id=safe_value(item, "endpoint_id"),
                credential_ref=(
                    item.get("credential_ref")
                    if isinstance(item, dict)
                    and isinstance(item.get("credential_ref"), str)
                    and re.fullmatch(
                        r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
                        item["credential_ref"].strip(),
                    )
                    else None
                ),
                model_profile_id=safe_value(item, "model_profile_id"),
                model_id=safe_value(item, "model_id") or safe_value(item, "remote_model_id"),
                identity_status=identity_status,
                reason=reason,
                detected_at=detected_at,
            )
        )

    def parse_rows(
        field: str,
        model_type: type[object],
        *,
        normalizer: Callable[[object], object] | None = None,
    ) -> list[object]:
        valid: list[object] = []
        for index, item in enumerate(source_rows(field)):
            try:
                value = parse_item(model_type, normalizer(item) if normalizer is not None else item)
            except LegacyModelMigrationError:
                issue(field.rstrip("s").upper(), item, index, "RECORD_MIGRATION_AMBIGUOUS")
                continue
            except Exception:  # noqa: BLE001 - quarantine boundary
                issue(field.rstrip("s").upper(), item, index, "RECORD_SCHEMA_INVALID")
                continue
            valid.append(value)
        return valid

    providers = parse_rows("providers", ProviderProfile)
    provider_ids: set[str] = set()
    unique_providers: list[ProviderProfile] = []
    for index, provider in enumerate(providers):
        if provider.provider_id in provider_ids:
            issue("PROVIDER", provider.model_dump(mode="python"), index, "PROVIDER_DUPLICATE")
            continue
        provider_ids.add(provider.provider_id)
        unique_providers.append(provider)

    endpoints = parse_rows("endpoints", EndpointProfile)
    endpoint_by_id: dict[str, EndpointProfile] = {}
    for index, endpoint in enumerate(endpoints):
        if endpoint.endpoint_id in endpoint_by_id or endpoint.provider_id not in provider_ids:
            issue(
                "ENDPOINT",
                endpoint.model_dump(mode="python"),
                index,
                "ENDPOINT_IDENTITY_INVALID",
            )
            continue
        endpoint_by_id[endpoint.endpoint_id] = endpoint

    credentials = parse_rows("credentials", CredentialProfile)
    credential_by_id: dict[str, CredentialProfile] = {}
    for index, credential in enumerate(credentials):
        if credential.credential_id in credential_by_id or credential.provider_id not in provider_ids:
            issue(
                "CREDENTIAL",
                credential.model_dump(mode="python"),
                index,
                "CREDENTIAL_IDENTITY_INVALID",
            )
            continue
        credential_by_id[credential.credential_id] = credential
    enabled_secret_refs = {
        item.secret_ref for item in credential_by_id.values() if item.enabled
    }

    migration_applied = False

    def legacy_value(item: object, *fields: str) -> object:
        if not isinstance(item, dict):
            return None
        for field in fields:
            if field in item:
                return item[field]
        return None

    def legacy_text(item: object, *fields: str) -> str | None:
        value = legacy_value(item, *fields)
        return safe_value({"value": value}, "value")

    def legacy_credential_ref(item: object, *fields: str) -> str | None:
        value = legacy_value(item, *fields)
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
            value.strip(),
        ):
            return None
        return value.strip()

    def legacy_sources() -> list[tuple[str, object]]:
        result: list[tuple[str, object]] = []
        profiles = current_payload.get("chat_provider_profiles")
        if profiles is not None:
            if not isinstance(profiles, dict):
                issue("PROVIDER_CONNECTION", profiles, 0, "RECORD_MIGRATION_AMBIGUOUS")
            else:
                for provider_id, raw in profiles.items():
                    if not isinstance(provider_id, str) or not isinstance(raw, dict):
                        issue("PROVIDER_CONNECTION", raw, len(result), "RECORD_MIGRATION_AMBIGUOUS")
                        continue
                    material = dict(raw)
                    material.setdefault("provider_id", provider_id)
                    result.append((f"chat_provider_profiles:{provider_id}", material))
        for field in ("legacy_provider_state", "legacy_provider", "provider_configuration"):
            raw = current_payload.get(field)
            if raw is not None:
                if isinstance(raw, dict):
                    result.append((field, raw))
                else:
                    issue("PROVIDER_CONNECTION", raw, len(result), "RECORD_MIGRATION_AMBIGUOUS")
        if not result and any(
            field in current_payload
            for field in (
                "chat_provider",
                "chat_connection_name",
                "chat_endpoint_id",
                "chat_credential_ref",
                "chat_base_url",
            )
        ):
            result.append(("chat_provider", current_payload))
        return result

    raw_connections = current_payload.get("connections")
    connections: list[ProviderConnection] = []
    migration_version = current_payload.get("migration_version", 0)
    if type(migration_version) is not int or migration_version < 0:
        migration_version = 0
        migration_applied = True
    migration_source_fingerprint = current_payload.get("migration_source_fingerprint")
    if not isinstance(migration_source_fingerprint, str):
        migration_source_fingerprint = None

    # A pre-connection state has no connection rows.  Only an explicit legacy
    # provider projection is eligible for automatic migration; preset provider
    # and endpoint rows alone must never manufacture configured connections.
    if raw_connections in (None, []):
        source_records = legacy_sources()
        source_materials: list[dict[str, str | None]] = []
        for index, (source_key, raw) in enumerate(source_records):
            provider_id = legacy_text(raw, "provider_id", "provider", "chat_provider")
            if provider_id is None:
                if len(provider_ids) == 1:
                    provider_id = next(iter(provider_ids))
                else:
                    issue("PROVIDER_CONNECTION", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                    continue
            if provider_id not in provider_ids:
                issue("PROVIDER_CONNECTION", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                continue
            provider_endpoints = tuple(
                endpoint
                for endpoint in endpoint_by_id.values()
                if endpoint.provider_id == provider_id
            )
            endpoint_id = legacy_text(raw, "endpoint_id", "chat_endpoint_id", "endpoint")
            base_url = legacy_text(raw, "base_url", "chat_base_url")
            endpoint = endpoint_by_id.get(endpoint_id) if endpoint_id is not None else None
            if endpoint_id is not None and (
                endpoint is None or endpoint.provider_id != provider_id
            ):
                endpoint = None
            if endpoint is None and endpoint_id is None and base_url is not None:
                matching = tuple(
                    item
                    for item in provider_endpoints
                    if item.base_url.rstrip("/") == base_url.rstrip("/")
                )
                if len(matching) == 1:
                    endpoint = matching[0]
            if endpoint is None and endpoint_id is None and base_url is None and len(provider_endpoints) == 1:
                endpoint = provider_endpoints[0]
            if endpoint is None:
                issue("PROVIDER_CONNECTION", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                continue

            credential_ref = legacy_credential_ref(
                raw, "credential_ref", "chat_credential_ref"
            )
            raw_credential = legacy_value(raw, "credential_ref", "chat_credential_ref")
            if raw_credential is not None and credential_ref is None:
                issue("PROVIDER_CONNECTION", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                continue
            if credential_ref is None:
                credential_ref = endpoint.credential_ref
            provider_credentials = tuple(
                sorted(
                    {
                        item.secret_ref
                        for item in credential_by_id.values()
                        if item.provider_id == provider_id
                    }
                )
            )
            if credential_ref is None:
                if len(provider_credentials) == 1:
                    credential_ref = provider_credentials[0]
                elif len(provider_credentials) > 1:
                    issue("PROVIDER_CONNECTION", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                    # The endpoint is known, so retain a quarantined aggregate
                    # without guessing a credential reference.
                    credential_ref = None
                elif provider_id != "local":
                    # This is the historical provider-scoped reference, not a
                    # secret.  The Credential Manager remains the secret owner.
                    credential_ref = f"credential:{provider_id}"
            name = legacy_text(raw, "connection_name", "chat_connection_name")
            if name is None:
                name = next(
                    item.display_name
                    for item in unique_providers
                    if item.provider_id == provider_id
                )
            source_identity = {
                "source": source_key,
                "provider_id": provider_id,
                "endpoint_id": endpoint.endpoint_id,
                "credential_ref": credential_ref,
            }
            source_materials.append(source_identity)
            source_fingerprint = _canonical_payload_fingerprint(source_identity)
            connection_id = legacy_text(raw, "connection_id")
            if connection_id is None:
                connection_id = f"conn-{uuid5(NAMESPACE_URL, source_fingerprint).hex}"
            source_is_ambiguous = len(provider_credentials) > 1 and credential_ref is None
            connection = ProviderConnection(
                connection_id=connection_id,
                provider_id=provider_id,
                connection_name=name,
                status=(
                    ProviderConnectionStatus.QUARANTINED
                    if source_is_ambiguous
                    else ProviderConnectionStatus.ACTIVE
                ),
                endpoint_id=endpoint.endpoint_id,
                credential_ref=credential_ref,
                created_at=detected_at,
                updated_at=detected_at,
                legacy_source=source_key,
            )
            if not any(item.endpoint_id == connection.endpoint_id for item in connections):
                connections.append(connection)
            migration_applied = True
        if source_materials:
            migration_version = max(migration_version, 1)
            migration_source_fingerprint = _canonical_payload_fingerprint(
                {"migration_version": 1, "sources": sorted(source_materials, key=lambda item: (item["source"] or "", item["provider_id"] or ""))}
            )
        elif source_records:
            migration_version = max(migration_version, 1)
            migration_source_fingerprint = _canonical_payload_fingerprint(
                {"migration_version": 1, "sources": []}
            )
            migration_applied = True
    else:
        connections = parse_rows("connections", ProviderConnection)  # type: ignore[assignment]

    connection_by_id: dict[str, ProviderConnection] = {}
    owned_endpoint_ids: set[str] = set()
    valid_connections: list[ProviderConnection] = []
    for index, connection in enumerate(connections):
        endpoint = endpoint_by_id.get(connection.endpoint_id)
        if (
            connection.connection_id in connection_by_id
            or connection.provider_id not in provider_ids
            or endpoint is None
            or endpoint.provider_id != connection.provider_id
            or connection.endpoint_id in owned_endpoint_ids
            or (
                endpoint.credential_ref is not None
                and endpoint.credential_ref != connection.credential_ref
            )
        ):
            issue(
                "PROVIDER_CONNECTION",
                connection.model_dump(mode="python"),
                index,
                "PROVIDER_CONNECTION_OWNERSHIP_INVALID",
            )
            continue
        connection_by_id[connection.connection_id] = connection
        owned_endpoint_ids.add(connection.endpoint_id)
        valid_connections.append(connection)

    def normalize_legacy_model(item: object) -> object:
        nonlocal migration_applied
        if not isinstance(item, dict):
            raise LegacyModelMigrationError("legacy model row is not an object")
        normalized = dict(item)
        original = dict(normalized)
        provider_id = safe_value(normalized, "provider_id") or safe_value(normalized, "provider")
        if provider_id is None:
            if len(provider_ids) != 1:
                raise LegacyModelMigrationError("legacy model provider is ambiguous")
            provider_id = next(iter(provider_ids))
        if provider_id not in provider_ids:
            raise LegacyModelMigrationError("legacy model provider is unknown")

        endpoint_id = safe_value(normalized, "endpoint_id") or safe_value(normalized, "endpoint")
        provider_endpoints = tuple(
            endpoint.endpoint_id
            for endpoint in endpoint_by_id.values()
            if endpoint.provider_id == provider_id
        )
        if endpoint_id is None:
            if len(provider_endpoints) != 1:
                raise LegacyModelMigrationError("legacy model endpoint is ambiguous")
            endpoint_id = provider_endpoints[0]
        endpoint = endpoint_by_id.get(endpoint_id)
        if endpoint is None or endpoint.provider_id != provider_id:
            raise LegacyModelMigrationError("legacy model endpoint is invalid")

        remote_model_id = safe_value(normalized, "remote_model_id") or safe_value(normalized, "model_id")
        if remote_model_id is None:
            raise LegacyModelMigrationError("legacy model id is missing")
        model_profile_id = safe_value(normalized, "model_profile_id") or safe_value(normalized, "id")
        if model_profile_id is None:
            model_profile_id = f"model-{provider_id}-{remote_model_id}"
        normalized.update(
            {
                "model_profile_id": model_profile_id,
                "provider_id": provider_id,
                "endpoint_id": endpoint_id,
                "remote_model_id": remote_model_id,
                "display_name": safe_value(normalized, "display_name") or remote_model_id,
                "advertised_family": safe_value(normalized, "advertised_family") or provider_id,
            }
        )
        # Pydantic's strict extra-field policy must not reject known legacy aliases.
        for alias in ("id", "provider", "endpoint", "model_id"):
            normalized.pop(alias, None)
        migration_applied = migration_applied or normalized != original
        return normalized

    models = parse_rows("models", ModelProfile, normalizer=normalize_legacy_model)
    model_by_id: dict[str, ModelProfile] = {}
    for index, model in enumerate(models):
        endpoint = endpoint_by_id.get(model.endpoint_id)
        if (
            model.model_profile_id in model_by_id
            or model.provider_id not in provider_ids
            or endpoint is None
            or endpoint.provider_id != model.provider_id
        ):
            issue("MODEL", model.model_dump(mode="python"), index, "MODEL_IDENTITY_INVALID")
            continue
        model_by_id[model.model_profile_id] = model

    def normalize_legacy_identity(
        item: object,
        *,
        allow_id_alias: bool = True,
    ) -> object:
        nonlocal migration_applied
        if not isinstance(item, dict):
            raise LegacyModelMigrationError("legacy identity is not an object")
        normalized = dict(item)
        original = dict(normalized)
        model_profile_id = safe_value(normalized, "model_profile_id")
        if model_profile_id is None and allow_id_alias:
            model_profile_id = safe_value(normalized, "id")
        model_id = safe_value(normalized, "model_id") or safe_value(normalized, "remote_model_id")
        provider_id = safe_value(normalized, "provider_id") or safe_value(normalized, "provider")
        endpoint_id = safe_value(normalized, "endpoint_id") or safe_value(normalized, "endpoint")
        candidates = tuple(model_by_id.values())
        if model_profile_id is not None:
            model = model_by_id.get(model_profile_id)
            if model is None:
                raise LegacyModelMigrationError("legacy policy model is unknown")
        else:
            matches = tuple(
                model
                for model in candidates
                if (provider_id is None or model.provider_id == provider_id)
                and (endpoint_id is None or model.endpoint_id == endpoint_id)
                and (model_id is None or model.remote_model_id == model_id)
            )
            if len(matches) != 1:
                raise LegacyModelMigrationError("legacy policy model is ambiguous")
            model = matches[0]
            model_profile_id = model.model_profile_id
        endpoint = endpoint_by_id.get(endpoint_id or model.endpoint_id)
        if endpoint is None or endpoint.provider_id != model.provider_id:
            raise LegacyModelMigrationError("legacy policy endpoint is invalid")
        normalized.update(
            {
                "provider_id": provider_id or model.provider_id,
                "endpoint_id": endpoint_id or model.endpoint_id,
                "credential_ref": safe_value(normalized, "credential_ref") or endpoint.credential_ref or "credential:local",
                "model_profile_id": model_profile_id,
                "model_id": model_id or model.remote_model_id,
            }
        )
        aliases = ["provider", "endpoint", "remote_model_id"]
        if allow_id_alias:
            aliases.append("id")
        for alias in aliases:
            normalized.pop(alias, None)
        migration_applied = migration_applied or normalized != original
        return normalized

    probes = parse_rows("capability_probes", CapabilityProbeResult)
    valid_probes: list[CapabilityProbeResult] = []
    for index, probe in enumerate(probes):
        model = model_by_id.get(probe.model_profile_id)
        if model is None or model.endpoint_id != probe.endpoint_id:
            issue(
                "CAPABILITY_PROBE",
                probe.model_dump(mode="python"),
                index,
                "MODEL_PROBE_IDENTITY_INVALID",
            )
            continue
        valid_probes.append(probe)

    def valid_identity(identity: object) -> tuple[bool, str, IdentityStatus]:
        if not isinstance(identity, dict):
            return False, "MODEL_GOVERNANCE_IDENTITY_INVALID", IdentityStatus.INVALID
        model = model_by_id.get(identity.get("model_profile_id"))
        endpoint = endpoint_by_id.get(identity.get("endpoint_id"))
        provider_id = identity.get("provider_id")
        if (
            model is None
            or endpoint is None
            or model.provider_id != provider_id
            or model.endpoint_id != identity.get("endpoint_id")
            or model.remote_model_id != identity.get("model_id")
            or endpoint.provider_id != provider_id
        ):
            return False, "MODEL_GOVERNANCE_IDENTITY_INVALID", IdentityStatus.INVALID
        credential_ref = identity.get("credential_ref")
        if endpoint.credential_ref is not None and endpoint.credential_ref != credential_ref:
            return False, "MODEL_GOVERNANCE_CREDENTIAL_MISMATCH", IdentityStatus.STALE
        if endpoint.credential_ref is not None and credential_ref not in enabled_secret_refs:
            return False, "MODEL_GOVERNANCE_CREDENTIAL_UNAVAILABLE", IdentityStatus.STALE
        return True, "", IdentityStatus.VALID

    allowed: list[AllowedModelPolicy] = []
    for index, raw in enumerate(source_rows("allowed_model_policies")):
        try:
            normalized_policy = normalize_legacy_identity(raw, allow_id_alias=False)
            policy = parse_item(
                AllowedModelPolicy,
                normalized_policy,
            )
        except LegacyModelMigrationError:
            issue("ALLOWED_MODEL_POLICY", raw, index, "RECORD_MIGRATION_AMBIGUOUS")
            continue
        except Exception:  # noqa: BLE001 - quarantine boundary
            issue("ALLOWED_MODEL_POLICY", raw, index, "POLICY_SCHEMA_INVALID")
            continue
        ok, reason, identity_status = valid_identity(policy.model_dump(mode="python"))
        if not ok:
            issue(
                "ALLOWED_MODEL_POLICY",
                policy.model_dump(mode="python"),
                index,
                reason,
                identity_status=identity_status,
            )
            continue
        allowed.append(policy)

    def parse_governance_policies(
        field: str,
        model_type: type[object],
    ) -> list[object]:
        valid: list[object] = []
        for index, raw in enumerate(source_rows(field)):
            try:
                normalized = dict(raw) if isinstance(raw, dict) else raw
                if isinstance(normalized, dict):
                    if normalized.get("fixed_model") is not None:
                        normalized["fixed_model"] = normalize_legacy_identity(normalized["fixed_model"])
                    if isinstance(normalized.get("candidate_models"), list):
                        normalized["candidate_models"] = [
                            normalize_legacy_identity(item)
                            for item in normalized["candidate_models"]
                        ]
                policy = parse_item(model_type, normalized)
            except LegacyModelMigrationError:
                issue(field.rstrip("s").upper(), raw, index, "RECORD_MIGRATION_AMBIGUOUS")
                continue
            except Exception:  # noqa: BLE001 - quarantine boundary
                issue(field.rstrip("s").upper(), raw, index, "POLICY_SCHEMA_INVALID")
                continue
            identities: list[object] = []
            fixed = getattr(policy, "fixed_model", None)
            if fixed is not None:
                identities.append(fixed.model_dump(mode="python"))
            identities.extend(item.model_dump(mode="python") for item in getattr(policy, "candidate_models", ()))
            invalid = next((valid_identity(item) for item in identities if not valid_identity(item)[0]), None)
            if invalid is not None:
                _, reason, identity_status = invalid
                issue(
                    field.rstrip("s").upper(),
                    policy.model_dump(mode="python"),
                    index,
                    reason,
                    identity_status=identity_status,
                )
                continue
            valid.append(policy)
        return valid

    stage_policies = parse_governance_policies("stage_model_policies", StageModelPolicy)
    default_policies = parse_governance_policies(
        "project_default_model_policies", ProjectDefaultModelPolicy
    )

    existing_issues: list[IdentityIntegrityIssue] = []
    raw_issues = current_payload.get("integrity_issues", [])
    if isinstance(raw_issues, list):
        for raw_issue in raw_issues:
            try:
                existing_issues.append(parse_item(IdentityIntegrityIssue, raw_issue))
            except Exception:  # noqa: BLE001 - invalid diagnostics are not trusted
                continue
    combined_issues = existing_issues + issues
    all_issues = _deduplicate_integrity_issues(combined_issues)
    for legacy_field in (
        "chat_provider",
        "chat_connection_name",
        "chat_endpoint_id",
        "chat_credential_ref",
        "chat_base_url",
        "chat_provider_profiles",
        "legacy_provider_state",
        "legacy_provider",
        "provider_configuration",
    ):
        current_payload.pop(legacy_field, None)
    current_payload.update(
        {
            "providers": [item.model_dump(mode="json") for item in unique_providers],
            "endpoints": [item.model_dump(mode="json") for item in endpoint_by_id.values()],
            "connections": [item.model_dump(mode="json") for item in valid_connections],
            "credentials": [item.model_dump(mode="json") for item in credential_by_id.values()],
            "models": [item.model_dump(mode="json") for item in model_by_id.values()],
            "capability_probes": [item.model_dump(mode="json") for item in valid_probes],
            "allowed_model_policies": [item.model_dump(mode="json") for item in allowed],
            "stage_model_policies": [item.model_dump(mode="json") for item in stage_policies],
            "project_default_model_policies": [item.model_dump(mode="json") for item in default_policies],
            "integrity_issues": [item.model_dump(mode="json") for item in all_issues],
            "migration_version": migration_version,
            "migration_source_fingerprint": migration_source_fingerprint,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    encoded = json.dumps(current_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        ModelGatewayState.model_validate_json(encoded),
        bool(issues) or migration_applied or len(all_issues) != len(combined_issues),
    )


def _load_state_with_current_fingerprint(payload: object, *, legacy: bool = False) -> ModelGatewayState:
    if not isinstance(payload, dict):
        raise ValueError("model gateway state payload is invalid")  # noqa: TRY004 - preserve public error contract
    current_payload = dict(payload)
    if legacy:
        current_payload.setdefault("engineering_stages", [item.model_dump(mode="json") for item in default_engineering_stage_registry()])
        current_payload.setdefault("allowed_model_policies", [])
        current_payload.setdefault("stage_model_policies", [])
        current_payload.setdefault("project_default_model_policies", [])
    current_payload["fingerprint"] = "sha256:" + "0" * 64
    encoded = json.dumps(current_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ModelGatewayState.model_validate_json(encoded)


def _canonical_payload_fingerprint(payload: dict[object, object]) -> str:
    normalized = dict(payload)
    normalized.pop("fingerprint", None)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _matches_known_legacy_fingerprint(
    state: ModelGatewayState,
    payload: dict[object, object],
    fingerprint: object,
) -> bool:
    models = payload.get("models")
    if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
        return False
    historical_fields = SQLiteModelGatewayStore._LEGACY_MODEL_PROFILE_FIELDS - {
        "availability_status",
        "availability_provenance",
    }
    if any(historical_fields.intersection(item) for item in models):
        return False
    historical_fields = SQLiteModelGatewayStore._LEGACY_MODEL_PROFILE_FIELDS - {
        "availability_status",
        "availability_provenance",
    }
    extension_present = any(
        SQLiteModelGatewayStore._MIGRATED_MODEL_PROFILE_FIELDS.intersection(item)
        for item in models
    )
    fields_to_strip = SQLiteModelGatewayStore._LEGACY_MODEL_PROFILE_FIELDS
    if not extension_present:
        fields_to_strip |= SQLiteModelGatewayStore._MIGRATED_MODEL_PROFILE_FIELDS

    def matches(strip_fields: frozenset[str]) -> bool:
        normalized = state.model_dump(mode="json", exclude={"fingerprint"})
        if "candidate_groups" not in payload:
            normalized.pop("candidate_groups", None)
        payload_stage_policies = payload.get("stage_model_policies")
        if isinstance(payload_stage_policies, list):
            for normalized_policy, source_policy in zip(
                normalized.get("stage_model_policies", []), payload_stage_policies, strict=False
            ):
                if isinstance(source_policy, dict):
                    for field in ("candidate_group_id", "candidate_group_revision"):
                        if field not in source_policy:
                            normalized_policy.pop(field, None)
        for field in (
            "engineering_stages",
            "allowed_model_policies",
            "stage_model_policies",
            "project_default_model_policies",
        ):
            if field not in payload:
                normalized.pop(field, None)
        if "connections" not in payload:
            normalized.pop("connections", None)
        if "migration_version" not in payload:
            normalized.pop("migration_version", None)
        if "migration_source_fingerprint" not in payload:
            normalized.pop("migration_source_fingerprint", None)
        if "model_identity_migration_version" not in payload:
            normalized.pop("model_identity_migration_version", None)
        if "model_identity_source_fingerprint" not in payload:
            normalized.pop("model_identity_source_fingerprint", None)
        if "identity_correlations" not in payload:
            normalized.pop("identity_correlations", None)
        payload_issues = payload.get("integrity_issues")
        if isinstance(payload_issues, list):
            normalized_issues = normalized.get("integrity_issues", [])
            for normalized_issue, payload_issue in zip(normalized_issues, payload_issues, strict=False):
                if isinstance(payload_issue, dict):
                    for field in (
                        "source_type",
                        "record_reference",
                        "source_fingerprint",
                        "migration_version",
                        "migration_sub_version",
                    ):
                        if field not in payload_issue:
                            normalized_issue.pop(field, None)
        for model in normalized["models"]:
            for field in strip_fields:
                model.pop(field, None)
        legacy_payload = dict(payload)
        for field in (
            "chat_provider",
            "chat_connection_name",
            "chat_endpoint_id",
            "chat_credential_ref",
            "chat_base_url",
            "chat_provider_profiles",
            "legacy_provider_state",
            "legacy_provider",
            "provider_configuration",
        ):
            legacy_payload.pop(field, None)
        legacy_payload["models"] = [dict(item) for item in models]
        for model in legacy_payload["models"]:
            for field in strip_fields:
                model.pop(field, None)
        return _canonical_payload_fingerprint(normalized) == _canonical_payload_fingerprint(legacy_payload) == fingerprint

    # The compatibility test above intentionally accepts both historical
    # payloads that predate the split fields and transitional payloads that
    # already contain the new authoritative status/provenance fields.
    return matches(historical_fields) or matches(fields_to_strip)


def _assert_secret_free(value: object) -> None:
    banned = {
        "api_key",
        "access_token",
        "authorization",
        "client_secret",
        "headers",
        "password",
        "private_key",
        "raw_secret",
        "secret",
        "token",
    }
    if isinstance(value, dict):
        if banned.intersection(value):
            raise ValueError("model gateway state contains a secret field")
        for item in value.values():
            _assert_secret_free(item)
    elif isinstance(value, list):
        for item in value:
            _assert_secret_free(item)


__all__ = (
    "IdentityIntegrityIssue",
    "LegacyIdentityCorrelation",
    "LegacyModelMigrationError",
    "ModelGatewayState",
    "SQLiteModelGatewayStore",
    "migrate_legacy_connections",
)
