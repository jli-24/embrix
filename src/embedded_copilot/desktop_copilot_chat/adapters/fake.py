from __future__ import annotations

import copy
from datetime import datetime

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRunResult,
)

from ..exceptions import ChatConflict, ChatUnavailable
from ..models import (
    ChatAuditEvent,
    ChatContextReference,
    ChatEvent,
    ChatProjectReference,
    ChatRuntimePanelView,
    ChatSession,
)


def _copy(value: object, model: type):
    if type(value) is not model:
        raise ChatUnavailable("fake chat adapter received invalid content")
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedChatClock:
    __slots__ = ("_value",)

    def __init__(self, value: datetime) -> None:
        self._value = copy.deepcopy(value)

    def now(self) -> datetime:
        return copy.deepcopy(self._value)

    def set(self, value: datetime) -> None:
        self._value = copy.deepcopy(value)


class FakeChatApplicationAdapter:
    __slots__ = ("calls", "fail", "result")

    def __init__(self, result: ApplicationRunResult | None = None) -> None:
        self.result = None if result is None else _copy(result, ApplicationRunResult)
        self.calls = 0
        self.fail = False

    def answer(self, request: ApplicationQuestionRequest) -> ApplicationRunResult:
        self.calls += 1
        ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        if self.fail or self.result is None:
            raise ChatUnavailable("fake application adapter failed")
        return _copy(self.result, ApplicationRunResult)


class FakeChatProjectProjection:
    __slots__ = ("_owners", "_projects", "_references", "fail")

    def __init__(
        self,
        projects: tuple[ChatProjectReference, ...],
        references: tuple[ChatContextReference, ...],
        owners: dict[tuple[str, str], str],
    ) -> None:
        self._projects = {
            (item.tenant_id, item.project_id): _copy(item, ChatProjectReference)
            for item in projects
        }
        self._references = tuple(
            _copy(item, ChatContextReference) for item in references
        )
        self._owners = dict(owners)
        self.fail = False

    def project(self, tenant_id: str, project_id: str) -> ChatProjectReference:
        if self.fail:
            raise ChatUnavailable("fake project projection failed")
        value = self._projects.get((tenant_id, project_id))
        if value is None:
            raise ChatUnavailable("fake project projection unavailable")
        return _copy(value, ChatProjectReference)

    def project_for_requester(
        self, tenant_id: str, project_id: str, requester: str
    ) -> ChatProjectReference:
        from ..exceptions import ChatRejected

        if self._owners.get((tenant_id, project_id)) != requester:
            raise ChatRejected("chat project access rejected")
        return self.project(tenant_id, project_id)

    def references(
        self, tenant_id: str, project_id: str
    ) -> tuple[ChatContextReference, ...]:
        if self.fail:
            raise ChatUnavailable("fake context projection failed")
        return tuple(
            _copy(item, ChatContextReference)
            for item in self._references
            if item.tenant_id == tenant_id and item.project_id == project_id
        )


class FakeChatEnvironmentProjection:
    __slots__ = ("_panels", "fail")

    def __init__(self, panels: dict[tuple[str, str], ChatRuntimePanelView]) -> None:
        self._panels = {
            key: _copy(value, ChatRuntimePanelView) for key, value in panels.items()
        }
        self.fail = False

    def panel(self, tenant_id: str, project_id: str) -> ChatRuntimePanelView:
        if self.fail:
            raise ChatUnavailable("fake environment projection failed")
        value = self._panels.get((tenant_id, project_id))
        if value is None:
            raise ChatUnavailable("fake environment projection unavailable")
        return _copy(value, ChatRuntimePanelView)


class FakeChatSessionStore:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items: dict[str, ChatSession] = {}
        self.fail = False

    def save(self, session: ChatSession) -> ChatSession:
        if self.fail:
            raise ChatUnavailable("fake chat session store failed")
        checked = _copy(session, ChatSession)
        existing = self._items.get(checked.session_id)
        if existing is not None and (
            existing.tenant_id != checked.tenant_id
            or existing.project_id != checked.project_id
            or existing.requester != checked.requester
            or existing.created_at != checked.created_at
            or existing.expires_at != checked.expires_at
        ):
            raise ChatConflict("fake chat session conflict")
        self._items[checked.session_id] = checked
        return _copy(checked, ChatSession)

    def get(self, session_id: str) -> ChatSession | None:
        if self.fail:
            raise ChatUnavailable("fake chat session store failed")
        value = self._items.get(session_id)
        return None if value is None else _copy(value, ChatSession)


class FakeChatAuditSink:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items: dict[str, ChatAuditEvent] = {}
        self.fail = False

    def append(self, event: ChatAuditEvent) -> ChatAuditEvent:
        if self.fail:
            raise ChatUnavailable("fake chat audit failed")
        checked = _copy(event, ChatAuditEvent)
        existing = self._items.get(checked.event_id)
        if existing is not None and existing != checked:
            raise ChatConflict("fake chat audit conflict")
        self._items[checked.event_id] = checked
        return _copy(checked, ChatAuditEvent)

    def get(self, event_id: str) -> ChatAuditEvent | None:
        value = self._items.get(event_id)
        return None if value is None else _copy(value, ChatAuditEvent)


class FakeChatEventStore:
    __slots__ = ("_capacity", "_items", "fail")

    def __init__(self, capacity: int = 32) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("fake chat event capacity is invalid")
        self._capacity = capacity
        self._items: dict[str, list[ChatEvent]] = {}
        self.fail = False

    def append(self, event: ChatEvent) -> ChatEvent:
        if self.fail:
            raise ChatUnavailable("fake chat event store failed")
        checked = _copy(event, ChatEvent)
        items = self._items.setdefault(checked.session_id, [])
        existing = next(
            (item for item in items if item.event_id == checked.event_id), None
        )
        if existing is not None:
            if existing != checked:
                raise ChatConflict("fake chat event conflict")
            return _copy(existing, ChatEvent)
        items.append(checked)
        del items[: -self._capacity]
        return _copy(checked, ChatEvent)

    def read_after(
        self, session_id: str, last_event_id: str | None
    ) -> tuple[ChatEvent, ...] | None:
        if self.fail:
            raise ChatUnavailable("fake chat event store failed")
        items = self._items.get(session_id, [])
        if last_event_id is None:
            return tuple(_copy(item, ChatEvent) for item in items)
        for index, item in enumerate(items):
            if item.event_id == last_event_id:
                return tuple(_copy(value, ChatEvent) for value in items[index + 1 :])
        return None
