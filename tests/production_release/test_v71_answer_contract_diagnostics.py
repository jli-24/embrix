from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v70_desktop_launcher.py"
    spec = importlib.util.spec_from_file_location("v71_answer_contract_diagnostics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_answer_contract_diagnostic_log_is_safe_and_whitelisted(tmp_path: Path) -> None:
    module = _launcher_module()
    target = logging.getLogger("embedded_copilot.desktop_copilot_chat.runtime")
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        module._configure_answer_contract_diagnostics(tmp_path)
        target.info(
            "desktop_runtime_failure_trace",
            extra={
                "event_name": "desktop_runtime_failure_trace",
                "route": "GREENFIELD_DISCOVERY",
                "evidence_count": 0,
                "gate_result": "FAIL_CLOSED",
                "model_gateway_invocation_count": 1,
                "provider_status": "RESPONSE_RECEIVED",
                "provider_response_present": True,
                "answer_validation_stage": "ENGINEERING_ANSWER",
                "proposed_requirements_count": 0,
                "open_questions_count": 2,
                "answer_text_present": True,
                "reason_code": "ANSWER_CONTRACT_MISMATCH",
                "terminal_event": "ANSWER_FAILED",
                "request_id": "request-sensitive",
                "conversation_id": "session-sensitive",
                "raw_prompt": "prompt-sensitive",
                "credential": "credential-sensitive",
                "nonce": "nonce-sensitive",
            },
        )
        target.info("unrelated_event", extra={"event_name": "unrelated_event"})
        for handler in target.handlers:
            handler.flush()

        lines = (tmp_path / "answer-contract-diagnostics.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "answer_text_present": True,
            "answer_validation_stage": "ENGINEERING_ANSWER",
            "event_name": "desktop_runtime_failure_trace",
            "evidence_count": 0,
            "gate_result": "FAIL_CLOSED",
            "model_gateway_invocation_count": 1,
            "open_questions_count": 2,
            "proposed_requirements_count": 0,
            "provider_response_present": True,
            "provider_status": "RESPONSE_RECEIVED",
            "reason_code": "ANSWER_CONTRACT_MISMATCH",
            "route": "GREENFIELD_DISCOVERY",
            "terminal_event": "ANSWER_FAILED",
        }
        assert "sensitive" not in lines[0]
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)


