from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v70_desktop_launcher.py"
    spec = importlib.util.spec_from_file_location("v70_native_launcher_modes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_mode_defaults_to_native_shell() -> None:
    launcher = _launcher_module()
    args = launcher._parse_args(())
    assert args.browser is False
    assert args.debug is False


def test_browser_mode_is_explicit_only() -> None:
    launcher = _launcher_module()
    assert launcher._parse_args(("--browser",)).browser is True


def test_runtime_start_is_idempotent(tmp_path: Path) -> None:
    from embedded_copilot.core.config import Settings
    from embedded_copilot.production_release.credentials import (
        InMemoryCredentialProvider,
    )
    from embedded_copilot.production_release.runtime import ProductionReleaseRuntime

    credentials = InMemoryCredentialProvider()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state", _env_file=None),
        credentials=credentials,
    )
    calls = 0
    original = runtime.migrations.prepare

    def counted_prepare():
        nonlocal calls
        calls += 1
        return original()

    runtime.migrations.prepare = counted_prepare  # type: ignore[method-assign]
    runtime.start()
    runtime.start()
    assert calls == 1


def test_execution_worker_store_failure_does_not_block_project_catalog_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embedded_copilot.production_release import migration

    original_version = migration.MigrationRegistry._version

    def fail_execution_store(database: Path) -> int:
        if Path(database).name == "desktop-v66.sqlite3":
            raise OSError("execution store unavailable")
        return original_version(database)

    monkeypatch.setattr(
        migration.MigrationRegistry,
        "_version",
        staticmethod(fail_execution_store),
    )

    from embedded_copilot.core.config import Settings
    from embedded_copilot.production_release.runtime import ProductionReleaseRuntime

    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            _env_file=None,
        )
    )

    runtime.start()

    execution_status = next(
        status
        for status in runtime.migrations.statuses()
        if status.identity == "execution-worker"
    )
    assert execution_status.status == "UNAVAILABLE"
    assert execution_status.core is False
