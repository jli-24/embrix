"""Canonical provider-scoped model descriptors and cache primitives."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import field_validator

from .contracts import ModelGatewayContract, identifier
from .profiles import (
    BillingClass,
    BillingProvenance,
    ModelAvailability,
    ModelCapability,
    ModelCatalogSource,
    ModelMetadata,
    ModelProfile,
    ProtocolFamily,
    RecordProvenance,
)


class ModelDescriptor(ModelGatewayContract):
    """Secret-free normalized output of provider model discovery."""

    provider_id: str
    endpoint_id: str
    model_id: str
    display_name: str
    protocol_family: ProtocolFamily | str
    availability: ModelAvailability = ModelAvailability.UNKNOWN
    availability_source: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    availability_provenance: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    capabilities: tuple[ModelCapability, ...] = ()
    catalog_source: ModelCatalogSource = ModelCatalogSource.UNKNOWN
    billing_class: BillingClass = BillingClass.UNKNOWN
    billing_provenance: BillingProvenance = BillingProvenance.UNKNOWN
    billing_source: str | None = None
    lifecycle_status: str = "ACTIVE"
    deprecated: bool = False
    metadata_updated_at: datetime | None = None
    metadata_source: str | None = None
    discovered_at: datetime | None = None
    verified_at: datetime | None = None
    credential_revision: str | None = None
    endpoint_revision: str | None = None
    record_provenance: RecordProvenance = RecordProvenance.UNKNOWN

    @field_validator("provider_id", "endpoint_id", "model_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("display_name", mode="before")
    @classmethod
    def _display_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            raise ValueError("display_name is invalid")
        return value.strip()

    @field_validator("protocol_family", mode="before")
    @classmethod
    def _protocol(cls, value: object) -> ProtocolFamily | str:
        if isinstance(value, ProtocolFamily):
            return value
        if isinstance(value, str) and value in ProtocolFamily._value2member_map_:
            return ProtocolFamily(value)
        return identifier(value, field="protocol_family")

    @field_validator("availability", mode="before")
    @classmethod
    def _availability(cls, value: object) -> ModelAvailability:
        return value if isinstance(value, ModelAvailability) else ModelAvailability(value)

    @field_validator("availability_source", "availability_provenance", "catalog_source", mode="before")
    @classmethod
    def _source(cls, value: object, info) -> ModelCatalogSource:
        return value if isinstance(value, ModelCatalogSource) else ModelCatalogSource(value)

    @field_validator("billing_class", mode="before")
    @classmethod
    def _billing_class(cls, value: object) -> BillingClass:
        return value if isinstance(value, BillingClass) else BillingClass(value)

    @field_validator("billing_provenance", mode="before")
    @classmethod
    def _billing_provenance(cls, value: object) -> BillingProvenance:
        return value if isinstance(value, BillingProvenance) else BillingProvenance(value)

    @field_validator("record_provenance", mode="before")
    @classmethod
    def _record_provenance(cls, value: object) -> RecordProvenance:
        return value if isinstance(value, RecordProvenance) else RecordProvenance(value)

    @field_validator("billing_source", "metadata_source", mode="before")
    @classmethod
    def _metadata_sources(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 1024:
            raise ValueError(f"{info.field_name} is invalid")
        return value.strip()

    @field_validator("lifecycle_status", mode="before")
    @classmethod
    def _lifecycle_status(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 256:
            raise ValueError("lifecycle_status is invalid")
        return value.strip()

    @field_validator("metadata_updated_at", mode="before")
    @classmethod
    def _metadata_updated_at(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("metadata_updated_at is invalid")
        return value.astimezone(UTC)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[ModelCapability, ...]:
        if isinstance(value, list):
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("capabilities must be a tuple")
        return tuple(item if isinstance(item, ModelCapability) else ModelCapability(item) for item in value)

    @field_validator("discovered_at", "verified_at", mode="before")
    @classmethod
    def _timestamps(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("timestamp is invalid")
        return value.astimezone(UTC)

    @field_validator("credential_revision", "endpoint_revision", mode="before")
    @classmethod
    def _revisions(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)


def descriptor_to_model_profile(value: ModelDescriptor) -> ModelProfile:
    """Project one descriptor into the existing canonical ModelProfile registry."""

    endpoint_suffix = "" if value.endpoint_id == f"endpoint-{value.provider_id}" else f"-{value.endpoint_id}"
    return ModelProfile(
        model_profile_id=f"model-{value.provider_id}{endpoint_suffix}-{value.model_id}"[:128],
        provider_id=value.provider_id,
        endpoint_id=value.endpoint_id,
        remote_model_id=value.model_id,
        display_name=value.display_name,
        advertised_family=value.protocol_family.value if isinstance(value.protocol_family, ProtocolFamily) else value.protocol_family,
        capabilities=value.capabilities,
        capability_sources=(),
        enabled=True,
        discovery_status="DISCOVERED",
        availability=value.availability,
        availability_status=value.availability,
        availability_source=value.availability_source,
        availability_provenance=(
            value.availability_provenance
            if value.availability_provenance is not ModelCatalogSource.UNKNOWN
            else value.availability_source
        ),
        catalog_source=value.catalog_source,
        billing_class=value.billing_class,
        billing_provenance=value.billing_provenance,
        billing_source=value.billing_source,
        lifecycle_status=value.lifecycle_status,
        deprecated=value.deprecated,
        metadata_updated_at=value.metadata_updated_at,
        metadata_source=value.metadata_source,
        discovered_at=value.discovered_at,
        verified_at=value.verified_at,
        credential_revision=value.credential_revision,
        endpoint_revision=value.endpoint_revision,
        record_provenance=value.record_provenance,
    )


def model_profile_to_descriptor(value: ModelProfile) -> ModelDescriptor:
    return ModelDescriptor(
        provider_id=value.provider_id,
        endpoint_id=value.endpoint_id,
        model_id=value.remote_model_id,
        display_name=value.display_name,
        protocol_family=value.advertised_family,
        availability=value.availability_status,
        availability_source=value.availability_source,
        availability_provenance=value.availability_provenance,
        catalog_source=value.catalog_source,
        billing_class=value.billing_class,
        billing_provenance=value.billing_provenance,
        billing_source=value.billing_source,
        lifecycle_status=value.lifecycle_status,
        deprecated=value.deprecated,
        metadata_updated_at=value.metadata_updated_at,
        metadata_source=value.metadata_source,
        capabilities=value.capabilities,
        discovered_at=value.discovered_at,
        verified_at=value.verified_at,
        credential_revision=value.credential_revision,
        endpoint_revision=value.endpoint_revision,
        record_provenance=value.record_provenance,
    )


USABLE_AVAILABILITY: frozenset[ModelAvailability] = frozenset(
    {ModelAvailability.AVAILABLE, ModelAvailability.RATE_LIMITED}
)


def get_usable_models(
    models: Iterable[ModelProfile],
    *,
    provider_id: str,
    endpoint_id: str,
    credential_revision: str | None,
    endpoint_revision: str | None = None,
) -> tuple[ModelProfile, ...]:
    """Return only models proven usable for the exact provider scope."""

    result: list[ModelProfile] = []
    for model in models:
        if model.provider_id != provider_id or model.endpoint_id != endpoint_id:
            continue
        if model.availability_status not in USABLE_AVAILABILITY or not model.enabled or model.deprecated:
            continue
        if credential_revision is None and model.credential_revision is not None:
            continue
        if credential_revision is not None and model.credential_revision != credential_revision:
            continue
        if endpoint_revision is not None and model.endpoint_revision != endpoint_revision:
            continue
        result.append(copy.deepcopy(model))
    return tuple(result)


_BILLING_PROVENANCE_RANK: dict[BillingProvenance, int] = {
    BillingProvenance.UNKNOWN: 0,
    BillingProvenance.DECLARED: 1,
    BillingProvenance.PROVIDER_METADATA: 2,
    BillingProvenance.PROBED: 3,
    BillingProvenance.VERIFIED: 4,
}


def merge_model_metadata(
    value: ModelDescriptor | ModelProfile,
    metadata: ModelMetadata | ModelProfile | None,
) -> ModelProfile:
    """Merge canonical provider metadata without changing account availability.

    Provider discovery proves account visibility only.  Billing and lifecycle
    come from the catalog's official metadata snapshot, while higher-priority
    observed/verified billing evidence is retained.
    """

    model = descriptor_to_model_profile(value) if isinstance(value, ModelDescriptor) else copy.deepcopy(value)
    if metadata is None:
        return model
    metadata_provider = metadata.provider_id
    metadata_model = metadata.model_id if isinstance(metadata, ModelMetadata) else metadata.remote_model_id
    if model.provider_id != metadata_provider or model.remote_model_id != metadata_model:
        raise ValueError("model metadata identity is invalid")
    if isinstance(metadata, ModelProfile):
        metadata_value = ModelMetadata(
            provider_id=metadata.provider_id,
            model_id=metadata.remote_model_id,
            display_name=metadata.display_name,
            billing_class=metadata.billing_class,
            billing_provenance=metadata.billing_provenance,
            billing_source=metadata.billing_source,
            lifecycle_status=metadata.lifecycle_status,
            deprecated=metadata.deprecated,
            capabilities=metadata.capabilities,
            metadata_updated_at=metadata.metadata_updated_at,
            metadata_source=metadata.metadata_source,
        )
    else:
        metadata_value = metadata
    updates: dict[str, object] = {
        "display_name": metadata_value.display_name,
        "lifecycle_status": metadata_value.lifecycle_status,
        "deprecated": metadata_value.deprecated,
        "metadata_updated_at": metadata_value.metadata_updated_at,
        "metadata_source": metadata_value.metadata_source,
        "catalog_source": model.catalog_source,
    }
    if metadata_value.capabilities:
        updates["capabilities"] = metadata_value.capabilities
        updates["input_modalities"] = metadata_value.capabilities
        updates["output_modalities"] = (ModelCapability.TEXT,)
    if _BILLING_PROVENANCE_RANK[metadata_value.billing_provenance] >= _BILLING_PROVENANCE_RANK[model.billing_provenance] or model.billing_class is BillingClass.UNKNOWN:
        updates["billing_class"] = metadata_value.billing_class
        updates["billing_provenance"] = metadata_value.billing_provenance
        updates["billing_source"] = metadata_value.billing_source
    if metadata_value.deprecated and model.availability_status in USABLE_AVAILABILITY:
        updates["availability"] = ModelAvailability.DEPRECATED
        updates["availability_status"] = ModelAvailability.DEPRECATED
        updates["availability_source"] = ModelCatalogSource.PROVIDER_METADATA
        updates["availability_provenance"] = ModelCatalogSource.PROVIDER_METADATA
    return model.model_copy(update=updates)


@dataclass(frozen=True, slots=True)
class ModelPoolCacheEntry:
    key: tuple[str | None, str, str, str | None, str | None]
    models: tuple[ModelProfile, ...]
    refreshed_at: datetime
    stale: bool = False


class ModelPoolCache:
    """Small TTL cache that keeps last-known-good models on refresh failure."""

    def __init__(self, *, ttl_seconds: float = 300.0, now=None) -> None:
        if type(ttl_seconds) not in (int, float) or ttl_seconds <= 0:
            raise ValueError("model pool cache TTL is invalid")
        self._ttl = timedelta(seconds=float(ttl_seconds))
        self._now = now or (lambda: datetime.now(UTC))
        self._entries: dict[
            tuple[str | None, str, str, str | None, str | None], ModelPoolCacheEntry
        ] = {}

    @staticmethod
    def _canonical_key(
        key: tuple[str, str, str | None] | tuple[str, str, str | None, str | None],
    ) -> tuple[str | None, str, str, str | None, str | None]:
        if len(key) == 3:
            return (None, key[0], key[1], key[2], None)
        if len(key) == 4:
            return (None, key[0], key[1], key[2], key[3])
        if len(key) == 5:
            return key
        raise ValueError("model pool cache key is invalid")

    def get(
        self,
        key: tuple[str, str, str | None] | tuple[str, str, str | None, str | None],
        *,
        allow_stale: bool = True,
    ) -> ModelPoolCacheEntry | None:
        key = self._canonical_key(key)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expired = self._now() - entry.refreshed_at >= self._ttl
        if expired and not allow_stale:
            return None
        if expired and not entry.stale:
            entry = ModelPoolCacheEntry(entry.key, entry.models, entry.refreshed_at, True)
            self._entries[key] = entry
        return copy.deepcopy(entry)

    def put(
        self,
        key: tuple[str, str, str | None] | tuple[str, str, str | None, str | None],
        models: Iterable[ModelProfile],
    ) -> ModelPoolCacheEntry:
        key = self._canonical_key(key)
        entry = ModelPoolCacheEntry(key, tuple(copy.deepcopy(tuple(models))), self._now(), False)
        self._entries[key] = entry
        return copy.deepcopy(entry)

    def invalidate(self, *, provider_id: str | None = None, endpoint_id: str | None = None) -> None:
        self.invalidate_scoped(provider_id=provider_id, endpoint_id=endpoint_id)

    def invalidate_scoped(
        self,
        *,
        connection_id: str | None = None,
        provider_id: str | None = None,
        endpoint_id: str | None = None,
    ) -> None:
        for key, entry in tuple(self._entries.items()):
            if connection_id is not None and key[0] != connection_id:
                continue
            if provider_id is not None and key[1] != provider_id:
                continue
            if endpoint_id is not None and key[2] != endpoint_id:
                continue
            self._entries[key] = ModelPoolCacheEntry(entry.key, entry.models, entry.refreshed_at, True)


__all__ = (
    "USABLE_AVAILABILITY",
    "ModelDescriptor",
    "ModelPoolCache",
    "ModelPoolCacheEntry",
    "descriptor_to_model_profile",
    "get_usable_models",
    "merge_model_metadata",
    "model_profile_to_descriptor",
)