def test_provider_response_boundary_telemetry_is_safe_and_whitelisted(
    tmp_path: Path,
) -> None:
    module = _launcher_module()
    targets = tuple(
        logging.getLogger(name)
        for name in module._PROVIDER_RESPONSE_TELEMETRY_LOGGERS
    )
    previous = {target: (tuple(target.handlers), target.level) for target in targets}
    try:
        module._configure_provider_response_telemetry(tmp_path)
        targets[0].info(
            "provider_response_boundary_telemetry",
            extra={
                "event_name": "provider_response_boundary_telemetry",
                "response_stage": "R3_CONTENT_EXTRACTION",
                "telemetry_status": "PASS",
                "content_present": True,
                "content_type": "STRING",
                "content_length": 11,
                "raw_response": "sensitive",
                "prompt": "sensitive",
                "credential": "sensitive",
                "session": "sensitive",
            },
        )
        targets[1].info(
            "provider_response_boundary_telemetry",
            extra={
                "event_name": "provider_response_boundary_telemetry",
                "response_stage": "R5_GREENFIELD_CONTRACT_VALIDATE",
                "telemetry_status": "FAIL",
                "validation_status": "FAIL",
                "validation_error_code": "ANSWER_CONTRACT_MISMATCH",
                "answer_text": "sensitive",
            },
        )
        for target in targets:
            for handler in target.handlers:
                handler.flush()

        lines = (tmp_path / "provider-response-boundary-telemetry.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 2
        assert '"response_stage":"R3_CONTENT_EXTRACTION"' in lines[0]
        assert '"content_length":11' in lines[0]
        assert '"response_stage":"R5_GREENFIELD_CONTRACT_VALIDATE"' in lines[1]
        assert '"validation_error_code":"ANSWER_CONTRACT_MISMATCH"' in lines[1]
        assert all(
            value not in "\n".join(lines)
            for value in ("sensitive", "raw_response", "prompt", "credential", "session")
        )
    finally:
        for target in targets:
            for handler in tuple(target.handlers):
                if handler not in previous[target][0]:
                    handler.close()
                    target.removeHandler(handler)
            target.setLevel(previous[target][1])


def test_provider_request_boundary_telemetry_is_safe_and_whitelisted(
    tmp_path: Path,
) -> None:
    module = _launcher_module()
    targets = tuple(
        logging.getLogger(name)
        for name in module._PROVIDER_REQUEST_TELEMETRY_LOGGERS
    )
    previous = {target: (tuple(target.handlers), target.level) for target in targets}
    try:
        assert module._configure_provider_request_telemetry(tmp_path) is True
        targets[0].info(
            "provider_request_boundary_telemetry",
            extra={
                "event_name": "provider_request_boundary_telemetry",
                "telemetry_status": "PASS",
                "request_model": "deepseek-chat",
                "request_message_count": 2,
                "system_message_present": True,
                "system_prompt_length": 128,
                "user_message_present": True,
                "user_prompt_length": 256,
                "context_message_present": False,
                "context_payload_length": 96,
                "project_context_attached": True,
                "requirement_context_attached": "UNOBSERVED",
                "max_tokens": 1024,
                "temperature": 0.0,
                "top_p": "UNOBSERVED",
                "response_format_present": False,
                "http_payload_valid": True,
                "messages_array_length": 2,
                "first_message_role": "system",
                "last_message_role": "user",
                "task_mode": "UNOBSERVED",
                "project_mode": "UNOBSERVED",
                "prompt": "sensitive",
                "credential": "sensitive",
                "token": "sensitive",
            },
        )
        for target in targets:
            for handler in target.handlers:
                handler.flush()

        lines = (tmp_path / "provider-request-boundary-telemetry.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "context_message_present": False,
            "context_payload_length": 96,
            "event_name": "provider_request_boundary_telemetry",
            "first_message_role": "system",
            "http_payload_valid": True,
            "last_message_role": "user",
            "max_tokens": 1024,
            "messages_array_length": 2,
            "project_context_attached": True,
            "project_mode": "UNOBSERVED",
            "request_message_count": 2,
            "request_model": "deepseek-chat",
            "requirement_context_attached": "UNOBSERVED",
            "response_format_present": False,
            "system_message_present": True,
            "system_prompt_length": 128,
            "task_mode": "UNOBSERVED",
            "temperature": 0.0,
            "telemetry_status": "PASS",
            "top_p": "UNOBSERVED",
            "user_message_present": True,
            "user_prompt_length": 256,
        }
        assert "sensitive" not in lines[0]
    finally:
        for target in targets:
            for handler in tuple(target.handlers):
                if handler not in previous[target][0]:
                    handler.close()
                    target.removeHandler(handler)
            target.setLevel(previous[target][1])


def test_application_evidence_boundary_telemetry_is_safe_and_whitelisted(
    tmp_path: Path,
) -> None:
    module = _launcher_module()
    target = logging.getLogger(module._APPLICATION_EVIDENCE_TELEMETRY_LOGGER)
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        assert module._configure_application_evidence_telemetry(tmp_path) is True
        target.info(
            "application_evidence_boundary_telemetry",
            extra={
                "event_name": "application_evidence_boundary_telemetry",
                "evidence_stage": "E4_EVIDENCE_VALIDATE",
                "telemetry_status": "FAIL",
                "requirement_context_present": True,
                "project_context_present": True,
                "project_id_present": True,
                "source_reference_count": 0,
                "evidence_count": 0,
                "answer_text_length": 2,
                "validation_error_code": "ANSWER_CONTRACT_MISMATCH",
                "raw_answer": "sensitive",
                "prompt": "sensitive",
                "credential": "sensitive",
                "token": "sensitive",
            },
        )
        for handler in target.handlers:
            handler.flush()

        lines = (tmp_path / "application-evidence-boundary-telemetry.log").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "answer_text_length": 2,
            "evidence_count": 0,
            "evidence_stage": "E4_EVIDENCE_VALIDATE",
            "event_name": "application_evidence_boundary_telemetry",
            "project_id_present": True,
            "project_context_present": True,
            "requirement_context_present": True,
            "source_reference_count": 0,
            "telemetry_status": "FAIL",
            "validation_error_code": "ANSWER_CONTRACT_MISMATCH",
        }
        assert "sensitive" not in lines[0]
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)


def test_provider_response_telemetry_failure_is_best_effort(
    tmp_path: Path,
    caplog,
) -> None:
    module = _launcher_module()
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=module.logger.name):
        enabled = module._configure_provider_response_telemetry(
            occupied / "logs"
        )

    assert enabled is False
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "provider_response_telemetry_unavailable"
    )
    assert record.telemetry_enabled is False
    assert record.reason_code == "TELEMETRY_LOG_FILE_CREATE_FAILED"


