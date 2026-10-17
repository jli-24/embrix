from __future__ import annotations

import copy
from datetime import datetime

from ..exceptions import (
    EnvironmentConflict,
    EnvironmentRejected,
    EnvironmentUnavailable,
)
from ..models import (
    CollaborationView,
    CopilotRuntimeHealth,
    EngineeringAuditView,
    EngineeringMemoryView,
    EnvironmentDeploymentProfile,
    EnvironmentEvent,
    ProjectView,
)


def _copy(value: object, model: type):
    if type(value) is not model:
        raise EnvironmentUnavailable(
            "fake environment adapter received invalid content"
        )
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FakeEnvironmentProjectAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[ProjectView, ...]) -> None:
        self._items = tuple(_copy(item, ProjectView) for item in items)
        self.fail = False

    def list_projects(self, tenant_id: str) -> tuple[ProjectView, ...]:
        if self.fail:
            raise EnvironmentUnavailable("fake project adapter failed")
        return tuple(
            _copy(item, ProjectView)
            for item in self._items
            if item.environment.tenant_id == tenant_id
        )

    def open_project(self, tenant_id: str, project_id: str) -> ProjectView:
        return self._project(tenant_id, project_id)

    def close_project(self, tenant_id: str, project_id: str) -> ProjectView:
        return self._project(tenant_id, project_id)

    def _project(self, tenant_id: str, project_id: str) -> ProjectView:
        if self.fail:
            raise EnvironmentUnavailable("fake project adapter failed")
        for item in self._items:
            if item.project.project_id == project_id:
                if item.environment.tenant_id != tenant_id:
                    raise EnvironmentRejected("project tenant mismatch")
                return _copy(item, ProjectView)
        raise EnvironmentRejected("project reference was not found")


class FakeTeamAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[CollaborationView, ...]) -> None:
        self._items = tuple(_copy(item, CollaborationView) for item in items)
        self.fail = False

    def team(self, tenant_id: str) -> CollaborationView:
        if self.fail:
            raise EnvironmentUnavailable("fake team adapter failed")
        for item in self._items:
            if item.environment.tenant_id == tenant_id:
                return _copy(item, CollaborationView)
        raise EnvironmentRejected("team tenant was not found")


class FakeRuntimeHealthAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[CopilotRuntimeHealth, ...]) -> None:
        self._items = tuple(_copy(item, CopilotRuntimeHealth) for item in items)
        self.fail = False

    def runtime_health(self, tenant_id: str) -> tuple[CopilotRuntimeHealth, ...]:
        if self.fail:
            raise EnvironmentUnavailable("fake runtime health adapter failed")
        return tuple(
            _copy(item, CopilotRuntimeHealth)
            for item in self._items
            if item.tenant_id == tenant_id
        )


class FakeAuditQueryAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[EngineeringAuditView, ...]) -> None:
        self._items = tuple(_copy(item, EngineeringAuditView) for item in items)
        self.fail = False

    def query_by_project(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringAuditView, ...]:
        return tuple(
            item for item in self._tenant(tenant_id) if item.project_id == project_id
        )

    def query_by_object(
        self, tenant_id: str, object_reference: str
    ) -> tuple[EngineeringAuditView, ...]:
        return tuple(
            item
            for item in self._tenant(tenant_id)
            if item.object_reference == object_reference
        )

    def query_by_time(
        self, tenant_id: str, start: datetime | None, end: datetime | None
    ) -> tuple[EngineeringAuditView, ...]:
        return tuple(
            item
            for item in self._tenant(tenant_id)
            if (start is None or item.timestamp >= start)
            and (end is None or item.timestamp <= end)
        )

    def _tenant(self, tenant_id: str) -> tuple[EngineeringAuditView, ...]:
        if self.fail:
            raise EnvironmentUnavailable("fake audit adapter failed")
        return tuple(
            _copy(item, EngineeringAuditView)
            for item in self._items
            if item.tenant_id == tenant_id
        )


class FakeMemoryViewAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[EngineeringMemoryView, ...]) -> None:
        self._items = tuple(_copy(item, EngineeringMemoryView) for item in items)
        self.fail = False

    def memory_views(self, tenant_id: str) -> tuple[EngineeringMemoryView, ...]:
        if self.fail:
            raise EnvironmentUnavailable("fake memory adapter failed")
        return tuple(
            _copy(item, EngineeringMemoryView)
            for item in self._items
            if item.tenant_id == tenant_id
        )


class FakeDeploymentProfileAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[EnvironmentDeploymentProfile, ...]) -> None:
        self._items = tuple(_copy(item, EnvironmentDeploymentProfile) for item in items)
        self.fail = False

    def deployment_profile(self, tenant_id: str) -> EnvironmentDeploymentProfile:
        if self.fail:
            raise EnvironmentUnavailable("fake deployment adapter failed")
        for item in self._items:
            if item.tenant_id == tenant_id:
                return _copy(item, EnvironmentDeploymentProfile)
        raise EnvironmentRejected("deployment tenant was not found")


class FakeEnvironmentEventStream:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[EnvironmentEvent, ...]) -> None:
        self._items: dict[str, list[EnvironmentEvent]] = {}
        self.fail = False
        for item in items:
            self.publish(item)

    def publish(self, event: EnvironmentEvent) -> EnvironmentEvent:
        if self.fail:
            raise EnvironmentUnavailable("fake event stream failed")
        checked = _copy(event, EnvironmentEvent)
        items = self._items.setdefault(checked.tenant_id, [])
        existing = next(
            (item for item in items if item.fingerprint == checked.fingerprint), None
        )
        if existing is not None:
            if existing != checked:
                raise EnvironmentConflict("environment event fingerprint conflict")
            return _copy(existing, EnvironmentEvent)
        items.append(checked)
        return _copy(checked, EnvironmentEvent)

    def read_after(
        self, tenant_id: str, last_event_fingerprint: str | None
    ) -> tuple[EnvironmentEvent, ...] | None:
        if self.fail:
            raise EnvironmentUnavailable("fake event stream failed")
        items = self._items.get(tenant_id, [])
        if last_event_fingerprint is None:
            return tuple(_copy(item, EnvironmentEvent) for item in items)
        for index, item in enumerate(items):
            if item.fingerprint == last_event_fingerprint:
                return tuple(
                    _copy(value, EnvironmentEvent) for value in items[index + 1 :]
                )
        return None
