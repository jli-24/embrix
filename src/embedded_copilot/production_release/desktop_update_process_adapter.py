"""Fixed updater lifecycle adapter. Never accepts public command/path inputs."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .desktop_update import UpdateRejected, _sha256
from .desktop_update_restart import _ordinary, _RestartPlan


class _OwnedCopy:
    """Only the file exclusively created by this launch may be removed."""

    def __init__(self, path: Path, identity: tuple[int, int]) -> None:
        self.path = path
        self.identity = identity
        self.digest: str | None = None

    def remove(self) -> None:
        _ordinary(self.path.parent)
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if (metadata.st_dev, metadata.st_ino) != self.identity:
            raise UpdateRejected("UPDATER_START_FAILED")
        _ordinary(self.path)
        if self.digest is not None and _sha256(self.path) != self.digest:
            raise UpdateRejected("UPDATER_START_FAILED")
        self.path.unlink()


class _UpdaterProcess:
    def __init__(self, process: subprocess.Popen, copied: _OwnedCopy) -> None:
        self._process = process
        self._copied = copied

    def stop(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        # Never delete a running helper or a pre-existing/replaced file.
        self._copied.remove()


class FixedUpdaterProcessAdapter:
    def launch(self, plan: _RestartPlan) -> _UpdaterProcess:
        if (type(plan) is not _RestartPlan or not plan.root.is_absolute()
                or plan.root.resolve() != plan.root
                or not re.fullmatch(r"[0-9a-f]{32}", plan.transaction_id)
                or not re.fullmatch(r"[0-9a-f]{64}", plan.helper_sha256)):
            raise UpdateRejected("UPDATE_LAYOUT_INVALID")
        stage = plan.root / "_staging" / ("update-" + plan.transaction_id) / "Embrix"
        helper = stage / "EmbrixUpdater.exe"
        directory = plan.root / ".update"
        for path in (plan.root, plan.root / "_staging", stage.parent, stage, helper, directory):
            _ordinary(path)
        if _sha256(helper) != plan.helper_sha256:
            raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
        detached = directory / ("updater-" + plan.transaction_id + ".exe")
        copied = None
        try:
            # Existing paths fail before ownership is established; never delete them.
            with helper.open("rb") as source, detached.open("xb") as target:
                metadata = os.fstat(target.fileno())
                copied = _OwnedCopy(detached, (metadata.st_dev, metadata.st_ino))
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(block)
            _ordinary(detached)
            copied.digest = _sha256(detached)
            if copied.digest != plan.helper_sha256:
                raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
            flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
            process = subprocess.Popen([str(detached), "--root", str(plan.root), "--old-pid", str(os.getpid())],
                                       cwd=directory, creationflags=flags,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return _UpdaterProcess(process, copied)
        except Exception:
            if copied is not None:
                copied.remove()
            raise
