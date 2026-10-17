from __future__ import annotations

import os
from pathlib import Path

import pytest

from embedded_copilot.production_release import windows_acl


def test_non_admin_writable_target_skips_security_descriptor_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "snapshot.sqlite3"
    target.touch()
    monkeypatch.setattr(windows_acl.os, "name", "nt")
    monkeypatch.setattr(windows_acl.os, "access", lambda path, mode: True)

    def unexpected_icacls(*args: object, **kwargs: object) -> object:
        raise AssertionError("icacls must not rewrite an already writable target")

    monkeypatch.setattr(windows_acl.subprocess, "run", unexpected_icacls)
    windows_acl.ensure_current_user_modify(target)


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL lifecycle")
def test_non_admin_snapshot_acl_lifecycle_survives_restart(
    tmp_path: Path,
) -> None:
    import sqlite3

    from embedded_copilot.production_release.migration import MigrationRegistry, MigrationTarget

    database = tmp_path / "database" / "desktop-v65.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(database).close()
    registry = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(
            MigrationTarget(
                "workspace-apply",
                database,
                maximum_schema_version=0,
                initializer=lambda: (
                    database.parent.mkdir(parents=True, exist_ok=True),
                    sqlite3.connect(database).close(),
                )[-1],
            ),
        ),
    )

    assert registry.prepare()[0].status == "READY"
    snapshot = next((tmp_path / "recovery").rglob("workspace-apply.sqlite3"))
    with sqlite3.connect(snapshot) as connection:
        connection.execute("PRAGMA quick_check").fetchone()

    restarted = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(MigrationTarget("workspace-apply", database, maximum_schema_version=0),),
    )
    assert restarted.prepare()[0].status == "READY"
