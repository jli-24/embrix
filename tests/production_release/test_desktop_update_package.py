from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_embrix_update_package as publisher
from embedded_copilot.production_release.desktop_update import (
    DesktopUpdateService,
    UpdateRejected,
)
from embedded_copilot.production_release.paths import (
    default_development_update_source,
    default_install_root,
)
from scripts.bootstrap_embrix_install import bootstrap
from scripts.build_embrix_update_package import publish_update_package


def _candidate(tmp_path: Path) -> tuple[Path, Path]:
    staging = tmp_path / "dist/_staging/run-1"
    app = staging / "Embrix"
    internal = app / "_internal"
    internal.mkdir(parents=True)
    (app / "Embrix.exe").write_bytes(b"application")
    (app / "EmbrixLauncher.exe").write_bytes(b"launcher")
    (app / "EmbrixUpdater.exe").write_bytes(b"updater")
    identity = {"schema_version": 1, "product_name": "Embrix", "product_version": "7.1.0", "build_id": "build-a", "commit": None, "dirty": True, "source_fingerprint": "a" * 64, "build_time": "2026-09-28T00:00:00+00:00", "mode": "development", "channel": "development", "platform": "windows", "architecture": "amd64"}
    (internal / "build-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    frontend = internal / "frontend-dist"
    frontend.mkdir()
    (frontend / "build-identity.json").write_text(json.dumps(identity), encoding="utf-8")
    record = tmp_path / "native-acceptance.json"
    record.write_text(json.dumps({"build_id": "build-a", "status": "PASS", "native_ui_automation": "PASS", "app_visible": "PASS", "restart_smoke": "PASS"}), encoding="utf-8")
    return staging, record


def test_publisher_requires_matching_native_acceptance(tmp_path: Path) -> None:
    staging, record = _candidate(tmp_path)
    record.write_text(json.dumps({"build_id": "build-old", "status": "PASS"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Native acceptance"):
        publish_update_package(staging, tmp_path / "updates", record, notes="Test", data_schema={"desktop": {"min_version": 0, "max_version": 1}})
    assert not (tmp_path / "updates").exists()


def test_publisher_archives_complete_app_and_publishes_manifest_last(tmp_path: Path) -> None:
    staging, record = _candidate(tmp_path)
    manifest = publish_update_package(staging, tmp_path / "updates", record, notes="Fix", data_schema={"desktop": {"min_version": 0, "max_version": 1}})
    package = manifest.parent / "packages" / manifest_data(manifest)["package"]["name"]
    data = manifest_data(manifest)
    assert package.stat().st_size == data["package"]["size"]
    assert hashlib.sha256(package.read_bytes()).hexdigest() == data["package"]["sha256"]
    assert data["compatibility"] == {"app_min_version": "7.1.0", "data_schema": {"desktop": {"min_version": 0, "max_version": 1}}, "config_schema_version": 1}
    assert data["notes"] == ["Fix"]
    with zipfile.ZipFile(package) as archive:
        assert "Embrix/Embrix.exe" in archive.namelist()
        assert "Embrix/EmbrixLauncher.exe" in archive.namelist()
        assert "Embrix/EmbrixUpdater.exe" in archive.namelist()
        assert "Embrix/_internal/frontend-dist/build-identity.json" in archive.namelist()


def manifest_data(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_canonical_install_and_development_feed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_install_root() == tmp_path / "Programs" / "Embedded Copilot"
    assert default_development_update_source() == default_install_root() / "update-source" / "development"


def test_frozen_runtime_uses_canonical_feed_with_explicit_override(monkeypatch, tmp_path: Path) -> None:
    from embedded_copilot.production_release import desktop_update

    executable = tmp_path / "install" / "Embrix-current" / "Embrix" / "Embrix.exe"
    monkeypatch.setattr(desktop_update.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_update.sys, "executable", str(executable))
    monkeypatch.delenv("EMBRIX_LOCAL_UPDATE_SOURCE", raising=False)
    runtime = SimpleNamespace(
        migrations=SimpleNamespace(statuses=list),
        settings=SimpleNamespace(desktop_state_database=tmp_path / "state" / "desktop.sqlite3"),
    )
    missing = DesktopUpdateService.for_runtime(runtime)
    assert missing is not None
    assert missing.source is None
    (executable.parents[2] / "update-source" / "development").mkdir(parents=True)
    service = DesktopUpdateService.for_runtime(runtime)
    assert service is not None
    assert service.source == executable.parents[2] / "update-source" / "development"
    override = tmp_path / "other-feed"
    monkeypatch.setenv("EMBRIX_LOCAL_UPDATE_SOURCE", str(override))
    overridden = DesktopUpdateService.for_runtime(runtime)
    assert overridden is not None
    assert overridden.source == override


def test_bootstrap_copies_accepted_stage_and_never_changes_external_source(tmp_path: Path) -> None:
    staging, record = _candidate(tmp_path)
    (staging / "EmbrixLauncher.exe").write_bytes(b"launcher")
    install = tmp_path / "install"
    current = bootstrap(staging, record, install_root=install)
    assert current == install / "Embrix-current"
    assert (current / "Embrix" / "Embrix.exe").read_bytes() == b"application"
    assert (install / "EmbrixLauncher.exe").read_bytes() == b"launcher"
    assert (current / "verification.json").exists()
    assert (staging / "Embrix" / "Embrix.exe").exists()
    with pytest.raises(UpdateRejected, match="INITIAL_INSTALL_ALREADY_EXISTS"):
        bootstrap(staging, record, install_root=install)


def test_bootstrap_rejects_mismatch_and_symlink_without_install(tmp_path: Path) -> None:
    staging, record = _candidate(tmp_path)
    (staging / "EmbrixLauncher.exe").write_bytes(b"launcher")
    install = tmp_path / "install"
    record.write_text(json.dumps({"build_id": "different", "status": "PASS", "native_ui_automation": "PASS", "app_visible": "PASS", "restart_smoke": "PASS"}))
    with pytest.raises(UpdateRejected, match="NATIVE_ACCEPTANCE_INVALID"):
        bootstrap(staging, record, install_root=install)
    assert not (install / "Embrix-current").exists()
    record.write_text(json.dumps({"build_id": "build-a", "status": "PASS", "native_ui_automation": "PASS", "app_visible": "PASS", "restart_smoke": "PASS"}))
    original_is_symlink = Path.is_symlink
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "is_symlink", lambda path: path == staging or original_is_symlink(path))
        with pytest.raises(UpdateRejected, match="PACKAGE_SYMLINK_REJECTED"):
            bootstrap(staging, record, install_root=install)
    assert not (install / "Embrix-current").exists()


def test_bootstrap_refuses_existing_launcher_or_nonstaging_source(tmp_path: Path) -> None:
    staging, record = _candidate(tmp_path)
    (staging / "EmbrixLauncher.exe").write_bytes(b"launcher")
    install = tmp_path / "install"
    install.mkdir()
    (install / "EmbrixLauncher.exe").write_bytes(b"existing")
    with pytest.raises(UpdateRejected, match="INITIAL_INSTALL_ALREADY_EXISTS"):
        bootstrap(staging, record, install_root=install)
    assert (install / "EmbrixLauncher.exe").read_bytes() == b"existing"
    assert not (install / "_staging").exists()
    with pytest.raises(UpdateRejected, match="STAGING_LOCATION_INVALID"):
        bootstrap(staging / "Embrix", record, install_root=tmp_path / "other-install")


def test_bootstrap_rejects_changed_copy_before_promotion(tmp_path: Path, monkeypatch) -> None:
    import scripts.bootstrap_embrix_install as bootstrap_module

    staging, record = _candidate(tmp_path)
    (staging / "EmbrixLauncher.exe").write_bytes(b"launcher")
    install = tmp_path / "install"
    original_copytree = bootstrap_module.shutil.copytree

    def corrupt_copy(source: Path, destination: Path, *args, **kwargs) -> Path:
        copied = original_copytree(source, destination, *args, **kwargs)
        if source == staging:
            (destination / "Embrix" / "Embrix.exe").write_bytes(b"corrupt")
        return copied

    monkeypatch.setattr(bootstrap_module.shutil, "copytree", corrupt_copy)
    with pytest.raises(UpdateRejected, match="PACKAGE_COPY_MISMATCH"):
        bootstrap(staging, record, install_root=install)
    assert not (install / "Embrix-current").exists()
    assert not (install / "EmbrixLauncher.exe").exists()


def test_publisher_keeps_existing_manifest_when_published_archive_corrupts(tmp_path: Path, monkeypatch) -> None:
    staging, record = _candidate(tmp_path)
    feed = tmp_path / "updates"
    feed.mkdir()
    manifest = feed / "manifest.json"
    manifest.write_bytes(b"old-manifest")
    original_replace = publisher.os.replace

    def corrupt_after_package(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination.suffix == ".zip":
            destination.write_bytes(b"corrupt")

    monkeypatch.setattr(publisher.os, "replace", corrupt_after_package)
    with pytest.raises(ValueError, match="integrity"):
        publish_update_package(staging, feed, record, notes="Fix", data_schema={"desktop": {"min_version": 0, "max_version": 1}})
    assert manifest.read_bytes() == b"old-manifest"
