from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from embedded_copilot.workspace_runtime import (
    FrozenWorkspaceSnapshot,
    WorkspaceInspectionRequest,
    WorkspacePort,
)

from .ports import ResolvedWorkspace, WorkspaceResolverPort


@dataclass(frozen=True)
class _RootIdentity:
    device: int
    inode: int
    mode: int

    @classmethod
    def capture(cls, path: Path) -> _RootIdentity:
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
            raise ValueError("workspace root is invalid")
        return cls(device=value.st_dev, inode=value.st_ino, mode=value.st_mode)


class RegisteredWorkspaceResolver(WorkspaceResolverPort):
    """Maps a registered WorkspacePort to its single preconfigured root."""

    __slots__ = ("_identity", "_port", "_root", "_workspace_id")

    def __init__(
        self, *, root: str | Path, workspace_id: str, port: WorkspacePort
    ) -> None:
        if not isinstance(port, WorkspacePort):
            raise TypeError("workspace port is invalid")
        candidate = Path(root)
        self._root = candidate.resolve(strict=True)
        self._identity = _RootIdentity.capture(self._root)
        self._workspace_id = workspace_id
        self._port = port

    def resolve(self, snapshot: object) -> ResolvedWorkspace:
        checked = FrozenWorkspaceSnapshot.model_validate(snapshot)
        if checked.workspace_id != self._workspace_id:
            raise ValueError("workspace identity rejected")
        self._assert_current()
        current = self._port.inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id=checked.workspace_id,
                relative_paths=tuple(item.relative_path for item in checked.files),
            )
        )
        if current.snapshot_fingerprint != checked.snapshot_fingerprint:
            raise ValueError("workspace snapshot rejected")
        self._assert_current()
        return ResolvedWorkspace(self._root, self._assert_current)

    def _assert_current(self) -> None:
        try:
            current = _RootIdentity.capture(self._root)
        except (OSError, ValueError):
            raise ValueError("workspace identity rejected") from None
        if current != self._identity:
            raise ValueError("workspace identity rejected")


__all__ = ("RegisteredWorkspaceResolver",)
