from __future__ import annotations

import hashlib
import ntpath
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
    ProjectLocationInvalid,
    ProjectLocationPermissionDenied,
    ProjectLocationUnsafe,
)
from .models import ProjectLocationSource, ProjectLocationStatus, ProjectLocationView

_DEFAULT_LOCATION_ID = "default-project-root"
_PRODUCTION_SOURCES = (
    ProjectLocationSource.PRODUCTION_CONFIG.value,
    ProjectLocationSource.USER_NATIVE_PICKER.value,
)
_KNOWN_FIXTURE_ROOTS = frozenset(
    {
        r"c:\tmp\embedded-copilot-native-picker-root",
    }
)
_KNOWN_FIXTURE_ANCHORS = frozenset(
    {
        r"c:\acceptance_projects",
        r"c:\acceptance_state",
    }
)
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def canonical_root_identity(value: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def known_fixture_root(value: Path) -> bool:
    identity = canonical_root_identity(value)
    return identity in _KNOWN_FIXTURE_ROOTS or any(
        identity == anchor or identity.startswith(anchor + "\\")
        for anchor in _KNOWN_FIXTURE_ANCHORS
    )


class ProjectLocationService:
    """Manage explicitly registered, tenant-scoped project roots."""

    def __init__(
        self,
        *,
        database: Path,
        default_root: Path | None,
        metadata_directory: Path,
        default_label: str = "Embedded Projects",
        allow_list: tuple[Path, ...] = (),
        purge_invalid_persisted: bool = False,
        production_mode: bool = False,
    ) -> None:
        self._database = Path(database).resolve(strict=False)
        self._metadata_directory = Path(metadata_directory).resolve(strict=False)
        configured = tuple(Path(item) for item in allow_list)
        self._default_root = None if default_root is None else self._normalize_allow_root(Path(default_root))
        normalized_configured = tuple(self._normalize_allow_root(item) for item in configured)
        self._configured_roots = tuple(dict.fromkeys(normalized_configured))
        self._allow_list = tuple(dict.fromkeys(item for item in ((self._default_root,) if self._default_root is not None else ()) + self._configured_roots))
        self._purge_invalid_persisted = purge_invalid_persisted
        self._production_mode = production_mode
        if self._default_root is not None:
            self._validate_root(self._default_root, allow_list=self._allow_list, reject_protected=False)
        self._default_label = default_label.strip() or "Embedded Projects"
        self._initialize()

    @staticmethod
    def _normalize_allow_root(value: Path) -> Path:
        raw = Path(value).expanduser()
        if raw.is_symlink():
            raise ProjectCatalogUnavailable("project location allow-list contains a symbolic link")
        try:
            return raw.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise ProjectCatalogUnavailable("project location allow-list is unavailable") from error

    def _validate_root(
        self,
        value: Path,
        *,
        allow_list: tuple[Path, ...],
        reject_protected: bool = True,
        require_allow_list: bool = True,
        require_write: bool = True,
    ) -> Path:
        raw = Path(value).expanduser()
        raw_text = str(raw).replace("/", "\\")
        _drive, tail = ntpath.splitdrive(raw_text)
        if raw_text.startswith("\\\\") or tail.startswith("\\\\") or raw_text.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
            raise ProjectLocationUnsafe("network and device paths are not allowed")
        if ":" in tail:
            raise ProjectLocationUnsafe("alternate data streams are not allowed")
        for component in tail.replace("/", "\\").split("\\"):
            if component and component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
                raise ProjectLocationUnsafe("reserved Windows path is not allowed")
        if self._contains_reparse_point(raw):
            raise ProjectLocationUnsafe("project location contains a reparse point")
        if raw.is_symlink():
            raise ProjectLocationUnsafe("project location cannot be a symbolic link")
        try:
            candidate = raw.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise ProjectLocationInvalid("project location path is unavailable") from error
        if not candidate.is_dir() or candidate.is_symlink():
            raise ProjectLocationInvalid("project location must be a real directory")
        try:
            if candidate != raw.absolute():
                raise ProjectLocationUnsafe("project location contains a symbolic link")
        except OSError as error:
            raise ProjectLocationInvalid("project location path is unavailable") from error
        try:
            if candidate.anchor == candidate:
                raise ProjectLocationUnsafe("filesystem root is not an allowed project location")
            if reject_protected and candidate.is_relative_to(Path.home().resolve(strict=False)):
                raise ProjectLocationUnsafe("user directory is not an allowed project location")
            windows_root = Path(os.environ.get("WINDIR", r"C:\Windows")).resolve(strict=False)
            if reject_protected and (candidate == windows_root or candidate.is_relative_to(windows_root)):
                raise ProjectLocationUnsafe("system directory is not an allowed project location")
            protected = (self._database.parent, self._metadata_directory)
            if reject_protected and any(candidate == item or candidate.is_relative_to(item) for item in protected):
                raise ProjectLocationUnsafe("database directory is not an allowed project location")
            if require_allow_list and not any(candidate == Path(item).resolve(strict=False) for item in allow_list):
                raise ProjectLocationUnsafe("project location is outside the configured allow-list")
        except ValueError as error:
            raise ProjectLocationInvalid("project location path is invalid") from error
        access_mode = os.R_OK | os.X_OK | (os.W_OK if require_write else 0)
        if not os.access(candidate, access_mode):
            raise ProjectLocationPermissionDenied("project location permission is insufficient")
        return candidate

    @staticmethod
    def _contains_reparse_point(value: Path) -> bool:
        """Reject symlink/junction components before canonical identity is trusted."""

        try:
            current = Path(value.absolute().anchor or Path.cwd().anchor)
            for component in value.absolute().parts[1:]:
                current = current / component
                if current.is_symlink():
                    return True
                attributes = int(getattr(current.stat(follow_symlinks=False), "st_file_attributes", 0))
                if attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
                    return True
        except (OSError, RuntimeError, ValueError):
            return True
        return False

    @staticmethod
    def _root_fingerprint(root: Path) -> str:
        stat = root.stat(follow_symlinks=False)
        material = f"{str(root).casefold()}|{stat.st_dev}|{stat.st_ino}".encode("utf-8")
        return "sha256:" + hashlib.sha256(material).hexdigest()

    @staticmethod
    def _location_id(root: Path) -> str:
        digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
        return f"location-{digest[:24]}"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_locations (
                    location_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    display_label TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    root_fingerprint TEXT NOT NULL,
                    canonical_root_identity TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'LEGACY_UNCLASSIFIED',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    registered_by TEXT NOT NULL DEFAULT '',
                    audit_reference TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, root_fingerprint)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS project_location_audit (
                    audit_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    requester TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(project_locations)")}
            additions = {
                "canonical_root_identity": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT 'LEGACY_UNCLASSIFIED'",
                "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
                "registered_by": "TEXT NOT NULL DEFAULT ''",
                "audit_reference": "TEXT NOT NULL DEFAULT ''",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE project_locations ADD COLUMN {name} {definition}")

            now = datetime.now(UTC).isoformat()
            if self._default_root is not None:
                default_fingerprint = self._root_fingerprint(self._default_root)
                default_source = (
                    ProjectLocationSource.TEST_FIXTURE.value
                    if known_fixture_root(self._default_root)
                    else ProjectLocationSource.PRODUCTION_CONFIG.value
                )
                default_status = (
                    ProjectLocationStatus.REVOKED.value
                    if default_source == ProjectLocationSource.TEST_FIXTURE.value and self._production_mode
                    else ProjectLocationStatus.ACTIVE.value
                )
                connection.execute(
                    """INSERT OR IGNORE INTO project_locations
                       (location_id, tenant_id, display_label, root_path, root_fingerprint,
                        canonical_root_identity, source, status, registered_by, audit_reference, created_at)
                       VALUES (?, '*', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _DEFAULT_LOCATION_ID,
                        self._default_label,
                        str(self._default_root),
                        default_fingerprint,
                        canonical_root_identity(self._default_root),
                        default_source,
                        default_status,
                        "production-composition",
                        "",
                        now,
                    ),
                )
                default_conflict = connection.execute(
                    """SELECT location_id, source
                       FROM project_locations
                       WHERE tenant_id='*' AND root_fingerprint=? AND location_id<>?""",
                    (default_fingerprint, _DEFAULT_LOCATION_ID),
                ).fetchone()
                if default_conflict is None:
                    connection.execute(
                        """UPDATE project_locations
                           SET root_path=?, root_fingerprint=?, canonical_root_identity=?, source=?, status=?, display_label=?
                           WHERE location_id=? AND tenant_id='*'""",
                        (
                            str(self._default_root),
                            default_fingerprint,
                            canonical_root_identity(self._default_root),
                            default_source,
                            default_status,
                            self._default_label,
                            _DEFAULT_LOCATION_ID,
                        ),
                    )
                elif (
                    default_source == ProjectLocationSource.TEST_FIXTURE.value
                    or default_conflict[1] == ProjectLocationSource.TEST_FIXTURE.value
                ):
                    collision_status = (
                        ProjectLocationStatus.REVOKED.value
                        if self._production_mode
                        else default_status
                    )
                    connection.execute(
                        "UPDATE project_locations SET source=?, status=? WHERE location_id=? AND tenant_id='*'",
                        (ProjectLocationSource.TEST_FIXTURE.value, collision_status, _DEFAULT_LOCATION_ID),
                    )
                    connection.execute(
                        "UPDATE project_locations SET source=?, status=? WHERE location_id=? AND tenant_id='*'",
                        (ProjectLocationSource.TEST_FIXTURE.value, collision_status, default_conflict[0]),
                    )

            # Static administrator roots are exact trusted roots.  They are
            # never treated as a parent allow-list for future registrations.
            for configured_root in self._configured_roots:
                if self._default_root is not None and configured_root == self._default_root:
                    continue
                if known_fixture_root(configured_root) and self._production_mode:
                    continue
                fingerprint = self._root_fingerprint(configured_root)
                location_id = self._location_id(configured_root)
                connection.execute(
                    """INSERT OR IGNORE INTO project_locations
                       (location_id, tenant_id, display_label, root_path, root_fingerprint,
                        canonical_root_identity, source, status, registered_by, audit_reference, created_at)
                       VALUES (?, '*', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        location_id,
                        configured_root.name or self._default_label,
                        str(configured_root),
                        fingerprint,
                        canonical_root_identity(configured_root),
                        ProjectLocationSource.PRODUCTION_CONFIG.value,
                        ProjectLocationStatus.ACTIVE.value,
                        "production-composition",
                        "",
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE project_locations
                       SET root_path=?, canonical_root_identity=?, source=?, status=?, registered_by=?
                       WHERE tenant_id='*' AND root_fingerprint=?""",
                    (
                        str(configured_root),
                        canonical_root_identity(configured_root),
                        ProjectLocationSource.PRODUCTION_CONFIG.value,
                        ProjectLocationStatus.ACTIVE.value,
                        "production-composition",
                        fingerprint,
                    ),
                )

            # Preserve legacy rows.  A user audit is sufficient evidence of
            # explicit historical consent; ambiguous rows remain unavailable
            # in production and are never deleted by name.
            legacy_rows = connection.execute(
                "SELECT location_id, tenant_id, root_path, source FROM project_locations"
            ).fetchall()
            for location_id, tenant_id, root_path, source in legacy_rows:
                if source != ProjectLocationSource.LEGACY_UNCLASSIFIED.value:
                    continue
                if self._production_mode and known_fixture_root(Path(root_path)):
                    connection.execute(
                        "UPDATE project_locations SET source=?, status=?, canonical_root_identity=? WHERE location_id=?",
                        (ProjectLocationSource.TEST_FIXTURE.value, ProjectLocationStatus.REVOKED.value, canonical_root_identity(Path(root_path)), location_id),
                    )
                    continue
                user_audit = connection.execute(
                    "SELECT 1 FROM project_location_audit WHERE location_id=? AND operation='REGISTER' AND requester <> 'production-composition' LIMIT 1",
                    (location_id,),
                ).fetchone()
                if user_audit is not None:
                    migrated_source = ProjectLocationSource.USER_NATIVE_PICKER.value
                    migrated_status = ProjectLocationStatus.ACTIVE.value
                else:
                    migrated_source = ProjectLocationSource.LEGACY_UNCLASSIFIED.value
                    migrated_status = ProjectLocationStatus.UNAVAILABLE.value if self._production_mode else ProjectLocationStatus.ACTIVE.value
                connection.execute(
                    "UPDATE project_locations SET source=?, status=?, canonical_root_identity=? WHERE location_id=?",
                    (migrated_source, migrated_status, canonical_root_identity(Path(root_path)), location_id),
                )
            if self._purge_invalid_persisted:
                rows = connection.execute(
                    "SELECT location_id, tenant_id, root_path, root_fingerprint, source FROM project_locations"
                ).fetchall()
                for location_id, tenant_id, root_path, stored_fingerprint, source in rows:
                    if location_id == _DEFAULT_LOCATION_ID and tenant_id == "*":
                        if self._default_root is not None and (root_path != str(self._default_root) or stored_fingerprint != default_fingerprint):
                            default_conflict = connection.execute(
                                """SELECT location_id, source
                                   FROM project_locations
                                   WHERE tenant_id='*' AND root_fingerprint=? AND location_id<>?""",
                                (default_fingerprint, _DEFAULT_LOCATION_ID),
                            ).fetchone()
                            if default_conflict is None:
                                connection.execute(
                                    "UPDATE project_locations SET root_path=?, root_fingerprint=?, canonical_root_identity=?, source=?, status=?, display_label=? WHERE location_id=?",
                                    (str(self._default_root), default_fingerprint, canonical_root_identity(self._default_root), ProjectLocationSource.PRODUCTION_CONFIG.value, ProjectLocationStatus.ACTIVE.value, self._default_label, _DEFAULT_LOCATION_ID),
                                )
                                connection.execute(
                                    "INSERT INTO project_location_audit(audit_id, tenant_id, requester, location_id, operation, status, created_at) VALUES(?,?,?,?,?,?,?)",
                                    (f"audit-{uuid4()}", "*", "production-composition", _DEFAULT_LOCATION_ID, "RECONFIGURE_DEFAULT", "AVAILABLE", datetime.now(UTC).isoformat()),
                                )
                            elif (
                                known_fixture_root(self._default_root)
                                or default_conflict[1] == ProjectLocationSource.TEST_FIXTURE.value
                            ):
                                collision_status = (
                                    ProjectLocationStatus.REVOKED.value
                                    if self._production_mode
                                    else default_status
                                )
                                connection.execute(
                                    "UPDATE project_locations SET source=?, status=? WHERE location_id=? AND tenant_id='*'",
                                    (ProjectLocationSource.TEST_FIXTURE.value, collision_status, _DEFAULT_LOCATION_ID),
                                )
                                connection.execute(
                                    "UPDATE project_locations SET source=?, status=? WHERE location_id=? AND tenant_id='*'",
                                    (ProjectLocationSource.TEST_FIXTURE.value, collision_status, default_conflict[0]),
                                )
                        continue
                    try:
                        self._validate_root(
                            Path(root_path),
                            allow_list=self._allow_list,
                            reject_protected=tenant_id != "*",
                            require_allow_list=tenant_id == "*" and source == ProjectLocationSource.PRODUCTION_CONFIG.value,
                        )
                    except ProjectCatalogRejected:
                        connection.execute(
                            "UPDATE project_locations SET status=? WHERE location_id=?",
                            (ProjectLocationStatus.UNAVAILABLE.value, location_id),
                        )
                        connection.execute(
                            "INSERT INTO project_location_audit(audit_id, tenant_id, requester, location_id, operation, status, created_at) VALUES(?,?,?,?,?,?,?)",
                            (f"audit-{uuid4()}", tenant_id, "production-composition", location_id, "PURGE_INVALID", "REJECTED", datetime.now(UTC).isoformat()),
                        )

    def _view(self, row: tuple[object, ...]) -> ProjectLocationView:
        location_id, _tenant_id, label, root, _root_fingerprint, _created = row[:6]
        return ProjectLocationView(
            location_id=location_id,
            display_label=label,
            availability="AVAILABLE",
            path_leaf=Path(root).name or label,
        )

    def _production_row_allowed(self, row: tuple[object, ...]) -> bool:
        source = str(row[7]) if len(row) > 7 else ProjectLocationSource.LEGACY_UNCLASSIFIED.value
        status = str(row[8]) if len(row) > 8 else ProjectLocationStatus.ACTIVE.value
        return status == ProjectLocationStatus.ACTIVE.value and source in _PRODUCTION_SOURCES

    def _row_for_location(self, location_id: str, *, tenant_id: str) -> tuple[object, ...] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT location_id, tenant_id, display_label, root_path, root_fingerprint, created_at,
                          canonical_root_identity, source, status, registered_by, audit_reference
                   FROM project_locations WHERE location_id=? AND tenant_id IN ('*', ?)""",
                (location_id, tenant_id),
            ).fetchone()
        if row is None:
            return None
        if self._production_mode and not self._production_row_allowed(row):
            return None
        return row

    def list(self, *, tenant_id: str, requester: str) -> tuple[ProjectLocationView, ...]:
        del requester
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT location_id, tenant_id, display_label, root_path, root_fingerprint, created_at,
                          canonical_root_identity, source, status, registered_by, audit_reference
                   FROM project_locations WHERE tenant_id IN ('*', ?) ORDER BY display_label, location_id""",
                (tenant_id,),
            ).fetchall()
        if self._production_mode:
            rows = tuple(row for row in rows if self._production_row_allowed(row))
        return tuple(self._view(row) for row in rows)

    def view(self, location_id: str, *, tenant_id: str) -> ProjectLocationView:
        row = self._row_for_location(location_id, tenant_id=tenant_id)
        if row is None:
            raise ProjectCatalogRejected("project location was not found")
        return self._view(row)

    def resolve(self, location_id: str, *, tenant_id: str) -> Path:
        row = self._row_for_location(location_id, tenant_id=tenant_id)
        if row is None:
            raise ProjectCatalogRejected("project location was not found")
        # The configured default root is an existing deployment boundary.  It
        # remains valid even when legacy/local development places it under the
        # user profile.  Newly registered locations use the stricter protected
        # directory checks in register().
        root = self._validate_root(
            Path(row[3]),
            allow_list=self._allow_list,
            reject_protected=row[1] != "*",
            require_allow_list=row[1] == "*" and row[7] == ProjectLocationSource.PRODUCTION_CONFIG.value,
            require_write=False,
        )
        if self._root_fingerprint(root) != row[4]:
            raise ProjectCatalogRejected("project location fingerprint mismatch")
        return root

    def register(self, *, path: str, display_label: str | None, tenant_id: str, requester: str) -> ProjectLocationView:
        root = self._validate_root(Path(path), allow_list=(), require_allow_list=False, require_write=False)
        if known_fixture_root(root):
            raise ProjectLocationUnsafe("project location is a known fixture root")
        fingerprint = self._root_fingerprint(root)
        location_id = self._location_id(root)
        stored_location_id = location_id if tenant_id == "*" else f"{location_id}-{uuid4().hex[:8]}"
        label = (display_label or root.name).strip() or root.name
        if len(label) > 128:
            raise ProjectCatalogRejected("project location label is invalid")
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT location_id, tenant_id, display_label, root_path, root_fingerprint, created_at,
                          canonical_root_identity, source, status, registered_by, audit_reference
                   FROM project_locations WHERE tenant_id=? AND root_fingerprint=?""",
                (tenant_id, fingerprint),
            ).fetchone()
            if existing is not None:
                if existing[8] == ProjectLocationStatus.REVOKED.value:
                    audit_id = f"audit-{uuid4()}"
                    connection.execute(
                        "UPDATE project_locations SET source=?, status=?, registered_by=?, audit_reference=? WHERE location_id=?",
                        (ProjectLocationSource.USER_NATIVE_PICKER.value, ProjectLocationStatus.ACTIVE.value, requester, audit_id, existing[0]),
                    )
                    connection.execute(
                        "INSERT INTO project_location_audit(audit_id, tenant_id, requester, location_id, operation, status, created_at) VALUES(?,?,?,?,?,?,?)",
                        (audit_id, tenant_id, requester, existing[0], "REGISTER_REACTIVATE", "ACTIVE", datetime.now(UTC).isoformat()),
                    )
                    return self._view((*existing[:6], canonical_root_identity(root), ProjectLocationSource.USER_NATIVE_PICKER.value, ProjectLocationStatus.ACTIVE.value, requester, audit_id))
                return self._view(existing)
            audit_id = f"audit-{uuid4()}"
            try:
                connection.execute(
                    """INSERT INTO project_locations(
                        location_id, tenant_id, display_label, root_path, root_fingerprint,
                        canonical_root_identity, source, status, registered_by, audit_reference, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        stored_location_id,
                        tenant_id,
                        label,
                        str(root),
                        fingerprint,
                        canonical_root_identity(root),
                        ProjectLocationSource.USER_NATIVE_PICKER.value,
                        ProjectLocationStatus.ACTIVE.value,
                        requester,
                        audit_id,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectCatalogConflict("project location is already registered") from error
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO project_location_audit(audit_id, tenant_id, requester, location_id, operation, status, created_at) VALUES(?,?,?,?,?,?,?)",
                (audit_id, tenant_id, requester, stored_location_id, "REGISTER", ProjectLocationStatus.ACTIVE.value, datetime.now(UTC).isoformat()),
            )
        return next(item for item in self.list(tenant_id=tenant_id, requester=requester) if item.location_id == stored_location_id)

    def revoke(self, location_id: str, *, tenant_id: str, requester: str) -> None:
        """Revoke trust without deleting the user directory from disk."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT location_id FROM project_locations WHERE location_id=? AND tenant_id=?",
                (location_id, tenant_id),
            ).fetchone()
            if row is None:
                raise ProjectCatalogRejected("project location was not found")
            audit_id = f"audit-{uuid4()}"
            connection.execute(
                "UPDATE project_locations SET status=?, registered_by=?, audit_reference=? WHERE location_id=?",
                (ProjectLocationStatus.REVOKED.value, requester, audit_id, location_id),
            )
            connection.execute(
                "INSERT INTO project_location_audit(audit_id, tenant_id, requester, location_id, operation, status, created_at) VALUES(?,?,?,?,?,?,?)",
                (audit_id, tenant_id, requester, location_id, "REVOKE", ProjectLocationStatus.REVOKED.value, datetime.now(UTC).isoformat()),
            )

    def audit_status(self, location_id: str, *, tenant_id: str) -> dict[str, str]:
        root = self.resolve(location_id, tenant_id=tenant_id)
        return {"location_id": location_id, "status": "AVAILABLE", "root_fingerprint": self._root_fingerprint(root)}


__all__ = ("ProjectLocationService",)
