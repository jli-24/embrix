"""Windows production-state layout and filesystem boundary checks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class StatePathRejected(ValueError):
    """A requested state path escaped the canonical production root."""


def versioned_database_path(database: str | Path, version: int) -> Path:
    """Return the version-specific SQLite identity derived from a base DB."""

    if version < 1:
        raise ValueError("database version must be positive")
    base = Path(database)
    return base.with_name(f"{base.stem}-v{version}{base.suffix}")


def default_local_app_data() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local"


def default_install_root() -> Path:
    """Per-user program root shared by the installer and desktop updater."""
    return default_local_app_data() / "Programs" / "Embedded Copilot"


def default_development_update_source(install_root: Path | None = None) -> Path:
    return (install_root if install_root is not None else default_install_root()) / "update-source" / "development"


@dataclass(frozen=True, slots=True)
class ProductionStateLayout:
    """The only mutable runtime layout used by a packaged v7.0 application."""

    root: Path

    @classmethod
    def for_current_user(cls, root: Path | None = None) -> ProductionStateLayout:
        return cls((root or default_local_app_data() / "Embedded Copilot").resolve())

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def production_config(self) -> Path:
        return self.config / "production.json"

    @property
    def database(self) -> Path:
        return self.current / "database"

    @property
    def knowledge(self) -> Path:
        return self.current / "knowledge"

    @property
    def chroma(self) -> Path:
        return self.current / "chroma"

    @property
    def artifacts(self) -> Path:
        return self.current / "artifacts"

    @property
    def attachments(self) -> Path:
        return self.current / "attachments"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def recovery(self) -> Path:
        return self.root / "recovery"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def audit(self) -> Path:
        return self.root / "audit"

    @property
    def diagnostics(self) -> Path:
        return self.root / "diagnostics"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def create(self) -> None:
        for directory in (
            self.database,
            self.knowledge,
            self.chroma,
            self.artifacts,
            self.attachments,
            self.staging,
            self.recovery,
            self.logs,
            self.audit,
            self.diagnostics,
            self.backups,
            self.config,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_under(self, directory: Path, relative: str | Path) -> Path:
        """Resolve a relative state artifact without following an escape path."""

        item = Path(relative)
        if item.is_absolute() or ".." in item.parts or ":" in str(item):
            raise StatePathRejected("state path is not relative")
        root = directory.resolve()
        candidate = (directory / item).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise StatePathRejected("state path escaped its root") from error
        return candidate


__all__ = (
    "ProductionStateLayout",
    "StatePathRejected",
    "default_development_update_source",
    "default_install_root",
    "default_local_app_data",
    "versioned_database_path",
)
