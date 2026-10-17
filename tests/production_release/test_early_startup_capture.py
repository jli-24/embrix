from __future__ import annotations

import builtins
import importlib.util
import json
import sys
import types
from pathlib import Path


def _entry_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v71_desktop_launcher.py"
    spec = importlib.util.spec_from_file_location("v71_early_startup_capture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v70_desktop_launcher.py"
    spec = importlib.util.spec_from_file_location("v70_early_startup_capture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original = sys.modules.get("embedded_copilot.api.main")
    stub = types.ModuleType("embedded_copilot.api.main")
    stub.create_app = lambda **kwargs: None
    sys.modules["embedded_copilot.api.main"] = stub
    try:
        spec.loader.exec_module(module)
    finally:
        if original is None:
            sys.modules.pop("embedded_copilot.api.main", None)
        else:
            sys.modules["embedded_copilot.api.main"] = original
    return module


def test_early_startup_event_contains_only_safe_fields(tmp_path: Path, monkeypatch) -> None:
    launcher = _launcher_module()
    monkeypatch.setattr(launcher, "_early_startup_log_paths", lambda: (tmp_path,))

    launcher._early_startup_event(
        "E3_BEFORE_PRODUCTION_PREFLIGHT",
        "FAIL",
        PermissionError("credential=secret prompt=private path=C:/private"),
        reason_code="PRODUCTION_PREFLIGHT_FAILED",
        exit_code=1,
    )

    event_path = tmp_path / "early-startup-diagnostic.log"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["stage"] == "E3_BEFORE_PRODUCTION_PREFLIGHT"
    assert event["status"] == "FAIL"
    assert event["exception_type"] == "PermissionError"
    assert len(event["exception_hash"]) == 16
    assert event["exception_module"] == "builtins"
    assert event["exception_function"] == "-"
    assert event["exception_line"] == "-"
    assert event["traceback_hash"] == "-"
    assert event["reason_code"] == "PRODUCTION_PREFLIGHT_FAILED"
    assert event["exit_code"] == 1
    serialized = json.dumps(event)
    assert "credential" not in serialized
    assert "prompt" not in serialized
    assert "private" not in serialized


def test_entrypoint_captures_safe_exception_origin_fields(tmp_path: Path, monkeypatch) -> None:
    launcher = _entry_module()
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path))

    class SyntheticSystemError(SystemError):
        pass

    try:
        raise SyntheticSystemError("credential=secret prompt=private")
    except SyntheticSystemError as error:
        launcher._early_entry_event("E1_LAUNCHER_INIT", "FAIL", error, reason_code="TEST")

    event_path = tmp_path / "Embedded Copilot" / "logs" / "early-startup-diagnostic.log"
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["exception_type"] == "SyntheticSystemError"
    assert event["exception_module"] == __name__
    assert event["exception_function"] == "test_entrypoint_captures_safe_exception_origin_fields"
    assert isinstance(event["exception_line"], int)
    assert len(event["traceback_hash"]) == 16
    serialized = json.dumps(event)
    assert "credential" not in serialized
    assert "prompt" not in serialized


def test_early_startup_event_is_best_effort(monkeypatch) -> None:
    launcher = _launcher_module()

    def unavailable_paths():
        raise OSError("diagnostic sink unavailable")

    monkeypatch.setattr(launcher, "_early_startup_log_paths", unavailable_paths)
    launcher._early_startup_event("E2_BEFORE_LOGGING_INIT", "ENTER")


def test_entrypoint_captures_launcher_import_failure(tmp_path: Path, monkeypatch) -> None:
    launcher = _entry_module()
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(launcher.tempfile, "gettempdir", lambda: str(tmp_path))
    original_import = builtins.__import__

    def fail_launcher_import(name, *args, **kwargs):
        if name == "v70_desktop_launcher":
            raise ModuleNotFoundError("launcher unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_launcher_import)
    assert launcher.main() == 1

    event_path = tmp_path / "Embedded Copilot" / "logs" / "early-startup-diagnostic.log"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["stage"] == "E0_PROCESS_ENTRY"
    assert events[-1]["status"] == "FAIL"
    assert events[-1]["reason_code"] == "LAUNCHER_IMPORT_FAILED"
    assert events[-1]["exit_code"] == 1


def test_earliest_capture_covers_process_and_launcher_boundaries() -> None:
    scripts_root = Path(__file__).resolve().parents[2] / "scripts"
    v70_source = (scripts_root / "v70_desktop_launcher.py").read_text(encoding="utf-8")
    v71_source = (scripts_root / "v71_desktop_launcher.py").read_text(encoding="utf-8")
    combined = f"{v70_source}\n{v71_source}"

    for stage in (
        "E0_PROCESS_ENTRY",
        "E1_LAUNCHER_INIT",
        "E1_ENTER",
        "E1_SINGLE_INSTANCE",
        "E1_LOGGING_INIT",
        "E1_TELEMETRY_INIT",
        "E1_RUNTIME_IMPORT",
        "E1_BACKEND_PREPARE",
        "E2_BEFORE_LOGGING_INIT",
        "E3_BEFORE_PRODUCTION_PREFLIGHT",
        "E4_AFTER_PRODUCTION_PREFLIGHT",
    ):
        assert stage in combined
