from __future__ import annotations

import json
import logging
import os
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.production_release.backup import BackupService, RestoreError
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.migration import MigrationRegistry, MigrationRejected, MigrationTarget
from embedded_copilot.production_release.paths import ProductionStateLayout, StatePathRejected
from embedded_copilot.production_release.session import LocalSessionManager, SessionRejected


def _layout(tmp_path: Path) -> ProductionStateLayout:
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    layout.create()
    return layout


def _credentials() -> InMemoryCredentialProvider:
    provider = InMemoryCredentialProvider()
    provider.set("backup-integrity", b"k" * 32)
    return provider


def _database(layout: ProductionStateLayout, value: str = "before") -> Path:
    database = layout.database / "desktop.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS state (value TEXT NOT NULL)")
        connection.execute("DELETE FROM state")
        connection.execute("INSERT INTO state(value) VALUES(?)", (value,))
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    finally:
        connection.close()
    return database


def _value(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        return str(connection.execute("SELECT value FROM state").fetchone()[0])


def test_state_layout_rejects_traversal_and_windows_ads(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(StatePathRejected):
        layout.resolve_under(layout.backups, "../outside.zip")
    with pytest.raises(StatePathRejected):
        layout.resolve_under(layout.backups, "backup.zip:payload")


def test_session_nonce_is_single_use_and_restart_invalidates_session() -> None:
    current = datetime(2026, 8, 15, tzinfo=UTC)
    manager = LocalSessionManager(now=lambda: current)
    nonce = manager.issue_bootstrap(requester="requester", tenant_id="tenant")
    token = manager.exchange(nonce)
    assert manager.authenticate(token).tenant_id == "tenant"
    with pytest.raises(SessionRejected):
        manager.exchange(nonce)
    with pytest.raises(SessionRejected):
        LocalSessionManager(now=lambda: current).authenticate(token)


def test_session_nonce_and_session_expiry_are_rejected() -> None:
    current = datetime(2026, 8, 15, tzinfo=UTC)
    manager = LocalSessionManager(now=lambda: current, nonce_ttl_seconds=1, session_ttl_seconds=1)
    nonce = manager.issue_bootstrap(requester="requester", tenant_id="tenant")
    current += timedelta(seconds=2)
    with pytest.raises(SessionRejected):
        manager.exchange(nonce)


def test_migration_registry_backs_up_then_rejects_future_schema(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = _database(layout)
    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(MigrationTarget("desktop", database, maximum_schema_version=1),),
    )
    assert registry.prepare()[0].status == "READY"
    assert list(layout.recovery.rglob("desktop.sqlite3"))
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationRejected):
        registry.prepare()


def test_snapshot_prepare_telemetry_is_complete_and_redacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    layout = _layout(tmp_path)
    database = _database(layout)
    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(
            MigrationTarget(
                "workspace-apply",
                database,
                maximum_schema_version=1,
                initializer=lambda: sqlite3.connect(database).close(),
            ),
        ),
    )

    with caplog.at_level(
        logging.INFO, logger="embedded_copilot.production_release.migration"
    ):
        assert registry.prepare()[0].status == "READY"

    events = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "snapshot_prepare_telemetry"
    ]
    stages = [
        "S0_SNAPSHOT_PREPARE_ENTER",
        "S1_SOURCE_DATABASE_VALIDATE",
        "S2_TARGET_SNAPSHOT_PATH_RESOLVE",
        "S3_SNAPSHOT_DIRECTORY_READY",
        "S4_SNAPSHOT_FILE_CREATE",
        "S5_SQLITE_BACKUP_INIT",
        "S6_SQLITE_BACKUP_STEP",
        "S7_SNAPSHOT_METADATA_WRITE",
        "S8_SNAPSHOT_BEGIN",
        "S9_SNAPSHOT_COMPLETE",
    ]
    assert [record.snapshot_stage for record in events] == [
        stage for stage in stages for _ in ("ENTER", "PASS")
    ]
    assert [record.telemetry_status for record in events] == [
        status for _ in stages for status in ("ENTER", "PASS")
    ]
    assert all(getattr(record, "exception_type", "-") == "-" for record in events)
    assert all(getattr(record, "sqlite_error_code", "-") == "-" for record in events)
    assert all(getattr(record, "win_error", "-") == "-" for record in events)
    assert str(database) not in " ".join(record.getMessage() for record in events)


