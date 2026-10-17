"""Controlled lifecycle transitions."""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from embedded_copilot.engineering_runtime.objects.models import (
    EngineeringObject,
    LifecycleStatus,
)


class LifecycleActor(str, Enum):
    AGENT = "AGENT"
    RUNTIME = "RUNTIME"
    REVIEWER = "REVIEWER"
    DEPENDENCY_ENGINE = "DEPENDENCY_ENGINE"


class LifecycleViolation(ValueError):
    pass


class LifecycleManager:
    _transitions: ClassVar[
        dict[tuple[LifecycleStatus, LifecycleStatus], frozenset[LifecycleActor]]
    ] = {
        (LifecycleStatus.DRAFT, LifecycleStatus.REVIEW): frozenset(
            {LifecycleActor.RUNTIME}
        ),
        (LifecycleStatus.REVIEW, LifecycleStatus.APPROVED): frozenset(
            {LifecycleActor.REVIEWER}
        ),
        (LifecycleStatus.REVIEW, LifecycleStatus.REJECTED): frozenset(
            {LifecycleActor.REVIEWER}
        ),
        (LifecycleStatus.APPROVED, LifecycleStatus.ACTIVE): frozenset(
            {LifecycleActor.RUNTIME}
        ),
        (LifecycleStatus.ACTIVE, LifecycleStatus.STALE): frozenset(
            {LifecycleActor.DEPENDENCY_ENGINE}
        ),
        (LifecycleStatus.ACTIVE, LifecycleStatus.SUPERSEDED): frozenset(
            {LifecycleActor.RUNTIME, LifecycleActor.DEPENDENCY_ENGINE}
        ),
        (LifecycleStatus.STALE, LifecycleStatus.SUPERSEDED): frozenset(
            {LifecycleActor.RUNTIME, LifecycleActor.DEPENDENCY_ENGINE}
        ),
    }

    def can_transition(
        self,
        current: LifecycleStatus,
        target: LifecycleStatus,
        actor: LifecycleActor,
    ) -> bool:
        return actor != LifecycleActor.AGENT and actor in self._transitions.get(
            (current, target), frozenset()
        )

    def transition(
        self,
        obj: EngineeringObject,
        target: LifecycleStatus,
        actor: LifecycleActor,
    ) -> EngineeringObject:
        if actor == LifecycleActor.AGENT:
            raise LifecycleViolation("Agent cannot mutate lifecycle state")
        if not self.can_transition(obj.status, target, actor):
            raise LifecycleViolation(
                f"invalid lifecycle transition: {obj.status} -> {target} by {actor}"
            )
        return obj.model_copy(update={"status": target})
