from __future__ import annotations

import json
from pathlib import Path

from embedded_copilot.production_release.build_identity import (
    create_build_identity,
    read_build_identity,
    source_fingerprint,
)
from scripts import build_v71_windows_package as builder


def test_source_fingerprint_tracks_package_inputs_and_ignores_secrets(tmp_path: Path) -> None:
    for relative in ("src/app.py", "scripts/entry.py", "frontend/src/App.tsx"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("first", encoding="utf-8")
    secret = tmp_path / "frontend" / ".env"
    secret.write_text("secret-one", encoding="utf-8")
    before = source_fingerprint(tmp_path)
    secret.write_text("secret-two", encoding="utf-8")
    assert source_fingerprint(tmp_path) == before
    (tmp_path / "frontend/src/App.tsx").write_text("second", encoding="utf-8")
    assert source_fingerprint(tmp_path) != before


def test_build_identity_is_unique_and_reads_only_packaged_metadata(tmp_path: Path) -> None:
    first = create_build_identity(tmp_path, product_version="7.1.0")
    second = create_build_identity(tmp_path, product_version="7.1.0")
    assert first["build_id"] != second["build_id"]
    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert first["build_time"].endswith("+00:00")
    assert {"commit", "dirty", "mode", "channel", "platform", "architecture"} <= first.keys()
    package = tmp_path / "_internal"
    package.mkdir()
    (package / "build-identity.json").write_text(json.dumps(first), encoding="utf-8")
    assert read_build_identity(tmp_path) == first
    assert read_build_identity(tmp_path / "missing") is None


def test_builder_defaults_to_unique_staging_and_embeds_identical_identity(tmp_path: Path, monkeypatch) -> None:
    assert builder.DEFAULT_DIST.parent == builder.ROOT / "dist" / "_staging"
    assert builder.DEFAULT_DIST.name
    captured: dict[str, object] = {}

    def fake_subprocess(command, *, cwd, check):
        assert check is True
        captured["command"] = command
        identity_source = Path(command[command.index("--add-data") + 1].split(";", 1)[0])
        captured["identity"] = identity_source.read_bytes()
        frontend_source = next(
            Path(value.split(";", 1)[0]) for value in command
            if isinstance(value, str) and value.endswith(";frontend-dist")
        )
        assert (frontend_source / "build-identity.json").read_bytes() == captured["identity"]

    frontend = tmp_path / "frontend" / "dist"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "create_build_identity", lambda root, product_version: {"build_id": "test-build"})
    monkeypatch.setattr(builder.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(builder, "_build_lifecycle_executables", lambda **kwargs: None)
    assert builder._build_package(dist=tmp_path / "dist", work=tmp_path / "work", build_frontend=False) == tmp_path / "dist/Embrix/Embrix.exe"
    assert b'"build_id"' in captured["identity"]


def test_lifecycle_executables_are_bundled_into_staging_app(tmp_path: Path, monkeypatch) -> None:
    app = tmp_path / "dist/Embrix"
    app.mkdir(parents=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("embrix_update_helper.py", "embrix_fixed_launcher.py"):
        (scripts / name).write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        calls.append(command)
        assert check
        name = command[command.index("--name") + 1]
        (app / f"{name}.exe").write_bytes(name.encode())

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    builder._build_lifecycle_executables(dist=tmp_path / "dist", work=tmp_path / "work")
    assert {call[call.index("--name") + 1] for call in calls} == {"EmbrixLauncher", "EmbrixUpdater"}
    assert all("--onefile" in call for call in calls)
    assert (tmp_path / "dist/EmbrixLauncher.exe").read_bytes() == b"EmbrixLauncher"
    assert (app / "EmbrixUpdater.exe").read_bytes() == b"EmbrixUpdater"


def test_builder_refuses_to_overwrite_current(tmp_path: Path, monkeypatch) -> None:
    import pytest

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="current"):
        builder._build_package(dist=tmp_path / "dist/Embrix-current", work=tmp_path / "work", build_frontend=False)
