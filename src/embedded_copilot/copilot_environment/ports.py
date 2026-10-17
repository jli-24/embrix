from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    CollaborationView,
    CopilotRuntimeHealth,
    EngineeringAuditView,
    EngineeringMemoryView,
    EnvironmentDeploymentProfile,
    EnvironmentEvent,
    ProjectView,
)


@runtime_checkable
class ProjectEnvironmentPort(Protocol):
    def list_projects(self, tenant_id: str) -> tuple[ProjectView, ...]: ...
    def open_project(self, tenant_id: str, project_id: str) -> ProjectView: ...
    def close_project(self, tenant_id: str, project_id: str) -> ProjectView: ...


@runtime_checkable
class TeamContextPort(Protocol):
    def team(self, tenant_id: str) -> CollaborationView: ...


@runtime_checkable
class RuntimeHealthPort(Protocol):
    def runtime_health(self, tenant_id: str) -> tuple[CopilotRuntimeHealth, ...]: ...


@runtime_checkable
class AuditQueryPort(Protocol):
    def query_by_project(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringAuditView, ...]: ...
    def query_by_object(
        self, tenant_id: str, object_reference: str
    ) -> tuple[EngineeringAuditView, ...]: ...
    def query_by_time(
        self, tenant_id: str, start: datetime | None, end: datetime | None
    ) -> tuple[EngineeringAuditView, ...]: ...


@runtime_checkable
class MemoryViewPort(Protocol):
    def memory_views(self, tenant_id: str) -> tuple[EngineeringMemoryView, ...]: ...


@runtime_checkable
class DeploymentProfilePort(Protocol):
    def deployment_profile(self, tenant_id: str) -> EnvironmentDeploymentProfile: ...


@runtime_checkable
class EnvironmentEventStreamPort(Protocol):
    def publish(self, event: EnvironmentEvent) -> EnvironmentEvent: ...
    def read_after(
        self, tenant_id: str, last_event_fingerprint: str | None
    ) -> tuple[EnvironmentEvent, ...] | None: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
