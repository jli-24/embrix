from __future__ import annotations

import sqlite3
from pathlib import Path

from embedded_copilot.core.config import Settings
from embedded_copilot.engineering_validation_proposal.adapters.sqlite import SQLiteV64Store
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.paths import (
    ProductionStateLayout,
    versioned_database_path,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.v63_memory_governance import SQLiteV63Store


def _table_names(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _user_version(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def test_production_state_layout_derives_distinct_v63_and_v64_database_identities(
    tmp_path: Path,
) -> None:
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    layout.create()
    base = layout.database / "desktop.sqlite3"

    v63_database = versioned_database_path(base, 63)
    v64_database = versioned_database_path(base, 64)

    assert v63_database == layout.database / "desktop-v63.sqlite3"
    assert v64_database == layout.database / "desktop-v64.sqlite3"
    assert v63_database != v64_database


def test_production_release_migrations_use_the_same_v63_and_v64_identities(
    tmp_path: Path,
) -> None:
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )

    targets = {target.identity: target for target in runtime.migrations._targets}
    v63_database = versioned_database_path(runtime.settings.desktop_state_database, 63)
    v64_database = versioned_database_path(runtime.settings.desktop_state_database, 64)

    assert targets["memory-governance"].database == v63_database
    assert targets["validation-proposal"].database == v64_database
    assert targets["memory-governance"].database != targets["validation-proposal"].database

    runtime.start()

    assert v63_database.exists()
    assert v64_database.exists()
    assert _user_version(v63_database) == 0
    assert _user_version(v64_database) == 0


def test_explicit_desktop_database_keeps_versioned_migration_identity(tmp_path: Path) -> None:
    base = tmp_path / "controlled" / "custom.sqlite3"
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            desktop_state_database=base,
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )

    targets = {target.identity: target for target in runtime.migrations._targets}

    assert targets["memory-governance"].database == versioned_database_path(base, 63)
    assert targets["validation-proposal"].database == versioned_database_path(base, 64)
    assert targets["memory-governance"].database != targets["validation-proposal"].database


def test_real_v63_and_v64_stores_do_not_contaminate_each_other(tmp_path: Path) -> None:
    base = tmp_path / "state" / "desktop.sqlite3"
    v63_database = versioned_database_path(base, 63)
    v64_database = versioned_database_path(base, 64)
    v63_store = SQLiteV63Store(v63_database)
    v64_store = SQLiteV64Store(v64_database)

    assert Path(v63_store.database) == v63_database
    assert Path(v64_store.database) == v64_database

    with v63_store._connect() as connection:
        connection.execute(
            "INSERT INTO v63_evidence(tenant_id, project_id, evidence_id, body) "
            "VALUES (?, ?, ?, ?)",
            ("tenant", "project", "evidence", "v63-only"),
        )
    with v64_store._connect() as connection:
        connection.execute(
            "INSERT INTO v64_audit(tenant_id, project_id, event_id, body) "
            "VALUES (?, ?, ?, ?)",
            ("tenant", "project", "audit", "v64-only"),
        )

    assert "v63_evidence" in _table_names(v63_database)
    assert "v64_audit" not in _table_names(v63_database)
    assert "v64_audit" in _table_names(v64_database)
    assert "v63_evidence" not in _table_names(v64_database)


def test_v63_and_v64_store_reopen_preserves_database_identity(tmp_path: Path) -> None:
    base = tmp_path / "state" / "desktop.sqlite3"
    v63_database = versioned_database_path(base, 63)
    v64_database = versioned_database_path(base, 64)

    SQLiteV63Store(v63_database)
    SQLiteV64Store(v64_database)
    reopened_v63 = SQLiteV63Store(v63_database)
    reopened_v64 = SQLiteV64Store(v64_database)

    assert Path(reopened_v63.database) == v63_database
    assert Path(reopened_v64.database) == v64_database
    assert _user_version(v63_database) == 1
    assert _user_version(v64_database) == 2
