"""Per-store SQLite migration governance without merging existing databases."""

from __future__ import annotations

import sqlite3
import logging
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .windows_acl import ensure_current_user_modify


MigrationIdentityValidator = Callable[[sqlite3.Connection], None]
_logger = logging.getLogger(__name__)

def _telemetry_failure_fields(error: Exception) -> dict[str, object]:
    sqlite_error_code = getattr(error, "sqlite_errorcode", "-")
    win_error = getattr(error, "winerror", "-")
    if isinstance(error, sqlite3.Error):
        safe_reason_code = "SQLITE_OPERATION_FAILED"
    elif isinstance(error, PermissionError) or win_error == 5:
        safe_reason_code = "ACCESS_DENIED"
    elif isinstance(error, FileNotFoundError) or win_error == 3:
        safe_reason_code = "PATH_NOT_FOUND"
    elif win_error == 32:
        safe_reason_code = "SHARING_VIOLATION"
    elif isinstance(error, OSError):
        safe_reason_code = "OS_OPERATION_FAILED"
    else:
        safe_reason_code = "SNAPSHOT_OPERATION_FAILED"
    return {
        "exception_type": type(error).__name__,
        "safe_reason_code": safe_reason_code,
        "sqlite_error_code": sqlite_error_code,
        "win_error": win_error,
    }


def _emit_snapshot_telemetry(
    stage: str, status: str, error: Exception | None = None
) -> None:
    extra: dict[str, object] = {
        "event_name": "snapshot_prepare_telemetry",
        "telemetry_status": status,
        "snapshot_stage": stage,
        "exception_type": "-",
        "safe_reason_code": "-",
        "sqlite_error_code": "-",
        "win_error": "-",
    }
    if error is not None:
        extra.update(_telemetry_failure_fields(error))
    _logger.log(
        logging.WARNING if status == "FAIL" else logging.INFO,
        "snapshot_prepare_telemetry",
        extra=extra,
    )
    for handler in _logger.handlers:
        handler.flush()


def _migration_failure_fields(error: Exception) -> dict[str, object]:
    sqlite_error_code = getattr(error, "sqlite_errorcode", "-")
    if isinstance(error, sqlite3.Error):
        reason_code = "SQLITE_OPERATION_FAILED"
        safe_error_category = "SQLITE"
    elif isinstance(error, PermissionError) or getattr(error, "winerror", None) == 5:
        reason_code = "ACCESS_DENIED"
        safe_error_category = "OS_ACCESS"
    elif isinstance(error, FileNotFoundError) or getattr(error, "winerror", None) == 3:
        reason_code = "PATH_NOT_FOUND"
        safe_error_category = "OS_PATH"
    elif getattr(error, "winerror", None) == 32:
        reason_code = "SHARING_VIOLATION"
        safe_error_category = "OS_SHARE"
    elif isinstance(error, MigrationRejected):
        reason_code = "MIGRATION_REJECTED"
        safe_error_category = "MIGRATION"
    elif isinstance(error, OSError):
        reason_code = "OS_OPERATION_FAILED"
        safe_error_category = "OS"
    else:
        reason_code = "MIGRATION_OPERATION_FAILED"
        safe_error_category = "MIGRATION"
    return {
        "exception_type": type(error).__name__,
        "reason_code": reason_code,
        "sqlite_error_code": sqlite_error_code,
        "safe_error_category": safe_error_category,
    }


def _emit_migration_telemetry(
    stage: str, status: str, error: Exception | None = None
) -> None:
    extra: dict[str, object] = {
        "event_name": "migration_post_snapshot_telemetry",
        "telemetry_status": status,
        "migration_stage": stage,
        "exception_type": "-",
        "reason_code": "-",
        "sqlite_error_code": "-",
        "safe_error_category": "-",
    }
    if error is not None:
        extra.update(_migration_failure_fields(error))
    _logger.log(
        logging.WARNING if status == "FAIL" else logging.INFO,
        "migration_post_snapshot_telemetry",
        extra=extra,
    )
    for handler in _logger.handlers:
        handler.flush()


class _SnapshotTelemetryStage:
    def __init__(self, stage: str) -> None:
        self._stage = stage

    def __enter__(self) -> None:
        _emit_snapshot_telemetry(self._stage, "ENTER")

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        if exc_value is not None and isinstance(exc_value, Exception):
            _emit_snapshot_telemetry(self._stage, "FAIL", exc_value)
        elif exc_value is None:
            _emit_snapshot_telemetry(self._stage, "PASS")
        return False


class _MigrationTelemetryStage:
    def __init__(self, stage: str) -> None:
        self._stage = stage

    def __enter__(self) -> None:
        _emit_migration_telemetry(self._stage, "ENTER")

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        if exc_value is not None and isinstance(exc_value, Exception):
            _emit_migration_telemetry(self._stage, "FAIL", exc_value)
        elif exc_value is None:
            _emit_migration_telemetry(self._stage, "PASS")
        return False


class MigrationRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    identity: str
    database: Path
    maximum_schema_version: int
    core: bool = True
    initializer: Callable[[], None] | None = None
    identity_validator: MigrationIdentityValidator | None = None


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    identity: str
    schema_version: int
    status: str
    core: bool


