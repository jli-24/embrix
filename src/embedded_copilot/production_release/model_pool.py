"""Provider-scoped model discovery, catalog projection and cache handling."""

from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from embedded_copilot.model_gateway import (
    DiscoveryStatus,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelGatewayManagementService,
    ModelPoolCache,
    ModelProfile,
    ModelRegistry,
    RecordProvenance,
    descriptor_to_model_profile,
    get_usable_models,
    merge_model_metadata,
    official_model_catalog,
)
from embedded_copilot.model_gateway.profiles import VerificationStatus

from .model_discovery import DiscoveryFailureEvidence, ModelDiscoveryFailure
from .provider_verification import (
    projection_revision as projection_revision,  # noqa: PLC0414 - preserve legacy named re-export.
)


class ProviderModelDiscoveryPort(Protocol):
    """Injected discovery seam; implementations must not expose credentials."""

    def discover(
        self,
        endpoint: object,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> tuple[ModelDescriptor | ModelProfile, ...]: ...


@dataclass(frozen=True, slots=True)
class ModelPoolRefreshResult:
    provider_id: str
    endpoint_id: str
    models: tuple[ModelProfile, ...]
    status: str
    error_code: str | None = None
    failure_evidence: DiscoveryFailureEvidence | None = None
    connection_id: str | None = None
    # ``None`` means the result came from a stale cache or an unavailable
    # discovery path whose raw provider payload was not observed.
    raw_model_count: int | None = None


class ProviderModelPool:
    """Project model discovery into the existing ModelRegistry."""

    def __init__(
        self,
        management: ModelGatewayManagementService,
        *,
        discovery: ProviderModelDiscoveryPort | None = None,
        now=None,
        ttl_seconds: float = 300.0,
    ) -> None:
        self._management = management
        self._discovery = discovery
        self._now = now or (lambda: datetime.now(UTC))
        self._cache = ModelPoolCache(ttl_seconds=ttl_seconds, now=self._now)

    def invalidate(
        self,
        provider_id: str | None = None,
        *,
        connection_id: str | None = None,
        endpoint_id: str | None = None,
    ) -> None:
        self._cache.invalidate_scoped(
            connection_id=connection_id,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
        )

    @staticmethod
    def _key(
        endpoint: object,
        *,
        connection_id: str | None,
        credential_revision: str | None,
        endpoint_revision: str,
    ) -> tuple[str | None, str, str, str | None, str]:
        return (
            connection_id,
            endpoint.provider_id,
            endpoint.endpoint_id,
            credential_revision,
            endpoint_revision,
        )

    @staticmethod
    def endpoint_revision(endpoint: object) -> str:
        from embedded_copilot.model_gateway._endpoint_revision import endpoint_revision

        return endpoint_revision(endpoint)

    def refresh(
        self,
        endpoint: object,
        *,
        credential_revision: str | None,
        credential_configured: bool,
        connection_id: str | None = None,
    ) -> ModelPoolRefreshResult:
        endpoint_revision = self.endpoint_revision(endpoint)
        key = self._key(
            endpoint,
            connection_id=connection_id,
            credential_revision=credential_revision,
            endpoint_revision=endpoint_revision,
        )
        if not getattr(endpoint, "enabled", True):
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                (),
                "UNAVAILABLE",
                "CONNECTION_DISABLED",
                connection_id=connection_id,
            )
        if not credential_configured:
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                (),
                "UNAVAILABLE",
                "CREDENTIAL_UNAVAILABLE",
                connection_id=connection_id,
            )
        if credential_revision is None and getattr(endpoint, "credential_ref", None) is not None:
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                (),
                "UNAVAILABLE",
                "CREDENTIAL_REVISION_UNAVAILABLE",
                connection_id=connection_id,
            )
        try:
            if self._discovery is not None:
                parameters = inspect.signature(self._discovery.discover).parameters
                if "connection_id" in parameters or any(
                    item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in parameters.values()
                ):
                    discovered = self._discovery.discover(
                        copy.deepcopy(endpoint),
                        connection_id=connection_id,
                        credential_revision=credential_revision,
                    )
                else:
                    discovered = self._discovery.discover(copy.deepcopy(endpoint))
                discovered = tuple(discovered)
                raw_model_count = len(discovered)
                models = tuple(self._normalize(item, endpoint, credential_revision, endpoint_revision) for item in discovered)
            else:
                models = self._catalog_models(
                    endpoint,
                    credential_revision=credential_revision,
                    endpoint_revision=endpoint_revision,
                    credential_configured=credential_configured,
                )
                raw_model_count = len(models)
            models = self._commit_refresh(endpoint, key, models)
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                models,
                "UPDATED",
                connection_id=connection_id,
                raw_model_count=raw_model_count,
            )
        except ModelDiscoveryFailure as error:
            cached = self._cache.get(key, allow_stale=True)
            if cached is not None:
                return ModelPoolRefreshResult(
                    endpoint.provider_id,
                    endpoint.endpoint_id,
                    cached.models,
                    "STALE",
                    error.evidence.classification,
                    error.evidence,
                    connection_id,
                )
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                (),
                "UNAVAILABLE",
                error.evidence.classification,
                error.evidence,
                connection_id,
            )
        except Exception:  # noqa: BLE001 - stale cache is the fail-closed recovery path
            cached = self._cache.get(key, allow_stale=True)
            if cached is not None:
                return ModelPoolRefreshResult(
                    endpoint.provider_id,
                    endpoint.endpoint_id,
                    cached.models,
                    "STALE",
                    "MODEL_DISCOVERY_REFRESH_FAILED",
                    connection_id=connection_id,
                )
            return ModelPoolRefreshResult(
                endpoint.provider_id,
                endpoint.endpoint_id,
                (),
                "UNAVAILABLE",
                "MODEL_DISCOVERY_REFRESH_FAILED",
                connection_id=connection_id,
            )

    def _commit_refresh(
        self,
        endpoint: object,
        key: tuple[str | None, str, str, str | None, str],
        models: tuple[ModelProfile, ...],
    ) -> tuple[ModelProfile, ...]:
        # Discovery runs before this lock.  The canonical registry and its
        # persisted snapshot must observe probe and refresh commits in order.
        with self._management._model_state_lock:
            current_models = {item.model_profile_id: item for item in self._management.list_models()}
            retained: list[ModelProfile] = []
            for model in models:
                previous = current_models.get(model.model_profile_id)
                if (
                    previous is not None
                    and previous.provider_id == model.provider_id
                    and previous.endpoint_id == model.endpoint_id
                    and previous.remote_model_id == model.remote_model_id
                    and previous.credential_revision == model.credential_revision
                    and previous.endpoint_revision == model.endpoint_revision
                ):
                    model = model.model_copy(update={
                        "capabilities": tuple(dict.fromkeys((*model.capabilities, *previous.capabilities))),
                        "capability_sources": previous.capability_sources,
                        "verification_status": VerificationStatus(previous.verification_status),
                        "verified_at": previous.verified_at,
                        "availability": previous.availability if model.availability is ModelAvailability.UNKNOWN else model.availability,
                        "availability_status": previous.availability_status if model.availability_status is ModelAvailability.UNKNOWN else model.availability_status,
                        "availability_source": previous.availability_source if model.availability_status is ModelAvailability.UNKNOWN else model.availability_source,
                        "availability_provenance": previous.availability_provenance if model.availability_status is ModelAvailability.UNKNOWN else model.availability_provenance,
                    })
                self._management.models.upsert(model)
                retained.append(model)
            models = tuple(retained)
            discovered_ids = {model.model_profile_id for model in models}
            for old in self._management.list_models():
                if old.provider_id == endpoint.provider_id and old.endpoint_id == endpoint.endpoint_id and old.model_profile_id not in discovered_ids:
                    self._management.models.upsert(
                        old.model_copy(update={
                            "availability": ModelAvailability.UNKNOWN,
                            "availability_status": ModelAvailability.UNKNOWN,
                            "availability_source": old.availability_provenance,
                            "availability_provenance": old.availability_provenance,
                            "discovery_status": DiscoveryStatus.UNKNOWN,
                        })
                    )
            if self._management.persistent:
                try:
                    self._management.save()
                except Exception:
                    self._management.models = ModelRegistry(tuple(current_models.values()))
                    raise
            self._cache.put(key, models)
            return models

    def usable(
        self,
        endpoint: object,
        *,
        credential_revision: str | None,
        connection_id: str | None = None,
    ) -> tuple[ModelProfile, ...]:
        if not getattr(endpoint, "enabled", True):
            return ()
        if credential_revision is None and getattr(endpoint, "credential_ref", None) is not None:
            return ()
        models = get_usable_models(
            self._management.list_models(),
            provider_id=endpoint.provider_id,
            endpoint_id=endpoint.endpoint_id,
            credential_revision=credential_revision,
            endpoint_revision=self.endpoint_revision(endpoint),
        )
        return tuple(
            item
            for item in models
            if item.record_provenance
            in {RecordProvenance.ENDPOINT_DISCOVERY, RecordProvenance.EXPLICIT_CONFIGURATION}
        )

    def record_verification(
        self,
        endpoint: object,
        *,
        model_id: str,
        credential_revision: str | None,
        availability: ModelAvailability,
        verified_at: datetime | None,
        connection_id: str | None = None,
    ) -> ModelProfile | None:
        if not getattr(endpoint, "enabled", True):
            return None
        existing = next(
            (
                item
                for item in self._management.list_models()
                if item.provider_id == endpoint.provider_id
                and item.endpoint_id == endpoint.endpoint_id
                and item.remote_model_id == model_id
                and item.credential_revision == credential_revision
                and item.endpoint_revision == self.endpoint_revision(endpoint)
                and item.record_provenance
                in {RecordProvenance.ENDPOINT_DISCOVERY, RecordProvenance.EXPLICIT_CONFIGURATION}
            ),
            None,
        )
        if existing is None:
            catalog = self._catalog_models(
                endpoint,
                credential_revision=credential_revision,
                endpoint_revision=self.endpoint_revision(endpoint),
                credential_configured=credential_revision is not None,
            )
            existing = next((item for item in catalog if item.remote_model_id == model_id), None)
        if existing is None:
            return None
        current_endpoint_revision = self.endpoint_revision(endpoint)
        updated = existing.model_copy(
            update={
                "availability": availability,
                "availability_status": availability,
                "availability_source": ModelCatalogSource.PROVIDER_API,
                "availability_provenance": ModelCatalogSource.RUNTIME_VERIFICATION,
                "verified_at": verified_at,
                "credential_revision": credential_revision,
                "endpoint_revision": current_endpoint_revision,
                "verification_status": "VERIFIED"
                if availability in {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
                else "UNVERIFIED",
            }
        )
        self._management.models.upsert(updated)
        self._cache.put(
            self._key(
                endpoint,
                connection_id=connection_id,
                credential_revision=credential_revision,
                endpoint_revision=current_endpoint_revision,
            ),
            tuple(
                updated if item.remote_model_id == model_id else item
                for item in self._management.list_models()
                if item.provider_id == endpoint.provider_id
                and item.endpoint_id == endpoint.endpoint_id
                and item.credential_revision == credential_revision
                and item.endpoint_revision == current_endpoint_revision
                and item.record_provenance
                in {RecordProvenance.ENDPOINT_DISCOVERY, RecordProvenance.EXPLICIT_CONFIGURATION}
            ),
        )
        return updated

    @staticmethod
    def _normalize(
        item: ModelDescriptor | ModelProfile,
        endpoint: object,
        credential_revision: str | None,
        endpoint_revision: str,
    ) -> ModelProfile:
        model = descriptor_to_model_profile(item) if isinstance(item, ModelDescriptor) else item
        if model.provider_id != endpoint.provider_id or model.endpoint_id != endpoint.endpoint_id:
            raise ValueError("discovered model identity is invalid")
        metadata = next(
            (candidate for candidate in official_model_catalog(endpoint.provider_id, endpoint.endpoint_id)
             if candidate.remote_model_id == model.remote_model_id),
            None,
        )
        model = merge_model_metadata(model, metadata)
        return model.model_copy(
            update={
                "record_provenance": RecordProvenance.ENDPOINT_DISCOVERY,
                "availability_status": model.availability_status,
                "availability_source": ModelCatalogSource.PROVIDER_API,
                "availability_provenance": ModelCatalogSource.PROVIDER_API,
                "catalog_source": ModelCatalogSource.PROVIDER_API,
                "credential_revision": credential_revision,
                "endpoint_revision": endpoint_revision,
                "discovered_at": model.discovered_at,
            }
        )

    def _catalog_models(
        self,
        endpoint: object,
        *,
        credential_revision: str | None,
        endpoint_revision: str,
        credential_configured: bool,
    ) -> tuple[ModelProfile, ...]:
        existing = {
            item.remote_model_id: item
            for item in self._management.list_models()
            if item.provider_id == endpoint.provider_id
            and item.endpoint_id == endpoint.endpoint_id
            and item.record_provenance
            in {RecordProvenance.ENDPOINT_DISCOVERY, RecordProvenance.EXPLICIT_CONFIGURATION}
        }
        # Official catalog entries are metadata-only.  Without an injected
        # discovery port, never project them as models found on this endpoint.
        return tuple(
            item
            for item in existing.values()
            if item.credential_revision == credential_revision
            and item.endpoint_revision == endpoint_revision
        )


__all__ = ("ModelPoolRefreshResult", "ProviderModelDiscoveryPort", "ProviderModelPool")
