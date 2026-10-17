from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import (
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan as ApprovedEngineeringPlan,
)
from embedded_copilot.engineering_copilot.contracts import (
    PlanReviewDecision,
)

from .models import (
    EngineeringPlanStep,
    EngineeringStepResult,
    EngineeringTaskRequest,
)


@runtime_checkable
class EngineeringRoleExecutionPort(Protocol):
    """Narrow role boundary; it exposes neither repositories nor runtime internals."""

    def execute_step(
        self,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        engineering_context: EngineeringContextSnapshot | None,
        memory_context: tuple[EngineeringContextEvidence, ...],
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> EngineeringStepResult: ...


@runtime_checkable
class EngineeringTaskCoordinationSourcePort(Protocol):
    """Caller-bound, authorized read-only source; no production adapter is wired here.

    Its authenticated principal must be authorized for the requested task and
    project. The resolved plan must belong to that task's tenant/project in the
    existing authority; implementations must not merely echo client IDs.
    """

    @property
    def bound_tenant_id(self) -> str: ...

    @property
    def bound_project_id(self) -> str: ...

    @property
    def bound_principal_id(self) -> str: ...

    def resolve_task_request(self, task_id: str) -> EngineeringTaskRequest | None: ...

    def resolve_plan(
        self, task_id: str, plan_id: str
    ) -> tuple[ApprovedEngineeringPlan, PlanReviewDecision] | None: ...


__all__ = ("EngineeringRoleExecutionPort", "EngineeringTaskCoordinationSourcePort")
