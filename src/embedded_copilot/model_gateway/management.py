"""Application service for provider/model catalog management and projections."""

from __future__ import annotations

import copy
from threading import RLock
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import uuid4

from .catalog import get_usable_models
from .contracts import ModelRequest, ModelUsage, canonical_fingerprint
from .governance import (
    AllowedModelPolicy,
    BoundModelIdentity,
    CandidateGroup,
    EngineeringStage,
    EngineeringStageDefinition,
    ModelSelectionResult,
    ModelSelectionTaskProfile,
    PolicyScope,
    ProjectDefaultModelPolicy,
    SelectionMode,
    StageModelPolicy,
    default_engineering_stage_registry,
)
from .metrics import AccountUsageSnapshot, ModelExecutionRecord
from .persistence import (
    IdentityIntegrityIssue,
    LegacyIdentityCorrelation,
    ModelGatewayState,
    SQLiteModelGatewayStore,
)
from .profiles import (
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    CredentialProfile,
    EndpointProfile,
    GlobalModelPolicy,
    IdentityStatus,
    ModelAvailability,
    ModelCapability,
    ModelProfile,
    ProjectModelPolicy,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    ProviderUsageCapability,
    RecordProvenance,
    RoutingRequest,
    RoutingStrategy,
    TaskProfile,
    TaskType,
    UsageSnapshotStatus,
    VerificationStatus,
    default_task_profiles,
)
from .registry import (
    CapabilityProbePort,
    CapabilityProbeResult,
    CapabilityProbeService,
    EndpointRegistry,
    ModelDiscoveryPort,
    ModelDiscoveryService,
    ModelRegistry,
    ProviderConnectionRegistry,
    ProviderRegistry,
)
from .router import ModelRouter
from .selection import ModelSelectionError, ModelSelectionService
from .usage import AccountUsagePort, AccountUsageSnapshotCache, ModelUsageAggregator


@runtime_checkable
class ConnectionProbePort(Protocol):
    def test_connection(self, endpoint: EndpointProfile) -> str: ...


