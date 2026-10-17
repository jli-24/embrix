from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from embedded_copilot.execution import (
    BuildRequest,
    BuildResult,
    DebugObservation,
    DebugRequest,
    FlashRequest,
    FlashResult,
)

from .contracts import AutonomousEngineeringLoopSnapshot


@runtime_checkable
class LoopStatePort(Protocol):
    def get(self, loop_id: str) -> AutonomousEngineeringLoopSnapshot | None: ...

    def save(
        self, snapshot: AutonomousEngineeringLoopSnapshot
    ) -> AutonomousEngineeringLoopSnapshot: ...


@runtime_checkable
class ExecutionRetryPort(Protocol):
    def build(self, request: BuildRequest) -> BuildResult: ...

    def flash(self, request: FlashRequest) -> FlashResult: ...

    def collect_logs(self, request: DebugRequest) -> DebugObservation: ...


class InMemoryLoopState:
    def __init__(self) -> None:
        self._snapshots: dict[str, AutonomousEngineeringLoopSnapshot] = {}

    def get(self, loop_id: str) -> AutonomousEngineeringLoopSnapshot | None:
        value = self._snapshots.get(loop_id)
        return (
            None
            if value is None
            else AutonomousEngineeringLoopSnapshot.model_validate(
                copy.deepcopy(value.model_dump(mode="python"))
            )
        )

    def save(
        self, snapshot: AutonomousEngineeringLoopSnapshot
    ) -> AutonomousEngineeringLoopSnapshot:
        checked = AutonomousEngineeringLoopSnapshot.model_validate(
            copy.deepcopy(snapshot.model_dump(mode="python"))
        )
        self._snapshots[checked.loop_id] = checked
        return AutonomousEngineeringLoopSnapshot.model_validate(
            copy.deepcopy(checked.model_dump(mode="python"))
        )


__all__ = ("ExecutionRetryPort", "InMemoryLoopState", "LoopStatePort")
