from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .ports import ResolvedWorkspaceTarget


@dataclass(frozen=True, slots=True)
class SystemWorkspaceClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedWorkspaceTargetResolver:
    """Offline acceptance resolver; production uses a Project Catalog adapter."""

    def __init__(self, root: Path, *, tenant_id: str = "tenant-golden", project_id: str = "project-golden", target_id: str = "project-root") -> None:
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("workspace root is invalid")
        self._target = ResolvedWorkspaceTarget(tenant_id, project_id, target_id, root.resolve(strict=True))

    def resolve(self, *, tenant_id: str, project_id: str, target_id: str | None = None) -> ResolvedWorkspaceTarget:
        if tenant_id != self._target.tenant_id or project_id != self._target.project_id:
            raise ValueError("workspace target identity mismatch")
        if target_id is not None and target_id != self._target.target_id:
            raise ValueError("workspace target is unavailable")
        return self._target


class ProjectCatalogWorkspaceTargetResolver:
    """Production resolver bound to the canonical Project Catalog."""

    def __init__(self, project_catalog: object, *, target_id: str = "project-root") -> None:
        if not callable(getattr(project_catalog, "knowledge_snapshot_root", None)):
            raise TypeError("project catalog resolver is invalid")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("target id is invalid")
        self._project_catalog = project_catalog
        self._target_id = target_id

    def resolve(self, *, tenant_id: str, project_id: str, target_id: str | None = None) -> ResolvedWorkspaceTarget:
        if target_id is not None and target_id != self._target_id:
            raise ValueError("workspace target is unavailable")
        root = self._project_catalog.knowledge_snapshot_root(project_id, tenant_id=tenant_id)
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("workspace target root is invalid")
        return ResolvedWorkspaceTarget(tenant_id, project_id, self._target_id, root.resolve(strict=True))


__all__ = ("FixedWorkspaceTargetResolver", "ProjectCatalogWorkspaceTargetResolver", "SystemWorkspaceClock")
