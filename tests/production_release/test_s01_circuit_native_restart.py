"""S01 Circuit tables remain a recognized desktop store on restart."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from embedded_copilot.production_release.migration import (
    MigrationRegistry,
    MigrationTarget,
)
from embedded_copilot.production_release.runtime import _validate_desktop_state_identity


def test_native_preflight_accepts_persisted_circuit_tables(tmp_path: Path) -> None:
    database = tmp_path / "desktop.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE project_circuit_proposals (proposal_id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE project_circuit_intents (object_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()

    registry = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(MigrationTarget(
            "desktop-catalog-chat", database, 2,
            identity_validator=_validate_desktop_state_identity,
        ),),
    )
    assert registry.prepare()[0].status == "READY"
