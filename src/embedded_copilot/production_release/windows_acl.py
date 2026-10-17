"""Narrow Windows ACL support for production-owned SQLite state."""

from __future__ import annotations

import csv
import os
import re
import subprocess
from pathlib import Path


class WindowsAclRejected(OSError):
    """The current Windows user could not receive required state access."""


_SID_PATTERN = re.compile(r"S-\d+(?:-\d+)+")


def _system_binary(name: str) -> str:
    root = os.environ.get("SystemRoot")
    return str(Path(root) / "System32" / name) if root else name


def _current_user_sid() -> str:
    result = subprocess.run(
        (_system_binary("whoami.exe"), "/user", "/fo", "csv", "/nh"),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WindowsAclRejected("current Windows user identity is unavailable")
    for row in csv.reader(result.stdout.splitlines()):
        for value in row:
            match = _SID_PATTERN.fullmatch(value.strip())
            if match is not None:
                return match.group(0)
    raise WindowsAclRejected("current Windows user identity is invalid")


def _has_effective_write_access(target: Path) -> bool:
    """Avoid a WRITE_DAC request when the current token already can write."""

    return os.access(target, os.W_OK)


def ensure_current_user_modify(path: Path, *, inherit_to_children: bool = False) -> None:
    """Grant only the current Windows user Modify access to owned SQLite state.

    The directory grant propagates to future SQLite, WAL and SHM files.  File
    grants repair an existing target without changing its SQLite contents.
    Non-Windows deployments retain their native filesystem behavior.
    """

    if os.name != "nt":
        return
    target = Path(path)
    if not target.exists():
        raise WindowsAclRejected("ACL target does not exist")
    if _has_effective_write_access(target):
        return
    sid = _current_user_sid()
    permission = "(OI)(CI)(M)" if inherit_to_children else "(M)"
    result = subprocess.run(
        (
            _system_binary("icacls.exe"),
            str(target),
            "/grant:r",
            f"*{sid}:{permission}",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WindowsAclRejected("current Windows user cannot receive SQLite state access")


__all__ = ("WindowsAclRejected", "ensure_current_user_modify")
