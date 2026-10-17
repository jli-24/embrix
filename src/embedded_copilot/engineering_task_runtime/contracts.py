"""Opt-in task/approved-plan/phase references for the coordination contract.

The caller supplies a read-only source already bound to the authenticated tenant,
project and principal. This module adds no authority, persistence, execution or
production adapter; source records remain authoritative and are revalidated here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    fp,
    ident,
    validate_copy,
)
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)

from .exceptions import EngineeringTaskRejected, EngineeringTaskUnavailable
from .models import EngineeringTaskRequest
from .ports import EngineeringTaskCoordinationSourcePort


class TaskIdentity(Fingerprinted):
    task_id: str
    tenant_id: str
    project_id: str

    @field_validator("task_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)


class RequestReference(Fingerprinted):
    task_id: str
    request_fingerprint: str

    @field_validator("task_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "task_id")

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fp(value, "request_fingerprint")


class ProjectReference(Fingerprinted):
    tenant_id: str
    project_id: str

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)


class PlanReference(Fingerprinted):
    plan_id: str
    content_fingerprint: str
    review_fingerprint: str

    @field_validator("plan_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "plan_id")

    @field_validator("content_fingerprint", "review_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fp(value, info.field_name)


class StageSourceReference(Fingerprinted):
    source_type: Literal["APPROVED_PLAN_PHASE"]
    phase_id: str
    phase_fingerprint: str

    @field_validator("phase_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "phase_id")

    @field_validator("phase_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fp(value, "phase_fingerprint")


class StageReference(Fingerprinted):
    stage_id: str
    plan_reference: PlanReference
    source_reference: StageSourceReference

    @field_validator("stage_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "stage_id")

    @model_validator(mode="after")
    def _bound(self) -> StageReference:
        if self.stage_id != self.source_reference.phase_id:
            raise ValueError("stage source identity mismatch")
        return self


class CoordinationMetadata(Fingerprinted):
    reference_id: str

    @field_validator("reference_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "reference_id")


class EngineeringTaskCoordination(Fingerprinted):
    task_identity: TaskIdentity
    request_reference: RequestReference
    project_reference: ProjectReference
    plan_reference: PlanReference
    current_stage_reference: StageReference
    coordination_metadata: CoordinationMetadata

    @model_validator(mode="after")
    def _bound(self) -> EngineeringTaskCoordination:
        task = self.task_identity
        if self.request_reference.task_id != task.task_id:
            raise ValueError("request identity mismatch")
        if (self.project_reference.tenant_id, self.project_reference.project_id) != (
            task.tenant_id, task.project_id
        ):
            raise ValueError("project identity mismatch")
        if self.current_stage_reference.plan_reference != self.plan_reference:
            raise ValueError("stage plan reference mismatch")
        return self


def validate_coordination(
    value: EngineeringTaskCoordination,
    source: EngineeringTaskCoordinationSourcePort,
) -> EngineeringTaskCoordination:
    """Validate references against a caller-bound, trusted, read-only source.

    The source must perform existing authorization before this call. Its bound
    identity cannot come from client reference fields. Missing or malformed
    source records fail closed; no alternate source or execution path is used.
    """

    try:
        return _validate_coordination(value, source)
    except (EngineeringTaskRejected, EngineeringTaskUnavailable):
        raise
    except (TypeError, ValueError):
        raise EngineeringTaskRejected("coordination reference rejected") from None


def _validate_coordination(
    value: EngineeringTaskCoordination,
    source: EngineeringTaskCoordinationSourcePort,
) -> EngineeringTaskCoordination:
    checked = validate_copy(value, EngineeringTaskCoordination)
    task = checked.task_identity
    try:
        bound_tenant = ident(source.bound_tenant_id, "bound_tenant_id")
        bound_project = ident(source.bound_project_id, "bound_project_id")
        ident(source.bound_principal_id, "bound_principal_id")
    except Exception:  # noqa: BLE001 - untrusted source getters may raise arbitrary errors
        raise EngineeringTaskUnavailable("coordination source unavailable") from None
    if (bound_tenant, bound_project) != (task.tenant_id, task.project_id):
        raise ValueError("bound identity mismatch")

    try:
        resolved_request = source.resolve_task_request(task.task_id)
    except Exception:  # noqa: BLE001 - source failures must not expose internal details
        raise EngineeringTaskUnavailable("coordination source unavailable") from None
    request = validate_copy(resolved_request, EngineeringTaskRequest)
    if (
        (request.task_id, request.tenant_id, request.project_id)
        != (task.task_id, task.tenant_id, task.project_id)
        or checked.request_reference.request_fingerprint != request.fingerprint
    ):
        raise ValueError("request reference mismatch")

    try:
        resolved = source.resolve_plan(task.task_id, checked.plan_reference.plan_id)
    except Exception:  # noqa: BLE001 - source failures must not expose internal details
        raise EngineeringTaskUnavailable("coordination source unavailable") from None
    if type(resolved) is not tuple or len(resolved) != 2:
        raise ValueError("approved plan source is unavailable")
    plan = validate_copy(resolved[0], EngineeringPlan)
    review = validate_copy(resolved[1], PlanReviewDecision)
    plan_ref = checked.plan_reference
    if (
        plan.status is not PlanStatus.APPROVED
        or review.status is not PlanReviewStatus.APPROVED
        or plan.plan_id != plan_ref.plan_id
        or plan.project_id != task.project_id
        or plan.content_fingerprint != plan_ref.content_fingerprint
        or review.plan_id != plan.plan_id
        or review.project_id != plan.project_id
        or review.proposal_fingerprint != plan.content_fingerprint
        or review.fingerprint != plan_ref.review_fingerprint
    ):
        raise ValueError("approved plan reference mismatch")

    stage = checked.current_stage_reference
    phase = next((item for item in plan.phases if item.phase_id == stage.stage_id), None)
    if (
        phase is None
        or phase.plan_id != plan.plan_id
        or phase.project_id != plan.project_id
        or stage.source_reference.phase_fingerprint != phase.fingerprint
    ):
        raise ValueError("stage source reference mismatch")
    return checked


__all__ = (
    "CoordinationMetadata",
    "EngineeringTaskCoordination",
    "PlanReference",
    "ProjectReference",
    "RequestReference",
    "StageReference",
    "StageSourceReference",
    "TaskIdentity",
    "validate_coordination",
)
