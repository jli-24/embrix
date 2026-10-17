from __future__ import annotations

from pathlib import Path

from embedded_copilot.core.config import Settings
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository


def test_desktop_migration_accepts_catalog_brownfield_build_context_table(
    tmp_path: Path,
) -> None:
    database = tmp_path / "desktop.sqlite3"
    SQLiteProjectCatalogRepository(database)
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            desktop_state_database=database,
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )

    runtime.start()

    assert runtime.health()["status"] == "READY"
