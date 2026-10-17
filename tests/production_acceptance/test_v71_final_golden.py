from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import v71_canonical_original_exception_golden as canonical
from scripts import v71_final_golden as golden


def test_final_golden_cli_help_runs_as_operator_command() -> None:
    result = subprocess.run(
        [sys.executable, str(Path(golden.__file__)), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--restart-check" in result.stdout


def _initial_report() -> dict[str, object]:
    return {
        "status": "PASS",
        "Startup": {"startup": "PASS", "bootstrap": "PASS", "health": "PASS"},
        "Application": {
            "chat_submit": "PASS",
            "conversation": "PASS",
            "project_resolve": "PASS",
            "context_build": "PASS",
            "requirement_context": "PASS",
        },
        "EngineeringQA": {
            "engineering_qa_enter": "PASS",
            "prompt_builder_enter": "PASS",
            "model_request_created": "PASS",
        },
        "Gateway": {"gateway_invocation_count": 1, "provider_invocation_count": 1},
        "Response": {"answer_text_length": 24, "validation_failure": "-"},
        "Evidence": {f"E{index}": "PASS" for index in range(6)},
        "connection_test_provider_budget": 1,
        "connection_test_invocation_count": 1,
        "connection_test_result": "PASS",
        "isolated_provider_status": "AVAILABLE",
        "verification_receipt_present": True,
        "verification_fingerprint_match": True,
        "verification_receipt_persisted": True,
        "provider_connection_verification_verified": True,
        "total_external_provider_calls": 2,
        "first_bad_stage": "-",
        "root_cause": "-",
    }


def test_final_golden_initial_gate_requires_every_observed_pass() -> None:
    golden.validate_initial_execution(_initial_report())

    incomplete = _initial_report()
    incomplete["Evidence"] = {**incomplete["Evidence"], "E4": "UNOBSERVED"}  # type: ignore[arg-type]
    with pytest.raises(golden.FinalGoldenUnverified, match="Evidence.E4"):
        golden.validate_initial_execution(incomplete)


def test_final_golden_rejects_wrong_single_provider_budget() -> None:
    incomplete = _initial_report()
    incomplete["Gateway"] = {"gateway_invocation_count": 1, "provider_invocation_count": 0}

    with pytest.raises(golden.FinalGoldenUnverified, match="provider_invocation_count"):
        golden.validate_initial_execution(incomplete)


def test_restart_resume_is_integrity_bound_and_not_part_of_public_evidence(
    tmp_path: Path,
) -> None:
    resume_path = tmp_path / "resume.json"
    golden.write_resume(
        resume_path,
        conversation_id="conversation-private",
        session_id="session-private",
        request_id="request-private",
        trace_id="trace-private",
        model_gateway_trace_count=1,
        canonical_exe_sha256="a" * 64,
    )

    resume = golden.read_resume(resume_path)
    assert resume["model_gateway_trace_count"] == 1
    public = golden.build_pending_restart_report(
        _initial_report(),
        canonical_exe_sha256="a" * 64,
        formal_database_changed=False,
    )
    assert public["v7_1_release_ready"] is False
    assert public["v7_2_allowed_to_start"] is False
    serialized = json.dumps(public, sort_keys=True)
    assert "conversation-private" not in serialized
    assert "session-private" not in serialized

    value = json.loads(resume_path.read_text(encoding="utf-8"))
    value["request_id"] = "request-tampered"
    resume_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(golden.FinalGoldenUnverified, match="RESUME_FINGERPRINT"):
        golden.read_resume(resume_path)


def test_recovery_resume_binds_persisted_context_answer_and_trace_anchors(
    tmp_path: Path,
) -> None:
    resume_path = tmp_path / "resume.json"
    golden.write_resume(
        resume_path,
        conversation_id="conversation-private",
        session_id="session-private",
        request_id="request-private",
        trace_id="trace-private",
        model_gateway_trace_count=1,
        canonical_exe_sha256="a" * 64,
        answer_sha256="b" * 64,
        project_context_sha256="c" * 64,
        requirement_context_sha256="d" * 64,
        trace_sha256="e" * 64,
        startup_log_line_count=4,
        provider_request_count=1,
        provider_response_count=6,
    )

    resume = golden.read_resume(resume_path)

    assert resume["answer_sha256"] == "b" * 64
    assert resume["project_context_sha256"] == "c" * 64
    assert resume["requirement_context_sha256"] == "d" * 64
    assert resume["trace_sha256"] == "e" * 64
    golden.require_recovery_anchors(resume)


def test_recovery_resume_without_anchors_is_not_restart_eligible(tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.json"
    golden.write_resume(
        resume_path,
        conversation_id="conversation-private",
        session_id="session-private",
        request_id="request-private",
        trace_id="trace-private",
        model_gateway_trace_count=1,
        canonical_exe_sha256="a" * 64,
    )

    with pytest.raises(golden.FinalGoldenUnverified, match="RECOVERY_ANCHORS_UNAVAILABLE"):
        golden.require_recovery_anchors(golden.read_resume(resume_path))


def test_final_release_report_exposes_flat_restart_gate_without_private_ids() -> None:
    report = golden.build_final_release_report(
        initial=_initial_report(),
        canonical_exe_sha256="a" * 64,
        restart_flags={
            "resume_handoff_present": True,
            "phase1_process_terminated": True,
            "restart_startup": "PASS",
            "restart_bootstrap": "PASS",
            "conversation_recovered": True,
            "project_context_recovered": True,
            "requirement_context_recovered": True,
            "answer_recovered": True,
            "trace_recovered": True,
            "restart_provider_invocation_count": 0,
            "restart_recovery": "PASS",
        },
        formal_database_changed=False,
    )

    assert report["golden_execution_verified"] is True
    assert report["restart_recovery_verified"] is True
    assert report["v7_1_release_ready"] is True
    assert report["v7_2_allowed_to_start"] is True
    assert report["provider_invocation_count"] == 1
    assert report["restart_provider_invocation_count"] == 0
    serialized = json.dumps(report, sort_keys=True)
    assert "conversation-private" not in serialized
    assert "session-private" not in serialized


def test_bootstrap_nonce_fresh_handoff_is_allowed(tmp_path: Path) -> None:
    handoff = tmp_path / "bootstrap.txt"
    handoff.write_text("http://127.0.0.1:8000/desktop/#fresh", encoding="utf-8")
    now = time.time()
    os.utime(handoff, (now - 1, now - 1))

    age_ms = golden._validate_bootstrap_handoff(handoff)

    assert age_ms <= 1_500
    assert golden._bootstrap_nonce_fresh(age_ms) is True
    assert golden.BOOTSTRAP_NONCE_TTL_SECONDS == 30


def test_bootstrap_nonce_expired_handoff_is_never_exchanged(tmp_path: Path) -> None:
    handoff = tmp_path / "bootstrap.txt"
    handoff.write_text("http://127.0.0.1:8000/desktop/#expired", encoding="utf-8")
    now = time.time()
    os.utime(handoff, (now - 31, now - 31))

    with pytest.raises(golden.FinalGoldenUnverified, match="BOOTSTRAP_NONCE_PRE_EXPIRY"):
        golden._prepare_bootstrap_handoff(
            type("Args", (), {"bootstrap_url_file": handoff, "jit_bootstrap": False})(),
            preflight_complete=True,
        )


def test_preexpiry_handoff_uses_jit_refresh_instead_of_old_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handoff = tmp_path / "bootstrap.txt"
    calls: list[str] = []

    def fake_launch(args: object) -> tuple[Path, int, int]:
        calls.append("jit")
        return handoff, 1234, 2

    monkeypatch.setattr(golden, "_launch_jit_bootstrap", fake_launch)
    args = type("Args", (), {"bootstrap_url_file": handoff, "jit_bootstrap": True})()

    result = golden._prepare_bootstrap_handoff(args, preflight_complete=True)

    assert calls == ["jit"]
    assert result == (handoff, 1234, 2)


def test_nonce_acquisition_occurs_after_preflight_and_immediately_before_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    initial = _initial_report()
    args = type(
        "Args",
        (),
        {
            "bootstrap_url_file": tmp_path / "bootstrap.txt",
            "state_database": tmp_path / "state.sqlite3",
            "logs_directory": tmp_path / "logs",
            "isolated_localappdata": tmp_path,
            "formal_database": tmp_path / "formal.sqlite3",
            "canonical_exe": tmp_path / "EmbeddedCopilot.exe",
            "resume_file": tmp_path / "resume.json",
            "jit_bootstrap": False,
        },
    )()
    args.formal_database.write_bytes(b"formal")
    args.canonical_exe.write_bytes(b"exe")
    args.bootstrap_url_file.write_text("http://127.0.0.1:8000/desktop/#fresh", encoding="utf-8")

    monkeypatch.setattr(
        golden,
        "_preflight",
        lambda *_args, **_kwargs: events.append("preflight")
        or {
            "canonical_exe_sha256": "a" * 64,
            "formal_database_manifest": [],
            "formal_database_sha256": golden._sha256_json([]),
            "module_alignment": True,
            "isolated_localappdata_fingerprint": "sha256:test",
        },
    )
    monkeypatch.setattr(
        golden,
        "_prepare_bootstrap_handoff",
        lambda *_args, **_kwargs: events.append("nonce")
            or (args.bootstrap_url_file, None, 2),
    )
    class _Client:
        nonce = "fresh"

        def request(self, *_args, **_kwargs):
            events.append("health")
            return 200, {"status": "READY"}

        def require(self, *_args, **_kwargs):
            events.append("bootstrap")
            return {}

    monkeypatch.setattr(canonical.LoopbackClient, "from_file", lambda *_args: _Client())
    monkeypatch.setattr(
        golden,
        "_run_connection_test",
        lambda *_args: events.append("connection")
        or {
            "connection_test_provider_budget": 1,
            "connection_test_invocation_count": 1,
            "connection_test_result": "PASS",
            "isolated_provider_status": "AVAILABLE",
            "verification_receipt_present": True,
            "verification_fingerprint_match": True,
            "verification_receipt_persisted": True,
            "credential_identity_present": True,
            "provider_request_count_after": 1,
            "provider_response_count_after": 1,
        },
    )
    monkeypatch.setattr(golden, "_database_manifest", lambda *_args: [])
    monkeypatch.setattr(golden, "_gateway_trace_count", lambda *_args: 1)
    monkeypatch.setattr(golden, "_read_canonical_resume", lambda *_args: {
        "conversation_id": "c",
        "session_id": "s",
        "request_id": "r",
        "trace_id": "t",
        "answer_sha256": "b" * 64,
        "project_context_sha256": "c" * 64,
        "requirement_context_sha256": "d" * 64,
        "trace_sha256": "e" * 64,
    })
    monkeypatch.setattr(canonical, "run", lambda *_args: events.append("exchange") or initial)
    monkeypatch.setattr(canonical, "_read_json_lines", lambda *_args: [])

    golden.run_initial(args)

    assert events == ["preflight", "nonce", "health", "bootstrap", "connection", "exchange"]


def test_bootstrap_budget_and_ttl_are_unchanged() -> None:
    assert golden.PROVIDER_BUDGET == 1
    assert golden.BOOTSTRAP_NONCE_TTL_SECONDS == 30
    assert golden.BOOTSTRAP_NONCE_SAFE_MARGIN_MS > 0


def test_provider_budgets_are_split_by_release_phase() -> None:
    assert golden.CONNECTION_TEST_PROVIDER_BUDGET == 1
    assert golden.RECOVERY_SEED_PROVIDER_BUDGET == 1
    assert golden.RESTART_PROVIDER_BUDGET == 0
    assert golden.TOTAL_EXTERNAL_PROVIDER_CALLS_MAX == 2


def test_connection_test_gate_requires_one_call_and_matching_receipt() -> None:
    report = {
        "connection_test_result": "PASS",
        "connection_test_invocation_count": 1,
        "isolated_provider_status": "AVAILABLE",
        "verification_receipt_present": True,
        "verification_fingerprint_match": True,
        "verification_receipt_persisted": True,
    }

    golden.validate_connection_test(report)

    invalid = {**report, "connection_test_invocation_count": 2}
    with pytest.raises(golden.FinalGoldenUnverified, match="CONNECTION_TEST_INVOCATION_COUNT"):
        golden.validate_connection_test(invalid)


def test_connection_test_failure_is_not_seed_eligible() -> None:
    failed = {
        "connection_test_result": "PROVIDER_AUTH_FAILED",
        "connection_test_invocation_count": 1,
        "isolated_provider_status": "PROVIDER_AUTH_FAILED",
        "verification_receipt_present": False,
        "verification_fingerprint_match": False,
    }

    with pytest.raises(golden.FinalGoldenUnverified, match="CONNECTION_TEST_RESULT"):
        golden.validate_connection_test(failed)


def test_final_release_report_requires_provider_connection_verification() -> None:
    initial = _initial_report()
    initial.update(
        {
            "provider_connection_verification_verified": True,
            "connection_test_provider_budget": 1,
            "connection_test_invocation_count": 1,
            "connection_test_result": "PASS",
            "verification_receipt_present": True,
            "verification_fingerprint_match": True,
            "total_external_provider_calls": 2,
        }
    )
    report = golden.build_final_release_report(
        initial=initial,
        canonical_exe_sha256="a" * 64,
        restart_flags={
            "resume_handoff_present": True,
            "phase1_process_terminated": True,
            "restart_startup": "PASS",
            "restart_bootstrap": "PASS",
            "conversation_recovered": True,
            "project_context_recovered": True,
            "requirement_context_recovered": True,
            "answer_recovered": True,
            "trace_recovered": True,
            "restart_provider_invocation_count": 0,
            "restart_recovery": "PASS",
        },
        formal_database_changed=False,
    )
    assert report["provider_connection_verification_verified"] is True
    assert report["total_external_provider_calls"] == 2


def test_restart_budget_is_zero_and_total_budget_is_not_a_retry() -> None:
    assert golden.RESTART_PROVIDER_BUDGET == 0
    assert golden.TOTAL_EXTERNAL_PROVIDER_CALLS_MAX == (
        golden.CONNECTION_TEST_PROVIDER_BUDGET
        + golden.RECOVERY_SEED_PROVIDER_BUDGET
    )


def test_connection_test_failure_prevents_seed_and_reports_single_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = type(
        "Args",
        (),
        {
            "bootstrap_url_file": tmp_path / "bootstrap.txt",
            "state_database": tmp_path / "state.sqlite3",
            "logs_directory": tmp_path / "logs",
            "isolated_localappdata": tmp_path,
            "formal_database": tmp_path / "formal.sqlite3",
            "canonical_exe": tmp_path / "EmbeddedCopilot.exe",
            "resume_file": tmp_path / "resume.json",
            "jit_bootstrap": False,
        },
    )()
    args.formal_database.write_bytes(b"formal")
    args.canonical_exe.write_bytes(b"exe")
    args.bootstrap_url_file.write_text("http://127.0.0.1:8000/desktop/#fresh", encoding="utf-8")
    monkeypatch.setattr(
        golden,
        "_preflight",
        lambda *_args, **_kwargs: {
            "canonical_exe_sha256": "a" * 64,
            "formal_database_manifest": [],
            "formal_database_sha256": golden._sha256_json([]),
            "module_alignment": True,
            "isolated_localappdata_fingerprint": "sha256:test",
        },
    )
    monkeypatch.setattr(
        golden,
        "_prepare_bootstrap_handoff",
        lambda *_args, **_kwargs: (args.bootstrap_url_file, None, 2),
    )

    class _Client:
        nonce = "fresh"

        def request(self, *_args, **_kwargs):
            return 200, {"status": "READY"}

        def require(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(canonical.LoopbackClient, "from_file", lambda *_args: _Client())
    monkeypatch.setattr(
        golden,
        "_run_connection_test",
        lambda *_args: {
            "connection_test_provider_budget": 1,
            "connection_test_invocation_count": 1,
            "connection_test_result": "PROVIDER_AUTH_FAILED",
            "isolated_provider_status": "PROVIDER_AUTH_FAILED",
            "verification_receipt_present": False,
            "verification_fingerprint_match": False,
            "verification_receipt_persisted": False,
            "credential_identity_present": True,
        },
    )
    seed_called = False

    def seed(*_args, **_kwargs):
        nonlocal seed_called
        seed_called = True
        return _initial_report()

    monkeypatch.setattr(canonical, "run", seed)
    result = golden.run_initial(args)

    assert result["first_bad_stage"] == "PROVIDER_CONNECTION_TEST"
    assert result["recovery_seed_golden"] == "NOT_RUN"
    assert result["recovery_seed_provider_invocation_count"] == 0
    assert result["total_external_provider_calls"] == 1
    assert seed_called is False


def test_restart_connection_test_invocation_is_a_hard_gate() -> None:
    initial = _initial_report()
    report = golden.build_final_release_report(
        initial=initial,
        canonical_exe_sha256="a" * 64,
        restart_flags={
            "resume_handoff_present": True,
            "phase1_process_terminated": True,
            "restart_startup": "PASS",
            "restart_bootstrap": "PASS",
            "conversation_recovered": True,
            "project_context_recovered": True,
            "requirement_context_recovered": True,
            "answer_recovered": True,
            "trace_recovered": True,
            "restart_provider_invocation_count": 0,
            "restart_connection_test_invocation_count": 1,
            "restart_recovery": "PASS",
        },
        formal_database_changed=False,
    )
    assert report["restart_recovery_verified"] is False
    assert report["v7_1_release_ready"] is False


def test_persisted_available_receipt_requires_matching_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "Embedded Copilot" / "config"
    root.mkdir(parents=True)
    receipt = {
        "chat_last_test_status": "AVAILABLE",
        "chat_tested_at": "2026-08-27T00:00:00+00:00",
        "chat_tested_configuration_fingerprint": "sha256:fp",
    }
    (root / "production.json").write_text(json.dumps(receipt), encoding="utf-8")

    assert golden._verification_receipt_persisted(tmp_path, "sha256:fp") is True
    assert golden._verification_receipt_persisted(tmp_path, "sha256:other") is False


def test_seed_failure_cannot_become_restart_eligible() -> None:
    initial = _initial_report()
    initial["initial_execution"] = "FAIL"
    initial["status"] = "FAIL"
    with pytest.raises(golden.FinalGoldenUnverified, match="INITIAL_EXECUTION_STATUS"):
        golden.validate_initial_execution(initial)


def test_seed_preprovider_failure_is_rendered_as_complete_fail_closed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = type(
        "Args",
        (),
        {
            "bootstrap_url_file": tmp_path / "bootstrap.txt",
            "state_database": tmp_path / "state.sqlite3",
            "logs_directory": tmp_path / "logs",
            "isolated_localappdata": tmp_path,
            "formal_database": tmp_path / "formal.sqlite3",
            "canonical_exe": tmp_path / "EmbeddedCopilot.exe",
            "resume_file": tmp_path / "resume.json",
            "jit_bootstrap": False,
        },
    )()
    args.formal_database.write_bytes(b"formal")
    args.canonical_exe.write_bytes(b"exe")
    args.bootstrap_url_file.write_text(
        "http://127.0.0.1:8000/desktop/#fresh", encoding="utf-8"
    )
    monkeypatch.setattr(
        golden,
        "_preflight",
        lambda *_args, **_kwargs: {
            "canonical_exe_sha256": "a" * 64,
            "formal_database_manifest": [],
            "formal_database_sha256": golden._sha256_json([]),
            "module_alignment": True,
            "isolated_localappdata_fingerprint": "sha256:test",
        },
    )
    monkeypatch.setattr(
        golden,
        "_prepare_bootstrap_handoff",
        lambda *_args, **_kwargs: (args.bootstrap_url_file, None, 2),
    )

    class _Client:
        nonce = "fresh"

        def request(self, *_args, **_kwargs):
            return 200, {"status": "READY"}

        def require(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(canonical.LoopbackClient, "from_file", lambda *_args: _Client())
    monkeypatch.setattr(
        golden,
        "_run_connection_test",
        lambda *_args: {
            "connection_test_provider_budget": 1,
            "connection_test_invocation_count": 1,
            "connection_test_result": "PASS",
            "isolated_provider_status": "AVAILABLE",
            "verification_receipt_present": True,
            "verification_fingerprint_match": True,
            "verification_receipt_persisted": True,
            "credential_identity_present": True,
            "provider_request_count_after": 1,
            "provider_response_count_after": 1,
        },
    )
    monkeypatch.setattr(golden, "_database_manifest", lambda *_args: [])

    def seed(*_args, **_kwargs):
        raise canonical.GoldenUnverified("canonical project 4 is unavailable")

    monkeypatch.setattr(canonical, "run", seed)

    result = golden.run_initial(args)

    assert result["status"] == "FAIL"
    assert result["first_bad_stage"] == "canonical project 4 is unavailable"
    assert result["connection_test_invocation_count"] == 1
    assert result["recovery_seed_golden"] == "NOT_RUN"
    assert result["recovery_seed_provider_invocation_count"] == 0
    assert result["formal_database_hash_proof"] == "PASS"
    assert result["database_changed"] is False


def test_empty_isolated_state_uses_normal_create_new_project_path_without_fixed_id() -> None:
    calls: list[tuple[str, str]] = []

    class _Client:
        def request(self, path: str, *, method: str = "GET", body: object | None = None) -> tuple[int, object]:
            del body
            calls.append((method, path))
            if path == "/api/v1/copilot/projects/locations":
                return 200, [{"location_id": "location-isolated", "fingerprint": "sha256:location", "availability": "AVAILABLE"}]
            raise AssertionError(path)

        def require(self, path: str, *, method: str = "GET", body: object | None = None, field: str) -> dict[str, object]:
            del body, field
            calls.append((method, path))
            if path == "/api/v1/copilot/projects/locations":
                return {"items": [{"location_id": "location-isolated", "fingerprint": "sha256:location", "availability": "AVAILABLE"}]}
            if path == "/api/v1/copilot/projects/previews":
                return {"preview_id": "preview-isolated", "fingerprint": "sha256:preview"}
            if path == "/api/v1/copilot/projects":
                return {
                    "project_id": "project-dynamic-isolated",
                    "name": "v71-recovery-seed",
                    "creation_mode": "CREATE_NEW",
                    "project_type": "STM32",
                    "initialization_status": "READY",
                }
            if path == "/api/v1/copilot/projects/project-dynamic-isolated":
                return {
                    "project_id": "project-dynamic-isolated",
                    "name": "v71-recovery-seed",
                    "creation_mode": "CREATE_NEW",
                    "project_type": "STM32",
                    "initialization_status": "READY",
                }
            raise AssertionError(path)

    project, metadata = canonical.create_isolated_project(_Client(), project_name="v71-recovery-seed")

    assert project["project_id"] == "project-dynamic-isolated"
    assert metadata["isolated_project_created"] is True
    assert metadata["project_creation_path"] == "CREATE_NEW"
    assert metadata["project_resolve"] == "PASS"
    assert metadata["isolated_project_id_hash"] == hashlib.sha256(
        b"project-dynamic-isolated"
    ).hexdigest()
    assert ("POST", "/api/v1/copilot/projects/previews") in calls
    assert ("POST", "/api/v1/copilot/projects") in calls
    assert ("GET", "/api/v1/copilot/projects/project-dynamic-isolated") in calls
    assert not any("provider" in path for _, path in calls)


def test_dynamic_project_identity_is_not_fixed_production_row() -> None:
    source = Path(canonical.__file__).read_text(encoding="utf-8")
    run_source = source[source.index("def run("):]

    assert "PROJECT_ID" not in run_source
    assert "project-ddd26bbc-7e4b-41c4-bc06-f50e4b569655" not in run_source
    assert canonical.project_is_canonical(
        {
            "project_id": "project-another-isolated-id",
            "name": "v71-recovery-seed",
            "creation_mode": "CREATE_NEW",
            "project_type": "STM32",
        }
    )


def test_provider_verification_restores_from_existing_receipt_without_connection_test(
    tmp_path: Path,
) -> None:
    config = tmp_path / "Embedded Copilot" / "config"
    config.mkdir(parents=True)
    (config / "production.json").write_text(
        json.dumps(
            {
                "chat_last_test_status": "AVAILABLE",
                "chat_provider": "deepseek",
                "chat_model": "deepseek-chat",
                "chat_tested_at": "2026-08-27T00:00:00+00:00",
                "chat_tested_configuration_fingerprint": "sha256:receipt",
            }
        ),
        encoding="utf-8",
    )

    restored = golden.verify_provider_verification_restored(tmp_path)
    assert restored["provider_verification_restored"] is True
    assert restored["verification_receipt_present"] is True
    assert restored["verification_receipt_persisted"] is True
    assert restored["verification_fingerprint_match"] is True
    assert restored["isolated_provider_status"] == "AVAILABLE"
    assert restored["verification_fingerprint"] == "sha256:receipt"


def test_project_root_preparation_preserves_existing_provider_receipt(tmp_path: Path) -> None:
    config = tmp_path / "Embedded Copilot" / "config"
    config.mkdir(parents=True)
    payload = {
        "chat_last_test_status": "AVAILABLE",
        "chat_tested_at": "2026-08-27T00:00:00+00:00",
        "chat_tested_configuration_fingerprint": "sha256:receipt",
    }
    path = config / "production.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    root = golden.prepare_isolated_project_root(tmp_path)
    restored = json.loads(path.read_text(encoding="utf-8"))

    assert root.is_dir()
    assert restored["chat_last_test_status"] == "AVAILABLE"
    assert restored["chat_tested_configuration_fingerprint"] == "sha256:receipt"
    assert restored["project_root"] == str(root)
    assert restored["project_location_allow_list"] == str(root)


def test_recovery_seed_reuses_receipt_and_never_runs_connection_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "Embedded Copilot" / "config"
    config.mkdir(parents=True)
    (config / "production.json").write_text(
        json.dumps(
            {
                "chat_last_test_status": "AVAILABLE",
                "chat_tested_at": "2026-08-27T00:00:00+00:00",
                "chat_tested_configuration_fingerprint": "sha256:receipt",
            }
        ),
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "bootstrap_url_file": tmp_path / "bootstrap.txt",
            "state_database": tmp_path / "state.sqlite3",
            "logs_directory": tmp_path / "logs",
            "isolated_localappdata": tmp_path,
            "formal_database": tmp_path / "formal.sqlite3",
            "canonical_exe": tmp_path / "EmbeddedCopilot.exe",
            "resume_file": tmp_path / "resume.json",
            "jit_bootstrap": False,
        },
    )()
    args.formal_database.write_bytes(b"formal")
    args.bootstrap_url_file.write_text(
        "http://127.0.0.1:8000/desktop/#fresh", encoding="utf-8"
    )
    monkeypatch.setattr(
        golden,
        "_preflight",
        lambda *_args, **_kwargs: {
            "canonical_exe_sha256": "a" * 64,
            "formal_database_manifest": [],
            "formal_database_sha256": golden._sha256_json([]),
            "module_alignment": True,
            "isolated_localappdata_fingerprint": "sha256:test",
        },
    )
    monkeypatch.setattr(
        golden,
        "_prepare_bootstrap_handoff",
        lambda *_args, **_kwargs: (args.bootstrap_url_file, 1234, 2),
    )

    class _Client:
        nonce = "fresh"

        def request(self, *_args, **_kwargs):
            return 200, {"status": "READY"}

        def require(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(canonical.LoopbackClient, "from_file", lambda *_args: _Client())
    monkeypatch.setattr(
        golden,
        "_provider_log_counts",
        lambda *_args: (1, 5),
    )
    monkeypatch.setattr(golden, "_database_manifest", lambda *_args: [])
    monkeypatch.setattr(golden, "_gateway_trace_count", lambda *_args: 1)
    monkeypatch.setattr(
        golden,
        "_run_connection_test",
        lambda *_args, **_kwargs: pytest.fail("Connection Test must not run during recovery seed"),
    )
    seed = _initial_report()
    seed.update(
        {
            "isolated_project_created": True,
            "isolated_project_id_hash": hashlib.sha256(b"project-dynamic-isolated").hexdigest(),
            "project_creation_path": "CREATE_NEW",
            "project_resolve": "PASS",
        }
    )
    monkeypatch.setattr(canonical, "run", lambda *_args, **_kwargs: seed)
    monkeypatch.setattr(
        golden,
        "_read_canonical_resume",
        lambda *_args: {
            "conversation_id": "conversation-isolated",
            "session_id": "session-isolated",
            "request_id": "request-isolated",
            "trace_id": "trace-isolated",
            "project_id": "project-dynamic-isolated",
            "answer_sha256": "b" * 64,
            "project_context_sha256": "c" * 64,
            "requirement_context_sha256": "d" * 64,
            "trace_sha256": "e" * 64,
        },
    )
    monkeypatch.setattr(golden, "write_resume", lambda *_args, **_kwargs: None)

    result = golden.run_recovery_seed(args)

    assert result["provider_verification_restored"] is True
    assert result["resume_connection_test_invocation_count"] == 0
    assert result["connection_test_invocation_count"] == 1
    assert result["recovery_seed_provider_invocation_count"] == 1
    assert result["total_external_provider_calls"] == 2
    assert result["isolated_project_created"] is True
    assert result["project_resolve"] == "PASS"
