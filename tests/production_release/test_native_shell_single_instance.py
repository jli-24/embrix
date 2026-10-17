from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from embedded_copilot.native_shell.single_instance import SingleInstanceGuard
import embedded_copilot.production_release.session as production_session
import embedded_copilot.native_shell.single_instance as single_instance


def test_single_instance_names_are_user_scoped_and_not_global() -> None:
    guard = SingleInstanceGuard()
    assert guard.mutex_name.startswith("Local\\EmbeddedCopilot-")
    assert not guard.mutex_name.startswith("Global\\")
    assert guard.activation_name.startswith("Local\\EmbeddedCopilot-activation-")


def test_formal_state_keeps_legacy_single_instance_namespace() -> None:
    guard = SingleInstanceGuard()

    assert "-state-" not in guard.mutex_name
    assert "-state-" not in guard.activation_name
    assert "-state-" not in guard.resolved_status_path.name


def test_pid_nonexistent_is_dead(monkeypatch) -> None:
    monkeypatch.setattr(single_instance.os, "getpid", lambda: 100)

    def raise_process_lookup(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(single_instance.os, "kill", raise_process_lookup)

    assert SingleInstanceGuard._pid_alive(4242) is False


def test_pid_permission_error_is_alive_or_unknown(monkeypatch) -> None:
    monkeypatch.setattr(single_instance.os, "getpid", lambda: 100)

    def raise_permission(_pid, _signal):
        raise PermissionError

    monkeypatch.setattr(single_instance.os, "kill", raise_permission)

    assert SingleInstanceGuard._pid_alive(4242) is True


def test_pid_system_error_is_stale_and_dead(monkeypatch) -> None:
    monkeypatch.setattr(single_instance.os, "getpid", lambda: 100)

    def raise_system_error(_pid, _signal):
        raise SystemError

    monkeypatch.setattr(single_instance.os, "kill", raise_system_error)

    assert SingleInstanceGuard._pid_alive(4242) is False


def test_pid_os_error_is_dead_or_unknown(monkeypatch) -> None:
    monkeypatch.setattr(single_instance.os, "getpid", lambda: 100)

    def raise_os_error(_pid, _signal):
        raise OSError

    monkeypatch.setattr(single_instance.os, "kill", raise_os_error)

    assert SingleInstanceGuard._pid_alive(4242) is False


def test_ready_existing_instance_blocks_recovery(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "instance-status.json"
    status_path.write_text(
        json.dumps({"pid": 4242, "state": "READY", "port": 43123}),
        encoding="utf-8",
    )
    monkeypatch.setattr(single_instance.os, "name", "nt")
    monkeypatch.setattr(single_instance.os, "kill", lambda pid, sig: None)

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    class Kernel32:
        CreateMutexW = Function(lambda *_args: 100)

        def GetLastError(self):
            return single_instance.ERROR_ALREADY_EXISTS

        def CloseHandle(self, _handle):
            return 1

    monkeypatch.setattr(single_instance.ctypes, "windll", SimpleNamespace(kernel32=Kernel32()), raising=False)
    guard = SingleInstanceGuard(
        status_path=status_path,
        health_probe=lambda port: port == 43123,
    )

    assert guard.acquire() is False
    assert guard.recovered_stale is False


def test_stale_ready_instance_is_recovered(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "instance-status.json"
    status_path.write_text(
        json.dumps({"pid": 4242, "state": "READY", "port": 43123}),
        encoding="utf-8",
    )
    monkeypatch.setattr(single_instance.os, "name", "nt")

    def raise_system_error(_pid, _signal):
        raise SystemError

    monkeypatch.setattr(single_instance.os, "kill", raise_system_error)

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    class Kernel32:
        CreateMutexW = Function(lambda *_args: 100)

        def GetLastError(self):
            return single_instance.ERROR_ALREADY_EXISTS

        def CloseHandle(self, _handle):
            return 1

    monkeypatch.setattr(single_instance.ctypes, "windll", SimpleNamespace(kernel32=Kernel32()), raising=False)
    guard = SingleInstanceGuard(status_path=status_path)

    assert guard.acquire() is True
    assert guard.recovered_stale is True
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["state"] == "STARTING"
    guard.close()


def test_unready_existing_instance_is_recovered_without_kill(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "instance-status.json"
    status_path.write_text(
        json.dumps({"pid": 4242, "state": "STARTING", "port": None}),
        encoding="utf-8",
    )
    monkeypatch.setattr(single_instance.os, "name", "nt")

    class Function:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    class Kernel32:
        CreateMutexW = Function(lambda *_args: 100)

        def GetLastError(self):
            return single_instance.ERROR_ALREADY_EXISTS

        def CloseHandle(self, _handle):
            return 1

    monkeypatch.setattr(single_instance.ctypes, "windll", SimpleNamespace(kernel32=Kernel32()), raising=False)
    guard = SingleInstanceGuard(status_path=status_path)

    assert guard.acquire() is True
    assert guard.recovered_stale is True
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["state"] == "STARTING"
    assert payload["pid"] != 4242
    guard.close()


def test_state_identity_scopes_mutex_activation_and_status_namespace(tmp_path) -> None:
    guard_a = SingleInstanceGuard(state_identity=tmp_path / "state-a")
    guard_b = SingleInstanceGuard(state_identity=tmp_path / "state-b")

    assert guard_a.mutex_name != guard_b.mutex_name
    assert guard_a.activation_name != guard_b.activation_name
    assert guard_a.resolved_status_path != guard_b.resolved_status_path
    assert guard_a.resolved_status_path.parent == guard_b.resolved_status_path.parent

    shared_override = tmp_path / "shared-status.json"
    assert SingleInstanceGuard(status_path=shared_override, state_identity="state-a").resolved_status_path != (
        SingleInstanceGuard(status_path=shared_override, state_identity="state-b").resolved_status_path
    )


def test_state_status_namespace_does_not_cross_read(tmp_path) -> None:
    guard_a = SingleInstanceGuard(state_identity=tmp_path / "state-a")
    guard_b = SingleInstanceGuard(state_identity=tmp_path / "state-b")

    assert guard_a._claim_status("READY", 12345) is True
    try:
        assert guard_a._read_status() == {
            "pid": os.getpid(),
            "state": "READY",
            "port": 12345,
        }
        assert guard_b._read_status() is None
    finally:
        guard_a.resolved_status_path.unlink(missing_ok=True)


def test_two_disposable_states_can_bootstrap_independently(tmp_path) -> None:
    guard_a = SingleInstanceGuard(state_identity=tmp_path / "state-a")
    guard_b = SingleInstanceGuard(state_identity=tmp_path / "state-b")

    assert guard_a.acquire() is True
    try:
        assert guard_b.acquire() is True
    finally:
        guard_b.close()
        guard_a.close()


def test_session_nonce_and_token_are_process_bound() -> None:
    manager_a = production_session.LocalSessionManager()
    manager_b = production_session.LocalSessionManager()
    nonce = manager_a.issue_bootstrap(
        requester="desktop-user", tenant_id="desktop-tenant"
    )

    with pytest.raises(production_session.SessionRejected):
        manager_b.exchange(nonce)

    token = manager_a.exchange(nonce)
    with pytest.raises(production_session.SessionRejected):
        manager_b.authenticate(token)
