"""In-memory object registry for the MVP."""

from __future__ import annotations

from collections import defaultdict

from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    LifecycleStatus,
)


class ObjectRegistry:
    def __init__(self) -> None:
        self._revisions: dict[str, list[EngineeringObject]] = defaultdict(list)
        self._current: dict[str, str] = {}
        self._active: dict[str, str] = {}

    def register(self, obj: EngineeringObject) -> EngineeringObject:
        revisions = self._revisions[obj.object_id]
        if any(item.revision == obj.revision for item in revisions):
            raise ValueError(f"revision already exists: {obj.revision_id}")
        if revisions and obj.revision == "r1":
            raise ValueError("new object revision must not restart at r1")
        revisions.append(obj)
        revisions.sort(key=lambda item: int(item.revision[1:]))
        self._current[obj.object_id] = obj.revision
        if obj.status == LifecycleStatus.ACTIVE:
            self._active[obj.object_id] = obj.revision
        return obj

    def register_object(self, obj: EngineeringObject) -> EngineeringObject:
        """Public name matching the Runtime object-registry contract."""
        return self.register(obj)

    def store_revision(self, obj: EngineeringObject) -> EngineeringObject:
        """Store an already lifecycle-managed revision during proposal apply."""
        revisions = self._revisions[obj.object_id]
        if any(item.revision == obj.revision for item in revisions):
            raise ValueError(f"revision already exists: {obj.revision_id}")
        if revisions and int(obj.revision[1:]) <= max(
            int(item.revision[1:]) for item in revisions
        ):
            raise ValueError("revision must increase monotonically")
        revisions.append(obj)
        self._current[obj.object_id] = obj.revision
        if obj.status == LifecycleStatus.ACTIVE:
            self._active[obj.object_id] = obj.revision
        return obj

    def replace(self, obj: EngineeringObject) -> EngineeringObject:
        revisions = self._revisions.get(obj.object_id)
        if not revisions:
            raise KeyError(obj.object_id)
        for index, item in enumerate(revisions):
            if item.revision == obj.revision:
                revisions[index] = obj
                self._current[obj.object_id] = obj.revision
                if obj.status in {LifecycleStatus.ACTIVE, LifecycleStatus.STALE}:
                    self._active[obj.object_id] = obj.revision
                return obj
        raise KeyError(obj.revision_id)

    def get_object(
        self, object_id: str, revision: str | None = None
    ) -> EngineeringObject:
        revisions = self._revisions[object_id]
        if revision is None:
            revision = self._current[object_id]
        for item in revisions:
            if item.revision == revision:
                return item
        raise KeyError(f"{object_id}@{revision}")

    def list_revision(self, object_id: str) -> tuple[EngineeringObject, ...]:
        return tuple(self._revisions.get(object_id, ()))

    def get_active_revision(self, object_id: str) -> EngineeringObject:
        """Return the current revision, including a stale current revision.

        Resolver callers must inspect ``status``.  Retaining the current stale
        revision makes impact analysis and user-facing diagnostics possible.
        """
        revision = self._active.get(object_id, self._current.get(object_id))
        if revision is None:
            raise KeyError(object_id)
        return self.get_object(object_id, revision)

    def all_current(self) -> tuple[EngineeringObject, ...]:
        return tuple(self.get_object(object_id) for object_id in sorted(self._current))

    def contains(self, object_id: str) -> bool:
        return object_id in self._revisions
