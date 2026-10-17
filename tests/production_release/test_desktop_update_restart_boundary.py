from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

from embedded_copilot.production_release.desktop_update import UpdateRejected
from tests.production_release.test_desktop_update import prepared as _shared_prepared

prepared = _shared_prepared


def _coordinator(prepared, *, admission=True, fail_launch=False, fail_close=False, timer_factory=None):
    from embedded_copilot.production_release.desktop_update_restart import (
        DesktopUpdateRestart,
    )
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    calls = []
    class Process:
        def stop(self):
            calls.append("stop")
    class Launcher:
        def launch(self, plan):
            calls.append(("launch", plan.transaction_id, plan.helper_sha256))
            if fail_launch:
                raise OSError("private diagnostic")
            return Process()
    class Admission:
        def acquire(self):
            calls.append("acquire")
            return SimpleNamespace(abort=lambda: calls.append("abort"))
    def close():
        calls.append("close")
        if fail_close:
            raise OSError("private close diagnostic")
    kwargs = {"timer_factory": timer_factory} if timer_factory is not None else {}
    coordinator = DesktopUpdateRestart(service=service, launcher=Launcher(),
                                       admission=Admission() if admission else None,
                                       request_close=close, **kwargs)
    return service, coordinator, calls




def test_restart_without_trusted_admission_never_launches_or_closes(prepared):
    service, coordinator, calls = _coordinator(prepared, admission=False)
    with pytest.raises(UpdateRejected, match="TASK_STATUS_UNAVAILABLE"):
        coordinator.restart(lambda _: pytest.fail("close must not schedule"))
    assert calls == []
    assert service.status()["state"] == "WAIT_RESTART"


def test_restart_validates_archive_and_prevents_duplicate_start(prepared):
    _, coordinator, calls = _coordinator(prepared)
    scheduled = []
    coordinator.restart(scheduled.append)
    with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
        coordinator.restart(scheduled.append)
    assert len([x for x in calls if isinstance(x, tuple)]) == 1
    assert len(scheduled) == 1
    scheduled[0]()
    assert calls[-1] == "close"
    assert "abort" not in calls


def test_changed_helper_never_launches_or_closes(prepared):
    service, coordinator, calls = _coordinator(prepared)
    tx = service._transaction()
    helper = service.root / "_staging" / tx["stage"] / "Embrix" / "EmbrixUpdater.exe"
    helper.write_bytes(b"tamper")
    with pytest.raises(UpdateRejected):
        coordinator.restart(lambda _: pytest.fail("close must not schedule"))
    assert not [x for x in calls if isinstance(x, tuple)]
    assert "close" not in calls
    assert calls[-1] == "abort"


@pytest.mark.parametrize("phase", ["launch", "schedule", "close"])
def test_restart_failures_abort_lease_and_stop_only_owned_helper(prepared, phase):
    _, coordinator, calls = _coordinator(prepared, fail_launch=phase == "launch", fail_close=phase == "close")
    def schedule(callback):
        if phase == "schedule":
            raise RuntimeError("queue failed")
        callback()
    with pytest.raises(UpdateRejected, match="UPDATER_START_FAILED"):
        coordinator.restart(schedule)
    assert ("abort" in calls) == (phase != "close")
    assert ("stop" in calls) == (phase != "launch")
    if phase == "close":
        with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
            coordinator.restart(lambda _: None)


def test_production_factory_cannot_synthesize_an_admission_guard(prepared):
    from embedded_copilot.production_release.desktop_update_restart import (
        production_restart,
    )
    service, _, _ = prepared
    coordinator = production_restart(service)
    with pytest.raises(UpdateRejected, match="TASK_STATUS_UNAVAILABLE"):
        coordinator.restart(lambda _: pytest.fail("no native close"))


class _ManualTimer:
    instances: ClassVar[list[_ManualTimer]] = []

    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


def test_restart_timeout_stops_owned_helper_and_late_close_is_inert(prepared):
    _, coordinator, calls = _coordinator(prepared, timer_factory=_ManualTimer)
    scheduled = []
    coordinator.restart(scheduled.append)
    timer = _ManualTimer.instances[-1]
    assert 0 < timer.interval <= 20
    timer.fire()
    assert "stop" in calls and "abort" in calls
    scheduled[0]()
    assert "close" not in calls


def test_scheduler_failure_cannot_later_close_host(prepared):
    _, coordinator, calls = _coordinator(prepared)
    saved = []
    def schedule(callback):
        saved.append(callback)
        raise RuntimeError("saved then failed")
    with pytest.raises(UpdateRejected):
        coordinator.restart(schedule)
    saved[0]()
    assert "close" not in calls


def test_concurrent_restart_has_one_launch(prepared):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _, coordinator, calls = _coordinator(prepared)
    entered, resume = Event(), Event()
    original = coordinator._launcher.launch
    def launch(plan):
        entered.set()
        assert resume.wait(5)
        return original(plan)
    coordinator._launcher.launch = launch
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(coordinator.restart, lambda callback: callback())
        assert entered.wait(5)
        try:
            with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
                coordinator.restart(lambda _: None)
        finally:
            resume.set()
        future.result(timeout=5)
    assert len([x for x in calls if isinstance(x, tuple)]) == 1


