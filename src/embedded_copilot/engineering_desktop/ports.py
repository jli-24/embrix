from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRunResult,
)
from embedded_copilot.engineering_workspace import EngineeringWorkspaceSnapshot

from .models import (
    DesktopAuditEvent,
    DesktopModelSettingsView,
    DesktopRuntimeComponentView,
    DesktopSession,
)


@runtime_checkable
class DesktopSessionPort(Protocol):
    def save(self, session: DesktopSession) -> DesktopSession: ...
    def get(self, session_id: str) -> DesktopSession | None: ...


@runtime_checkable
class DesktopWorkspacePort(Protocol):
    def get_workspace(self, project_id: str) -> EngineeringWorkspaceSnapshot | None: ...


@runtime_checkable
class DesktopApplicationPort(Protocol):
    def answer(self, request: ApplicationQuestionRequest) -> ApplicationRunResult: ...


@runtime_checkable
class DesktopCapabilityPort(Protocol):
    def runtime_status(
        self, project_id: str
    ) -> tuple[DesktopRuntimeComponentView, ...]: ...


@runtime_checkable
class DesktopModelSettingsPort(Protocol):
    def get_settings(self, project_id: str) -> DesktopModelSettingsView: ...


@runtime_checkable
class DesktopAuditPort(Protocol):
    def append(self, event: DesktopAuditEvent) -> DesktopAuditEvent: ...
    def get(self, event_id: str) -> DesktopAuditEvent | None: ...


@runtime_checkable
class DesktopClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
