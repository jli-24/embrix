"""Identity registries and injectable discovery/probe seams."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import ClassVar, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import field_validator

from .contracts import ModelGatewayContract, fingerprint, identifier, safe_text
from .profiles import (
    _ref,
    CapabilityStatus,
    DiscoveryStatus,
    EndpointProfile,
    ModelCatalogSource,
    ModelProfile,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    RecordProvenance,
)


class ProviderRegistry:
    def __init__(self, providers: Sequence[ProviderProfile] = ()) -> None:
        self._items: dict[str, ProviderProfile] = {}
        for item in providers:
            self.register(item)

    def register(self, profile: ProviderProfile) -> None:
        if type(profile) is not ProviderProfile:
            raise TypeError("provider profile is invalid")
        if profile.provider_id in self._items:
            raise ValueError("duplicate provider profile")
        self._items[profile.provider_id] = copy.deepcopy(profile)

    def replace(self, profile: ProviderProfile) -> None:
        if type(profile) is not ProviderProfile or profile.provider_id not in self._items:
            raise ValueError("provider profile is not registered")
        self._items[profile.provider_id] = copy.deepcopy(profile)

    def get(self, provider_id: str) -> ProviderProfile | None:
        return copy.deepcopy(self._items.get(provider_id))

    def list(self) -> tuple[ProviderProfile, ...]:
        return tuple(copy.deepcopy(value) for value in self._items.values())


class EndpointRegistry:
    def __init__(self, endpoints: Sequence[EndpointProfile] = ()) -> None:
        self._items: dict[str, EndpointProfile] = {}
        for item in endpoints:
            self.register(item)

    def register(self, profile: EndpointProfile) -> None:
        if type(profile) is not EndpointProfile:
            raise TypeError("endpoint profile is invalid")
        if profile.endpoint_id in self._items:
            raise ValueError("duplicate endpoint profile")
        self._items[profile.endpoint_id] = copy.deepcopy(profile)

    def replace(self, profile: EndpointProfile) -> None:
        if type(profile) is not EndpointProfile or profile.endpoint_id not in self._items:
            raise ValueError("endpoint profile is not registered")
        self._items[profile.endpoint_id] = copy.deepcopy(profile)

    def get(self, endpoint_id: str) -> EndpointProfile | None:
        return copy.deepcopy(self._items.get(endpoint_id))

    def list(self) -> tuple[EndpointProfile, ...]:
        return tuple(copy.deepcopy(value) for value in self._items.values())


class ProviderConnectionRegistry:
    """Registry for stable provider connection aggregates.

    The registry owns no credentials and performs no discovery or invocation.
    It only validates the provider/endpoint binding and lifecycle transitions.
    """

    _TRANSITIONS: ClassVar[dict[ProviderConnectionStatus, frozenset[ProviderConnectionStatus]]] = {
        ProviderConnectionStatus.ACTIVE: frozenset(
            {
                ProviderConnectionStatus.ACTIVE,
                ProviderConnectionStatus.DISABLED,
                ProviderConnectionStatus.DELETED,
                ProviderConnectionStatus.QUARANTINED,
            }
        ),
        ProviderConnectionStatus.DISABLED: frozenset(
            {
                ProviderConnectionStatus.ACTIVE,
                ProviderConnectionStatus.DISABLED,
                ProviderConnectionStatus.DELETED,
                ProviderConnectionStatus.QUARANTINED,
            }
        ),
        ProviderConnectionStatus.QUARANTINED: frozenset(
            {
                ProviderConnectionStatus.ACTIVE,
                ProviderConnectionStatus.QUARANTINED,
                ProviderConnectionStatus.DISABLED,
                ProviderConnectionStatus.DELETED,
            }
        ),
        ProviderConnectionStatus.DELETED: frozenset({ProviderConnectionStatus.DELETED}),
    }

    def __init__(
        self,
        connections: Sequence[ProviderConnection] = (),
        *,
        providers: ProviderRegistry | Sequence[ProviderProfile] = (),
        endpoints: EndpointRegistry | Sequence[EndpointProfile] = (),
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._providers = providers if isinstance(providers, ProviderRegistry) else ProviderRegistry(providers)
        self._endpoints = endpoints if isinstance(endpoints, EndpointRegistry) else EndpointRegistry(endpoints)
        self._items: dict[str, ProviderConnection] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{uuid4().hex}")
        for item in connections:
            self.register(item)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    def _new_id(self) -> str:
        candidate = self._id_factory("conn")
        if not isinstance(candidate, str):
            raise TypeError("connection id factory returned an invalid id")
        if candidate in self._items:
            candidate = f"conn-{uuid4().hex}"
        return candidate

    def _validate_provider_endpoint(self, connection: ProviderConnection) -> None:
        provider = self._providers.get(connection.provider_id)
        if provider is None:
            raise ValueError("connection provider is not registered")
        endpoint = self._endpoints.get(connection.endpoint_id)
        if endpoint is None:
            raise ValueError("connection endpoint is not registered")
        if endpoint.provider_id != connection.provider_id:
            raise ValueError("provider/endpoint ownership mismatch")
        if endpoint.credential_ref is not None and endpoint.credential_ref != connection.credential_ref:
            raise ValueError("endpoint credential ownership mismatch")

    def validate_identity(self, connection: ProviderConnection) -> bool:
        if type(connection) is not ProviderConnection:
            raise TypeError("provider connection is invalid")
        self._validate_provider_endpoint(connection)
        return True

    def validate_ownership(self, connection: ProviderConnection, *, replacing: str | None = None) -> bool:
        self.validate_identity(connection)
        for current in self._items.values():
            if current.connection_id == replacing:
                continue
            if current.endpoint_id == connection.endpoint_id:
                raise ValueError("endpoint ownership is already registered")
        return True

    def register(self, connection: ProviderConnection) -> ProviderConnection:
        if type(connection) is not ProviderConnection:
            raise TypeError("provider connection is invalid")
        if connection.connection_id in self._items:
            raise ValueError("duplicate provider connection")
        self.validate_ownership(connection)
        self._items[connection.connection_id] = copy.deepcopy(connection)
        return copy.deepcopy(connection)

    def create(
        self,
        *,
        provider_id: str,
        endpoint_id: str,
        connection_name: str | None = None,
        credential_ref: str | None = None,
        status: ProviderConnectionStatus = ProviderConnectionStatus.ACTIVE,
        legacy_source: str | None = None,
        connection_id: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> ProviderConnection:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise ValueError("connection provider is not registered")
        now = self._now()
        checked_status = status if isinstance(status, ProviderConnectionStatus) else ProviderConnectionStatus(status)
        created = created_at or now
        updated = updated_at or now
        connection = ProviderConnection(
            connection_id=connection_id or self._new_id(),
            provider_id=provider_id,
            connection_name=(
                provider.display_name
                if connection_name is None
                else connection_name
            ),
            status=checked_status,
            endpoint_id=endpoint_id,
            credential_ref=credential_ref,
            created_at=created,
            updated_at=updated,
            deleted_at=now if checked_status is ProviderConnectionStatus.DELETED else None,
            legacy_source=legacy_source,
        )
        return self.register(connection)

    def get(self, connection_id: str) -> ProviderConnection | None:
        return copy.deepcopy(self._items.get(connection_id))

    def list(self) -> tuple[ProviderConnection, ...]:
        return tuple(copy.deepcopy(value) for value in self._items.values())

    def _update(
        self,
        connection: str | ProviderConnection,
        replacement: ProviderConnection | None = None,
        **changes: object,
    ) -> ProviderConnection:
        if isinstance(connection, ProviderConnection):
            if replacement is not None:
                raise TypeError("connection replacement is ambiguous")
            replacement = connection
            connection_id = connection.connection_id
        else:
            connection_id = connection
        current = self._items.get(connection_id)
        if current is None:
            raise KeyError("provider connection is not registered")
        if replacement is not None:
            if type(replacement) is not ProviderConnection or replacement.connection_id != current.connection_id:
                raise ValueError("provider connection identity is immutable")
            material = replacement.model_dump(mode="python")
            material = {**material, **changes}
        else:
            unknown = set(changes) - {
                "provider_id",
                "connection_name",
                "status",
                "endpoint_id",
                "credential_ref",
                "updated_at",
                "deleted_at",
                "legacy_source",
            }
            if unknown:
                raise TypeError("provider connection update contains unsupported fields")
            material = current.model_dump(mode="python")
            material = {**material, **changes}
        if material.get("provider_id") != current.provider_id:
            raise ValueError("provider connection provider is immutable")
        next_status = material.get("status", current.status)
        if not isinstance(next_status, ProviderConnectionStatus):
            next_status = ProviderConnectionStatus(next_status)
        if next_status not in self._TRANSITIONS[current.status]:
            raise ValueError("provider connection lifecycle transition is invalid")
        now = self._now()
        material["connection_id"] = current.connection_id
        material["created_at"] = current.created_at
        material["updated_at"] = material.get("updated_at") or now
        material["deleted_at"] = now if next_status is ProviderConnectionStatus.DELETED else None
        material["fingerprint"] = "sha256:" + "0" * 64
        checked = ProviderConnection.model_validate(material)
        self.validate_ownership(checked, replacing=current.connection_id)
        self._items[current.connection_id] = copy.deepcopy(checked)
        return copy.deepcopy(checked)

    def update(
        self,
        connection: str | ProviderConnection,
        replacement: ProviderConnection | None = None,
        **changes: object,
    ) -> ProviderConnection:
        return self._update(connection, replacement, **changes)

    def disable(self, connection_id: str) -> ProviderConnection:
        return self._update(connection_id, status=ProviderConnectionStatus.DISABLED)

    def tombstone(self, connection_id: str) -> ProviderConnection:
        return self._update(connection_id, status=ProviderConnectionStatus.DELETED)

    delete = tombstone


class ModelRegistry:
    def __init__(self, models: Sequence[ModelProfile] = ()) -> None:
        self._items: dict[str, ModelProfile] = {}
        for item in models:
            self.register(item)

    def register(self, profile: ModelProfile) -> None:
        if type(profile) is not ModelProfile:
            raise TypeError("model profile is invalid")
        if profile.model_profile_id in self._items:
            raise ValueError("duplicate model profile")
        self._items[profile.model_profile_id] = copy.deepcopy(profile)

    def upsert(self, profile: ModelProfile) -> None:
        if type(profile) is not ModelProfile:
            raise TypeError("model profile is invalid")
        self._items[profile.model_profile_id] = copy.deepcopy(profile)

    def get(self, model_profile_id: str) -> ModelProfile | None:
        return copy.deepcopy(self._items.get(model_profile_id))

    def list(self) -> tuple[ModelProfile, ...]:
        return tuple(copy.deepcopy(value) for value in self._items.values())


class CapabilityProbeResult(ModelGatewayContract):
    endpoint_id: str
    model_profile_id: str
    probe_type: str
    probe_version: str
    timestamp: datetime
    status: CapabilityStatus
    response_fingerprint: str | None = None
    provider_id: str | None = None
    credential_ref: str | None = None
    model_id: str | None = None
    credential_revision: str | None = None
    endpoint_revision: str | None = None

    @field_validator("endpoint_id", "model_profile_id", "probe_type", "probe_version", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("timestamp is invalid") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("timestamp is invalid")
        return value.astimezone(UTC)

    @field_validator("response_fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="response_fingerprint")

    @field_validator("provider_id", "model_id", "credential_revision", "endpoint_revision", mode="before")
    @classmethod
    def _optional_identity(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential_ref(cls, value: object) -> str | None:
        return None if value is None else _ref(value, field="credential_ref")


@runtime_checkable
class ModelDiscoveryPort(Protocol):
    def discover(self, endpoint: EndpointProfile) -> tuple[ModelProfile, ...]: ...


@runtime_checkable
class CapabilityProbePort(Protocol):
    def probe(self, endpoint: EndpointProfile, model: ModelProfile, probe_type: str) -> CapabilityProbeResult: ...


class ModelDiscoveryService:
    def __init__(self, port: ModelDiscoveryPort) -> None:
        if not isinstance(port, ModelDiscoveryPort):
            raise TypeError("model discovery port is invalid")
        self._port = port

    def discover(self, endpoint: EndpointProfile) -> tuple[ModelProfile, ...]:
        result = self._port.discover(copy.deepcopy(endpoint))
        checked: list[ModelProfile] = []
        for model in result:
            if type(model) is not ModelProfile or model.endpoint_id != endpoint.endpoint_id or model.provider_id != endpoint.provider_id:
                raise ValueError("discovered model identity is invalid")
            checked.append(
                model.model_copy(
                    update={
                        "discovery_status": DiscoveryStatus.DISCOVERED,
                        "record_provenance": RecordProvenance.ENDPOINT_DISCOVERY,
                        "availability_source": ModelCatalogSource.PROVIDER_API,
                        "availability_provenance": ModelCatalogSource.PROVIDER_API,
                    }
                )
            )
        return tuple(checked)


class CapabilityProbeService:
    def __init__(self, port: CapabilityProbePort) -> None:
        if not isinstance(port, CapabilityProbePort):
            raise TypeError("capability probe port is invalid")
        self._port = port

    def probe(self, endpoint: EndpointProfile, model: ModelProfile, probe_type: str) -> CapabilityProbeResult:
        checked_type = safe_text(probe_type, field="probe_type", maximum=64)
        result = self._port.probe(copy.deepcopy(endpoint), copy.deepcopy(model), checked_type)
        expected_credential_ref = endpoint.credential_ref or (
            "credential:local" if endpoint.provider_id == "local" else None
        )
        if (
            type(result) is not CapabilityProbeResult
            or result.endpoint_id != endpoint.endpoint_id
            or result.model_profile_id != model.model_profile_id
            or result.provider_id not in {None, endpoint.provider_id}
            or result.model_id not in {None, model.remote_model_id}
            or result.credential_ref not in {None, expected_credential_ref}
        ):
            raise ValueError("capability probe identity is invalid")
        return result


__all__ = (
    "CapabilityProbePort",
    "CapabilityProbeResult",
    "CapabilityProbeService",
    "EndpointRegistry",
    "ModelDiscoveryPort",
    "ModelDiscoveryService",
    "ModelRegistry",
    "ProviderConnectionRegistry",
    "ProviderRegistry",
)