def test_snapshot_prepare_telemetry_reports_safe_failure_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import embedded_copilot.production_release.migration as migration

    layout = _layout(tmp_path)
    database = _database(layout)

    def fail_acl(*args: object, **kwargs: object) -> None:
        raise PermissionError(5, "sensitive path must not be logged")

    monkeypatch.setattr(migration, "ensure_current_user_modify", fail_acl)
    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(MigrationTarget("workspace-apply", database, maximum_schema_version=1),),
    )

    with caplog.at_level(
        logging.INFO, logger="embedded_copilot.production_release.migration"
    ):
        with pytest.raises(MigrationRejected):
            registry.prepare()

    event = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "snapshot_prepare_telemetry"
        and getattr(record, "telemetry_status", None) == "FAIL"
    )
    assert event.snapshot_stage == "S0_SNAPSHOT_PREPARE_ENTER"
    assert event.exception_type == "PermissionError"
    assert event.safe_reason_code == "ACCESS_DENIED"
    assert str(database) not in event.getMessage()


def test_post_snapshot_migration_telemetry_is_complete_and_redacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    layout = _layout(tmp_path)
    database = _database(layout)
    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(
            MigrationTarget(
                "workspace-apply",
                database,
                maximum_schema_version=1,
                initializer=lambda: sqlite3.connect(database).close(),
            ),
        ),
    )

    with caplog.at_level(
        logging.INFO, logger="embedded_copilot.production_release.migration"
    ):
        assert registry.prepare()[0].status == "READY"

    events = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "migration_post_snapshot_telemetry"
    ]
    assert [(record.migration_stage, record.telemetry_status) for record in events] == [
        ("M10_MIGRATION_INITIALIZER_ENTER", "ENTER"),
        ("M10_MIGRATION_INITIALIZER_ENTER", "PASS"),
        ("M11_MIGRATION_STATE_LOAD", "ENTER"),
        ("M11_MIGRATION_STATE_LOAD", "PASS"),
        ("M12_SOURCE_TARGET_VERSION_COMPARE", "ENTER"),
        ("M12_SOURCE_TARGET_VERSION_COMPARE", "PASS"),
        ("M13_MIGRATION_PLAN_BUILD", "ENTER"),
        ("M13_MIGRATION_PLAN_BUILD", "PASS"),
        ("M14_MIGRATION_VALIDATION_ENTER", "ENTER"),
        ("M15_MIGRATION_VALIDATION_PASS", "PASS"),
        ("M17_STORE_REGISTRATION_ENTER", "ENTER"),
        ("M18_STORE_REGISTRATION_PASS", "PASS"),
        ("M20_MIGRATION_COMMIT_PREPARE", "ENTER"),
        ("M20_MIGRATION_COMMIT_PREPARE", "PASS"),
        ("M21_MIGRATION_COMPLETE", "ENTER"),
        ("M21_MIGRATION_COMPLETE", "PASS"),
    ]
    assert all(getattr(record, "exception_type", "-") == "-" for record in events)
    assert all(getattr(record, "reason_code", "-") == "-" for record in events)
    assert all(getattr(record, "sqlite_error_code", "-") == "-" for record in events)
    assert all(getattr(record, "safe_error_category", "-") == "-" for record in events)
    assert str(database) not in " ".join(record.getMessage() for record in events)


def test_post_snapshot_migration_telemetry_reports_state_load_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import embedded_copilot.production_release.migration as migration

    layout = _layout(tmp_path)
    database = _database(layout)
    original_version = migration.MigrationRegistry._version
    calls = 0

    def fail_post_snapshot_version(path: Path) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("database detail must not be logged")
        return original_version(path)

    monkeypatch.setattr(
        migration.MigrationRegistry, "_version", staticmethod(fail_post_snapshot_version)
    )
    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(MigrationTarget("workspace-apply", database, maximum_schema_version=1),),
    )

    with caplog.at_level(
        logging.INFO, logger="embedded_copilot.production_release.migration"
    ):
        with pytest.raises(MigrationRejected):
            registry.prepare()

    event = next(
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "migration_post_snapshot_telemetry"
        and getattr(record, "telemetry_status", None) == "FAIL"
    )
    assert event.migration_stage == "M11_MIGRATION_STATE_LOAD"
    assert event.exception_type == "OperationalError"
    assert event.reason_code == "SQLITE_OPERATION_FAILED"
    assert hasattr(event, "sqlite_error_code")
    assert event.safe_error_category == "SQLITE"
    assert str(database) not in event.getMessage()