def test_adapter_start_failure_removes_only_its_new_copy_and_allows_retry(prepared, monkeypatch):
    from embedded_copilot.production_release.desktop_update_process_adapter import (
        FixedUpdaterProcessAdapter,
    )
    service, coordinator, calls = _coordinator(prepared)
    coordinator._launcher = FixedUpdaterProcessAdapter()
    def failed(*args, **kwargs):
        raise OSError("private launch failure")
    monkeypatch.setattr("embedded_copilot.production_release.desktop_update_process_adapter.subprocess.Popen", failed)
    for _ in range(2):
        with pytest.raises(UpdateRejected, match="UPDATER_START_FAILED"):
            coordinator.restart(lambda _: pytest.fail("no close"))
        tx = service._transaction()
        assert not (service.root / ".update" / ("updater-" + tx["transaction_id"] + ".exe")).exists()
    assert calls.count("abort") == 2


def test_adapter_never_removes_preexisting_detached_file(prepared):
    from embedded_copilot.production_release.desktop_update_process_adapter import (
        FixedUpdaterProcessAdapter,
    )
    service, coordinator, _ = _coordinator(prepared)
    coordinator._launcher = FixedUpdaterProcessAdapter()
    tx = service._transaction()
    existing = service.root / ".update" / ("updater-" + tx["transaction_id"] + ".exe")
    existing.write_bytes(b"existing user-owned bytes")
    with pytest.raises(UpdateRejected):
        coordinator.restart(lambda _: pytest.fail("no close"))
    assert existing.read_bytes() == b"existing user-owned bytes"


def test_uncertain_cleanup_keeps_admission_closed(prepared):
    _, coordinator, calls = _coordinator(prepared)
    original = coordinator._launcher.launch
    def launch(plan):
        process = original(plan)
        def stop():
            raise OSError("cannot prove stopped")
        process.stop = stop
        return process
    coordinator._launcher.launch = launch
    with pytest.raises(UpdateRejected, match="UPDATER_START_FAILED"):
        coordinator.restart(lambda _: (_ for _ in ()).throw(RuntimeError("queue")))
    assert "abort" not in calls
    with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
        coordinator.restart(lambda _: None)


def test_updater_start_error_preserves_http_500_contract():
    from embedded_copilot.api.desktop_update_routes import _rejected
    assert _rejected(UpdateRejected("UPDATER_START_FAILED")).status_code == 500


def test_hung_close_timeout_keeps_admission_closed_after_owned_cleanup(prepared):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _, coordinator, calls = _coordinator(prepared, timer_factory=_ManualTimer)
    entered, resume = Event(), Event()
    original = coordinator._close
    def close():
        entered.set()
        assert resume.wait(5)
        original()
    coordinator._close = close
    saved = []
    coordinator.restart(saved.append)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(saved[0])
        assert entered.wait(5)
        try:
            _ManualTimer.instances[-1].fire()
            assert "stop" in calls
            assert "abort" not in calls
            with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
                coordinator.restart(lambda _: None)
        finally:
            resume.set()
        future.result(timeout=5)
    assert "abort" not in calls


def test_owned_copy_cleanup_rejects_replaced_identity(tmp_path):
    from embedded_copilot.production_release.desktop_update_process_adapter import (
        _OwnedCopy,
    )
    path = tmp_path / "owned-helper.exe"
    path.write_bytes(b"partial copy")
    metadata = path.stat()
    owned = _OwnedCopy(path, (metadata.st_dev, metadata.st_ino))
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement user bytes")
    replacement.replace(path)
    with pytest.raises(UpdateRejected):
        owned.remove()
    assert path.read_bytes() == b"replacement user bytes"


def test_restart_close_failure_after_close_requested_keeps_admission_closed(prepared):
    _, coordinator, calls = _coordinator(prepared, timer_factory=_ManualTimer)
    def close_then_fail():
        calls.append("close-requested")
        raise RuntimeError("host may close later")
    coordinator._close = close_then_fail
    with pytest.raises(UpdateRejected, match="UPDATER_START_FAILED"):
        coordinator.restart(lambda callback: callback())
    assert "stop" in calls
    assert "abort" not in calls
    with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
        coordinator.restart(lambda _: None)


def test_restart_schedule_failure_after_synchronous_close_keeps_admission_closed(prepared):
    _, coordinator, calls = _coordinator(prepared, timer_factory=_ManualTimer)
    def schedule(callback):
        callback()
        raise RuntimeError("queue reports late failure")
    with pytest.raises(UpdateRejected, match="UPDATER_START_FAILED"):
        coordinator.restart(schedule)
    assert "close" in calls
    assert "stop" in calls
    assert "abort" not in calls
    with pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
        coordinator.restart(lambda _: None)
