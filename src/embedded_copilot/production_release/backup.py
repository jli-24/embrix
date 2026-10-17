"""Controlled application-state backup and restore for the v7.0 state layout."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .credentials import CredentialProviderPort
from .paths import ProductionStateLayout, StatePathRejected
from .release import PRODUCT_VERSION


class RestoreError(RuntimeError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_id: str
    archive: Path
    manifest_fingerprint: str


class BackupService:
    """Backs up only persistent application state, never credentials or projects."""

    def __init__(self, *, layout: ProductionStateLayout, credentials: CredentialProviderPort) -> None:
        self._layout = layout
        self._credentials = credentials

    def _key(self) -> bytes:
        get_or_create = getattr(self._credentials, "get_or_create_backup_integrity_key", None)
        if callable(get_or_create):
            return get_or_create()
        value = self._credentials.get("backup-integrity")
        if value is None:
            raise RestoreError("backup integrity key is unavailable")
        return value

    def _manifest(self, *, backup_id: str, tenant_id: str, files: list[dict[str, str]]) -> dict[str, object]:
        body: dict[str, object] = {
            "backup_id": backup_id,
            "product_version": PRODUCT_VERSION,
            "tenant_id": tenant_id,
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
        }
        body["manifest_fingerprint"] = "sha256:" + hashlib.sha256(_canonical_json(body)).hexdigest()
        body["integrity_hmac"] = hmac.new(self._key(), _canonical_json(body), hashlib.sha256).hexdigest()
        return body

    def create(self, *, tenant_id: str) -> BackupResult:
        if not tenant_id:
            raise ValueError("tenant is required")
        self._layout.create()
        backup_id = f"backup-{uuid.uuid4()}"
        stage = self._layout.resolve_under(self._layout.staging, backup_id)
        database_stage = stage / "database"
        database_stage.mkdir(parents=True, exist_ok=False)
        files: list[dict[str, str]] = []
        try:
            for database in sorted(self._layout.database.glob("*.sqlite3")):
                snapshot = database_stage / database.name
                with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
                with closing(sqlite3.connect(snapshot)) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RestoreError("database snapshot integrity check failed")
                files.append({"path": f"database/{database.name}", "fingerprint": _sha256(snapshot)})
            manifest = self._manifest(backup_id=backup_id, tenant_id=tenant_id, files=files)
            manifest_path = stage / "manifest.json"
            manifest_path.write_bytes(_canonical_json(manifest))
            archive = self._layout.resolve_under(self._layout.backups, f"{backup_id}.zip")
            with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
                output.write(manifest_path, "manifest.json")
                for item in files:
                    output.write(stage / item["path"], item["path"])
            return BackupResult(backup_id, archive, str(manifest["manifest_fingerprint"]))
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _validated_manifest(self, archive: Path, *, tenant_id: str, destination: Path) -> dict[str, object]:
        try:
            with zipfile.ZipFile(archive) as source:
                names = source.namelist()
                if "manifest.json" not in names or len(names) != len(set(names)):
                    raise RestoreError("backup archive manifest is invalid")
                for name in names:
                    candidate = Path(name)
                    if candidate.is_absolute() or ".." in candidate.parts or ":" in name or "\\" in name:
                        raise RestoreError("backup archive path is unsafe")
                    info = source.getinfo(name)
                    if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise RestoreError("backup archive entry is unsafe")
                manifest = json.loads(source.read("manifest.json"))
                signature = manifest.pop("integrity_hmac", None)
                if not isinstance(signature, str) or not hmac.compare_digest(signature, hmac.new(self._key(), _canonical_json(manifest), hashlib.sha256).hexdigest()):
                    raise RestoreError("backup manifest integrity verification failed")
                expected_fingerprint = manifest.get("manifest_fingerprint")
                without_fingerprint = dict(manifest)
                without_fingerprint.pop("manifest_fingerprint", None)
                actual_fingerprint = "sha256:" + hashlib.sha256(_canonical_json(without_fingerprint)).hexdigest()
                if expected_fingerprint != actual_fingerprint or manifest.get("product_version") != PRODUCT_VERSION or manifest.get("tenant_id") != tenant_id:
                    raise RestoreError("backup manifest identity is invalid")
                files = manifest.get("files")
                if not isinstance(files, list):
                    raise RestoreError("backup manifest files are invalid")
                destination.mkdir(parents=True, exist_ok=False)
                for item in files:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not str(item["path"]).startswith("database/"):
                        raise RestoreError("backup file declaration is invalid")
                    output = self._layout.resolve_under(destination, str(item["path"]))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(str(item["path"])) as input_file, output.open("xb") as output_file:
                        shutil.copyfileobj(input_file, output_file)
                    if _sha256(output) != item.get("fingerprint"):
                        raise RestoreError("backup file fingerprint is invalid")
                    with closing(sqlite3.connect(output)) as connection:
                        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise RestoreError("restored database integrity check failed")
                return manifest
        except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile, sqlite3.Error, StatePathRejected) as error:
            raise RestoreError("backup archive validation failed") from error

    def restore(self, *, backup_id: str, tenant_id: str) -> str:
        archive = self._layout.resolve_under(self._layout.backups, f"{backup_id}.zip")
        if not archive.is_file():
            raise RestoreError("backup does not exist")
        restore_id = f"restore-{uuid.uuid4()}"
        stage = self._layout.resolve_under(self._layout.staging, restore_id)
        recovery = self._layout.resolve_under(self._layout.recovery, restore_id)
        try:
            manifest = self._validated_manifest(archive, tenant_id=tenant_id, destination=stage)
            current = self._layout.database
            recovery.mkdir(parents=True, exist_ok=False)
            # Windows keeps directory handles open more aggressively than POSIX.
            # Snapshot and switch individual database files after the caller has
            # quiesced store handles; this preserves an explicit rollback point.
            originals = sorted(current.glob("*.sqlite3"))
            for original in originals:
                self._snapshot_database(original, recovery / original.name)
            replaced: list[Path] = []
            try:
                for staged in sorted((stage / "database").glob("*.sqlite3")):
                    destination = current / staged.name
                    os.replace(staged, destination)
                    replaced.append(destination)
            except OSError:
                for destination in replaced:
                    snapshot = recovery / destination.name
                    if snapshot.exists():
                        os.replace(snapshot, destination)
                raise
            return str(manifest["manifest_fingerprint"])
        except Exception as error:
            if isinstance(error, RestoreError):
                raise
            raise RestoreError("backup restore failed") from error
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _snapshot_database(source: Path, destination: Path) -> None:
        with closing(sqlite3.connect(source)) as source_connection, closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)


__all__ = ("BackupResult", "BackupService", "RestoreError")
