"""Opt-in guard for the approved S02 offline verification commands only."""
import subprocess
import sys
from pathlib import Path

import pytest

_guard = pytest.MonkeyPatch()


def pytest_configure():
    # Install before collection/imports; fixtures additionally protect app aliases.
    from embedded_copilot import execution_worker
    from embedded_copilot.execution_worker import kicad_toolchain

    _guard.setattr(kicad_toolchain, "discover_kicad_cli", lambda **kwargs: None)
    _guard.setattr(execution_worker, "discover_kicad_cli", lambda **kwargs: None)


def pytest_unconfigure():
    _guard.undo()


@pytest.fixture(autouse=True)
def prevent_real_eda_and_provider_processes(monkeypatch):
    from embedded_copilot.api import main
    # Formal offline composition returns unavailable for unconfigured real tools.
    monkeypatch.setattr(main, "discover_kicad_cli", lambda **kwargs: None)
    real_popen = subprocess.Popen
    def python_process_only(args, *rest, **kwargs):
        executable = args[0] if isinstance(args, (list, tuple)) and args else ""
        if not executable or Path(executable).resolve() != Path(sys.executable).resolve():
            raise AssertionError("S02 offline guard forbids real EDA/provider/native process")
        return real_popen(args, *rest, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", python_process_only)
