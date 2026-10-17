from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ResolvedWorkspaceTarget:
    tenant_id: str
    project_id: str
    target_id: str
    root: Path


@runtime_checkable
class WorkspaceTargetResolverPort(Protocol):
    def resolve(self, *, tenant_id: str, project_id: str, target_id: str | None = None) -> ResolvedWorkspaceTarget: ...


@runtime_checkable
class WorkspaceClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = ("ResolvedWorkspaceTarget", "WorkspaceClockPort", "WorkspaceTargetResolverPort")
