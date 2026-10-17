"""Private host update coordination; never an engineering execution gateway."""
from __future__ import annotations

import hashlib
import logging
import math
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock, Timer
from typing import Protocol

from .desktop_update import (
    DesktopUpdateService,
    UpdateRejected,
    _sha256,
    installation_lock,
)


@dataclass(frozen=True)
class _RestartPlan:
    root: Path
    transaction_id: str
    helper_sha256: str


class _Watchdog(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


class _OwnedProcess(Protocol):
    def stop(self) -> None: ...


class _Launcher(Protocol):
    def launch(self, plan: _RestartPlan) -> _OwnedProcess: ...


class _AdmissionLease(Protocol):
    def abort(self) -> None: ...


class _Admission(Protocol):
    """Host-only gate: block engineering admission until exit; abort on failure only.

    Installation locks and a one-time idle query do not satisfy this contract.
    """

    def acquire(self) -> _AdmissionLease: ...


def _ordinary(path: Path) -> None:
    metadata = path.lstat()
    if path.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise UpdateRejected("UPDATE_LAYOUT_INVALID")


class DesktopUpdateRestart:
    def __init__(self, *, service: DesktopUpdateService, launcher: _Launcher,
                 admission: _Admission | None, request_close: Callable[[], None] | None,
                 timer_factory: Callable[[float, Callable[[], None]], _Watchdog] = Timer,
                 close_timeout: float = 20.0) -> None:
        self._service = service
        self._launcher = launcher
        self._admission = admission
        self._close = request_close
        if type(close_timeout) not in (int, float) or not math.isfinite(close_timeout) or not 0 < close_timeout <= 20:
            raise ValueError("restart timeout is invalid")
        self._timer_factory = timer_factory
        self._close_timeout = close_timeout
        self._in_flight = Lock()

    def _plan(self) -> _RestartPlan:
        service = self._service
        service.restart_allowed()
        transaction = service._transaction()
        if transaction is None:
            raise UpdateRejected("UPDATE_NOT_STAGED")
        manifest, archive = service._read_candidate()
        package = manifest["package"]
        if manifest["build_id"] != transaction["build_id"] or package["sha256"] != transaction["sha256"]:
            raise UpdateRejected("CANDIDATE_CHANGED")
        if not archive.is_file():
            raise UpdateRejected("PACKAGE_MISSING")
        if archive.stat().st_size != package["size"] or _sha256(archive) != transaction["sha256"]:
            raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
        root = service.root
        stage = root / "_staging" / transaction["stage"] / "Embrix"
        helper = stage / "EmbrixUpdater.exe"
        for path in (root, root / "_staging", stage.parent, stage, helper, root / ".update"):
            _ordinary(path)
        if not helper.is_file() or helper.resolve().parent != stage.resolve():
            raise UpdateRejected("UPDATER_MISSING")
        with zipfile.ZipFile(archive) as source:
            entries = [entry for entry in source.infolist() if entry.filename == "Embrix/EmbrixUpdater.exe"]
            if len(entries) != 1 or entries[0].is_dir() or not 0 < entries[0].file_size <= 1024**3:
                raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
            digest = hashlib.sha256()
            with source.open(entries[0]) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        expected = digest.hexdigest()
        if _sha256(helper) != expected:
            raise UpdateRejected("PACKAGE_INTEGRITY_FAILED")
        return _RestartPlan(root=root, transaction_id=transaction["transaction_id"], helper_sha256=expected)

    def restart(self, schedule: Callable[[Callable[[], None]], object]) -> dict:
        if self._admission is None or not callable(self._close):
            raise UpdateRejected("TASK_STATUS_UNAVAILABLE")
        if not self._in_flight.acquire(blocking=False):
            raise UpdateRejected("UPDATE_BUSY")
        lease = None
        process = None
        watchdog = None
        failed = False
        close_started = False
        close_finished = False
        state = RLock()

        def abort() -> None:
            nonlocal failed
            with state:
                if failed:
                    return
                failed = True
                if watchdog is not None:
                    watchdog.cancel()
                try:
                    if process is not None:
                        process.stop()
                    if close_started:
                        # A started close may still finish later after any failure.
                        return
                    if lease is not None:
                        lease.abort()
                except Exception:  # noqa: BLE001 - injected lifecycle boundary fails closed
                    # Uncertain cleanup must not reopen admission or allow retry.
                    raise UpdateRejected("UPDATER_START_FAILED") from None
                self._in_flight.release()

        def timed_out() -> None:
            with state:
                if close_finished:
                    return
            cleaned = True
            try:
                abort()
            except UpdateRejected:
                cleaned = False
            logging.getLogger(__name__).warning(
                "desktop_update_restart_timeout",
                extra={"event_name": "desktop_update_restart_timeout", "cleanup_confirmed": cleaned},
            )

        def close() -> None:
            nonlocal close_started, close_finished
            with state:
                if failed or close_started:
                    return
                close_started = True
            try:
                assert self._close is not None
                self._close()
            except Exception:  # noqa: BLE001 - injected lifecycle boundary fails closed
                abort()
                raise UpdateRejected("UPDATER_START_FAILED") from None
            with state:
                close_finished = True
                if watchdog is not None:
                    watchdog.cancel()

        try:
            lease = self._admission.acquire()
            if not callable(getattr(lease, "abort", None)):
                raise UpdateRejected("TASK_STATUS_UNAVAILABLE")
            with installation_lock(self._service.root):
                plan = self._plan()
                process = self._launcher.launch(plan)
                if not callable(getattr(process, "stop", None)):
                    raise UpdateRejected("UPDATER_START_FAILED")
            result = self._service.status()
            watchdog = self._timer_factory(self._close_timeout, timed_out)
            watchdog.daemon = True
            watchdog.start()
            schedule(close)
            return result
        except Exception as error:
            abort()
            if isinstance(error, UpdateRejected):
                raise
            raise UpdateRejected("UPDATER_START_FAILED") from None


def production_restart(service: DesktopUpdateService) -> DesktopUpdateRestart:
    from .desktop_update_process_adapter import FixedUpdaterProcessAdapter

    # No audited common host-close/admission fence currently exists.
    # Do not synthesize one from installation_lock or active_execution_reason.
    return DesktopUpdateRestart(service=service, launcher=FixedUpdaterProcessAdapter(),
                                admission=None, request_close=None)
