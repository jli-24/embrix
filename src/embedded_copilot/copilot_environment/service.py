from __future__ import annotations

import copy
from datetime import datetime

from .contracts import identifier, safe_text, utc_datetime
from .exceptions import EnvironmentRejected, EnvironmentUnavailable
from .models import (
    CollaborationView,
    CopilotRuntimeHealth,
    EngineeringAuditView,
    EngineeringMemoryView,
    EnvironmentDeploymentProfile,
    EnvironmentEvent,
    ProjectView,
)
from .ports import (
    AuditQueryPort,
    DeploymentProfilePort,
    EnvironmentEventStreamPort,
    MemoryViewPort,
    ProjectEnvironmentPort,
    RuntimeHealthPort,
    TeamContextPort,
)


def _copy(value: object, model: type, *, field: str):
    if type(value) is not model:
        raise EnvironmentUnavailable(f"{field} returned an invalid type")
    try:
        return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
    except (TypeError, ValueError) as error:
        raise EnvironmentUnavailable(f"{field} returned invalid content") from error


class EngineeringEnvironmentRuntime:
    """Read-only tenant-scoped operating-environment projection runtime."""

    __slots__ = (
        "_audit",
        "_deployment",
        "_events",
        "_health",
        "_memory",
        "_projects",
        "_teams",
    )

    def __init__(
        self,
        *,
        projects: ProjectEnvironmentPort,
        teams: TeamContextPort,
        health: RuntimeHealthPort,
        audit: AuditQueryPort,
        memory: MemoryViewPort,
        deployment: DeploymentProfilePort,
        events: EnvironmentEventStreamPort,
    ) -> None:
        self._projects = projects
        self._teams = teams
        self._health = health
        self._audit = audit
        self._memory = memory
        self._deployment = deployment
        self._events = events

    @staticmethod
    def _tenant(tenant_id: str) -> str:
        return identifier(tenant_id, field="tenant_id")

    @staticmethod
    def _project_id(project_id: str) -> str:
        return identifier(project_id, field="project_id")

    @staticmethod
    def _project_view(value: object, tenant_id: str, *, field: str) -> ProjectView:
        checked = _copy(value, ProjectView, field=field)
        if (
            checked.environment.tenant_id != tenant_id
            or checked.project.tenant_id != tenant_id
        ):
            raise EnvironmentRejected("project tenant mismatch")
        return checked

    def list_projects(self, tenant_id: str) -> tuple[ProjectView, ...]:
        checked_tenant = self._tenant(tenant_id)
        try:
            values = self._projects.list_projects(checked_tenant)
        except EnvironmentRejected:
            raise
        except Exception as error:
            raise EnvironmentUnavailable(
                "project environment is unavailable"
            ) from error
        return tuple(
            self._project_view(value, checked_tenant, field="project environment")
            for value in values
        )

    def project(self, tenant_id: str, project_id: str) -> ProjectView:
        checked_tenant = self._tenant(tenant_id)
        checked_project = self._project_id(project_id)
        try:
            value = self._projects.open_project(checked_tenant, checked_project)
        except EnvironmentRejected:
            raise
        except Exception as error:
            raise EnvironmentUnavailable(
                "project environment is unavailable"
            ) from error
        checked = self._project_view(value, checked_tenant, field="project environment")
        if checked.project.project_id != checked_project:
            raise EnvironmentRejected("project identity mismatch")
        return checked

    def closed_project(self, tenant_id: str, project_id: str) -> ProjectView:
        checked_tenant = self._tenant(tenant_id)
        checked_project = self._project_id(project_id)
        try:
            value = self._projects.close_project(checked_tenant, checked_project)
        except EnvironmentRejected:
            raise
        except Exception as error:
            raise EnvironmentUnavailable(
                "project environment is unavailable"
            ) from error
        checked = self._project_view(value, checked_tenant, field="project environment")
        if checked.project.project_id != checked_project:
            raise EnvironmentRejected("project identity mismatch")
        return checked

    def team(self, tenant_id: str) -> CollaborationView:
        checked_tenant = self._tenant(tenant_id)
        try:
            value = self._teams.team(checked_tenant)
        except EnvironmentRejected:
            raise
        except Exception as error:
            raise EnvironmentUnavailable("team context is unavailable") from error
        checked = _copy(value, CollaborationView, field="team context")
        if (
            checked.environment.tenant_id != checked_tenant
            or checked.team.tenant_id != checked_tenant
        ):
            raise EnvironmentRejected("team tenant mismatch")
        return checked

    def runtime_health(self, tenant_id: str) -> tuple[CopilotRuntimeHealth, ...]:
        checked_tenant = self._tenant(tenant_id)
        try:
            values = self._health.runtime_health(checked_tenant)
        except Exception as error:
            raise EnvironmentUnavailable("runtime health is unavailable") from error
        checked = tuple(
            _copy(value, CopilotRuntimeHealth, field="runtime health")
            for value in values
        )
        if any(value.tenant_id != checked_tenant for value in checked):
            raise EnvironmentRejected("runtime health tenant mismatch")
        return checked

    def audit_views(
        self,
        tenant_id: str,
        *,
        project_id: str | None = None,
        object_reference: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[EngineeringAuditView, ...]:
        checked_tenant = self._tenant(tenant_id)
        checked_project = None if project_id is None else self._project_id(project_id)
        checked_reference = (
            None
            if object_reference is None
            else safe_text(object_reference, field="object_reference", maximum=256)
        )
        checked_start = None if start is None else utc_datetime(start, field="start")
        checked_end = None if end is None else utc_datetime(end, field="end")
        if (
            checked_start is not None
            and checked_end is not None
            and checked_start > checked_end
        ):
            raise EnvironmentRejected("audit time range is invalid")
        try:
            if checked_project is not None:
                values = self._audit.query_by_project(checked_tenant, checked_project)
            elif checked_reference is not None:
                values = self._audit.query_by_object(checked_tenant, checked_reference)
            else:
                values = self._audit.query_by_time(
                    checked_tenant, checked_start, checked_end
                )
        except Exception as error:
            raise EnvironmentUnavailable("audit projection is unavailable") from error
        checked = tuple(
            _copy(value, EngineeringAuditView, field="audit projection")
            for value in values
        )
        if any(value.tenant_id != checked_tenant for value in checked):
            raise EnvironmentRejected("audit tenant mismatch")
        return tuple(
            value
            for value in checked
            if (checked_project is None or value.project_id == checked_project)
            and (
                checked_reference is None or value.object_reference == checked_reference
            )
            and (checked_start is None or value.timestamp >= checked_start)
            and (checked_end is None or value.timestamp <= checked_end)
        )

    def memory_views(
        self, tenant_id: str, *, project_id: str | None = None
    ) -> tuple[EngineeringMemoryView, ...]:
        checked_tenant = self._tenant(tenant_id)
        checked_project = None if project_id is None else self._project_id(project_id)
        try:
            values = self._memory.memory_views(checked_tenant)
        except Exception as error:
            raise EnvironmentUnavailable("memory view is unavailable") from error
        checked = tuple(
            _copy(value, EngineeringMemoryView, field="memory view") for value in values
        )
        if any(value.tenant_id != checked_tenant for value in checked):
            raise EnvironmentRejected("memory tenant mismatch")
        return tuple(
            value
            for value in checked
            if checked_project is None or value.project_id == checked_project
        )

    def deployment_profile(self, tenant_id: str) -> EnvironmentDeploymentProfile:
        checked_tenant = self._tenant(tenant_id)
        try:
            value = self._deployment.deployment_profile(checked_tenant)
        except Exception as error:
            raise EnvironmentUnavailable("deployment profile is unavailable") from error
        checked = _copy(value, EnvironmentDeploymentProfile, field="deployment profile")
        if checked.tenant_id != checked_tenant:
            raise EnvironmentRejected("deployment tenant mismatch")
        return checked

    def events_after(
        self, tenant_id: str, *, last_event_fingerprint: str | None
    ) -> tuple[EnvironmentEvent, ...] | None:
        checked_tenant = self._tenant(tenant_id)
        try:
            values = self._events.read_after(checked_tenant, last_event_fingerprint)
        except Exception as error:
            raise EnvironmentUnavailable(
                "environment events are unavailable"
            ) from error
        if values is None:
            return None
        checked = tuple(
            _copy(value, EnvironmentEvent, field="environment event")
            for value in values
        )
        if any(value.tenant_id != checked_tenant for value in checked):
            raise EnvironmentRejected("event tenant mismatch")
        return checked
