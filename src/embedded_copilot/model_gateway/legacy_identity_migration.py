"""D2 historical identity correlation migration.

The migration is deliberately a small persistence consumer of the D1
``LegacyIdentityResolver``.  It plans every row first and writes one validated
``ModelGatewayState`` through the existing SQLite store, so correlations,
record issues and the marker commit together.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, model_validator

from .contracts import ModelGatewayContract
from .legacy_identity import (
    LegacyIdentityResolution,
    LegacyIdentityResolver,
    LegacyIdentitySource,
    LegacyIdentityStatus,
    integrity_issue_from_resolution,
)
from embedded_copilot.model_gateway_storage import (
    IdentityIntegrityIssue,
    LegacyIdentityCorrelation,
    ModelGatewayState,
    SQLiteModelGatewayStore,
)


_D2_MIGRATION_VERSION = 2
_D2_MIGRATION_SUB_VERSION = 1
_AUTO_FINGERPRINT = "sha256:" + "0" * 64


class LegacyIdentityMigrationSummary(ModelGatewayContract):
    unique_migrated: int = Field(default=0, ge=0)
    ambiguous_quarantined: int = Field(default=0, ge=0)
    unresolved_quarantined: int = Field(default=0, ge=0)
    unchanged: int = Field(default=0, ge=0)
    results: tuple[LegacyIdentityResolution, ...] = ()
    marker_fingerprint: str | None = None

    @model_validator(mode="after")
    def _marker(self) -> LegacyIdentityMigrationSummary:
        if self.marker_fingerprint is not None and not self.marker_fingerprint.startswith("sha256:"):
            raise ValueError("marker_fingerprint is invalid")
        return self


def _marker_fingerprint(sources: tuple[LegacyIdentitySource, ...]) -> str:
    material = {
        "migration_version": _D2_MIGRATION_VERSION,
        "migration_sub_version": _D2_MIGRATION_SUB_VERSION,
        "sources": sorted(
            (item.source_type, item.record_reference, item.source_fingerprint)
            for item in sources
        ),
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalize_sources(
    sources: Iterable[LegacyIdentitySource | Mapping[str, Any]],
) -> tuple[LegacyIdentitySource, ...]:
    normalized: list[LegacyIdentitySource] = []
    seen: set[tuple[str, str, str]] = set()
    for item in sources:
        if isinstance(item, LegacyIdentitySource):
            source = item
        elif isinstance(item, Mapping):
            source = LegacyIdentitySource.from_mapping(item)
        else:
            raise TypeError("legacy migration source is invalid")
        key = (source.source_type, source.record_reference, source.source_fingerprint)
        if key not in seen:
            seen.add(key)
            normalized.append(source)
    return tuple(normalized)


def _source_semantics(
    source: LegacyIdentitySource,
    semantics: str | Mapping[object, str] | None,
) -> str:
    if isinstance(semantics, str):
        return semantics
    if isinstance(semantics, Mapping):
        value = semantics.get((source.source_type, source.record_reference), semantics.get(source.record_reference))
        if value is not None:
            return str(value)
    return "LEGACY_MODEL"


def _same_issue(
    issue: IdentityIntegrityIssue,
    source: LegacyIdentitySource,
    resolution: LegacyIdentityResolution,
) -> bool:
    return (
        issue.source_type == source.source_type
        and issue.record_reference == source.record_reference
        and issue.source_fingerprint == source.source_fingerprint
        and issue.reason == resolution.reason_code
        and issue.migration_version == _D2_MIGRATION_VERSION
        and issue.migration_sub_version == _D2_MIGRATION_SUB_VERSION
    )


def _correlation_from_resolution(
    source: LegacyIdentitySource,
    resolution: LegacyIdentityResolution,
    *,
    source_semantics: str,
) -> LegacyIdentityCorrelation:
    identity = resolution.resolved_identity
    if resolution.status is not LegacyIdentityStatus.UNIQUE or identity is None:
        raise ValueError("only UNIQUE resolution can become a correlation")
    return LegacyIdentityCorrelation(
        source_type=source.source_type,
        record_reference=source.record_reference,
        source_fingerprint=source.source_fingerprint,
        migration_version=_D2_MIGRATION_VERSION,
        migration_sub_version=_D2_MIGRATION_SUB_VERSION,
        status=resolution.status.value,
        provider_id=identity.provider_id,
        endpoint_id=identity.endpoint_id,
        credential_ref=identity.credential_ref,
        model_profile_id=identity.model_profile_id,
        model_id=identity.model_id,
        connection_id=resolution.connection_id,
        source_semantics=source_semantics,
        endpoint_revision_source=resolution.endpoint_revision_source,
        endpoint_revision_current=resolution.endpoint_revision_current,
        endpoint_revision_currentness=resolution.endpoint_revision_currentness,
        credential_revision_source=resolution.credential_revision_source,
        credential_revision_current=resolution.credential_revision_current,
        credential_revision_currentness=resolution.credential_revision_currentness,
        model_revision_source=resolution.model_revision_source,
        model_revision_current=resolution.model_revision_current,
        model_revision_currentness=resolution.model_revision_currentness,
        verification_current=resolution.verification_current,
        current_eligible=resolution.current_eligible,
        historical_correlation=resolution.historical_correlation,
    )


class LegacyIdentityMigrationService:
    """Apply D2 correlations through the existing state store only."""

    migration_version = _D2_MIGRATION_VERSION
    migration_sub_version = _D2_MIGRATION_SUB_VERSION

    def __init__(
        self,
        store: SQLiteModelGatewayStore,
        *,
        resolver: LegacyIdentityResolver | None = None,
    ) -> None:
        if not isinstance(store, SQLiteModelGatewayStore):
            raise TypeError("legacy identity migration store is invalid")
        self.store = store
        self.resolver = resolver

    def _state_and_resolver(self) -> tuple[ModelGatewayState, LegacyIdentityResolver]:
        state = self.store.load() or ModelGatewayState()
        resolver = self.resolver or LegacyIdentityResolver.from_state(state)
        return state, resolver

    def preview(
        self,
        sources: Iterable[LegacyIdentitySource | Mapping[str, Any]],
    ) -> LegacyIdentityMigrationSummary:
        normalized = _normalize_sources(sources)
        _, resolver = self._state_and_resolver()
        results = resolver.preview(normalized).results
        return LegacyIdentityMigrationSummary(
            unique_migrated=sum(item.status is LegacyIdentityStatus.UNIQUE for item in results),
            ambiguous_quarantined=sum(item.status is LegacyIdentityStatus.AMBIGUOUS for item in results),
            unresolved_quarantined=sum(item.status is LegacyIdentityStatus.UNRESOLVED for item in results),
            results=results,
            marker_fingerprint=_marker_fingerprint(normalized) if normalized else None,
        )

    def apply(
        self,
        sources: Iterable[LegacyIdentitySource | Mapping[str, Any]],
        *,
        source_semantics: str | Mapping[object, str] | None = None,
        before_commit: Callable[[ModelGatewayState], None] | None = None,
    ) -> LegacyIdentityMigrationSummary:
        normalized = _normalize_sources(sources)
        if not normalized:
            return LegacyIdentityMigrationSummary()
        state, resolver = self._state_and_resolver()
        preview = resolver.preview(normalized)
        marker = _marker_fingerprint(normalized)

        correlations = {
            (item.source_type, item.record_reference): item
            for item in state.identity_correlations
        }
        issues = list(state.integrity_issues)
        unique_migrated = 0
        ambiguous_quarantined = 0
        unresolved_quarantined = 0
        unchanged = 0

        for source, resolution in zip(normalized, preview.results, strict=True):
            key = (source.source_type, source.record_reference)
            previous_issue = next(
                (
                    item
                    for item in state.integrity_issues
                    if _same_issue(item, source, resolution)
                ),
                None,
            )
            correlations.pop(key, None)
            issues = [
                issue
                for issue in issues
                if not (
                    issue.source_type == source.source_type
                    and issue.record_reference == source.record_reference
                    and issue.migration_sub_version == _D2_MIGRATION_SUB_VERSION
                )
            ]
            if resolution.status is LegacyIdentityStatus.UNIQUE:
                correlation = _correlation_from_resolution(
                    source,
                    resolution,
                    source_semantics=_source_semantics(source, source_semantics),
                )
                previous = next(
                    (
                        item
                        for item in state.identity_correlations
                        if (item.source_type, item.record_reference) == key
                    ),
                    None,
                )
                if previous is not None and previous.correlation_fingerprint == correlation.correlation_fingerprint:
                    unchanged += 1
                else:
                    unique_migrated += 1
                correlations[key] = correlation
                continue

            issue = integrity_issue_from_resolution(
                resolution,
                migration_version=_D2_MIGRATION_VERSION,
                migration_sub_version=_D2_MIGRATION_SUB_VERSION,
                detected_at=datetime.now(UTC),
            )
            if issue is None:
                raise AssertionError("non-UNIQUE resolution did not produce an issue")
            if previous_issue is not None:
                unchanged += 1
            elif resolution.status is LegacyIdentityStatus.AMBIGUOUS:
                ambiguous_quarantined += 1
            else:
                unresolved_quarantined += 1
            issues.append(previous_issue or issue)

        payload = state.model_dump(mode="json")
        payload["identity_correlations"] = [item.model_dump(mode="json") for item in correlations.values()]
        payload["integrity_issues"] = [item.model_dump(mode="json") for item in issues]
        payload["model_identity_migration_version"] = _D2_MIGRATION_VERSION
        payload["model_identity_source_fingerprint"] = marker
        payload["fingerprint"] = _AUTO_FINGERPRINT
        # Persisted JSON uses arrays while the in-memory contracts require
        # tuples; validate through the same JSON boundary as the store loader.
        next_state = ModelGatewayState.model_validate_json(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

        if before_commit is not None:
            before_commit(next_state)
        self.store.save(next_state)
        return LegacyIdentityMigrationSummary(
            unique_migrated=unique_migrated,
            ambiguous_quarantined=ambiguous_quarantined,
            unresolved_quarantined=unresolved_quarantined,
            unchanged=unchanged,
            results=preview.results,
            marker_fingerprint=marker,
        )


def apply_legacy_identity_migration(
    store: SQLiteModelGatewayStore,
    sources: Iterable[LegacyIdentitySource | Mapping[str, Any]],
    **kwargs: Any,
) -> LegacyIdentityMigrationSummary:
    return LegacyIdentityMigrationService(store).apply(sources, **kwargs)


__all__ = (
    "LegacyIdentityMigrationService",
    "LegacyIdentityMigrationSummary",
    "apply_legacy_identity_migration",
)