class ModelGatewayManagementService:
    """Keep catalog, policy and metrics state explicit and injectable."""

    def __init__(
        self,
        *,
        store: SQLiteModelGatewayStore | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        self.providers = ProviderRegistry()
        self.endpoints = EndpointRegistry()
        self.connections = ProviderConnectionRegistry(
            providers=self.providers,
            endpoints=self.endpoints,
            clock=self._clock,
            id_factory=self._id_factory,
        )
        self.models = ModelRegistry()
        self._model_state_lock = RLock()
        self._credentials: dict[str, CredentialProfile] = {}
        self._task_profiles: dict[str, TaskProfile] = {
            item.task_type.value: copy.deepcopy(item) for item in default_task_profiles()
        }
        self._project_policies: dict[str, ProjectModelPolicy] = {}
        self._global_policy = GlobalModelPolicy()
        self._store = store
        self._usage = ModelUsageAggregator()
        self._account_usage: AccountUsageSnapshotCache | None = None
        self._restored_usage_snapshots: tuple[AccountUsageSnapshot, ...] = ()
        self._capability_probes: dict[tuple[str, str], CapabilityProbeResult] = {}
        self._engineering_stages: tuple[EngineeringStageDefinition, ...] = default_engineering_stage_registry()
        self._allowed_model_policies: dict[str, AllowedModelPolicy] = {}
        self._stage_model_policies: dict[tuple[str, EngineeringStage], StageModelPolicy] = {}
        self._candidate_groups: dict[tuple[str, int], CandidateGroup] = {}
        self._project_default_model_policies: dict[str, ProjectDefaultModelPolicy] = {}
        self._integrity_issues: tuple[IdentityIntegrityIssue, ...] = ()
        self._identity_correlations: tuple[LegacyIdentityCorrelation, ...] = ()
        self._migration_version = 0
        self._migration_source_fingerprint: str | None = None
        self._model_identity_migration_version = 0
        self._model_identity_source_fingerprint: str | None = None
        self._discovery: ModelDiscoveryService | None = None
        self._probes: CapabilityProbeService | None = None

    def add_provider(self, profile: ProviderProfile) -> None:
        self.providers.register(profile)

    def upsert_provider(self, profile: ProviderProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            self.providers.register(profile)
        else:
            self.providers.replace(profile)

    @property
    def receipt_store(self) -> SQLiteModelGatewayStore | None:
        """Return the shared persistence boundary for runtime receipts."""

        return self._store

    def add_endpoint(self, profile: EndpointProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            raise ValueError("endpoint provider is not registered")
        self.endpoints.register(profile)

    def upsert_endpoint(self, profile: EndpointProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            raise ValueError("endpoint provider is not registered")
        for connection in self.connections.list():
            if connection.endpoint_id != profile.endpoint_id:
                continue
            if connection.provider_id != profile.provider_id:
                raise ValueError("endpoint provider ownership is already registered")
            if (
                profile.credential_ref is not None
                and profile.credential_ref != connection.credential_ref
            ):
                raise ValueError("endpoint credential ownership is already registered")
        if self.endpoints.get(profile.endpoint_id) is None:
            self.endpoints.register(profile)
        else:
            self.endpoints.replace(profile)

    def add_connection(self, connection: ProviderConnection) -> None:
        self.connections.register(connection)

    def create_connection(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        connection_name: str | None = None,
        credential_ref: str | None = None,
        status: ProviderConnectionStatus = ProviderConnectionStatus.ACTIVE,
        legacy_source: str | None = None,
        connection_id: str | None = None,
    ) -> ProviderConnection:
        return self.connections.create(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            connection_name=connection_name,
            credential_ref=credential_ref,
            status=status,
            legacy_source=legacy_source,
            connection_id=connection_id,
        )

    def connection(self, connection_id: str) -> ProviderConnection | None:
        return self.connections.get(connection_id)

    def list_connections(self) -> tuple[ProviderConnection, ...]:
        return self.connections.list()

    def update_connection(
        self,
        connection: str | ProviderConnection,
        replacement: ProviderConnection | None = None,
        **changes: object,
    ) -> ProviderConnection:
        return self.connections._update(connection, replacement, **changes)

    def disable_connection(self, connection_id: str) -> ProviderConnection:
        result = self.connections.disable(connection_id)
        self._invalidate_connection_runtime(result)
        return result

    def tombstone_connection(self, connection_id: str) -> ProviderConnection:
        result = self.connections.tombstone(connection_id)
        self._invalidate_connection_runtime(result)
        return result

    def _invalidate_connection_runtime(self, connection: ProviderConnection) -> None:
        endpoint = self.endpoints.get(connection.endpoint_id)
        if endpoint is not None and endpoint.enabled:
            self.endpoints.replace(
                endpoint.model_copy(
                    update={
                        "enabled": False,
                        "health_status": "UNKNOWN",
                        "updated_at": self._clock(),
                    }
                )
            )
        if self._account_usage is not None:
            self._account_usage.invalidate(
                connection_id=connection.connection_id,
                endpoint_id=connection.endpoint_id,
            )

    def add_credential(self, profile: CredentialProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            raise ValueError("credential provider is not registered")
        if profile.credential_id in self._credentials:
            raise ValueError("duplicate credential profile")
        self._credentials[profile.credential_id] = copy.deepcopy(profile)

    def upsert_credential(self, profile: CredentialProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            raise ValueError("credential provider is not registered")
        self._credentials[profile.credential_id] = copy.deepcopy(profile)

    def add_model(self, profile: ModelProfile) -> None:
        if self.providers.get(profile.provider_id) is None:
            raise ValueError("model provider is not registered")
        endpoint = self.endpoints.get(profile.endpoint_id)
        if endpoint is None or endpoint.provider_id != profile.provider_id:
            raise ValueError("model endpoint binding is invalid")
        if profile.record_provenance is RecordProvenance.UNKNOWN:
            profile = profile.model_copy(update={"record_provenance": RecordProvenance.EXPLICIT_CONFIGURATION})
        self.models.register(profile)

    def set_discovery_port(self, port: ModelDiscoveryPort) -> None:
        self._discovery = ModelDiscoveryService(port)

    def set_capability_probe_port(self, port: CapabilityProbePort) -> None:
        self._probes = CapabilityProbeService(port)

    def discover_models(self, endpoint_id: str) -> tuple[ModelProfile, ...]:
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            raise ValueError("endpoint is not registered")
        if self._discovery is None:
            raise RuntimeError("model discovery is unavailable")
        result = self._discovery.discover(endpoint)
        for profile in result:
            self.models.upsert(profile)
        return result

    def probe_capability(self, model_profile_id: str, probe_type: str):
        model = self.models.get(model_profile_id)
        if model is None:
            raise ValueError("model is not registered")
        endpoint = self.endpoints.get(model.endpoint_id)
        if endpoint is None:
            raise ValueError("model endpoint is not registered")
        if self._probes is None:
            raise RuntimeError("capability probe is unavailable")
        result = self._probes.probe(endpoint, model, probe_type)
        with self._model_state_lock:
            return self._commit_capability_probe(model_profile_id, probe_type, model, endpoint, result)

    def _commit_capability_probe(
        self,
        model_profile_id: str,
        probe_type: str,
        model: ModelProfile,
        endpoint: EndpointProfile,
        result: CapabilityProbeResult,
    ) -> CapabilityProbeResult:
        current = self.models.get(model_profile_id)
        if (
            current is None
            or current.provider_id != model.provider_id
            or current.endpoint_id != model.endpoint_id
            or current.remote_model_id != model.remote_model_id
            or current.credential_revision != model.credential_revision
            or current.endpoint_revision != model.endpoint_revision
        ):
            raise ValueError("capability probe model revision changed")
        model = current
        try:
            capability = ModelCapability(probe_type.upper())
        except ValueError:
            capability = None
        if capability is not None:
            expected_credential_ref = endpoint.credential_ref or (
                "credential:local" if endpoint.provider_id == "local" else None
            )
            if (
                result.provider_id != model.provider_id
                or result.endpoint_id != model.endpoint_id
                or result.credential_ref != expected_credential_ref
                or result.model_profile_id != model.model_profile_id
                or result.model_id != model.remote_model_id
                or result.credential_revision != model.credential_revision
                or result.endpoint_revision != model.endpoint_revision
            ):
                raise ValueError("capability probe identity is invalid")
            evidence = CapabilityEvidence(
                capability=capability,
                provenance=CapabilityProvenance.PROBED,
                status=result.status,
                source="capability_probe",
                observed_at=result.timestamp,
                provider_id=result.provider_id,
                endpoint_id=result.endpoint_id,
                credential_ref=result.credential_ref,
                model_profile_id=result.model_profile_id,
                model_id=result.model_id,
                credential_revision=result.credential_revision,
                endpoint_revision=result.endpoint_revision,
            )
            existing = tuple(item for item in model.capability_sources if item.capability is not capability)
            updated = model.model_copy(
                update={
                    "capability_sources": existing + (evidence,),
                    "capabilities": (
                        tuple(dict.fromkeys((*model.capabilities, capability)))
                        if result.status is CapabilityStatus.PASS else model.capabilities
                    ),
                    "verification_status": VerificationStatus.VERIFIED if result.status is CapabilityStatus.PASS else model.verification_status,
                    "verified_at": result.timestamp if result.status is CapabilityStatus.PASS else model.verified_at,
                }
            )
            self.models.upsert(updated)
        key = (result.model_profile_id, result.probe_type)
        previous_probe = self._capability_probes.get(key)
        self._capability_probes[key] = copy.deepcopy(result)
        if self.persistent:
            try:
                self.save()
            except Exception:
                if capability is not None:
                    self.models.upsert(model)
                if previous_probe is None:
                    self._capability_probes.pop(key, None)
                else:
                    self._capability_probes[key] = previous_probe
                raise
        return result

    def list_providers(self) -> tuple[ProviderProfile, ...]:
        return self.providers.list()

    def list_endpoints(self) -> tuple[EndpointProfile, ...]:
        return self.endpoints.list()

    def list_credentials(self) -> tuple[CredentialProfile, ...]:
        return tuple(copy.deepcopy(value) for value in self._credentials.values())

    def list_models(self) -> tuple[ModelProfile, ...]:
        return self.models.list()

    def get_usable_models(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        credential_revision: str | None = None,
        endpoint_revision: str | None = None,
    ) -> tuple[ModelProfile, ...]:
        """Canonical provider/endpoint-scoped selector input."""

        return get_usable_models(
            self.models.list(),
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            credential_revision=credential_revision,
            endpoint_revision=endpoint_revision,
        )

    def model_profile(self, model_profile_id: str) -> ModelProfile | None:
        return self.models.get(model_profile_id)

    def endpoint_profile(self, endpoint_id: str) -> EndpointProfile | None:
        return self.endpoints.get(endpoint_id)

    def credential_profile(self, credential_id: str) -> CredentialProfile | None:
        return copy.deepcopy(self._credentials.get(credential_id))

    def set_global_policy(self, policy: GlobalModelPolicy) -> None:
        self._global_policy = copy.deepcopy(policy)

    def set_active_model(self, model_profile_id: str) -> None:
        if self.models.get(model_profile_id) is None:
            raise ValueError("model is not registered")
        self._global_policy = self._global_policy.model_copy(
            update={"active_model_profile_id": model_profile_id}
        )

    def set_project_policy(self, policy: ProjectModelPolicy) -> None:
        self._project_policies[policy.project_id] = copy.deepcopy(policy)

    def set_task_profile(self, profile: TaskProfile) -> None:
        self._task_profiles[profile.task_type.value] = copy.deepcopy(profile)

    def task_profiles(self) -> tuple[TaskProfile, ...]:
        return tuple(copy.deepcopy(value) for value in self._task_profiles.values())

    def global_policy(self) -> GlobalModelPolicy:
        return copy.deepcopy(self._global_policy)

    def project_policy(self, project_id: str) -> ProjectModelPolicy | None:
        return copy.deepcopy(self._project_policies.get(project_id))

    def engineering_stages(self) -> tuple[EngineeringStageDefinition, ...]:
        return copy.deepcopy(self._engineering_stages)

    def integrity_issues(self) -> tuple[IdentityIntegrityIssue, ...]:
        """Return persisted binding problems in a secret-free diagnostic view."""

        return copy.deepcopy(self._integrity_issues)

    def identity_correlations(self) -> tuple[LegacyIdentityCorrelation, ...]:
        """Return historical identity evidence without granting runtime authority."""

        return copy.deepcopy(self._identity_correlations)

    def list_allowed_model_policies(self) -> tuple[AllowedModelPolicy, ...]:
        return tuple(copy.deepcopy(value) for value in self._allowed_model_policies.values())

    def allowed_model_policy(self, policy_id: str) -> AllowedModelPolicy | None:
        return copy.deepcopy(self._allowed_model_policies.get(policy_id))

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    def _new_id(self, prefix: str) -> str:
        value = self._id_factory(prefix)
        used = set(self._allowed_model_policies) | {item.id for item in self._stage_model_policies.values()} | {item.id for item in self._project_default_model_policies.values()}
        if value not in used:
            return value
        suffix = 2
        while f"{value}-{suffix}" in used:
            suffix += 1
        return f"{value}-{suffix}"

    @staticmethod
    def _identity_key(value: AllowedModelPolicy | BoundModelIdentity) -> tuple[str, str, str, str, str]:
        return (
            value.provider_id,
            value.endpoint_id,
            value.credential_ref,
            value.model_profile_id,
            value.model_id,
        )

    def _validate_bound_identity(self, value: BoundModelIdentity) -> ModelProfile:
        # This is the canonical catalog lookup.  Policy writes must never
        # persist an identity merely because the client supplied matching
        # strings.
        model = self.models.get(value.model_profile_id)
        if model is None:
            raise ModelSelectionError("MODEL_NOT_FOUND")
        if model.provider_id != value.provider_id:
            raise ModelSelectionError("MODEL_PROVIDER_MISMATCH")
        if model.endpoint_id != value.endpoint_id:
            raise ModelSelectionError("MODEL_ENDPOINT_MISMATCH")
        if model.remote_model_id != value.model_id:
            raise ModelSelectionError("MODEL_MODEL_ID_MISMATCH")
        endpoint = self.endpoints.get(value.endpoint_id)
        if endpoint is None or endpoint.provider_id != value.provider_id:
            raise ModelSelectionError("MODEL_ENDPOINT_MISMATCH")
        if endpoint.credential_ref is not None and endpoint.credential_ref != value.credential_ref:
            raise ModelSelectionError("MODEL_CREDENTIAL_MISMATCH")
        if endpoint.credential_ref is not None and not any(
            item.secret_ref == value.credential_ref
            and item.provider_id == value.provider_id
            and item.enabled
            for item in self._credentials.values()
        ):
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        return model

    def model_identity_status(
        self,
        model: ModelProfile,
        *,
        credential_ref: str,
    ) -> tuple[IdentityStatus, str | None]:
        """Return identity integrity without conflating model availability."""

        provider = self.providers.get(model.provider_id)
        endpoint = self.endpoints.get(model.endpoint_id)
        if provider is None or endpoint is None or endpoint.provider_id != model.provider_id:
            return IdentityStatus.INVALID, "MODEL_IDENTITY_INVALID"
        expected_credential = endpoint.credential_ref or "credential:local"
        if endpoint.credential_ref is None and model.provider_id == "local":
            expected_credential = credential_ref
        if credential_ref != expected_credential:
            return IdentityStatus.INVALID, "MODEL_CREDENTIAL_MISMATCH"
        if not endpoint.enabled or (
            endpoint.credential_ref is not None
            and not any(
                item.secret_ref == endpoint.credential_ref
                and item.provider_id == endpoint.provider_id
                and item.enabled
                for item in self._credentials.values()
            )
        ):
            return IdentityStatus.STALE, "MODEL_CREDENTIAL_GATE_FAILED"
        if not model.enabled:
            return IdentityStatus.STALE, "MODEL_DISABLED"
        return IdentityStatus.VALID, None

    def model_enablement(
        self,
        identity: BoundModelIdentity,
    ) -> tuple[bool, str | None, IdentityStatus]:
        """Derive enablement from canonical state; never accept a client flag."""

        try:
            model = self._validate_bound_identity(identity)
        except ModelSelectionError as error:
            return False, error.code, IdentityStatus.INVALID
        identity_status, identity_reason = self.model_identity_status(
            model, credential_ref=identity.credential_ref
        )
        if identity_status is not IdentityStatus.VALID:
            return False, identity_reason or "MODEL_IDENTITY_INVALID", identity_status
        availability = (
            model.availability_status
            if isinstance(model.availability_status, ModelAvailability)
            else ModelAvailability(model.availability_status)
        )
        if availability not in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}:
            return False, "MODEL_AVAILABILITY_GATE_FAILED", identity_status
        verification_status = (
            model.verification_status
            if isinstance(model.verification_status, VerificationStatus)
            else VerificationStatus(model.verification_status)
        )
        if verification_status is not VerificationStatus.VERIFIED:
            return False, "MODEL_VERIFICATION_GATE_FAILED", identity_status
        record_provenance = (
            model.record_provenance
            if isinstance(model.record_provenance, RecordProvenance)
            else RecordProvenance(model.record_provenance)
        )
        if record_provenance in {RecordProvenance.UNKNOWN, RecordProvenance.OFFICIAL_CATALOG}:
            return False, "MODEL_PROVENANCE_GATE_FAILED", identity_status
        evidence_by_capability = {
            item.capability: item for item in model.capability_sources
        }
        if not model.capabilities or any(
            evidence_by_capability.get(capability) is None
            or (
                evidence_by_capability[capability].status
                if isinstance(evidence_by_capability[capability].status, CapabilityStatus)
                else CapabilityStatus(evidence_by_capability[capability].status)
            ) is not CapabilityStatus.PASS
            or not evidence_by_capability[capability].matches_model_identity(
                provider_id=model.provider_id,
                endpoint_id=model.endpoint_id,
                credential_ref=identity.credential_ref,
                model_profile_id=model.model_profile_id,
                model_id=model.remote_model_id,
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
            )
            for capability in model.capabilities
        ):
            return False, "MODEL_CAPABILITY_GATE_FAILED", identity_status
        return True, None, identity_status

    def create_allowed_model_policy(
        self,
        *,
        scope: PolicyScope,
        project_id: str | None,
        stage_id: EngineeringStage | None,
        identity: BoundModelIdentity,
        enabled: bool = True,
        priority: int = 100,
    ) -> AllowedModelPolicy:
        model = self._validate_bound_identity(identity)
        if enabled:
            can_enable, reason, _ = self.model_enablement(identity)
            if not can_enable:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
        now = self._now()
        policy = AllowedModelPolicy(
            id=self._new_id("allowed-model"),
            scope=scope,
            project_id=project_id,
            stage_id=stage_id,
            provider_id=identity.provider_id,
            endpoint_id=identity.endpoint_id,
            credential_ref=identity.credential_ref,
            model_profile_id=identity.model_profile_id,
            model_id=identity.model_id,
            enabled=enabled,
            priority=priority,
            created_at=now,
            updated_at=now,
        )
        self._assert_allowed_unique(policy)
        self._allowed_model_policies[policy.id] = copy.deepcopy(policy)
        return policy

    def _assert_allowed_unique(self, policy: AllowedModelPolicy, *, replacing: str | None = None) -> None:
        for current in self._allowed_model_policies.values():
            if current.id == replacing:
                continue
            if (
                current.scope is policy.scope
                and current.project_id == policy.project_id
                and current.stage_id is policy.stage_id
                and self._identity_key(current) == self._identity_key(policy)
            ):
                raise ValueError("allowed model policy already exists")

    def update_allowed_model_policy(self, policy: AllowedModelPolicy) -> AllowedModelPolicy:
        if policy.id not in self._allowed_model_policies:
            raise KeyError("allowed model policy not found")
        identity = BoundModelIdentity(
            provider_id=policy.provider_id,
            endpoint_id=policy.endpoint_id,
            credential_ref=policy.credential_ref,
            model_profile_id=policy.model_profile_id,
            model_id=policy.model_id,
        )
        model = self._validate_bound_identity(identity)
        if policy.enabled:
            can_enable, reason, _ = self.model_enablement(identity)
            if not can_enable:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
        self._assert_allowed_unique(policy, replacing=policy.id)
        self._allowed_model_policies[policy.id] = copy.deepcopy(policy)
        return policy

    def set_allowed_model_enabled(self, policy_id: str, enabled: bool) -> AllowedModelPolicy:
        current = self._allowed_model_policies.get(policy_id)
        if current is None:
            raise KeyError("allowed model policy not found")
        if type(enabled) is not bool:
            raise TypeError("enabled is invalid")
        if enabled:
            identity = BoundModelIdentity(
                provider_id=current.provider_id,
                endpoint_id=current.endpoint_id,
                credential_ref=current.credential_ref,
                model_profile_id=current.model_profile_id,
                model_id=current.model_id,
            )
            can_enable, reason, _ = self.model_enablement(identity)
            if not can_enable:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
        updated = current.model_copy(update={"enabled": enabled, "updated_at": self._now()})
        self._allowed_model_policies[policy_id] = updated
        return copy.deepcopy(updated)

    def effective_allowed_model_policies(self, *, project_id: str, stage_id: EngineeringStage) -> tuple[AllowedModelPolicy, ...]:
        return ModelSelectionService.effective_allowed(
            self.list_allowed_model_policies(), project_id=project_id, stage_id=stage_id
        )

    def stage_model_policy(self, project_id: str, stage_id: EngineeringStage) -> StageModelPolicy | None:
        return copy.deepcopy(self._stage_model_policies.get((project_id, stage_id)))

    def stage_model_policies(self, project_id: str) -> tuple[StageModelPolicy, ...]:
        return tuple(copy.deepcopy(item) for (pid, _), item in self._stage_model_policies.items() if pid == project_id)

    def add_candidate_group(self, group: CandidateGroup) -> CandidateGroup:
        if type(group) is not CandidateGroup:
            raise TypeError("candidate group is invalid")
        checked = CandidateGroup.model_validate(copy.deepcopy(group.model_dump(mode="python")))
        key = (checked.group_id, checked.revision)
        if key in self._candidate_groups:
            raise ValueError("candidate group version already exists")
        self._candidate_groups[key] = checked
        return copy.deepcopy(checked)

    def candidate_group(self, group_id: str, revision: int) -> CandidateGroup | None:
        return copy.deepcopy(self._candidate_groups.get((group_id, revision)))

    def set_stage_model_policy(self, policy: StageModelPolicy) -> StageModelPolicy:
        if policy.selection_mode is SelectionMode.CANDIDATE_GROUP:
            group = self.candidate_group(policy.candidate_group_id, policy.candidate_group_revision)
            if group is None or group.stage is not policy.stage_id:
                raise ModelSelectionError("MODEL_SELECTION_FAILED")
        allowed = {
            self._identity_key(item)
            for item in self.effective_allowed_model_policies(
                project_id=policy.project_id, stage_id=policy.stage_id
            )
        }
        for identity in ((policy.fixed_model,) if policy.fixed_model is not None else ()) + tuple(policy.candidate_models):
            model = self._validate_bound_identity(identity)
            can_enable, reason, _ = self.model_enablement(identity)
            if not can_enable:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
            if self._identity_key(identity) not in allowed:
                raise ModelSelectionError("MODEL_NOT_ALLOWED")
            required_caps = next(
                (set(item.required_capabilities) for item in self._engineering_stages if item.stage_id is policy.stage_id),
                set(),
            )
            evidence_by_capability = {item.capability: item for item in model.capability_sources}
            if not required_caps.issubset(set(model.capabilities)) or any(
                evidence_by_capability.get(capability) is None
                or evidence_by_capability[capability].status is not CapabilityStatus.PASS
                or not evidence_by_capability[capability].matches_model_identity(
                    provider_id=model.provider_id,
                    endpoint_id=model.endpoint_id,
                    credential_ref=identity.credential_ref,
                    model_profile_id=model.model_profile_id,
                    model_id=model.remote_model_id,
                    credential_revision=model.credential_revision,
                    endpoint_revision=model.endpoint_revision,
                )
                for capability in required_caps
            ):
                raise ModelSelectionError("MODEL_CAPABILITY_MISMATCH")
        self._stage_model_policies[(policy.project_id, policy.stage_id)] = copy.deepcopy(policy)
        return policy

    def delete_stage_model_policy(self, project_id: str, stage_id: EngineeringStage) -> bool:
        return self._stage_model_policies.pop((project_id, stage_id), None) is not None

    def project_default_model_policy(self, project_id: str) -> ProjectDefaultModelPolicy | None:
        return copy.deepcopy(self._project_default_model_policies.get(project_id))

    def set_project_default_model_policy(self, policy: ProjectDefaultModelPolicy) -> ProjectDefaultModelPolicy:
        project_allowed = {
            self._identity_key(item)
            for item in self.list_allowed_model_policies()
            if item.enabled
            and item.scope in {PolicyScope.GLOBAL, PolicyScope.PROJECT}
            and (item.scope is PolicyScope.GLOBAL or item.project_id == policy.project_id)
        }
        for identity in ((policy.fixed_model,) if policy.fixed_model is not None else ()) + tuple(policy.candidate_models):
            can_enable, reason, _ = self.model_enablement(identity)
            if not can_enable:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
            if self._identity_key(identity) not in project_allowed:
                raise ModelSelectionError("MODEL_NOT_ALLOWED")
        self._project_default_model_policies[policy.project_id] = copy.deepcopy(policy)
        return policy

    def delete_project_default_model_policy(self, project_id: str) -> bool:
        return self._project_default_model_policies.pop(project_id, None) is not None

    def select_governed_model(
        self,
        task_profile: ModelSelectionTaskProfile,
        *,
        request_override: BoundModelIdentity | None = None,
        available_credential_refs: Sequence[str] | None = None,
        endpoint_health: Mapping[str, str] | None = None,
        quota_available_by_endpoint: Mapping[str, bool] | None = None,
        strict_gates: bool = False,
        candidate_runtime_eligibility: Callable[[BoundModelIdentity], None] | None = None,
    ) -> ModelSelectionResult:
        stage_policy = self.stage_model_policy(task_profile.project_id, task_profile.stage)
        project_policy = self.project_default_model_policy(task_profile.project_id)
        effective_policy = stage_policy or project_policy
        auto_mode = (
            request_override is None
            and effective_policy is not None
            and effective_policy.selection_mode is SelectionMode.AUTO
        )
        auto_evidence_ready = strict_gates and candidate_runtime_eligibility is not None
        endpoint_credentials = {
            item.endpoint_id: item.credential_ref
            for item in self.endpoints.list()
            if item.credential_ref is not None
        }
        health = (
            dict(endpoint_health)
            if endpoint_health is not None
            else {item.endpoint_id: item.health_status for item in self.endpoints.list()}
        )
        quota = {}
        snapshots_by_endpoint: dict[str, list[AccountUsageSnapshot]] = {}
        for snapshot in self.account_usage_snapshots():
            snapshots_by_endpoint.setdefault(snapshot.endpoint_id, []).append(snapshot)
        for endpoint_id, snapshots in snapshots_by_endpoint.items():
            newest_at = max(item.updated_at for item in snapshots)
            latest = [item for item in snapshots if item.updated_at == newest_at]
            if len(latest) == 1 and latest[0].quota_remaining is not None:
                quota[endpoint_id] = latest[0].quota_remaining > 0
        # A provider without quota query support cannot produce a fresh quota
        # snapshot. Absence alone is not evidence of exhaustion; an existing
        # snapshot or explicit exhausted endpoint health remains authoritative.
        for endpoint in self.endpoints.list():
            provider = self.providers.get(endpoint.provider_id)
            if (
                endpoint.endpoint_id not in snapshots_by_endpoint
                and provider is not None
                and ProviderUsageCapability.QUOTA_QUERY not in provider.usage_capabilities
            ):
                quota[endpoint.endpoint_id] = True
        if quota_available_by_endpoint is not None:
            quota = dict(quota_available_by_endpoint)
        service = ModelSelectionService(
            models=self.models.list(),
            credential_ref_by_endpoint=endpoint_credentials,
            available_credential_refs=(
                tuple(available_credential_refs)
                if available_credential_refs is not None
                else tuple(item.secret_ref for item in self._credentials.values() if item.enabled)
            ),
            endpoint_health=health,
            quota_available_by_endpoint=quota,
            strict_gates=True if auto_mode else strict_gates,
        )

        def candidate_eligibility(identity: BoundModelIdentity) -> None:
            enabled, reason, _ = self.model_enablement(identity)
            if not enabled:
                raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
            if auto_mode and not auto_evidence_ready:
                raise ModelSelectionError("INSUFFICIENT_EVIDENCE", insufficient=True)
            if candidate_runtime_eligibility is not None:
                candidate_runtime_eligibility(identity)

        return service.select_model(
            task_profile,
            stage_policy,
            self.list_allowed_model_policies(),
            request_override=request_override,
            project_policy=project_policy,
            candidate_group=(
                self.candidate_group(stage_policy.candidate_group_id, stage_policy.candidate_group_revision)
                if stage_policy is not None and stage_policy.selection_mode is SelectionMode.CANDIDATE_GROUP
                else None
            ),
            candidate_eligibility=candidate_eligibility,
        )

    def _validate_runtime_eligibility(
        self,
        identity: BoundModelIdentity,
        *,
        credential_revision_for_connection: Callable[[str], str | None],
        endpoint_revision: Callable[[EndpointProfile], str | None],
        auto_task_profile: ModelSelectionTaskProfile | None = None,
        auto_policy_revision: str | None = None,
    ) -> tuple[str, str | None, str]:
        """Recheck the selected identity against current governance before execution."""

        if auto_task_profile is not None:
            policy = self.stage_model_policy(auto_task_profile.project_id, auto_task_profile.stage)
            if policy is None:
                policy = self.project_default_model_policy(auto_task_profile.project_id)
            if (
                policy is None
                or policy.selection_mode is not SelectionMode.AUTO
                or auto_policy_revision is None
                or canonical_fingerprint(policy, exclude=set()) != auto_policy_revision
            ):
                raise ModelSelectionError("MODEL_SELECTION_FAILED")
            if self._identity_key(identity) not in {
                self._identity_key(item)
                for item in self.effective_allowed_model_policies(
                    project_id=auto_task_profile.project_id,
                    stage_id=auto_task_profile.stage,
                )
            }:
                raise ModelSelectionError("MODEL_NOT_ALLOWED")

        enabled, reason, _ = self.model_enablement(identity)
        if not enabled:
            raise ModelSelectionError(reason or "MODEL_ENABLEMENT_GATE_FAILED")
        provider = self.providers.get(identity.provider_id)
        endpoint = self.endpoints.get(identity.endpoint_id)
        model = self.models.get(identity.model_profile_id)
        if provider is None or not provider.enabled or endpoint is None or not endpoint.enabled or model is None:
            raise ModelSelectionError("MODEL_AVAILABILITY_GATE_FAILED")

        connections = tuple(
            item for item in self.connections.list()
            if item.provider_id == identity.provider_id
            and item.endpoint_id == identity.endpoint_id
            and item.credential_ref == endpoint.credential_ref
            and item.status is ProviderConnectionStatus.ACTIVE
        )
        if len(connections) != 1:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        connection = connections[0]
        try:
            self.connections.validate_identity(connection)
        except (TypeError, ValueError) as error:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED") from error

        try:
            current_credential_revision = credential_revision_for_connection(connection.connection_id)
            current_endpoint_revision = endpoint_revision(endpoint)
        except (TypeError, ValueError, RuntimeError) as error:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED") from error
        if endpoint.credential_ref is not None and current_credential_revision is None:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        if model.credential_revision != current_credential_revision:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        if current_endpoint_revision is None or model.endpoint_revision != current_endpoint_revision:
            raise ModelSelectionError("MODEL_ENDPOINT_MISMATCH")

        # The latest evidence for this connection and endpoint is authoritative;
        # an older positive observation must not override a newer failure.
        all_snapshots = self.account_usage_snapshots()
        snapshots = tuple(
            item for item in all_snapshots
            if item.endpoint_id == identity.endpoint_id
        )
        if not snapshots:
            identity_snapshot_exists = any(
                item.provider_id == identity.provider_id
                and (
                    item.connection_id == connection.connection_id
                    or item.credential_ref == identity.credential_ref
                )
                for item in all_snapshots
            )
            if (
                ProviderUsageCapability.QUOTA_QUERY not in provider.usage_capabilities
                and not identity_snapshot_exists
            ):
                return connection.connection_id, current_credential_revision, current_endpoint_revision
            raise ModelSelectionError("MODEL_QUOTA_GATE_FAILED", insufficient=True)
        newest_at = max(item.updated_at for item in snapshots)
        latest = tuple(item for item in snapshots if item.updated_at == newest_at)
        if len(latest) != 1:
            raise ModelSelectionError("MODEL_QUOTA_GATE_FAILED", insufficient=True)
        snapshot = latest[0]
        age = self._now() - snapshot.updated_at
        if (
            snapshot.provider_id != identity.provider_id
            or snapshot.connection_id != connection.connection_id
            or snapshot.credential_ref != identity.credential_ref
            or snapshot.credential_revision != current_credential_revision
            or snapshot.status is not UsageSnapshotStatus.AVAILABLE
            or snapshot.quota_remaining is None
            or snapshot.quota_remaining <= 0
            or age < timedelta(0)
            or age > timedelta(seconds=300)
            or snapshot.updated_at < endpoint.updated_at
        ):
            raise ModelSelectionError(
                "MODEL_QUOTA_GATE_FAILED",
                insufficient=(
                    snapshot.status in {UsageSnapshotStatus.UNKNOWN, UsageSnapshotStatus.UNAVAILABLE, UsageSnapshotStatus.STALE}
                    or snapshot.quota_remaining is None
                    or age < timedelta(0)
                    or age > timedelta(seconds=300)
                    or snapshot.updated_at < endpoint.updated_at
                ),
            )
        return connection.connection_id, current_credential_revision, current_endpoint_revision

    def route(self, request: RoutingRequest):
        return ModelRouter().select(
            request,
            self.models.list(),
            reliability={item.model_profile_id: item for item in self._usage.reliability()},
        )

    def routing_request_for(
        self,
        request: ModelRequest,
        *,
        available_credential_refs: tuple[str, ...] = (),
    ) -> RoutingRequest:
        """Project a provider-neutral request into the canonical router contract."""

        task_type = TaskType.GENERAL_CONVERSATION
        if request.task_type is not None:
            try:
                task_type = TaskType(request.task_type)
            except ValueError:
                task_type = TaskType.GENERAL_CONVERSATION
        task_profile = self._task_profiles.get(task_type.value) or self._task_profiles[TaskType.GENERAL_CONVERSATION.value]
        project_id = request.project_id or request.engineering_context.project_id
        project_policy = self._project_policies.get(project_id)
        credential_by_endpoint = tuple(
            (endpoint.endpoint_id, endpoint.credential_ref)
            for endpoint in self.endpoints.list()
            if endpoint.credential_ref is not None
        )
        endpoint_health = tuple(
            (endpoint.endpoint_id, endpoint.health_status)
            for endpoint in self.endpoints.list()
        )
        return RoutingRequest(
            request_id=request.request_id,
            project_id=project_id,
            task_profile=task_profile,
            routing_strategy=(
                RoutingStrategy(request.routing_strategy)
                if request.routing_strategy is not None and request.routing_strategy in RoutingStrategy._value2member_map_
                else None
            ),
            request_model_override=None,
            project_policy=copy.deepcopy(project_policy),
            global_policy=copy.deepcopy(self._global_policy),
            available_credential_refs=tuple(available_credential_refs),
            credential_ref_by_endpoint=credential_by_endpoint,
            endpoint_health=endpoint_health,
            preferred_model=request.preferred_model,
            preferred_provider=request.preferred_provider,
        )

    def route_model_request(
        self,
        request: ModelRequest,
        *,
        available_credential_refs: tuple[str, ...] = (),
    ) -> tuple[object, ModelProfile]:
        routing_request = self.routing_request_for(
            request, available_credential_refs=available_credential_refs
        )
        selection = self.route(routing_request)
        model = self.models.get(selection.model_profile_id)
        if model is None:
            raise ValueError("selected model is not registered")
        return selection, model

    def test_connection(self, endpoint_id: str, probe: ConnectionProbePort) -> EndpointProfile:
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            raise ValueError("endpoint is not registered")
        if not isinstance(probe, ConnectionProbePort):
            raise TypeError("connection probe port is invalid")
        status = probe.test_connection(copy.deepcopy(endpoint))
        if not isinstance(status, str) or status not in {"AVAILABLE", "DEGRADED", "UNAVAILABLE", "AUTH_FAILED", "QUOTA_EXHAUSTED", "UNKNOWN"}:
            raise ValueError("connection probe result is invalid")
        checked = endpoint.model_copy(update={"health_status": status})
        self.endpoints.replace(checked)
        return checked

    def record_execution(self, record: ModelExecutionRecord, *, task_accepted: bool | None = None) -> None:
        self._usage.record(self._with_estimated_cost(record), task_accepted=task_accepted)

    def _with_estimated_cost(self, record: ModelExecutionRecord) -> ModelExecutionRecord:
        usage = record.usage
        model = self.models.get(record.selected_model)
        pricing = model.pricing if model is not None else None
        if usage is None or pricing is None or usage.provider_reported_cost is not None or usage.estimated_cost is not None:
            return record
        if pricing.input_token_price is None or pricing.output_token_price is None or pricing.currency is None or pricing.pricing_unit not in {"TOKEN", "1K_TOKENS"}:
            return record
        cached = usage.cached_input_tokens or 0
        input_price = pricing.input_token_price
        cached_price = pricing.cached_input_price if pricing.cached_input_price is not None else input_price
        cost = ((max(usage.input_tokens - cached, 0) * input_price) + (cached * cached_price) + (usage.output_tokens * pricing.output_token_price))
        if pricing.pricing_unit == "1K_TOKENS":
            cost /= 1000.0
        material = {
            **usage.model_dump(mode="python"),
            "estimated_cost": cost,
            "currency": pricing.currency,
            "usage_source": "ESTIMATED",
            "fingerprint": "sha256:" + "0" * 64,
        }
        estimated = ModelUsage.model_validate(material)
        return record.model_copy(update={"usage": estimated})

    def set_account_usage_port(self, port: AccountUsagePort, *, ttl_seconds: float = 300.0) -> None:
        self._account_usage = AccountUsageSnapshotCache(port, ttl_seconds=ttl_seconds)
        self._account_usage.restore(self._restored_usage_snapshots)

    def read_account_usage(
        self,
        endpoint_id: str,
        *,
        credential_ref: str | None = None,
        connection_id: str | None = None,
        credential_revision: str | None = None,
        force: bool = False,
    ) -> AccountUsageSnapshot:
        endpoint = self.endpoints.get(endpoint_id)
        if endpoint is None:
            raise ValueError("endpoint is not registered")
        if connection_id is not None:
            connection = self.connections.get(connection_id)
            if connection is None or connection.endpoint_id != endpoint_id:
                raise ValueError("PROVIDER_CONNECTION_ENDPOINT_MISMATCH")
            if connection.status is not ProviderConnectionStatus.ACTIVE or not endpoint.enabled:
                return AccountUsageSnapshot(
                    provider_id=endpoint.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    connection_id=connection_id,
                    credential_ref=credential_ref,
                    credential_revision=credential_revision,
                    status=UsageSnapshotStatus.UNAVAILABLE,
                )
        provider = self.providers.get(endpoint.provider_id)
        if provider is not None and ProviderUsageCapability.USAGE_QUERY not in provider.usage_capabilities:
            return AccountUsageSnapshot(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                connection_id=connection_id,
                credential_ref=credential_ref,
                credential_revision=credential_revision,
                status=UsageSnapshotStatus.UNSUPPORTED,
            )
        if self._account_usage is None:
            return AccountUsageSnapshot(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                connection_id=connection_id,
                credential_ref=credential_ref,
                credential_revision=credential_revision,
                status=UsageSnapshotStatus.UNAVAILABLE,
            )
        return self._account_usage.read(
            endpoint,
            credential_ref=credential_ref,
            connection_id=connection_id,
            credential_revision=credential_revision,
            force=force,
        )

    def account_usage_snapshots(self) -> tuple[AccountUsageSnapshot, ...]:
        return self._account_usage.cached() if self._account_usage is not None else self._restored_usage_snapshots

    def usage(self):
        return self._usage.usage()

    def reliability(self):
        return self._usage.reliability()

    def task_metrics(self):
        return self._usage.task_metrics()

    def save(self) -> None:
        if self._store is None:
            raise RuntimeError("model gateway persistence is unavailable")
        self._store.save(self.state())

    @property
    def persistent(self) -> bool:
        return self._store is not None

    def restore_from_store(self) -> bool:
        if self._store is None:
            return False
        state = self._store.load()
        if state is None:
            return False
        self.restore(state)
        return True

    def state(self) -> ModelGatewayState:
        return ModelGatewayState(
            providers=self.providers.list(),
            endpoints=self.endpoints.list(),
            connections=self.connections.list(),
            credentials=self.list_credentials(),
            models=self.models.list(),
            capability_probes=tuple(self._capability_probes.values()),
            task_profiles=tuple(self._task_profiles.values()),
            global_policy=self._global_policy,
            project_policies=tuple(self._project_policies.values()),
            reliability=self._usage.reliability(),
            task_metrics=self._usage.task_metrics(),
            usage_aggregates=self._usage.usage(),
            usage_snapshots=self.account_usage_snapshots(),
            engineering_stages=self._engineering_stages,
            allowed_model_policies=tuple(self._allowed_model_policies.values()),
            stage_model_policies=tuple(self._stage_model_policies.values()),
            candidate_groups=tuple(self._candidate_groups.values()),
            project_default_model_policies=tuple(self._project_default_model_policies.values()),
            integrity_issues=self._integrity_issues,
            identity_correlations=self._identity_correlations,
            migration_version=self._migration_version,
            migration_source_fingerprint=self._migration_source_fingerprint,
            model_identity_migration_version=self._model_identity_migration_version,
            model_identity_source_fingerprint=self._model_identity_source_fingerprint,
        )

    def restore(self, state: ModelGatewayState) -> None:
        if type(state) is not ModelGatewayState:
            raise TypeError("model gateway state is invalid")
        self.providers = ProviderRegistry(state.providers)
        self.endpoints = EndpointRegistry(state.endpoints)
        self.connections = ProviderConnectionRegistry(
            state.connections,
            providers=self.providers,
            endpoints=self.endpoints,
            clock=self._clock,
            id_factory=self._id_factory,
        )
        self.models = ModelRegistry(state.models)
        self._capability_probes = {
            (item.model_profile_id, item.probe_type): copy.deepcopy(item)
            for item in state.capability_probes
        }
        self._credentials = {item.credential_id: copy.deepcopy(item) for item in state.credentials}
        profiles = state.task_profiles or default_task_profiles()
        self._task_profiles = {item.task_type.value: copy.deepcopy(item) for item in profiles}
        self._global_policy = copy.deepcopy(state.global_policy)
        self._project_policies = {item.project_id: copy.deepcopy(item) for item in state.project_policies}
        self._engineering_stages = tuple(copy.deepcopy(state.engineering_stages or default_engineering_stage_registry()))
        self._allowed_model_policies = {item.id: copy.deepcopy(item) for item in state.allowed_model_policies}
        self._stage_model_policies = {(item.project_id, item.stage_id): copy.deepcopy(item) for item in state.stage_model_policies}
        self._candidate_groups = {(item.group_id, item.revision): copy.deepcopy(item) for item in state.candidate_groups}
        self._project_default_model_policies = {item.project_id: copy.deepcopy(item) for item in state.project_default_model_policies}
        self._integrity_issues = tuple(copy.deepcopy(state.integrity_issues))
        self._identity_correlations = tuple(copy.deepcopy(state.identity_correlations))
        self._migration_version = state.migration_version
        self._migration_source_fingerprint = state.migration_source_fingerprint
        self._model_identity_migration_version = state.model_identity_migration_version
        self._model_identity_source_fingerprint = state.model_identity_source_fingerprint
        self._usage.restore(
            state.usage_aggregates,
            state.reliability,
            state.task_metrics,
        )
        self._restored_usage_snapshots = tuple(copy.deepcopy(state.usage_snapshots))
        if self._account_usage is not None:
            self._account_usage.restore(self._restored_usage_snapshots)


__all__ = ("ConnectionProbePort", "ModelGatewayManagementService")
