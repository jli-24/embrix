from __future__ import annotations

import copy

from .contracts import deterministic_id
from .exceptions import WorkspaceConflict, WorkspaceRejected, WorkspaceUnavailable
from .models import (
    EngineeringWorkspaceRequest,
    EngineeringWorkspaceSnapshot,
    WorkspaceObjectReference,
    WorkspaceResolvedReference,
    WorkspaceTimelineEntry,
)
from .ports import WorkspaceClockPort, WorkspaceReferenceSourcePort


class EngineeringWorkspaceService:
    __slots__ = ("_clock", "_source")

    def __init__(
        self, *, source: WorkspaceReferenceSourcePort, clock: WorkspaceClockPort
    ) -> None:
        if not isinstance(source, WorkspaceReferenceSourcePort) or not isinstance(
            clock, WorkspaceClockPort
        ):
            raise TypeError("workspace dependency is invalid")
        self._source = source
        self._clock = clock

    @staticmethod
    def _checked(value: object, model: type, *, field: str):
        if type(value) is not model:
            raise WorkspaceRejected(f"{field} is invalid")
        try:
            return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except Exception as error:
            raise WorkspaceRejected(f"{field} is invalid") from error

    def _resolve(
        self, reference: WorkspaceObjectReference
    ) -> WorkspaceResolvedReference:
        try:
            raw = self._source.resolve(copy.deepcopy(reference))
        except WorkspaceConflict:
            raise
        except Exception as error:
            raise WorkspaceUnavailable(
                "workspace reference source unavailable"
            ) from error
        if raw is None:
            raise WorkspaceRejected("workspace reference is missing")
        resolved = self._checked(
            raw, WorkspaceResolvedReference, field="workspace resolved reference"
        )
        if resolved.reference != reference:
            if resolved.reference.identity == reference.identity:
                raise WorkspaceConflict("workspace reference fingerprint conflict")
            raise WorkspaceRejected("workspace reference was altered")
        return resolved

    def build_snapshot(
        self, request: EngineeringWorkspaceRequest
    ) -> EngineeringWorkspaceSnapshot:
        checked = self._checked(
            request, EngineeringWorkspaceRequest, field="workspace request"
        )
        resolved = tuple(self._resolve(reference) for reference in checked.references)
        ordered = tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.reference.occurred_at,
                    item.reference.source_domain.value,
                    item.reference.source_kind.value,
                    item.reference.source_object_id,
                ),
            )
        )
        timeline = tuple(
            WorkspaceTimelineEntry(
                entry_id=deterministic_id(
                    "workspace-entry", item.reference.fingerprint, str(index)
                ),
                sequence=index,
                reference=item.reference,
                lineage=item.lineage,
            )
            for index, item in enumerate(ordered, start=1)
        )
        try:
            generated_at = self._clock.now()
        except Exception as error:
            raise WorkspaceUnavailable("workspace clock unavailable") from error
        try:
            snapshot = EngineeringWorkspaceSnapshot(
                workspace_id=checked.workspace_id,
                project_id=checked.project_id,
                source_references=tuple(item.reference for item in ordered),
                timeline=timeline,
                generated_at=generated_at,
            )
        except Exception as error:
            raise WorkspaceRejected("workspace snapshot is invalid") from error
        return self._checked(
            snapshot, EngineeringWorkspaceSnapshot, field="workspace snapshot"
        )


__all__ = ("EngineeringWorkspaceService",)