def test_safe_file_logging_continues_when_provider_telemetry_fails(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    module = _launcher_module()
    telemetry_targets = (
        module.logger,
        logging.getLogger(module._SNAPSHOT_TELEMETRY_LOGGER),
        logging.getLogger(module.LOGGER_NAME),
    )
    previous_target_state = {
        target: (tuple(target.handlers), target.level)
        for target in telemetry_targets
    }

    def fail_provider_telemetry(_: Path) -> bool:
        raise PermissionError("telemetry sink unavailable")

    monkeypatch.setattr(
        module,
        "_configure_provider_response_telemetry",
        fail_provider_telemetry,
    )
    try:
        with caplog.at_level(logging.WARNING, logger=module.logger.name):
            module._configure_safe_file_logging(tmp_path)
        assert (tmp_path / "launcher.log").is_file()
        assert any(
            record.getMessage() == "provider_response_telemetry_unavailable"
            and record.telemetry_enabled is False
            for record in caplog.records
        )
        assert "telemetry_enabled=False" in (tmp_path / "launcher.log").read_text(
            encoding="utf-8"
        )
    finally:
        for target, (handlers, level) in previous_target_state.items():
            for handler in tuple(target.handlers):
                if handler not in handlers:
                    handler.close()
                    target.removeHandler(handler)
            target.setLevel(level)


def test_snapshot_telemetry_log_is_safe_and_whitelisted(tmp_path: Path) -> None:
    module = _launcher_module()
    target = logging.getLogger("embedded_copilot.production_release.migration")
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        module._configure_snapshot_telemetry(tmp_path)
        target.warning(
            "snapshot_prepare_telemetry",
            extra={
                "event_name": "snapshot_prepare_telemetry",
                "telemetry_status": "FAIL",
                "snapshot_stage": "S4_SNAPSHOT_FILE_CREATE",
                "exception_type": "PermissionError",
                "safe_reason_code": "ACCESS_DENIED",
                "sqlite_error_code": "-",
                "win_error": 5,
                "absolute_path": "path-sensitive",
                "username": "user-sensitive",
                "database_content": "content-sensitive",
                "credential": "credential-sensitive",
            },
        )
        for handler in target.handlers:
            handler.flush()

        line = (tmp_path / "migration-preflight-telemetry.log").read_text(
            encoding="utf-8"
        ).strip()
        assert "status=FAIL" in line
        assert "stage=S4_SNAPSHOT_FILE_CREATE" in line
        assert "exception_type=PermissionError" in line
        assert "safe_reason_code=ACCESS_DENIED" in line
        assert "sqlite_error_code=-" in line
        assert "win_error=5" in line
        assert all(value not in line for value in ("path-sensitive", "user-sensitive", "content-sensitive", "credential-sensitive"))
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)


def test_post_snapshot_migration_telemetry_log_is_safe_and_whitelisted(
    tmp_path: Path,
) -> None:
    module = _launcher_module()
    target = logging.getLogger("embedded_copilot.production_release.migration")
    previous_handlers = tuple(target.handlers)
    previous_level = target.level
    try:
        module._configure_migration_telemetry(tmp_path)
        target.warning(
            "migration_post_snapshot_telemetry",
            extra={
                "event_name": "migration_post_snapshot_telemetry",
                "telemetry_status": "FAIL",
                "migration_stage": "M11_MIGRATION_STATE_LOAD",
                "exception_type": "OperationalError",
                "reason_code": "SQLITE_OPERATION_FAILED",
                "sqlite_error_code": 5,
                "safe_error_category": "SQLITE",
                "absolute_path": "path-sensitive",
                "database_content": "content-sensitive",
                "credential": "credential-sensitive",
            },
        )
        for handler in target.handlers:
            handler.flush()

        line = (tmp_path / "migration-post-snapshot-telemetry.log").read_text(
            encoding="utf-8"
        ).strip()
        assert "status=FAIL" in line
        assert "stage=M11_MIGRATION_STATE_LOAD" in line
        assert "exception_type=OperationalError" in line
        assert "reason_code=SQLITE_OPERATION_FAILED" in line
        assert "sqlite_error_code=5" in line
        assert "safe_error_category=SQLITE" in line
        assert all(
            value not in line
            for value in ("path-sensitive", "content-sensitive", "credential-sensitive")
        )
    finally:
        for handler in tuple(target.handlers):
            if handler not in previous_handlers:
                handler.close()
                target.removeHandler(handler)
        target.setLevel(previous_level)