def test_migration_registry_prepares_target_and_snapshot_acl_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every production SQLite target and migration snapshot receives the current-user ACL policy."""

    import embedded_copilot.production_release.migration as migration

    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        migration,
        "ensure_current_user_modify",
        lambda path, *, inherit_to_children=False: calls.append(
            (Path(path), inherit_to_children)
        ),
    )
    database = tmp_path / "database" / "desktop-v66.sqlite3"
    registry = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(
            MigrationTarget(
                "execution-worker",
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
    assert registry.prepare()[0].status == "READY"
    assert calls[0] == (tmp_path / "recovery", True)
    assert (database.parent, True) in calls
    assert (database, False) in calls
    snapshot = next((tmp_path / "recovery").rglob("execution-worker.sqlite3"))
    assert (snapshot.parent, True) in calls
    assert (snapshot, False) in calls


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL lifecycle")
def test_windows_non_admin_migration_database_is_readwrite_wal_capable_and_restartable(
    tmp_path: Path,
) -> None:
    """A standard Windows token can reopen a newly migrated execution database."""

    database = tmp_path / "database" / "desktop-v66.sqlite3"
    registry = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(
            MigrationTarget(
                "execution-worker",
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
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute("CREATE TABLE restart_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO restart_probe(value) VALUES(7)")
        connection.commit()
        assert database.with_name(f"{database.name}-wal").exists()
        assert database.with_name(f"{database.name}-shm").exists()

    restarted = MigrationRegistry(
        backup_root=tmp_path / "recovery",
        targets=(MigrationTarget("execution-worker", database, maximum_schema_version=0),),
    )
    assert restarted.prepare()[0].status == "READY"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM restart_probe").fetchone()[0] == 7
    snapshot = next((tmp_path / "recovery").rglob("execution-worker.sqlite3"))
    with sqlite3.connect(snapshot) as connection:
        connection.execute("CREATE TABLE snapshot_access_probe (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO snapshot_access_probe(value) VALUES(1)")
        connection.commit()


def test_shared_desktop_state_accepts_legacy_manifest_schema_two(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = layout.database / "desktop.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE knowledge_ingestion_manifest (document_id TEXT PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    from embedded_copilot.production_release.runtime import _validate_desktop_state_identity

    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(
            MigrationTarget(
                "desktop-catalog-chat",
                database,
                maximum_schema_version=2,
                identity_validator=_validate_desktop_state_identity,
            ),
        ),
    )
    assert registry.prepare()[0].status == "READY"
    assert database.exists()


def test_shared_desktop_state_rejects_unknown_identity(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = layout.database / "desktop.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated_state (value TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    from embedded_copilot.production_release.runtime import _validate_desktop_state_identity

    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(
            MigrationTarget(
                "desktop-catalog-chat",
                database,
                maximum_schema_version=2,
                identity_validator=_validate_desktop_state_identity,
            ),
        ),
    )
    with pytest.raises(MigrationRejected):
        registry.prepare()


def test_shared_desktop_state_rejects_versioned_empty_identity(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = layout.database / "desktop.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    from embedded_copilot.production_release.runtime import _validate_desktop_state_identity

    registry = MigrationRegistry(
        backup_root=layout.recovery,
        targets=(MigrationTarget("desktop-catalog-chat", database, 2, identity_validator=_validate_desktop_state_identity),),
    )
    with pytest.raises(MigrationRejected):
        registry.prepare()


def test_restore_rejection_preserves_current_database_and_restart_readability(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = _database(layout, "before")
    service = BackupService(layout=layout, credentials=_credentials())
    backup = service.create(tenant_id="tenant")
    with zipfile.ZipFile(backup.archive, "a") as archive:
        archive.writestr("database/desktop.sqlite3", b"tampered")
    with pytest.raises(RestoreError):
        service.restore(backup_id=backup.backup_id, tenant_id="tenant")
    assert _value(database) == "before"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_backup_restore_restores_application_state_without_integrity_key_leak(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = _database(layout, "before")
    provider = _credentials()
    service = BackupService(layout=layout, credentials=provider)
    backup = service.create(tenant_id="tenant")
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM state")
        connection.execute("INSERT INTO state(value) VALUES('after')")
        connection.commit()
    finally:
        connection.close()
    service.restore(backup_id=backup.backup_id, tenant_id="tenant")
    assert _value(database) == "before"
    with zipfile.ZipFile(backup.archive) as archive:
        contents = b"".join(archive.read(name) for name in archive.namelist())
    assert b"k" * 32 not in contents


def test_backup_rejects_wrong_tenant_without_switching_current_state(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    database = _database(layout)
    service = BackupService(layout=layout, credentials=_credentials())
    backup = service.create(tenant_id="tenant-a")
    with pytest.raises(RestoreError):
        service.restore(backup_id=backup.backup_id, tenant_id="tenant-b")
    assert _value(database) == "before"