class MigrationRegistry:
    """Validates every persistent SQLite store before production composition."""

    def __init__(self, *, backup_root: Path, targets: tuple[MigrationTarget, ...]) -> None:
        if not targets or len({target.identity for target in targets}) != len(targets):
            raise ValueError("migration targets are invalid")
        self._backup_root = Path(backup_root)
        self._targets = targets
        self._statuses: tuple[MigrationStatus, ...] = ()

    @staticmethod
    def _version(database: Path) -> int:
        with closing(sqlite3.connect(database)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise MigrationRejected("SQLite integrity check failed")
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _prepare_target_acl(database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        ensure_current_user_modify(database.parent, inherit_to_children=True)
        if database.exists():
            ensure_current_user_modify(database)

    @staticmethod
    def _prepare_backup_root_acl(backup_root: Path) -> None:
        """Initialize migration snapshot storage before creating a run folder."""

        backup_root.mkdir(parents=True, exist_ok=True)
        ensure_current_user_modify(backup_root, inherit_to_children=True)

    @staticmethod
    def _snapshot(source: Path, destination: Path) -> None:
        source_connection: sqlite3.Connection | None = None
        destination_connection: sqlite3.Connection | None = None
        with _SnapshotTelemetryStage("S2_TARGET_SNAPSHOT_PATH_RESOLVE"):
            destination = Path(destination)

        with _SnapshotTelemetryStage("S3_SNAPSHOT_DIRECTORY_READY"):
            destination.parent.mkdir(parents=True, exist_ok=True)
            ensure_current_user_modify(destination.parent, inherit_to_children=True)

        with _SnapshotTelemetryStage("S4_SNAPSHOT_FILE_CREATE"):
            source_connection = sqlite3.connect(source)
            destination_connection = sqlite3.connect(destination)
        try:
            with _SnapshotTelemetryStage("S5_SQLITE_BACKUP_INIT"):
                if source_connection is None or destination_connection is None:
                    raise RuntimeError("snapshot connections unavailable")

            with _SnapshotTelemetryStage("S6_SQLITE_BACKUP_STEP"):
                source_connection.backup(destination_connection)

            with _SnapshotTelemetryStage("S7_SNAPSHOT_METADATA_WRITE"):
                ensure_current_user_modify(destination)

            with _SnapshotTelemetryStage("S8_SNAPSHOT_BEGIN"):
                pass

            with _SnapshotTelemetryStage("S9_SNAPSHOT_COMPLETE"):
                pass
        finally:
            if destination_connection is not None:
                destination_connection.close()
            if source_connection is not None:
                source_connection.close()

    def prepare(self) -> tuple[MigrationStatus, ...]:
        """Backup, initialize, then verify each target; no fallback is possible."""

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        statuses: list[MigrationStatus] = []
        backup_root_prepared = False
        for target in self._targets:
            database = target.database
            try:
                if database.exists():
                    with _SnapshotTelemetryStage("S0_SNAPSHOT_PREPARE_ENTER"):
                        if not backup_root_prepared:
                            self._prepare_backup_root_acl(self._backup_root)
                            backup_root_prepared = True
                        self._prepare_target_acl(database)
                    with _SnapshotTelemetryStage("S1_SOURCE_DATABASE_VALIDATE"):
                        before = self._version(database)
                        if before > target.maximum_schema_version:
                            raise MigrationRejected("database schema is from a future runtime")
                        if target.identity_validator is not None:
                            with closing(sqlite3.connect(database)) as connection:
                                target.identity_validator(connection)
                    self._snapshot(database, self._backup_root / stamp / f"{target.identity}.sqlite3")
                else:
                    if not backup_root_prepared:
                        self._prepare_backup_root_acl(self._backup_root)
                        backup_root_prepared = True
                    self._prepare_target_acl(database)
                if target.initializer is not None:
                    with _MigrationTelemetryStage("M10_MIGRATION_INITIALIZER_ENTER"):
                        target.initializer()
                with _MigrationTelemetryStage("M11_MIGRATION_STATE_LOAD"):
                    self._prepare_target_acl(database)
                    if not database.exists():
                        raise MigrationRejected("store initializer did not create a database")
                    after = self._version(database)
                with _MigrationTelemetryStage("M12_SOURCE_TARGET_VERSION_COMPARE"):
                    version_compatible = after <= target.maximum_schema_version
                with _MigrationTelemetryStage("M13_MIGRATION_PLAN_BUILD"):
                    pass
                _emit_migration_telemetry("M14_MIGRATION_VALIDATION_ENTER", "ENTER")
                try:
                    if not version_compatible:
                        raise MigrationRejected("database migration exceeded supported schema")
                except (OSError, sqlite3.Error, RuntimeError, ValueError, MigrationRejected) as error:
                    _emit_migration_telemetry(
                        "M16_MIGRATION_VALIDATION_FAIL", "FAIL", error
                    )
                    raise
                _emit_migration_telemetry("M15_MIGRATION_VALIDATION_PASS", "PASS")
                _emit_migration_telemetry("M17_STORE_REGISTRATION_ENTER", "ENTER")
                try:
                    status = MigrationStatus(target.identity, after, "READY", target.core)
                except (OSError, sqlite3.Error, RuntimeError, ValueError, MigrationRejected) as error:
                    _emit_migration_telemetry(
                        "M19_STORE_REGISTRATION_FAIL", "FAIL", error
                    )
                    raise
                _emit_migration_telemetry("M18_STORE_REGISTRATION_PASS", "PASS")
                with _MigrationTelemetryStage("M20_MIGRATION_COMMIT_PREPARE"):
                    statuses.append(status)
                with _MigrationTelemetryStage("M21_MIGRATION_COMPLETE"):
                    pass
            except (OSError, sqlite3.Error, RuntimeError, ValueError, MigrationRejected) as error:
                if target.core:
                    raise MigrationRejected(f"core store {target.identity} is unavailable") from error
                statuses.append(MigrationStatus(target.identity, -1, "UNAVAILABLE", target.core))
        self._statuses = tuple(statuses)
        return self._statuses

    def statuses(self) -> tuple[MigrationStatus, ...]:
        return self._statuses


__all__ = (
    "MigrationIdentityValidator",
    "MigrationRegistry",
    "MigrationRejected",
    "MigrationStatus",
    "MigrationTarget",
)
