"""Context request and immutable snapshot contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import field_validator

from embedded_copilot.engineering_runtime.common import (
    ZERO_FINGERPRINT,
    RuntimeContract,
    canonical_fingerprint,
    validate_fingerprint,
    validate_identifier,
    validate_text_tuple,
)
from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    EvidenceObject,
    ObjectType,
)
from embedded_copilot.engineering_runtime.relations.models import Relation


class ContextResolutionError(RuntimeError):
    pass


class BindingStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class ContextRequest(RuntimeContract):
    project_id: str
    target_object_type: ObjectType
    required_object_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    _project = field_validator("project_id")(
        lambda v: validate_identifier(v, field="project_id")
    )
    _objects = field_validator("required_object_ids")(
        lambda v: validate_text_tuple(v, field="required_object_ids")
    )
    _evidence = field_validator("evidence_ids")(
        lambda v: validate_text_tuple(v, field="evidence_ids")
    )
    _constraints = field_validator("constraints")(
        lambda v: validate_text_tuple(v, field="constraints")
    )

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        target_object_type: ObjectType,
        required_object_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
    ) -> ContextRequest:
        return cls(
            project_id=project_id,
            target_object_type=ObjectType(target_object_type),
            required_object_ids=required_object_ids,
            evidence_ids=evidence_ids,
            constraints=constraints,
        )


class EngineeringContextSnapshot(RuntimeContract):
    project_id: str
    target_object_type: ObjectType
    objects: tuple[EngineeringObject, ...]
    relations: tuple[Relation, ...] = ()
    evidence_bundle: tuple[EvidenceObject, ...] = ()
    constraints: tuple[str, ...] = ()
    binding_status: BindingStatus | None = None
    fingerprint: str = ZERO_FINGERPRINT

    _project = field_validator("project_id")(
        lambda v: validate_identifier(v, field="project_id")
    )
    _constraints = field_validator("constraints")(
        lambda v: validate_text_tuple(v, field="constraints")
    )
    _fingerprint = field_validator("fingerprint")(validate_fingerprint)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        target_object_type: ObjectType,
        objects: tuple[EngineeringObject, ...],
        relations: tuple[Relation, ...],
        evidence_bundle: tuple[EvidenceObject, ...],
        constraints: tuple[str, ...],
        binding_status: BindingStatus | None = None,
    ) -> EngineeringContextSnapshot:
        draft = cls(
            project_id=project_id,
            target_object_type=target_object_type,
            objects=objects,
            relations=relations,
            evidence_bundle=evidence_bundle,
            constraints=constraints,
            binding_status=binding_status,
            fingerprint=ZERO_FINGERPRINT,
        )
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(draft, exclude={"fingerprint"})
            }
        )
