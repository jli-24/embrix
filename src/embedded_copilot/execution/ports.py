from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import ExecutionRequest, ExecutionResult
from .integration_contracts import (
    BuildRequest,
    BuildResult,
    DebugObservation,
    DebugRequest,
    FlashRequest,
    FlashResult,
)


@runtime_checkable
class BuildPort(Protocol):
    def build(
        self, request: BuildRequest | ExecutionRequest
    ) -> BuildResult | ExecutionResult: ...


@runtime_checkable
class FlashPort(Protocol):
    def flash(
        self, request: FlashRequest | ExecutionRequest
    ) -> FlashResult | ExecutionResult: ...


@runtime_checkable
class DebugPort(Protocol):
    def collect_logs(
        self, request: DebugRequest | ExecutionRequest
    ) -> DebugObservation | ExecutionResult: ...


@runtime_checkable
class CancellationPort(Protocol):
    def is_cancelled(self, cancellation_token: str) -> bool: ...


@runtime_checkable
class WorkspaceResolverPort(Protocol):
    def resolve(self, snapshot: object) -> ResolvedWorkspace: ...


class ResolvedWorkspace:
    """Private filesystem capability returned only by a trusted resolver."""

    __slots__ = ("_assert_current", "_root")

    def __init__(self, root: Path, assert_current) -> None:
        self._root = root
        self._assert_current = assert_current

    @property
    def root(self) -> Path:
        return self._root

    def assert_current(self) -> None:
        self._assert_current()


__all__ = (
    "BuildPort",
    "CancellationPort",
    "DebugPort",
    "FlashPort",
    "ResolvedWorkspace",
    "WorkspaceResolverPort",
)
