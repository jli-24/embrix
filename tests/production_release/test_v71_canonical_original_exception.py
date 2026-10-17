from __future__ import annotations

import importlib.util
import json
import logging
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v71_runtime_boundary_log_is_whitelisted_and_redacted(tmp_path: Path) -> None:
    launcher = _module("v71_runtime_boundary_test", "scripts/v71_desktop_launcher.py")
    target = logging.getLogger("embedded_copilot.runtime_diagnostics")
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        assert launcher._configure_runtime_boundary_telemetry(tmp_path) is True
        target.info(
            "application_runtime_outer_boundary_telemetry",
            extra={
                "event_name": "application_runtime_outer_boundary_telemetry",
                "stage": "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                "telemetry_status": "FAIL",
                "original_exception_type": "TypeError",
                "original_exception_hash": "a" * 64,
                "exception_layer": "APPLICATIONRUNTIME",
                "exception_stage": "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                "traceback_fingerprint": "b" * 64,
                "reason_code": "APPLICATION_UNAVAILABLE",
                "raw_prompt": "sensitive-prompt",
                "credential": "sensitive-credential",
            },
        )
        target.info("unrelated", extra={"event_name": "unrelated"})
        for handler in target.handlers:
            handler.flush()

        lines = (tmp_path / "runtime-boundary-telemetry.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload == {
            "event_name": "application_runtime_outer_boundary_telemetry",
            "exception_layer": "APPLICATIONRUNTIME",
            "exception_stage": "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
            "original_exception_hash": "a" * 64,
            "original_exception_type": "TypeError",
            "reason_code": "APPLICATION_UNAVAILABLE",
            "stage": "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
            "telemetry_status": "FAIL",
            "traceback_fingerprint": "b" * 64,
        }
        assert "sensitive" not in lines[0]
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)


def test_v71_validation_vector_is_persisted_with_fixed_whitelist(
    tmp_path: Path,
) -> None:
    launcher = _module("v71_validation_vector_test", "scripts/v71_desktop_launcher.py")
    target = logging.getLogger("embedded_copilot.runtime_diagnostics")
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        assert launcher._configure_runtime_boundary_telemetry(tmp_path) is True
        target.info(
            "model_request_validation_boundary_telemetry",
            extra={
                "event_name": "model_request_validation_boundary_telemetry",
                "validation_error_count": 1,
                "validation_errors": (
                    {
                        "location_hash": "a" * 64,
                        "error_type": "VALUE_ERROR",
                        "received_type": "DICT",
                        "message_code": "MODEL_REQUEST_SCHEMA_MISMATCH",
                        "input": "sensitive-input",
                    },
                ),
                "prompt": "sensitive-prompt",
                "context": "sensitive-context",
                "response": "sensitive-response",
                "credential": "sensitive-credential",
                "token": "sensitive-token",
            },
        )
        for handler in target.handlers:
            handler.flush()

        lines = (tmp_path / "runtime-boundary-telemetry.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "event_name": "model_request_validation_boundary_telemetry",
            "validation_error_count": 1,
            "validation_errors": [
                {
                    "error_type": "VALUE_ERROR",
                    "location_hash": "a" * 64,
                    "message_code": "MODEL_REQUEST_SCHEMA_MISMATCH",
                    "received_type": "DICT",
                }
            ],
        }
        assert "sensitive" not in lines[0]
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)


def test_canonical_module_hash_is_stable_and_detects_mismatch() -> None:
    alignment = _module(
        "v71_alignment_test", "scripts/v71_canonical_module_alignment.py"
    )
    first = compile("def value():\n    return 1\n", "one.py", "exec")
    same = compile("def value():\n    return 1\n", "different-path.py", "exec")
    changed = compile("def value():\n    return 2\n", "one.py", "exec")

    assert alignment.canonical_code_hash(first) == alignment.canonical_code_hash(same)
    assert alignment.canonical_code_hash(first) != alignment.canonical_code_hash(changed)
    assert alignment.manifest_hash({"example": first}) == alignment.manifest_hash(
        {"example": same}
    )
    assert "embedded_copilot.application.bindings" in alignment.TARGET_MODULES
    assert "embedded_copilot.production_release.provider" in alignment.TARGET_MODULES


def test_canonical_golden_freezes_project_request_and_budget_gate() -> None:
    golden = _module(
        "v71_canonical_golden_test",
        "scripts/v71_canonical_original_exception_golden.py",
    )
    assert golden.GOLDEN_QUESTION == "电竞智能小车工程分析"
    assert golden.PROVIDER_BUDGET == 1
    assert golden.request_id_hash("request-1") == golden.request_id_hash("request-1")
    assert golden.request_id_hash("request-1") != golden.request_id_hash("request-2")
    assert golden.project_is_canonical(
        {
            "project_id": "project-dynamic-isolated-id",
            "name": "4",
            "creation_mode": "CREATE_NEW",
            "project_type": "STM32",
        }
    )
    assert not golden.project_is_canonical(
        {
            "project_id": "project-dynamic-isolated-id",
            "name": "4",
            "creation_mode": "ATTACH_EXISTING",
            "project_type": "STM32",
        }
    )


def test_canonical_golden_collects_latest_redacted_validation_vector() -> None:
    golden = _module(
        "v71_canonical_validation_vector_test",
        "scripts/v71_canonical_original_exception_golden.py",
    )
    vector = golden._validation_vector(
        [
            {
                "event_name": "model_request_validation_boundary_telemetry",
                "validation_error_count": 1,
                "validation_errors": [
                    {
                        "location_hash": "a" * 64,
                        "error_type": "VALUE_ERROR",
                        "received_type": "DICT",
                        "message_code": "MODEL_REQUEST_SCHEMA_MISMATCH",
                        "input": "must-not-propagate",
                    }
                ],
            }
        ]
    )

    assert vector == {
        "validation_error_count": 1,
        "validation_errors": [
            {
                "location_hash": "a" * 64,
                "error_type": "VALUE_ERROR",
                "received_type": "DICT",
                "message_code": "MODEL_REQUEST_SCHEMA_MISMATCH",
            }
        ],
    }
    assert golden._validation_vector([]) == {
        "validation_error_count": 0,
        "validation_errors": [],
    }


def test_canonical_golden_reads_relative_trace_database(
    tmp_path: Path, monkeypatch,
) -> None:
    golden = _module(
        "v71_canonical_relative_database_test",
        "scripts/v71_canonical_original_exception_golden.py",
    )
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE desktop_chat_trace_events "
            "(trace_id TEXT, stage TEXT, event_id TEXT, body TEXT)"
        )
        connection.execute(
            "INSERT INTO desktop_chat_trace_events VALUES (?, ?, ?, ?)",
            ("trace-1", "UI_SUBMIT", "event-1", '{"stage":"UI_SUBMIT"}'),
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.chdir(tmp_path)

    assert golden._trace_rows(Path("state.sqlite3"), "trace-1") == [
        {"stage": "UI_SUBMIT"}
    ]
