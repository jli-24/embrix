from __future__ import annotations

import copy
from datetime import datetime

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRunResult,
)
from embedded_copilot.engineering_workspace import EngineeringWorkspaceSnapshot

from ..exceptions import DesktopConflict, DesktopUnavailable
from ..models import (
    DesktopAuditEvent,
    DesktopModelSettingsView,
    DesktopRuntimeComponentView,
    DesktopSession,
)


def _copy(value: object, model: type):
    if type(value) is not model:
        raise DesktopUnavailable("fake adapter received an invalid model")
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedDesktopClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FakeSessionAdapter:
    __slots__ = ("_sessions", "fail")

    def __init__(self) -> None:
        self._sessions: dict[str, DesktopSession] = {}
        self.fail = False

    def save(self, session: DesktopSession) -> DesktopSession:
        if self.fail:
            raise DesktopUnavailable("fake session adapter failed")
        checked = _copy(session, DesktopSession)
        existing = self._sessions.get(checked.session_id)
        if existing is not None and existing != checked:
            self._sessions[checked.session_id] = checked
        else:
            self._sessions[checked.session_id] = checked
        return _copy(checked, DesktopSession)

    def get(self, session_id: str) -> DesktopSession | None:
        if self.fail:
            raise DesktopUnavailable("fake session adapter failed")
        value = self._sessions.get(session_id)
        return None if value is None else _copy(value, DesktopSession)


class FakeWorkspaceAdapter:
    __slots__ = ("_snapshots", "fail")

    def __init__(
        self, snapshots: tuple[EngineeringWorkspaceSnapshot, ...] = ()
    ) -> None:
        self._snapshots = {
            item.project_id: _copy(item, EngineeringWorkspaceSnapshot)
            for item in snapshots
        }
        self.fail = False

    def get_workspace(self, project_id: str) -> EngineeringWorkspaceSnapshot | None:
        if self.fail:
            raise DesktopUnavailable("fake workspace adapter failed")
        value = self._snapshots.get(project_id)
        return None if value is None else _copy(value, EngineeringWorkspaceSnapshot)


class FakeApplicationAdapter:
    __slots__ = ("calls", "fail", "result")

    def __init__(self, result: ApplicationRunResult | None = None) -> None:
        self.result = None if result is None else _copy(result, ApplicationRunResult)
        self.calls = 0
        self.fail = False

    def answer(self, request: ApplicationQuestionRequest) -> ApplicationRunResult:
        self.calls += 1
        _copy(request, ApplicationQuestionRequest)
        if self.fail or self.result is None:
            raise DesktopUnavailable("fake application adapter failed")
        return _copy(self.result, ApplicationRunResult)


class FakeCapabilityAdapter:
    __slots__ = ("_components", "calls", "fail")

    def __init__(self, components: tuple[DesktopRuntimeComponentView, ...]) -> None:
        self._components = tuple(
            _copy(item, DesktopRuntimeComponentView) for item in components
        )
        self.calls = 0
        self.fail = False

    def runtime_status(
        self, project_id: str
    ) -> tuple[DesktopRuntimeComponentView, ...]:
        if self.fail:
            raise DesktopUnavailable("fake capability adapter failed")
        self.calls += 1
        return tuple(
            _copy(item, DesktopRuntimeComponentView) for item in self._components
        )


class FakeModelSettingsAdapter:
    __slots__ = ("fail", "settings")

    def __init__(self, settings: DesktopModelSettingsView) -> None:
        self.settings = _copy(settings, DesktopModelSettingsView)
        self.fail = False

    def get_settings(self, project_id: str) -> DesktopModelSettingsView:
        if self.fail:
            raise DesktopUnavailable("fake model settings adapter failed")
        return _copy(self.settings, DesktopModelSettingsView)


class FakeAuditSink:
    __slots__ = ("_events", "fail")

    def __init__(self) -> None:
        self._events: dict[str, DesktopAuditEvent] = {}
        self.fail = False

    def append(self, event: DesktopAuditEvent) -> DesktopAuditEvent:
        if self.fail:
            raise DesktopUnavailable("fake audit sink failed")
        checked = _copy(event, DesktopAuditEvent)
        existing = self._events.get(checked.event_id)
        if existing is not None and existing != checked:
            raise DesktopConflict("desktop audit event conflict")
        self._events[checked.event_id] = checked
        return _copy(checked, DesktopAuditEvent)

    def get(self, event_id: str) -> DesktopAuditEvent | None:
        value = self._events.get(event_id)
        return None if value is None else _copy(value, DesktopAuditEvent)
