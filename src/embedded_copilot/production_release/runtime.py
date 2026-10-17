"""Composition root for v7.0 release services, separate from engineering runtime."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from embedded_copilot.core.config import Settings

from .backup import BackupService
from .configuration import production_settings
from .credentials import CredentialProviderPort, WindowsCredentialManager
from .diagnostics import DiagnosticsService
from .migration import MigrationRegistry, MigrationRejected, MigrationTarget
from .paths import ProductionStateLayout, versioned_database_path
from .release import product_version_payload
from .session import LocalSessionManager


def _create_empty_database(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.close()


_DESKTOP_STATE_TABLES: Final[frozenset[str]] = frozenset(
    {
        "application_audit",
        "brownfield_build_contexts",
        "conversation_sessions",
        "conversations",
        "desktop_chat_audit",
        "desktop_chat_events",
        "desktop_chat_trace_events",
        "desktop_chat_sessions",
        "knowledge_ingestion_manifest",
        "knowledge_project_tombstone",
        "project_audit",
        "project_circuit_intents",
        "project_circuit_proposals",
        "project_contexts",
        "project_location_audit",
        "project_locations",
        "project_owners",
        "project_previews",
        "projects",
        # Existing approved Catalog persistence families; retain unknown-table rejection.
        "component_source_heads",
        "component_source_publications",
        "hardware_selection_revision_decisions",
        "hardware_selection_revision_heads",
        "hardware_selection_revisions",
        "project_eda_import_previews",
        "project_eda_import_revisions",
        "project_eda_native_mapping_reviews",
        "project_eda_native_move_candidates",
        "project_predesign_circuit_proposals",
        "project_schematic_evidence_v1",
        "project_schematic_verifications",
        "schematic_legacy_owner_snapshots",
        "schematic_plan_legacy_snapshots",
        "schematic_plan_revision_decisions",
        "schematic_plan_revision_heads",
        "schematic_plan_revisions",
        "selection_schematic_plan_anchors",
    }
)


def _validate_desktop_state_identity(connection: sqlite3.Connection) -> None:
    """Validate the shared legacy desktop-state database identity.

    Project catalog, desktop chat, and the knowledge manifest historically
    share ``desktop.sqlite3``.  Its ``user_version`` therefore belongs to the
    manifest migration family (currently version 2), rather than to a
    standalone chat/catalog schema.  An empty SQLite file is a valid fresh
    state; a non-empty file must contain only known desktop-state tables.
    """

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if not str(row[0]).startswith("sqlite_")
    }
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if not tables and version != 0:
        raise MigrationRejected("database identity is unknown")
    unknown = tables - _DESKTOP_STATE_TABLES
    if unknown:
        raise MigrationRejected("database identity is unknown")


@dataclass(slots=True)
class ProductionReleaseRuntime:
    settings: Settings
    layout: ProductionStateLayout
    credentials: CredentialProviderPort
    sessions: LocalSessionManager
    migrations: MigrationRegistry
    backups: BackupService
    diagnostics: DiagnosticsService
    configuration_source: str = "EXPLICIT_PRODUCTION_CONFIG"
    _started: bool = False

    @classmethod
    def create(cls, settings: Settings, *, credentials: CredentialProviderPort | None = None) -> ProductionReleaseRuntime:
        resolved, layout = production_settings(settings)
        credential_port = credentials or WindowsCredentialManager()
        v63_database = versioned_database_path(resolved.desktop_state_database, 63)
        v64_database = versioned_database_path(resolved.desktop_state_database, 64)
        registry = MigrationRegistry(
            backup_root=layout.recovery / "migration",
            targets=(
                MigrationTarget(
                    "desktop-catalog-chat",
                    resolved.desktop_state_database,
                    2,
                    initializer=lambda: _create_empty_database(resolved.desktop_state_database),
                    identity_validator=_validate_desktop_state_identity,
                ),
                MigrationTarget("memory-governance", v63_database, 2, initializer=lambda database=v63_database: _create_empty_database(database)),
                MigrationTarget("validation-proposal", v64_database, 2, initializer=lambda database=v64_database: _create_empty_database(database)),
                MigrationTarget("workspace-apply", resolved.desktop_state_database.with_name("desktop-v65.sqlite3"), 3, initializer=lambda: _create_empty_database(resolved.desktop_state_database.with_name("desktop-v65.sqlite3"))),
                # Execution state is an optional downstream capability.  A
                # locked or otherwise unavailable v6.6 WAL store must not
                # prevent the project catalog and workbench from starting.
                MigrationTarget(
                    "execution-worker",
                    resolved.desktop_state_database.with_name("desktop-v66.sqlite3"),
                    0,
                    core=False,
                    initializer=lambda: _create_empty_database(
                        resolved.desktop_state_database.with_name("desktop-v66.sqlite3")
                    ),
                ),
            ),
        )
        return cls(
            settings=resolved,
            layout=layout,
            credentials=credential_port,
            sessions=LocalSessionManager(
                nonce_ttl_seconds=resolved.desktop_bootstrap_ttl_seconds,
                session_ttl_seconds=resolved.desktop_session_ttl_seconds,
            ),
            migrations=registry,
            backups=BackupService(layout=layout, credentials=credential_port),
            diagnostics=DiagnosticsService(layout),
            configuration_source="EXPLICIT_PRODUCTION_CONFIG",
        )

    def start(self) -> None:
        if self._started:
            return
        self.migrations.prepare()
        self._started = True

    def health(self) -> dict[str, object]:
        return {
            **product_version_payload(),
            "status": "READY",
            "state_root": "CURRENT_USER_LOCALAPPDATA",
            "project_root_source": self.configuration_source,
            "migration": [
                {"identity": value.identity, "schema_version": value.schema_version, "status": value.status}
                for value in self.migrations.statuses()
            ],
            "browser_automation": "UNAVAILABLE / NOT PRESENT",
            "hardware_golden": "DEFERRED",
            "clean_machine_acceptance": "UNVERIFIED",
            "installer_signing": "UNAVAILABLE / UNVERIFIED",
        }


__all__ = ("ProductionReleaseRuntime",)
