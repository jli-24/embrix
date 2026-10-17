from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import EngineeringPlan, EngineeringRequirement


@runtime_checkable
class EngineeringPlannerPort(Protocol):
    def plan(
        self, requirement: EngineeringRequirement, context: object
    ) -> EngineeringPlan: ...


__all__ = ("EngineeringPlannerPort",)
