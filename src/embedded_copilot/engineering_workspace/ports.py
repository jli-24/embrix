from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import WorkspaceObjectReference, WorkspaceResolvedReference


@runtime_checkable
class WorkspaceReferenceSourcePort(Protocol):
    def resolve(
        self, reference: WorkspaceObjectReference
    ) -> WorkspaceResolvedReference | None: ...


@runtime_checkable
class WorkspaceClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = ("WorkspaceClockPort", "WorkspaceReferenceSourcePort")
