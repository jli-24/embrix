"""Revision creation and identity rules."""

from __future__ import annotations

from typing import Any

from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    LifecycleStatus,
    Provenance,
)


class RevisionManager:
    def next_revision(self, obj: EngineeringObject) -> str:
        return f"r{int(obj.revision[1:]) + 1}"

    def create_revision(
        self,
        obj: EngineeringObject,
        changes: dict[str, Any],
        provenance: Provenance | None = None,
    ) -> EngineeringObject:
        if not changes:
            raise ValueError("a new revision must contain changes")
        forbidden = {
            "object_id",
            "project_id",
            "object_type",
            "revision",
            "status",
            "fingerprint",
        }
        if forbidden.intersection(changes):
            raise ValueError(
                "revision changes cannot replace object identity or lifecycle fields"
            )
        values = obj.model_dump(mode="python")
        values.update(changes)
        values["revision"] = self.next_revision(obj)
        values["status"] = LifecycleStatus.DRAFT
        values["fingerprint"] = "sha256:" + "0" * 64
        if provenance is not None:
            values["provenance"] = provenance
        return type(obj).create(**values)
