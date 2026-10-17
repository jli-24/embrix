"""Internal transport binding contracts for the model gateway.

These values deliberately stay inside the runtime boundary.  They carry an
adapter object for execution, while all public projections continue to expose
only the redacted provider/endpoint/credential references.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .ports import ModelGatewayPort, ModelProviderPort

TransportKey = tuple[str, str, str]


def _identity(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not checked or len(checked) > 256 or "\n" in checked or "\r" in checked:
        raise ValueError(f"{field} is invalid")
    return checked


@dataclass(frozen=True, slots=True)
class ResolvedTransportEntry:
    """One atomically resolved provider transport and its ownership metadata."""

    provider_id: str
    endpoint_id: str
    credential_ref: str
    adapter: ModelProviderPort = field(repr=False)
    endpoint_revision: str | None = None
    credential_revision: str | None = None
    connection_id: str | None = None
    protocol_metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _identity(self.provider_id, field="provider_id"))
        object.__setattr__(self, "endpoint_id", _identity(self.endpoint_id, field="endpoint_id"))
        object.__setattr__(self, "credential_ref", _identity(self.credential_ref, field="credential_ref"))
        if not isinstance(self.adapter, ModelProviderPort):
            raise TypeError("transport adapter is invalid")
        for name in ("endpoint_revision", "credential_revision", "connection_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identity(value, field=name))
        if type(self.protocol_metadata) is not tuple:
            raise TypeError("protocol metadata must be a tuple")
        checked: list[tuple[str, str]] = []
        for item in self.protocol_metadata:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("protocol metadata entry is invalid")
            checked.append(
                (
                    _identity(item[0], field="protocol metadata key"),
                    _identity(item[1], field="protocol metadata value"),
                )
            )
        object.__setattr__(self, "protocol_metadata", tuple(checked))

    @property
    def key(self) -> TransportKey:
        return (self.provider_id, self.endpoint_id, self.credential_ref)


@dataclass(frozen=True, slots=True)
class GatewayTransportBinding:
    """Immutable transport snapshot paired with one gateway execution object."""

    entries: tuple[ResolvedTransportEntry, ...]
    gateway: ModelGatewayPort

    def __post_init__(self) -> None:
        if not isinstance(self.gateway, ModelGatewayPort):
            raise TypeError("model gateway port is invalid")
        if type(self.entries) is not tuple:
            raise TypeError("transport entries must be a tuple")
        seen: set[TransportKey] = set()
        for entry in self.entries:
            if not isinstance(entry, ResolvedTransportEntry):
                raise TypeError("transport entry is invalid")
            if entry.key in seen:
                raise ValueError("transport entry key must be unique")
            seen.add(entry.key)

    @classmethod
    def create(
        cls,
        gateway: ModelGatewayPort,
        entries: Sequence[ResolvedTransportEntry] | Mapping[TransportKey, ResolvedTransportEntry],
    ) -> GatewayTransportBinding:
        if isinstance(entries, Mapping):
            resolved: list[ResolvedTransportEntry] = []
            for key, item in entries.items():
                if isinstance(item, ResolvedTransportEntry):
                    if key != item.key:
                        raise ValueError("transport entry key does not match metadata")
                    resolved.append(item)
                    continue
                if not isinstance(key, tuple) or len(key) != 3:
                    raise TypeError("transport entry key is invalid")
                resolved.append(
                    ResolvedTransportEntry(
                        provider_id=key[0],
                        endpoint_id=key[1],
                        credential_ref=key[2],
                        adapter=item,
                    )
                )
            values = tuple(resolved)
        else:
            values = tuple(entries)
        return cls(entries=values, gateway=gateway)

    def resolve(self, key: TransportKey) -> ResolvedTransportEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    @property
    def keys(self) -> tuple[TransportKey, ...]:
        return tuple(entry.key for entry in self.entries)


__all__ = ("GatewayTransportBinding", "ResolvedTransportEntry", "TransportKey")
