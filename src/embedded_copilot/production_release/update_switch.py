"""Small platform install lifecycle used by the out-of-process updater."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .desktop_update import (
    UpdateRejected,
    _atomic_json,
    _package_identity,
    installation_lock,
    slot_identity,
    validated_transaction,
)


def _transaction(root: Path) -> dict:
    path = root / ".update" / "transaction.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateRejected("UPDATE_TRANSACTION_INVALID") from error
    if not isinstance(value, dict):
        raise UpdateRejected("UPDATE_TRANSACTION_INVALID")
    return validated_transaction(value)


def _save(root: Path, value: dict) -> None:
    _atomic_json(root / ".update" / "transaction.json", value)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _recover_locked(root: Path, transaction: dict, *, reason: str) -> dict:
    state = transaction["state"]
    if state in {"WAIT_RESTART", "BOOT_VERIFIED", "ROLLED_BACK"}:
        return transaction
    current = root / "Embrix-current"
    rollback = root / ".update" / f"rollback-{transaction['transaction_id']}"
    failed = root / "_staging" / f"failed-{transaction['transaction_id']}"
    previous = root / "Embrix-previous"
    history = root / ".update" / f"previous-{transaction['transaction_id']}"
    if rollback.is_dir():
        if current.exists():
            if failed.exists():
                raise UpdateRejected("UPDATE_RECOVERY_CONFLICT")
            current.rename(failed)
        rollback.rename(current)
    elif not current.is_dir():
        raise UpdateRejected("UPDATE_RECOVERY_CONFLICT")
    if not previous.exists() and history.is_dir():
        history.rename(previous)
    transaction["state"] = "ROLLED_BACK"
    transaction["error_code"] = reason
    _save(root, transaction)
    return transaction


def _finalize_locked(root: Path, transaction: dict) -> dict:
    current, current_verification = slot_identity(root, "Embrix-current")
    if current is None or current.get("build_id") != transaction["build_id"] or current_verification != "BOOT_VERIFIED":
        raise UpdateRejected("UPDATE_FINALIZE_CONFLICT")
    previous = root / "Embrix-previous"
    history = root / ".update" / f"previous-{transaction['transaction_id']}"
    rollback = root / ".update" / f"rollback-{transaction['transaction_id']}"
    if rollback.is_dir():
        if previous.exists():
            if history.exists():
                raise UpdateRejected("UPDATE_FINALIZE_CONFLICT")
            previous.rename(history)
        rollback.rename(previous)
    old_identity, old_verification = slot_identity(root, "Embrix-previous")
    if old_identity is None or old_identity.get("build_id") != transaction["old_build_id"] or old_verification == "UNVERIFIED":
        raise UpdateRejected("UPDATE_FINALIZE_CONFLICT")
    transaction["state"] = "BOOT_VERIFIED"
    transaction["error_code"] = None
    _save(root, transaction)
    return transaction


def recover_interrupted(root: Path) -> dict | None:
    """Fixed launcher restores an interrupted switch before choosing current."""
    path = root / ".update" / "transaction.json"
    if not path.exists():
        return None
    with installation_lock(root):
        transaction = _transaction(root)
        if transaction["state"] in {"SWITCHING", "BOOTING"}:
            return _recover_locked(root, transaction, reason="UPDATE_INTERRUPTED")
        if transaction["state"] == "FINALIZING":
            return _finalize_locked(root, transaction)
        return transaction


def mark_boot_ready(root: Path, *, transaction_id: str, build_id: str,
                    assets: Path, migration_statuses: tuple[object, ...]) -> bool:
    """Bind backend, frontend and store readiness to this launched process."""
    transaction = _transaction(root)
    if transaction.get("state") != "BOOTING" or transaction.get("transaction_id") != transaction_id:
        return False
    if transaction.get("build_id") != build_id:
        return False
    identity = _package_identity(root / "Embrix-current" / "Embrix")
    if identity.get("build_id") != build_id:
        return False
    try:
        frontend = json.loads((assets / "build-identity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if frontend != identity or any(getattr(item, "status", None) != "READY" and getattr(item, "core", False)
                                   for item in migration_statuses):
        return False
    _atomic_json(root / ".update" / f"boot-{transaction_id}.json", {
        "transaction_id": transaction_id, "build_id": build_id, "pid": os.getpid(),
        "backend_ready": True, "frontend_identity": build_id, "storage_compatible": True,
    })
    return True


def _boot_confirmed(root: Path, transaction: dict, pid: int) -> bool:
    path = root / ".update" / f"boot-{transaction['transaction_id']}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (value.get("pid") == pid and value.get("transaction_id") == transaction["transaction_id"]
            and value.get("build_id") == transaction["build_id"] and value.get("backend_ready") is True
            and value.get("frontend_identity") == transaction["build_id"]
            and value.get("storage_compatible") is True)


def switch_after_exit(root: Path, *, old_pid: int, exit_timeout: float = 30,
                      boot_timeout: float = 90) -> dict:
    """Run in the copied helper, never in the application being replaced."""
    root = root.resolve()
    deadline = time.monotonic() + exit_timeout
    while _pid_alive(old_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_alive(old_pid):
        raise UpdateRejected("APPLICATION_STILL_RUNNING")
    with installation_lock(root):
        transaction = _transaction(root)
        if transaction["state"] != "WAIT_RESTART":
            raise UpdateRejected("UPDATE_NOT_STAGED")
        current = root / "Embrix-current"
        stage = root / "_staging" / transaction["stage"]
        rollback = root / ".update" / f"rollback-{transaction['transaction_id']}"
        if not current.is_dir() or not stage.is_dir() or rollback.exists():
            raise UpdateRejected("UPDATE_LAYOUT_INVALID")
        if _package_identity(current / "Embrix").get("build_id") != transaction["old_build_id"]:
            raise UpdateRejected("CURRENT_CHANGED")
        if slot_identity(root, "Embrix-current")[1] == "UNVERIFIED":
            raise UpdateRejected("CURRENT_NOT_VERIFIED")
        if _package_identity(stage / "Embrix").get("build_id") != transaction["build_id"]:
            raise UpdateRejected("STAGED_CHANGED")
        transaction["state"] = "SWITCHING"
        _save(root, transaction)
        try:
            current.rename(rollback)
            stage.rename(current)
            transaction["state"] = "BOOTING"
            _save(root, transaction)
        except OSError:
            _recover_locked(root, transaction, reason="SWITCH_FAILED")
            raise
        marker = root / ".update" / f"boot-{transaction['transaction_id']}.json"
        if marker.exists():
            marker.unlink()
        environment = os.environ.copy()
        environment["EMBRIX_UPDATE_TRANSACTION_ID"] = transaction["transaction_id"]
        environment["EMBRIX_EXPECTED_BUILD_ID"] = transaction["build_id"]
        try:
            process = subprocess.Popen([str(current / "Embrix" / "Embrix.exe")], cwd=current / "Embrix", env=environment)
        except OSError:
            _recover_locked(root, transaction, reason="BOOT_LAUNCH_FAILED")
            raise
        deadline = time.monotonic() + boot_timeout
        confirmed = False
        while time.monotonic() < deadline and process.poll() is None:
            if _boot_confirmed(root, transaction, process.pid):
                confirmed = True
                break
            time.sleep(0.2)
        if not confirmed:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            _recover_locked(root, transaction, reason="BOOT_TIMEOUT_OR_FAILED")
            subprocess.Popen([str(current / "Embrix" / "Embrix.exe")], cwd=current / "Embrix")
            return _transaction(root)
        # A marker written immediately before a crash is not a healthy launch.
        stable_until = time.monotonic() + 2.0
        while time.monotonic() < stable_until:
            if process.poll() is not None:
                _recover_locked(root, transaction, reason="BOOT_EXITED_EARLY")
                subprocess.Popen([str(current / "Embrix" / "Embrix.exe")], cwd=current / "Embrix")
                return _transaction(root)
            time.sleep(0.1)
        _atomic_json(current / "verification.json", {
            "schema_version": 1, "build_id": transaction["build_id"], "verification": "BOOT_VERIFIED"
        })
        transaction["state"] = "FINALIZING"
        _save(root, transaction)
        return _finalize_locked(root, transaction)


__all__ = ("mark_boot_ready", "recover_interrupted", "switch_after_exit")
