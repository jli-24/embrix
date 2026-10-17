from __future__ import annotations

import copy
from datetime import datetime

from ..exceptions import ProductConflict, ProductUnavailable
from ..models import DesktopProductEvent, DesktopUserPreferences, ProductSession


def _copy(value: object, model: type):
    if type(value) is not model:
        raise ProductUnavailable("fake product adapter received invalid content")
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedProductClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


class FakeProductSessionStore:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items: dict[str, ProductSession] = {}
        self.fail = False

    def save(self, session: ProductSession) -> ProductSession:
        if self.fail:
            raise ProductUnavailable("fake product session store failed")
        checked = _copy(session, ProductSession)
        existing = self._items.get(checked.product_session_id)
        if (
            existing is not None
            and existing.status is checked.status
            and existing != checked
        ):
            raise ProductConflict("product session conflict")
        self._items[checked.product_session_id] = checked
        return _copy(checked, ProductSession)

    def get(self, product_session_id: str) -> ProductSession | None:
        if self.fail:
            raise ProductUnavailable("fake product session store failed")
        value = self._items.get(product_session_id)
        return None if value is None else _copy(value, ProductSession)


class FakeDesktopPreferenceStore:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items: dict[str, DesktopUserPreferences] = {}
        self.fail = False

    def save(self, preferences: DesktopUserPreferences) -> DesktopUserPreferences:
        if self.fail:
            raise ProductUnavailable("fake desktop preference store failed")
        checked = _copy(preferences, DesktopUserPreferences)
        self._items[checked.requester] = checked
        return _copy(checked, DesktopUserPreferences)

    def get(self, requester: str) -> DesktopUserPreferences | None:
        if self.fail:
            raise ProductUnavailable("fake desktop preference store failed")
        value = self._items.get(requester)
        return None if value is None else _copy(value, DesktopUserPreferences)


class FakeDesktopProductEventStore:
    __slots__ = ("_items", "capacity", "fail")

    def __init__(self, *, capacity: int = 100) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("event capacity is invalid")
        self._items: dict[str, list[DesktopProductEvent]] = {}
        self.capacity = capacity
        self.fail = False

    def append(self, event: DesktopProductEvent) -> DesktopProductEvent:
        if self.fail:
            raise ProductUnavailable("fake desktop event store failed")
        checked = _copy(event, DesktopProductEvent)
        items = self._items.setdefault(checked.product_session_id, [])
        existing = next(
            (item for item in items if item.event_id == checked.event_id), None
        )
        if existing is not None:
            if existing != checked:
                raise ProductConflict("desktop product event conflict")
            return _copy(existing, DesktopProductEvent)
        if items and checked.sequence != items[-1].sequence + 1:
            raise ProductConflict("desktop product event sequence conflict")
        items.append(checked)
        del items[: -self.capacity]
        return _copy(checked, DesktopProductEvent)

    def read_after(
        self, product_session_id: str, last_event_id: str | None
    ) -> tuple[DesktopProductEvent, ...] | None:
        if self.fail:
            raise ProductUnavailable("fake desktop event store failed")
        items = self._items.get(product_session_id, [])
        if last_event_id is None:
            return tuple(_copy(item, DesktopProductEvent) for item in items)
        for index, item in enumerate(items):
            if item.event_id == last_event_id:
                return tuple(
                    _copy(value, DesktopProductEvent) for value in items[index + 1 :]
                )
        return None
