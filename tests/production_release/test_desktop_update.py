"""Offline, isolated installation and recovery contract tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.api.desktop_update_routes import router as update_router
from embedded_copilot.production_release.desktop_update import (
    DesktopUpdateService,
    UpdateRejected,
    active_execution_reason,
    installation_lock,
)
from embedded_copilot.production_release.update_switch import (
    _boot_confirmed,
    recover_interrupted,
    switch_after_exit,
)
from scripts.promote_embrix_initial_package import promote


def identity(build_id: str, version: str = "7.1.0") -> dict:
    return {
        "schema_version": 1, "product_name": "Embrix", "product_version": version,
        "build_id": build_id, "commit": "a" * 40, "dirty": False,
        "source_fingerprint": "b" * 64, "build_time": "2026-09-28T00:00:00+00:00",
        "mode": "development", "channel": "development", "platform": "windows",
        "architecture": "amd64",
    }


def app(root: Path, value: dict) -> None:
    internal = root / "Embrix" / "_internal"
    (internal / "frontend-dist").mkdir(parents=True)
    (root / "Embrix" / "Embrix.exe").write_bytes(b"exe")
    (root / "Embrix" / "EmbrixUpdater.exe").write_bytes(b"helper")
    for path in (internal / "build-identity.json", internal / "frontend-dist" / "build-identity.json"):
        path.write_text(json.dumps(value), encoding="utf-8")
    (root / "verification.json").write_text(json.dumps({
        "schema_version": 1, "build_id": value["build_id"], "verification": "NATIVE_ACCEPTED"
    }), encoding="utf-8")


def source(root: Path, value: dict, *, entries: dict[str, bytes] | None = None) -> Path:
    directory = root / "feed"
    packages = directory / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    archive = packages / "candidate.zip"
    files = entries or {
        "Embrix/Embrix.exe": b"exe", "Embrix/EmbrixUpdater.exe": b"helper",
        "Embrix/_internal/build-identity.json": json.dumps(value).encode(),
        "Embrix/_internal/frontend-dist/build-identity.json": json.dumps(value).encode(),
    }
    with zipfile.ZipFile(archive, "w") as output:
        for name, body in files.items():
            output.writestr(name, body)
    manifest = {
        "schema_version": 1, "product_name": "Embrix", "channel": value["channel"],
        "platform": value["platform"], "architecture": value["architecture"],
        "product_version": value["product_version"], "build_id": value["build_id"],
        "package": {"name": archive.name, "size": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "notes": ["local update"],
        "compatibility": {"app_min_version": "7.1.0", "config_schema_version": 1,
                          "data_schema": {"desktop": {"min_version": 2, "max_version": 2}}},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[DesktopUpdateService, Path, Path]:
    root = tmp_path / "install"
    app(root / "Embrix-current", identity("old-build"))
    data = tmp_path / "user-data" / "database.sqlite3"
    data.parent.mkdir()
    data.write_bytes(b"private-state")
    feed = source(tmp_path, identity("new-build"))
    service = DesktopUpdateService(install_root=root, source=feed, schema_versions={"desktop": 2},
                                   execution_database=tmp_path / "operations.sqlite3")
    return service, feed, data


def test_same_product_version_new_build_is_candidate_and_installed_identity_is_real(prepared) -> None:
    service, _, _ = prepared
    result = service.check()
    assert result["state"] == "CANDIDATE"
    assert result["installed_identity"]["build_id"] == "old-build"
    assert result["candidate"]["build_id"] == "new-build"
    staged = service.install(build_id="new-build", sha256=result["candidate"]["sha256"])
    assert staged["state"] == "WAIT_RESTART"
    assert staged["installed_verification"] == "NATIVE_ACCEPTED"
    assert staged["installed_identity"]["build_id"] == "old-build"
    assert (service.root / "Embrix-current" / "Embrix" / "Embrix.exe").is_file()


@pytest.mark.parametrize("terminal_state", ["BOOT_VERIFIED", "ROLLED_BACK"])
def test_completed_transaction_does_not_hide_next_candidate(prepared, terminal_state: str) -> None:
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    transaction = service._transaction()
    transaction["state"] = terminal_state
    transaction["error_code"] = "BOOT_TIMEOUT_OR_FAILED" if terminal_state == "ROLLED_BACK" else None
    service.transaction.write_text(json.dumps(transaction), encoding="utf-8")
    restarted = DesktopUpdateService(
        install_root=service.root,
        source=service.source,
        schema_versions=service.schema_versions,
        execution_database=service.execution_database,
    )

    assert restarted.status()["state"] == terminal_state
    result = restarted.check()
    assert result["state"] == "CANDIDATE"
    assert result["candidate"]["build_id"] == "new-build"
    assert result["error_code"] is None


def test_completed_transaction_does_not_hide_next_check_failure(prepared) -> None:
    service, feed, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    transaction = service._transaction()
    transaction["state"] = "ROLLED_BACK"
    transaction["error_code"] = "BOOT_TIMEOUT_OR_FAILED"
    service.transaction.write_text(json.dumps(transaction), encoding="utf-8")
    manifest_path = feed / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platform"] = "linux"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = service.check()
    assert result["state"] == "CHECK_FAILED"
    assert result["error_code"] == "TARGET_MISMATCH"


@pytest.mark.parametrize("change,code", [
    (lambda m: m.update(schema_version=2), "MANIFEST_INVALID"),
    (lambda m: m["package"].update(name="missing.zip"), "PACKAGE_MISSING"),
    (lambda m: m["package"].update(size=1), "PACKAGE_INTEGRITY_FAILED"),
    (lambda m: m["package"].update(sha256="0" * 64), "PACKAGE_INTEGRITY_FAILED"),
    (lambda m: m.update(platform="linux"), "TARGET_MISMATCH"),
    (lambda m: m["compatibility"]["data_schema"]["desktop"].update(max_version=1), "INCOMPATIBLE_DATA"),
    (lambda m: m.update(product_version="7.0.0"), "DOWNGRADE_REJECTED"),
])
def test_invalid_candidate_fails_closed(prepared, change, code) -> None:
    service, feed, _ = prepared
    path = feed / "manifest.json"
    manifest = json.loads(path.read_text())
    change(manifest)
    path.write_text(json.dumps(manifest))
    if code in {"PACKAGE_INTEGRITY_FAILED"}:
        candidate = service.check()["candidate"]
        assert candidate is not None
        with pytest.raises(UpdateRejected, match=code):
            service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    else:
        assert service.check()["error_code"] == code
    assert not (service.root / ".update" / "transaction.json").exists()


def test_confirmation_rejects_changed_candidate(prepared) -> None:
    service, feed, _ = prepared
    candidate = service.check()["candidate"]
    manifest_path = feed / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["build_id"] = "replacement"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(UpdateRejected, match="CANDIDATE_CHANGED"):
        service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])


def test_archive_escape_and_repeated_install_leave_current_unchanged(prepared) -> None:
    service, _, data = prepared
    result = service.check()
    service.install(build_id="new-build", sha256=result["candidate"]["sha256"])
    with pytest.raises(UpdateRejected, match="UPDATE_ALREADY_STAGED"):
        service.install(build_id="new-build", sha256=result["candidate"]["sha256"])
    assert data.read_bytes() == b"private-state"
    assert service._installed()["build_id"] == "old-build"


def test_archive_escape_rejected(prepared) -> None:
    service, feed, _ = prepared
    bad = source(feed.parent, identity("new-build"), entries={"Embrix/../outside.txt": b"bad"})
    service.source = bad
    candidate = service.check()["candidate"]
    with pytest.raises(UpdateRejected, match="PACKAGE_PATH_REJECTED"):
        service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    assert service._installed()["build_id"] == "old-build"


def test_concurrent_lock_and_active_operation_block_restart(prepared) -> None:
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    with installation_lock(service.root), pytest.raises(UpdateRejected, match="UPDATE_BUSY"):
        service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    database = service.execution_database
    assert database is not None
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE v66_objects(kind TEXT, body TEXT)")
        connection.execute("INSERT INTO v66_objects VALUES('status', ?)", (json.dumps({"status": "RUNNING"}),))
    with pytest.raises(UpdateRejected, match="ACTIVE_ENGINEERING_OPERATION"):
        service.restart_allowed()


def test_restart_allows_empty_optional_execution_store(prepared) -> None:
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    database = service.execution_database
    assert database is not None
    database.touch()

    service.restart_allowed()
    assert service._transaction()["state"] == "WAIT_RESTART"


def test_execution_store_missing_status_table_with_other_data_fails_closed(prepared) -> None:
    service, _, _ = prepared
    database = service.execution_database
    assert database is not None
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unexpected_state(value TEXT)")

    with pytest.raises(UpdateRejected, match="TASK_STATUS_UNAVAILABLE"):
        active_execution_reason(database)


def test_interrupted_switch_restores_old_current_and_preserves_user_data(prepared) -> None:
    service, _, data = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    transaction = service._transaction()
    rollback = service.root / ".update" / f"rollback-{transaction['transaction_id']}"
    (service.root / "Embrix-current").rename(rollback)
    transaction["state"] = "SWITCHING"
    service.transaction.write_text(json.dumps(transaction))
    assert recover_interrupted(service.root)["state"] == "ROLLED_BACK"
    assert service._installed()["build_id"] == "old-build"
    assert data.read_bytes() == b"private-state"


@pytest.mark.parametrize("body", [
    {"schema_version": 1},
    {"schema_version": 1, "state": "SWITCHING", "stage": "../../outside"},
])
def test_corrupt_transaction_fails_closed_without_touching_current(prepared, body) -> None:
    service, _, data = prepared
    service.transaction.parent.mkdir(parents=True, exist_ok=True)
    service.transaction.write_text(json.dumps(body))
    with pytest.raises(UpdateRejected, match="UPDATE_TRANSACTION_INVALID"):
        recover_interrupted(service.root)
    with pytest.raises(UpdateRejected, match="UPDATE_TRANSACTION_INVALID"):
        service.status()
    assert service._installed()["build_id"] == "old-build"
    assert data.read_bytes() == b"private-state"


def test_boot_marker_requires_launched_pid_and_expected_build(prepared) -> None:
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    transaction = service._transaction()
    marker = service.root / ".update" / f"boot-{transaction['transaction_id']}.json"
    marker.write_text(json.dumps({"pid": 22, "transaction_id": transaction["transaction_id"],
                                  "build_id": "old-build", "frontend_identity": "old-build",
                                  "backend_ready": True, "storage_compatible": True}))
    assert not _boot_confirmed(service.root, transaction, 22)
    assert not _boot_confirmed(service.root, transaction, 23)


def test_boot_failure_restores_previous_verified_current(prepared, monkeypatch) -> None:
    service, _, data = prepared
    app(service.root / "Embrix-previous", identity("older-build"))
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    monkeypatch.setattr("embedded_copilot.production_release.update_switch._pid_alive", lambda pid: False)

    class FailedProcess:
        pid = 12345

        def poll(self):
            return 1

    monkeypatch.setattr("embedded_copilot.production_release.update_switch.subprocess.Popen", lambda *args, **kwargs: FailedProcess())
    result = switch_after_exit(service.root, old_pid=99999, boot_timeout=0)
    assert result["state"] == "ROLLED_BACK"
    assert service._installed()["build_id"] == "old-build"
    assert json.loads((service.root / "Embrix-previous" / "Embrix" / "_internal" / "build-identity.json").read_text())["build_id"] == "older-build"
    assert data.read_bytes() == b"private-state"


def test_bound_boot_confirmation_moves_verified_old_current_to_previous(prepared, monkeypatch) -> None:
    service, _, data = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    monkeypatch.setattr("embedded_copilot.production_release.update_switch._pid_alive", lambda pid: False)
    monkeypatch.setattr("embedded_copilot.production_release.update_switch._boot_confirmed", lambda *args: True)

    class RunningProcess:
        pid = 45678

        def poll(self):
            return None

    monkeypatch.setattr("embedded_copilot.production_release.update_switch.subprocess.Popen", lambda *args, **kwargs: RunningProcess())
    result = switch_after_exit(service.root, old_pid=99999)
    assert result["state"] == "BOOT_VERIFIED"
    assert service._installed()["build_id"] == "new-build"
    assert service.status()["installed_verification"] == "BOOT_VERIFIED"
    assert service.status()["previous_verification"] == "NATIVE_ACCEPTED"
    assert json.loads((service.root / "Embrix-previous" / "Embrix" / "_internal" / "build-identity.json").read_text())["build_id"] == "old-build"
    assert data.read_bytes() == b"private-state"


def test_interrupted_finalization_keeps_confirmed_new_build_and_old_previous(prepared) -> None:
    service, _, data = prepared
    app(service.root / "Embrix-previous", identity("older-build"))
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    transaction = service._transaction()
    rollback = service.root / ".update" / f"rollback-{transaction['transaction_id']}"
    current = service.root / "Embrix-current"
    current.rename(rollback)
    (service.root / "_staging" / transaction["stage"]).rename(current)
    (current / "verification.json").write_text(json.dumps({
        "schema_version": 1, "build_id": "new-build", "verification": "BOOT_VERIFIED"
    }))
    transaction["state"] = "FINALIZING"
    service.transaction.write_text(json.dumps(transaction))
    assert recover_interrupted(service.root)["state"] == "BOOT_VERIFIED"
    assert service.status()["installed_identity"]["build_id"] == "new-build"
    assert service.status()["previous_identity"]["build_id"] == "old-build"
    assert data.read_bytes() == b"private-state"


def test_desktop_session_routes_expose_real_candidate_and_reject_active_restart(prepared) -> None:
    service, _, _ = prepared
    application = FastAPI()
    application.include_router(update_router)
    application.state.desktop_update_service = service
    application.dependency_overrides[desktop_identity] = lambda: TrustedDesktopIdentity(
        requester="local", tenant_id="desktop", authentication="TEST"
    )
    client = TestClient(application)
    assert client.get("/api/v1/copilot/desktop/update/status").json()["state"] == "UNCHECKED"
    candidate = client.post("/api/v1/copilot/desktop/update/check").json()["candidate"]
    response = client.post("/api/v1/copilot/desktop/update/install", json={
        "build_id": candidate["build_id"], "sha256": candidate["sha256"]
    })
    assert response.status_code == 200
    assert response.json()["state"] == "WAIT_RESTART"
    database = service.execution_database
    assert database is not None
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE v66_objects(kind TEXT, body TEXT)")
        connection.execute("INSERT INTO v66_objects VALUES('status', ?)", (json.dumps({"status": "RUNNING"}),))
    blocked = client.post("/api/v1/copilot/desktop/update/restart")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "ACTIVE_ENGINEERING_OPERATION"


def test_initial_promotion_requires_native_record_and_never_overwrites_current(tmp_path: Path) -> None:
    root = tmp_path / "install"
    staged = root / "_staging" / "candidate"
    app(staged, identity("first-build"))
    (staged / "EmbrixLauncher.exe").write_bytes(b"launcher")
    acceptance = tmp_path / "native.json"
    acceptance.write_text(json.dumps({"build_id": "wrong", "status": "PASS",
                                      "native_ui_automation": "PASS", "app_visible": "PASS",
                                      "restart_smoke": "PASS"}))
    with pytest.raises(UpdateRejected, match="NATIVE_ACCEPTANCE_INVALID"):
        promote(staged, root, acceptance)
    assert staged.is_dir()
    acceptance.write_text(json.dumps({"build_id": "first-build", "status": "PASS",
                                      "native_ui_automation": "PASS", "app_visible": "PASS",
                                      "restart_smoke": "PASS"}))
    assert promote(staged, root, acceptance) == root / "Embrix-current"
    assert (root / "EmbrixLauncher.exe").read_bytes() == b"launcher"
    another = root / "_staging" / "another"
    app(another, identity("second-build"))
    (another / "EmbrixLauncher.exe").write_bytes(b"new-launcher")
    acceptance.write_text(json.dumps({"build_id": "second-build", "status": "PASS",
                                      "native_ui_automation": "PASS", "app_visible": "PASS",
                                      "restart_smoke": "PASS"}))
    with pytest.raises(UpdateRejected, match="INITIAL_INSTALL_ALREADY_EXISTS"):
        promote(another, root, acceptance)


def test_restart_route_starts_detached_helper_then_requests_native_close(prepared, monkeypatch) -> None:
    service, _, _ = prepared
    candidate = service.check()["candidate"]
    service.install(build_id=candidate["build_id"], sha256=candidate["sha256"])
    calls: list[list[str]] = []
    monkeypatch.setattr("embedded_copilot.production_release.desktop_update_process_adapter.subprocess.Popen",
                        lambda command, **kwargs: calls.append(command))

    class NativeHost:
        closed = False

        def request_close(self):
            self.closed = True

    host = NativeHost()
    from types import SimpleNamespace

    from embedded_copilot.production_release.desktop_update_process_adapter import (
        FixedUpdaterProcessAdapter,
    )
    from embedded_copilot.production_release.desktop_update_restart import (
        DesktopUpdateRestart,
    )
    class OfflineAdmission:
        def acquire(self):
            return SimpleNamespace(abort=lambda: None)
    service._restart_coordinator = DesktopUpdateRestart(
        service=service, launcher=FixedUpdaterProcessAdapter(),
        admission=OfflineAdmission(), request_close=host.request_close,
    )
    application = FastAPI()
    application.include_router(update_router)
    application.state.desktop_update_service = service
    application.state.native_desktop_host = host
    application.dependency_overrides[desktop_identity] = lambda: TrustedDesktopIdentity(
        requester="local", tenant_id="desktop", authentication="TEST"
    )
    response = TestClient(application).post("/api/v1/copilot/desktop/update/restart")
    assert response.status_code == 200
    assert response.json()["state"] == "WAIT_RESTART"
    assert len(calls) == 1 and Path(calls[0][0]).parent == service.root / ".update"
    assert host.closed
