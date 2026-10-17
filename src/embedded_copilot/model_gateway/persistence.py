from typing import TYPE_CHECKING, Any

"""Persistence contracts for the model gateway.

The concrete SQLite adapter lives outside the gateway package so the model
execution boundary cannot acquire filesystem mutation capabilities by import.
"""

from .legacy_identity import (
    LegacyIdentityDryRunPreview,
    LegacyIdentityResolution,
    LegacyIdentityResolutionStatus,
    LegacyIdentityResolver,
    LegacyIdentitySource,
    LegacyIdentityStatus,
    integrity_issue_from_resolution,
)

__all__ = ['IdentityIntegrityIssue', 'LegacyIdentityCorrelation', 'LegacyModelMigrationError', 'ModelGatewayState', 'SQLiteModelGatewayStore', 'migrate_legacy_connections', 'LegacyIdentityDryRunPreview', 'LegacyIdentityResolution', 'LegacyIdentityResolutionStatus', 'LegacyIdentityResolver', 'LegacyIdentitySource', 'LegacyIdentityStatus', 'integrity_issue_from_resolution', 'LegacyIdentityMigrationService', 'LegacyIdentityMigrationSummary', 'apply_legacy_identity_migration']  # noqa: RUF022 - preserve legacy export order.


_LAZY_EXPORTS = {'LegacyIdentityMigrationService': 'embedded_copilot.model_gateway.legacy_identity_migration', 'LegacyIdentityMigrationSummary': 'embedded_copilot.model_gateway.legacy_identity_migration', 'apply_legacy_identity_migration': 'embedded_copilot.model_gateway.legacy_identity_migration', 'IdentityIntegrityIssue': 'embedded_copilot.model_gateway_storage', 'LegacyIdentityCorrelation': 'embedded_copilot.model_gateway_storage', 'LegacyModelMigrationError': 'embedded_copilot.model_gateway_storage', 'ModelGatewayState': 'embedded_copilot.model_gateway_storage', 'SQLiteModelGatewayStore': 'embedded_copilot.model_gateway_storage', 'migrate_legacy_connections': 'embedded_copilot.model_gateway_storage'}

def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

if TYPE_CHECKING:
    from embedded_copilot.model_gateway.legacy_identity_migration import (
        LegacyIdentityMigrationService,
        LegacyIdentityMigrationSummary,
        apply_legacy_identity_migration,
    )
    from embedded_copilot.model_gateway_storage import (
        IdentityIntegrityIssue,
        LegacyIdentityCorrelation,
        LegacyModelMigrationError,
        ModelGatewayState,
        SQLiteModelGatewayStore,
        migrate_legacy_connections,
    )
