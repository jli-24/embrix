"""Pure legacy model identity resolution for the D1 migration foundation.

The resolver intentionally has no persistence or runtime dependencies.  It
only correlates a legacy source row with the authoritative provider, endpoint,
credential, connection and model records supplied by its caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .contracts import ModelGatewayContract, identifier, safe_text
from .governance import BoundModelIdentity
from .profiles import (
    CredentialProfile,
    EndpointProfile,
    IdentityStatus,
    ModelProfile,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
)


class LegacyIdentityStatus(StrEnum):
    UNIQUE = "UNIQUE"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


# A readable compatibility alias for callers that use the longer name.
LegacyIdentityResolutionStatus = LegacyIdentityStatus

_CREDENTIAL_REF = re.compile(
    r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
)
_AUTO_FINGERPRINT = "sha256:" + "0" * 64


def _optional_id(value: object, *, field: str) -> str | None:
    return None if value is None else identifier(value, field=field)


def _optional_ref(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _CREDENTIAL_REF.fullmatch(value.strip()):
        raise ValueError("credential_ref is invalid")
    return value.strip()


def _nonsecret(value: object, *, field: str, maximum: int = 512) -> str:
    return safe_text(value, field=field, maximum=maximum)


class LegacyIdentitySource(ModelGatewayContract):
    """Normalized, non-secret identity fields from one legacy record."""

    source_type: str = "LEGACY"
    record_reference: str = "legacy-record"
    provider_id: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    model_profile_id: str | None = None
    model_id: str | None = None
    endpoint_revision: str | None = None
    credential_revision: str | None = None
    model_revision: str | None = None
    migration_sub_version: int = Field(default=1, ge=1)
    source_fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("source_type", "record_reference", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return _nonsecret(value, field=info.field_name)

    @field_validator(
        "provider_id", "endpoint_id", "model_profile_id", "model_id",
        "endpoint_revision", "credential_revision", "model_revision",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str | None:
        return _optional_id(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def _credential(cls, value: object) -> str | None:
        return _optional_ref(value)

    @model_validator(mode="after")
    def _fingerprint(self) -> LegacyIdentitySource:
        material = {
            "source_type": self.source_type,
            "record_reference": self.record_reference,
            "provider_id": self.provider_id,
            "endpoint_id": self.endpoint_id,
            "credential_ref": self.credential_ref,
            "model_profile_id": self.model_profile_id,
            "model_id": self.model_id,
            "endpoint_revision": self.endpoint_revision,
            "credential_revision": self.credential_revision,
            "model_revision": self.model_revision,
            "migration_sub_version": self.migration_sub_version,
        }
        expected = "sha256:" + hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        if self.source_fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "source_fingerprint", expected)
        elif self.source_fingerprint != expected:
            raise ValueError("source_fingerprint mismatch")
        return self

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> LegacyIdentitySource:
        """Normalize common legacy aliases without filling missing identity."""
        raw = dict(value)
        aliases = {
            "provider": "provider_id",
            "endpoint": "endpoint_id",
            "credential": "credential_ref",
            "profile_id": "model_profile_id",
            "remote_model_id": "model_id",
            "chat_model": "model_id",
            "id": "record_reference",
        }
        for old, new in aliases.items():
            if old in raw:
                if new in raw and raw[new] != raw[old]:
                    raise ValueError(f"conflicting legacy identity field: {new}")
                raw[new] = raw.pop(old)
        return cls.model_validate(raw)


class LegacyIdentityResolution(ModelGatewayContract):
    status: LegacyIdentityStatus
    source_type: str
    record_reference: str
    source_fingerprint: str
    candidate_count: int = Field(ge=0)
    resolved_identity: BoundModelIdentity | None = None
    connection_id: str | None = None
    source_provider_id: str | None = None
    source_endpoint_id: str | None = None
    source_credential_ref: str | None = None
    source_model_profile_id: str | None = None
    source_model_id: str | None = None
    reason_code: str
    reason_detail: str
    non_sensitive_context: tuple[tuple[str, str], ...] = ()
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
    unchanged: bool = False
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("source_type", "record_reference", "reason_code", "reason_detail", mode="before")
    @classmethod
    def _safe(cls, value: object, info) -> str:
        return _nonsecret(value, field=info.field_name, maximum=2048 if info.field_name == "reason_detail" else 512)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("source_fingerprint is invalid")
        return value

    @field_validator("non_sensitive_context", mode="before")
    @classmethod
    def _context(cls, value: object) -> tuple[tuple[str, str], ...]:
        if value is None:
            return ()
        if isinstance(value, Mapping):
            value = tuple(sorted(value.items()))
        if type(value) is not tuple:
            raise TypeError("non_sensitive_context must be a tuple")
        return tuple((_nonsecret(k, field="context_key", maximum=128), _nonsecret(v, field="context_value")) for k, v in value)

    @model_validator(mode="after")
    def _fp(self) -> LegacyIdentityResolution:
        material = self.model_dump(mode="json", exclude={"fingerprint"})
        expected = "sha256:" + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("resolution fingerprint mismatch")
        return self


class LegacyIdentityDryRunPreview(ModelGatewayContract):
    results: tuple[LegacyIdentityResolution, ...] = ()
    unique_count: int = Field(default=0, ge=0)
    ambiguous_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)


def _as_tuple(value: Iterable[Any] | None) -> tuple[Any, ...]:
    if value is None:
        return ()
    listed = getattr(value, "list", None)
    if callable(listed):
        value = listed()
    return tuple(value)


class LegacyIdentityResolver:
    """Deterministic resolver.  It has no methods that mutate its inputs."""

    def __init__(
        self,
        *,
        providers: Iterable[ProviderProfile | str] | None = None,
        endpoints: Iterable[EndpointProfile] | None = None,
        connections: Iterable[ProviderConnection] | None = None,
        credentials: Iterable[CredentialProfile] | None = None,
        models: Iterable[ModelProfile] | None = None,
        credential_revisions: Mapping[str, str] | None = None,
        endpoint_revisions: Mapping[str, str] | None = None,
        model_revisions: Mapping[str, str] | None = None,
    ) -> None:
        self._providers = frozenset(item if isinstance(item, str) else item.provider_id for item in _as_tuple(providers))
        self._endpoints = _as_tuple(endpoints)
        self._connections = _as_tuple(connections)
        self._credentials = _as_tuple(credentials)
        self._models = _as_tuple(models)
        self._credential_revisions = dict(credential_revisions or {})
        self._endpoint_revisions = dict(endpoint_revisions or {})
        self._model_revisions = dict(model_revisions or {})

    @classmethod
    def from_state(cls, state: Any, **kwargs: Any) -> LegacyIdentityResolver:
        return cls(
            providers=getattr(state, "providers", ()),
            endpoints=getattr(state, "endpoints", ()),
            connections=getattr(state, "connections", ()),
            credentials=getattr(state, "credentials", ()),
            models=getattr(state, "models", ()),
            **kwargs,
        )

    @staticmethod
    def _source(source: LegacyIdentitySource | Mapping[str, Any]) -> LegacyIdentitySource:
        if isinstance(source, LegacyIdentitySource):
            return source
        if isinstance(source, Mapping):
            return LegacyIdentitySource.from_mapping(source)
        raise TypeError("legacy identity source is invalid")

    def resolve(
        self,
        source: LegacyIdentitySource | Mapping[str, Any],
        *,
        conflicting_sources: Iterable[LegacyIdentitySource | Mapping[str, Any]] = (),
    ) -> LegacyIdentityResolution:
        src = self._source(source)
        conflicts = tuple(self._source(item) for item in conflicting_sources)
        if any(self._identity_key(item) != self._identity_key(src) for item in conflicts):
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 2, None, "RECORD_MIGRATION_AMBIGUOUS", "source records conflict")

        # A model id without independent ownership is never enough.
        if src.model_id is not None and src.model_profile_id is None and src.provider_id is None and src.endpoint_id is None and src.credential_ref is None:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "model identity has no ownership context")

        provider_alias = None
        if src.provider_id == "custom-relay":
            custom_ids = tuple(sorted(item for item in self._providers if item.startswith("custom-")))
            if len(custom_ids) == 1:
                provider_alias = custom_ids[0]
            elif len(custom_ids) > 1:
                return self._result(src, LegacyIdentityStatus.AMBIGUOUS, len(custom_ids), None, "RECORD_MIGRATION_AMBIGUOUS", "custom relay alias maps to multiple providers")
        model_candidates = self._model_candidates(src, provider_override=provider_alias)
        if len(model_candidates) > 1:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, len(model_candidates), None, "RECORD_MIGRATION_AMBIGUOUS", "multiple model profiles match")
        if not model_candidates:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "authoritative model profile is missing")
        model = model_candidates[0]

        provider_id = provider_alias or src.provider_id or model.provider_id
        endpoint_id = src.endpoint_id or model.endpoint_id
        if provider_id != model.provider_id or endpoint_id != model.endpoint_id:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 1, None, "RECORD_MIGRATION_AMBIGUOUS", "source and model ownership conflict")
        if self._providers and provider_id not in self._providers:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "provider record is missing")
        if src.provider_id is not None and src.endpoint_id is None and src.credential_ref is None and src.model_profile_id is None:
            provider_connections = tuple(item for item in self._connections if item.provider_id == provider_id)
            if len(provider_connections) > 1:
                return self._result(src, LegacyIdentityStatus.AMBIGUOUS, len(provider_connections), None, "RECORD_MIGRATION_AMBIGUOUS", "provider has multiple connection contexts")
        endpoint = next((item for item in self._endpoints if item.endpoint_id == endpoint_id), None)
        if endpoint is None or endpoint.provider_id != provider_id:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "endpoint ownership is not proven")

        connections = tuple(
            item for item in self._connections
            if item.provider_id == provider_id
            and item.endpoint_id == endpoint_id
            and (src.credential_ref is None or item.credential_ref == src.credential_ref)
        )
        # A source without endpoint can only use a connection when that
        # ownership is unique; current status never breaks a tie.
        if src.endpoint_id is None:
            connections = tuple(item for item in self._connections if item.provider_id == provider_id and (src.credential_ref is None or item.credential_ref == src.credential_ref))
        if len(connections) > 1:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, len(connections), None, "RECORD_MIGRATION_AMBIGUOUS", "provider connection ownership is ambiguous")
        if not connections:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "provider connection ownership is missing")
        connection = connections[0]
        credential_ref = src.credential_ref or connection.credential_ref or endpoint.credential_ref
        if credential_ref is None:
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "credential ownership is not proven")
        if connection.credential_ref != credential_ref and endpoint.credential_ref != credential_ref:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 1, None, "RECORD_MIGRATION_AMBIGUOUS", "credential ownership conflicts")
        credential = next((item for item in self._credentials if item.secret_ref == credential_ref), None)
        if credential is not None and credential.provider_id != provider_id:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 1, None, "RECORD_MIGRATION_AMBIGUOUS", "credential provider ownership conflicts")
        # External credentials must have a catalog record.  The local
        # compatibility sentinel is the sole credential-less exception.
        if credential is None and not (provider_id == "local" and credential_ref == "credential:local"):
            return self._result(src, LegacyIdentityStatus.UNRESOLVED, 0, None, "LEGACY_IDENTITY_UNRESOLVED", "credential record is missing")
        if src.model_profile_id is not None and src.model_profile_id != model.model_profile_id:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 1, None, "RECORD_MIGRATION_AMBIGUOUS", "model profile identity conflicts")
        if src.model_id is not None and src.model_id != model.remote_model_id:
            return self._result(src, LegacyIdentityStatus.AMBIGUOUS, 1, None, "RECORD_MIGRATION_AMBIGUOUS", "remote model identity conflicts")

        identity = BoundModelIdentity.from_profile(model, credential_ref)
        endpoint_revision_current = self._endpoint_revisions.get(endpoint_id) or getattr(model, "endpoint_revision", None)
        credential_revision_current = self._credential_revisions.get(credential_ref) or getattr(model, "credential_revision", None)
        endpoint_current = self._current_revision(src.endpoint_revision, endpoint_id, endpoint_revision_current)
        credential_current = self._current_revision(src.credential_revision, credential_ref, credential_revision_current)
        model_current = self._current_revision(src.model_revision, model.model_profile_id, self._model_revisions.get(model.model_profile_id))
        revisions_current = (endpoint_current, credential_current, model_current)
        verification_current = (
            getattr(model.verification_status, "value", model.verification_status) == "VERIFIED"
            and not any(value is False for value in revisions_current)
        )
        historical = connection.status is not ProviderConnectionStatus.ACTIVE
        current_eligible = connection.status is ProviderConnectionStatus.ACTIVE and model.enabled and model.lifecycle_status.upper() == "ACTIVE" and not model.deprecated
        return self._result(
            src, LegacyIdentityStatus.UNIQUE, 1, identity, "IDENTITY_UNIQUE", "full authoritative identity proven",
            connection_id=connection.connection_id,
            endpoint_revision_current=endpoint_revision_current, endpoint_revision_currentness=endpoint_current,
            credential_revision_current=credential_revision_current, credential_revision_currentness=credential_current,
            model_revision_current=self._model_revisions.get(model.model_profile_id), model_revision_currentness=model_current,
            verification_current=verification_current, current_eligible=current_eligible,
            historical_correlation=historical, unchanged=endpoint_current is not False and credential_current is not False and model_current is not False,
        )

    def preview(self, sources: Iterable[LegacyIdentitySource | Mapping[str, Any]]) -> LegacyIdentityDryRunPreview:
        normalized = tuple(self._source(item) for item in sources)
        grouped: dict[tuple[str, str], list[LegacyIdentitySource]] = {}
        for item in normalized:
            grouped.setdefault((item.source_type, item.record_reference), []).append(item)
        results = tuple(
            self.resolve(
                item,
                conflicting_sources=(
                    other
                    for other in grouped[(item.source_type, item.record_reference)]
                    if self._identity_key(other) != self._identity_key(item)
                ),
            )
            for item in normalized
        )
        return LegacyIdentityDryRunPreview(
            results=results,
            unique_count=sum(item.status is LegacyIdentityStatus.UNIQUE for item in results),
            ambiguous_count=sum(item.status is LegacyIdentityStatus.AMBIGUOUS for item in results),
            unresolved_count=sum(item.status is LegacyIdentityStatus.UNRESOLVED for item in results),
            unchanged_count=sum(item.unchanged for item in results),
        )

    def dry_run(self, sources: Iterable[LegacyIdentitySource | Mapping[str, Any]]) -> LegacyIdentityDryRunPreview:
        """Preview alias kept internal; this method performs no writes."""
        return self.preview(sources)

    def resolve_source(
        self,
        source: LegacyIdentitySource | Mapping[str, Any],
        *,
        conflicting_sources: Iterable[LegacyIdentitySource | Mapping[str, Any]] = (),
    ) -> LegacyIdentityResolution:
        return self.resolve(source, conflicting_sources=conflicting_sources)

    def _model_candidates(self, src: LegacyIdentitySource, *, provider_override: str | None = None) -> tuple[ModelProfile, ...]:
        if src.model_profile_id is not None:
            return tuple(item for item in self._models if item.model_profile_id == src.model_profile_id)
        if src.model_id is None:
            return ()
        provider_id = provider_override or src.provider_id
        return tuple(item for item in self._models if item.remote_model_id == src.model_id and (provider_id is None or item.provider_id == provider_id) and (src.endpoint_id is None or item.endpoint_id == src.endpoint_id))

    @staticmethod
    def _identity_key(source: LegacyIdentitySource) -> tuple[str | None, ...]:
        return (source.provider_id, source.endpoint_id, source.credential_ref, source.model_profile_id, source.model_id)

    @staticmethod
    def _current_revision(source: str | None, key: str, current: str | None) -> bool | None:
        if source is None:
            return None
        return current is not None and source == current

    @staticmethod
    def _result(src: LegacyIdentitySource, status: LegacyIdentityStatus, count: int, identity: BoundModelIdentity | None, reason: str, detail: str, **kwargs: Any) -> LegacyIdentityResolution:
        return LegacyIdentityResolution(
            status=status, source_type=src.source_type, record_reference=src.record_reference,
            source_fingerprint=src.source_fingerprint, candidate_count=count,
            resolved_identity=identity,
            source_provider_id=src.provider_id,
            source_endpoint_id=src.endpoint_id,
            source_credential_ref=src.credential_ref,
            source_model_profile_id=src.model_profile_id,
            source_model_id=src.model_id,
            reason_code=reason, reason_detail=detail,
            endpoint_revision_source=src.endpoint_revision, credential_revision_source=src.credential_revision,
            model_revision_source=src.model_revision, **kwargs,
        )


def integrity_issue_from_resolution(
    resolution: LegacyIdentityResolution,
    *,
    migration_version: int = 0,
    migration_sub_version: int = 1,
    detected_at: Any = None,
) -> Any:
    """Build quarantine metadata without persisting or promoting anything."""
    if resolution.status is LegacyIdentityStatus.UNIQUE:
        return None
    from datetime import UTC, datetime
    from .persistence import IdentityIntegrityIssue

    identity = resolution.resolved_identity
    return IdentityIntegrityIssue(
        record_type="LEGACY_IDENTITY",
        record_id=resolution.record_reference,
        provider_id=identity.provider_id if identity else resolution.source_provider_id,
        endpoint_id=identity.endpoint_id if identity else resolution.source_endpoint_id,
        credential_ref=identity.credential_ref if identity else resolution.source_credential_ref,
        model_profile_id=identity.model_profile_id if identity else resolution.source_model_profile_id,
        model_id=identity.model_id if identity else resolution.source_model_id,
        identity_status=IdentityStatus.INVALID,
        reason=resolution.reason_code,
        detected_at=detected_at or datetime.now(UTC),
        source_type=resolution.source_type,
        record_reference=resolution.record_reference,
        source_fingerprint=resolution.source_fingerprint,
        migration_version=migration_version,
        migration_sub_version=migration_sub_version,
    )


__all__ = (
    "LegacyIdentityDryRunPreview",
    "LegacyIdentityResolution",
    "LegacyIdentityResolutionStatus",
    "LegacyIdentityResolver",
    "LegacyIdentitySource",
    "LegacyIdentityStatus",
    "integrity_issue_from_resolution",
)
