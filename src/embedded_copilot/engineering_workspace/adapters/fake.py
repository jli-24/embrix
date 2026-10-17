from __future__ import annotations

import copy
from datetime import datetime

from ..exceptions import WorkspaceConflict, WorkspaceUnavailable
from ..models import WorkspaceObjectReference, WorkspaceResolvedReference


def _copy(value: object, model: type):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedWorkspaceClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeWorkspaceReferenceSourceAdapter:
    __slots__ = ("_items", "fail")

    def __init__(self, items: tuple[WorkspaceResolvedReference, ...]) -> None:
        self._items: dict[tuple[object, object, str], WorkspaceResolvedReference] = {}
        self.fail = False
        for item in items:
            checked = _copy(item, WorkspaceResolvedReference)
            identity = checked.reference.identity
            prior = self._items.get(identity)
            if prior is not None and prior != checked:
                raise WorkspaceConflict("workspace fake reference conflict")
            self._items[identity] = checked

    def resolve(
        self, reference: WorkspaceObjectReference
    ) -> WorkspaceResolvedReference | None:
        if self.fail:
            raise WorkspaceUnavailable("injected workspace source failure")
        checked = _copy(reference, WorkspaceObjectReference)
        item = self._items.get(checked.identity)
        return None if item is None else _copy(item, WorkspaceResolvedReference)


__all__ = ("FakeWorkspaceReferenceSourceAdapter", "FixedWorkspaceClock")
