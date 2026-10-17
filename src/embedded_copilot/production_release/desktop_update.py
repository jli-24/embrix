"""Local, explicitly configured desktop application update boundary.

This module never writes the user state layout.  The installer only stages a
verified complete application; a separate helper switches packages after exit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .build_identity import read_build_identity
from .paths import default_development_update_source

if TYPE_CHECKING:
    from .desktop_update_restart import DesktopUpdateRestart
    from .runtime import ProductionReleaseRuntime


class UpdateRejected(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateRejected("MANIFEST_INVALID") from error
    if not isinstance(value, dict):
        raise UpdateRejected("MANIFEST_INVALID")
    return value


def validated_transaction(value: dict) -> dict:
    """Reject malformed or escaping transaction fields before any path use."""
    transaction_id = value.get("transaction_id")
    if (value.get("schema_version") != 1
            or not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id)
            or value.get("stage") != f"update-{transaction_id}"
            or value.get("state") not in {"WAIT_RESTART", "SWITCHING", "BOOTING", "FINALIZING", "BOOT_VERIFIED", "ROLLED_BACK"}
            or not isinstance(value.get("build_id"), str) or not value["build_id"]
            or not isinstance(value.get("old_build_id"), str) or not value["old_build_id"]
            or not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
        raise UpdateRejected("UPDATE_TRANSACTION_INVALID")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version(value: str) -> tuple[int, int, int]:
    # V1 accepts the product's three-component release version only. Build ID
    # decides freshness when the product version is equal.
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise UpdateRejected("VERSION_UNSUPPORTED")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


@contextmanager
def installation_lock(root: Path) -> Iterator[None]:
    """OS-released, cross-process lock for install and switch transactions."""
    directory = root / ".update"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "install.lock").open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise UpdateRejected("UPDATE_BUSY") from error
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _extract_application(archive: Path, target: Path) -> None:
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                name = item.filename
                parts = PurePosixPath(name).parts
                mode = (item.external_attr >> 16) & 0o170000
                if (
                    not parts or parts[0] != "Embrix" or len(parts) < 2
                    or name.startswith("/") or "\\" in name or ":" in name
                    or any(part in {"", ".", ".."} for part in parts)
                    or mode not in {0, 0o100000, 0o040000}
                    or name.casefold() in seen
                ):
                    raise UpdateRejected("PACKAGE_PATH_REJECTED")
                seen.add(name.casefold())
                destination = target.joinpath(*parts[1:])
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    if item.file_size > 1024 * 1024 * 1024:
                        raise UpdateRejected("PACKAGE_ENTRY_TOO_LARGE")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(item) as input_stream, destination.open("xb") as output_stream:
                        while block := input_stream.read(1024 * 1024):
                            output_stream.write(block)
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise UpdateRejected("PACKAGE_INVALID") from error


def _package_identity(application: Path) -> dict:
    backend = read_build_identity(application / "_internal")
    if backend is None:
        raise UpdateRejected("PACKAGE_IDENTITY_MISMATCH")
    frontend = _json(application / "_internal" / "frontend-dist" / "build-identity.json")
    if (backend != frontend or not (application / "Embrix.exe").is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(backend.get("source_fingerprint")))
            or not isinstance(backend.get("build_time"), str) or not re.search(r"(?:Z|[+-]\d\d:\d\d)$", backend["build_time"])):
        raise UpdateRejected("PACKAGE_IDENTITY_MISMATCH")
    return backend


def slot_identity(root: Path, slot: str) -> tuple[dict | None, str]:
    """Read identity and its separately recorded verification status."""
    if slot not in {"Embrix-current", "Embrix-previous"}:
        raise ValueError("invalid application slot")
    directory = root / slot
    if directory.is_symlink() or (directory / "Embrix").is_symlink():
        return None, "UNVERIFIED"
    try:
        identity = _package_identity(directory / "Embrix")
    except UpdateRejected:
        return None, "UNVERIFIED"
    try:
        marker = _json(directory / "verification.json")
    except UpdateRejected:
        return identity, "UNVERIFIED"
    level = marker.get("verification")
    if marker.get("build_id") != identity.get("build_id") or level not in {"NATIVE_ACCEPTED", "BOOT_VERIFIED"}:
        return identity, "UNVERIFIED"
    return identity, level


def active_execution_reason(database: Path) -> str | None:
    """Read authoritative execution statuses across all projects before exit."""
    if not database.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            status_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v66_objects'"
            ).fetchone()
            if status_table is None:
                has_user_schema = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                ).fetchone() is not None
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if not has_user_schema and version == 0:
                    return None
                raise UpdateRejected("TASK_STATUS_UNAVAILABLE")
            rows = connection.execute("SELECT body FROM v66_objects WHERE kind='status'").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise UpdateRejected("TASK_STATUS_UNAVAILABLE") from error
    blocking = {"QUEUED", "RUNNING", "CANCEL_REQUESTED", "CANCEL_PENDING", "RECOVERY_REQUIRED"}
    try:
        if any(json.loads(row[0]).get("status") in blocking for row in rows):
            return "ACTIVE_ENGINEERING_OPERATION"
    except (TypeError, ValueError, AttributeError) as error:
        raise UpdateRejected("TASK_STATUS_UNAVAILABLE") from error
    return None


class DesktopUpdateService:
    def __init__(self, *, install_root: Path, source: Path | None, schema_versions: dict[str, int],
                 execution_database: Path | None = None) -> None:
        self.root = install_root.resolve()
        self.source = source.resolve() if source is not None else None
        self.schema_versions = dict(schema_versions)
        self.execution_database = execution_database
        self.transaction = self.root / ".update" / "transaction.json"
        self._last_state = "UNCHECKED"
        self._last_error: str | None = None
        self._candidate: dict | None = None
        self._restart_coordinator: DesktopUpdateRestart | None = None

    @classmethod
    def for_runtime(cls, runtime: ProductionReleaseRuntime) -> DesktopUpdateService | None:
        if not getattr(sys, "frozen", False):
            return None
        executable = Path(sys.executable).resolve()
        if executable.name.casefold() != "embrix.exe" or executable.parent.name != "Embrix":
            return None
        package_slot = executable.parent.parent
        if package_slot.name != "Embrix-current":
            return None
        source_value = os.environ.get("EMBRIX_LOCAL_UPDATE_SOURCE")
        migrations = runtime.migrations
        statuses = migrations.statuses()
        schemas = {item.identity: item.schema_version for item in statuses if item.status == "READY"}
        settings = runtime.settings
        database = Path(settings.desktop_state_database).with_name("desktop-v66.sqlite3")
        default_source = default_development_update_source(package_slot.parent)
        source = Path(source_value) if source_value else (default_source if default_source.is_dir() else None)
        return cls(install_root=package_slot.parent, source=source,
                   schema_versions=schemas, execution_database=database)

    def _installed(self) -> dict | None:
        return slot_identity(self.root, "Embrix-current")[0]

    def _transaction(self) -> dict | None:
        if not self.transaction.exists():
            return None
        try:
            return validated_transaction(_json(self.transaction))
        except UpdateRejected as error:
            raise UpdateRejected("UPDATE_TRANSACTION_INVALID") from error

    def status(self) -> dict:
        installed, verification = slot_identity(self.root, "Embrix-current")
        previous, previous_verification = slot_identity(self.root, "Embrix-previous")
        transaction = self._transaction()
        transaction_state = transaction.get("state") if transaction else None
        checked_after_terminal = (
            transaction_state in {"BOOT_VERIFIED", "ROLLED_BACK"}
            and self._last_state != "UNCHECKED"
        )
        state = self._last_state if checked_after_terminal else transaction_state or self._last_state
        if installed is None:
            state = "UNAVAILABLE"
        return {
            "state": state,
            "installed_identity": installed,
            "installed_verification": verification,
            "previous_identity": previous,
            "previous_verification": previous_verification,
            "channel": installed.get("channel") if installed else None,
            "candidate": self._candidate,
            "notes": self._candidate.get("notes", []) if self._candidate else [],
            "error_code": self._last_error if checked_after_terminal or transaction is None else transaction.get("error_code"),
        }

    def _read_candidate(self) -> tuple[dict, Path]:
        installed = self._installed()
        if self.source is None or installed is None:
            raise UpdateRejected("UPDATE_SOURCE_UNAVAILABLE")
        manifest = _json(self.source / "manifest.json")
        try:
            package = manifest["package"]
            name = package["name"]
            if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
                raise ValueError
            archive = self.source / "packages" / name
            if manifest["schema_version"] != 1 or manifest["product_name"] != installed["product_name"]:
                raise ValueError
            if manifest["channel"] != installed["channel"] or manifest["platform"] != installed["platform"] or manifest["architecture"] != installed["architecture"]:
                raise UpdateRejected("TARGET_MISMATCH")
            if manifest["build_id"] == installed["build_id"]:
                raise UpdateRejected("NO_UPDATE")
            if _version(manifest["product_version"]) < _version(installed["product_version"]):
                raise UpdateRejected("DOWNGRADE_REJECTED")
            compatibility = manifest["compatibility"]
            if _version(installed["product_version"]) < _version(compatibility["app_min_version"]):
                raise UpdateRejected("INCOMPATIBLE_APP")
            if compatibility["config_schema_version"] != 1:
                raise UpdateRejected("INCOMPATIBLE_CONFIG")
            schemas = compatibility["data_schema"]
            if not isinstance(schemas, dict) or set(schemas) != set(self.schema_versions):
                raise UpdateRejected("INCOMPATIBLE_DATA")
            for identity, version in self.schema_versions.items():
                limits = schemas[identity]
                if not isinstance(limits, dict) or not (
                    type(limits.get("min_version")) is int and type(limits.get("max_version")) is int
                    and limits["min_version"] == version == limits["max_version"]
                ):
                    raise UpdateRejected("INCOMPATIBLE_DATA")
            if not isinstance(manifest.get("notes"), list) or not all(isinstance(x, str) for x in manifest["notes"]):
                raise ValueError
            if not isinstance(package["size"], int) or package["size"] <= 0 or not re.fullmatch(r"[0-9a-f]{64}", package["sha256"]):
                raise ValueError
        except UpdateRejected:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise UpdateRejected("MANIFEST_INVALID") from error
        return manifest, archive

    def check(self) -> dict:
        self._candidate = None
        self._last_error = None
        try:
            manifest, archive = self._read_candidate()
            if not archive.is_file():
                raise UpdateRejected("PACKAGE_MISSING")
            package = manifest["package"]
            self._candidate = {
                "product_version": manifest["product_version"], "build_id": manifest["build_id"],
                "sha256": package["sha256"], "size": package["size"], "notes": manifest["notes"],
            }
            self._last_state = "CANDIDATE"
        except UpdateRejected as error:
            self._last_state = "NO_UPDATE" if error.code == "NO_UPDATE" else "CHECK_FAILED"
            self._last_error = None if error.code == "NO_UPDATE" else error.code
        return self.status()

    def install(self, *, build_id: str, sha256: str) -> dict:
        with installation_lock(self.root):
            if slot_identity(self.root, "Embrix-current")[1] == "UNVERIFIED":
                raise UpdateRejected("CURRENT_NOT_VERIFIED")
            if self._transaction() is not None and self._transaction().get("state") == "WAIT_RESTART":
                raise UpdateRejected("UPDATE_ALREADY_STAGED")
            manifest, archive = self._read_candidate()
            package = manifest["package"]
            if manifest["build_id"] != build_id or package["sha256"] != sha256:
                raise UpdateRejected("CANDIDATE_CHANGED")
            if not archive.is_file():
                raise UpdateRejected("PACKAGE_MISSING")
            if archive.stat().st_size != package["size"] or _sha256(archive) != sha256:
                raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
            transaction_id = uuid.uuid4().hex
            stage = self.root / "_staging" / f"update-{transaction_id}"
            stage.mkdir(parents=True, exist_ok=False)
            try:
                _extract_application(archive, stage / "Embrix")
                identity = _package_identity(stage / "Embrix")
                if any(identity.get(key) != manifest.get(key) for key in
                       ("product_name", "product_version", "build_id", "channel", "platform", "architecture")):
                    raise UpdateRejected("PACKAGE_IDENTITY_MISMATCH")
                if not (stage / "Embrix" / "EmbrixUpdater.exe").is_file():
                    raise UpdateRejected("UPDATER_MISSING")
            except Exception:
                # Keep failed staging for diagnosis; current and previous remain untouched.
                self._last_state = "VALIDATION_FAILED"
                raise
            _atomic_json(self.transaction, {
                "schema_version": 1, "transaction_id": transaction_id, "state": "WAIT_RESTART",
                "build_id": build_id, "sha256": sha256, "stage": stage.name,
                "old_build_id": self._installed()["build_id"], "error_code": None,
            })
            self._candidate = {"product_version": manifest["product_version"], "build_id": build_id,
                               "sha256": sha256, "size": package["size"], "notes": manifest["notes"]}
            return self.status()

    def restart(self, schedule: Callable[[Callable[[], None]], object]) -> dict:
        self.restart_allowed()
        from .desktop_update_restart import production_restart

        if self._restart_coordinator is None:
            self._restart_coordinator = production_restart(self)
        return self._restart_coordinator.restart(schedule)

    def restart_allowed(self) -> None:
        transaction = self._transaction()
        if transaction is None or transaction.get("state") != "WAIT_RESTART":
            raise UpdateRejected("UPDATE_NOT_STAGED")
        if self.execution_database is None:
            raise UpdateRejected("TASK_STATUS_UNAVAILABLE")
        reason = active_execution_reason(self.execution_database)
        if reason is not None:
            raise UpdateRejected(reason)


__all__ = ("DesktopUpdateService", "UpdateRejected", "active_execution_reason", "installation_lock")
