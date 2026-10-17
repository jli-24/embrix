"""Typed relations between engineering object revisions."""

from __future__ import annotations

from enum import Enum

from pydantic import field_validator

from embedded_copilot.engineering_runtime.common import (
    RuntimeContract,
    canonical_fingerprint,
    validate_fingerprint,
    validate_identifier,
    validate_text_tuple,
)
from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    ObjectType,
)


class RelationType(str, Enum):
    SATISFIED_BY = "satisfied_by"
    ALLOCATED_TO = "allocated_to"
    BOUNDED_BY = "bounded_by"
    IMPLEMENTED_BY = "implemented_by"
    EXPRESSED_BY = "expressed_by"
    VALIDATED_BY = "validated_by"
    DEPENDS_ON = "depends_on"


class ObjectRef(RuntimeContract):
    project_id: str
    object_id: str
    object_type: ObjectType
    revision: str
    fingerprint: str

    _project = field_validator("project_id")(
        lambda v: validate_identifier(v, field="project_id")
    )
    _object = field_validator("object_id")(
        lambda v: validate_identifier(v, field="object_id")
    )
    _revision = field_validator("revision")(
        lambda v: validate_identifier(v, field="revision")
    )
    _fingerprint = field_validator("fingerprint")(validate_fingerprint)

    @classmethod
    def from_object(cls, obj: EngineeringObject) -> ObjectRef:
        return cls(
            project_id=obj.project_id,
            object_id=obj.object_id,
            object_type=obj.object_type,
            revision=obj.revision,
            fingerprint=obj.fingerprint,
        )


class Relation(RuntimeContract):
    relation_id: str
    project_id: str
    source: ObjectRef
    relation_type: RelationType
    target: ObjectRef
    evidence_refs: tuple[str, ...] = ()
    fingerprint: str

    _relation_id = field_validator("relation_id")(
        lambda v: validate_identifier(v, field="relation_id")
    )
    _project = field_validator("project_id")(
        lambda v: validate_identifier(v, field="project_id")
    )
    _evidence = field_validator("evidence_refs")(
        lambda v: validate_text_tuple(v, field="evidence_refs")
    )

    @classmethod
    def create(
        cls,
        *,
        relation_id: str,
        project_id: str,
        source: ObjectRef,
        relation_type: RelationType,
        target: ObjectRef,
        evidence_refs: tuple[str, ...] = (),
    ) -> Relation:
        if source.project_id != project_id or target.project_id != project_id:
            raise ValueError("relation endpoints must belong to the same project")
        if source.object_id == target.object_id:
            raise ValueError("self-relations are not allowed")
        draft = cls(
            relation_id=relation_id,
            project_id=project_id,
            source=source,
            relation_type=relation_type,
            target=target,
            evidence_refs=evidence_refs,
            fingerprint="sha256:" + "0" * 64,
        )
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})
            }
        )
