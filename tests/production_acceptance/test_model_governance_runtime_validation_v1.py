from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from embedded_copilot.model_gateway import (
    ModelInvocationRequest,
    ModelInvocationStatus,
)
from scripts.model_governance_runtime_validation_v1 import (
    EXPECTED_RESPONSE,
    IDENTITY,
    PROJECT_ID,
    STAGE_ID,
    _build_harness,
    _model_request,
    execution_fingerprint,
    run_offline_runtime_validation,
    run_validation,
)


def test_offline_validation_covers_runtime_chain_and_fail_closed_cases(tmp_path: Path):
    result = run_offline_runtime_validation(tmp_path, tmp_path / "evidence")

    assert result["fixed_pass"] is True
    assert result["application_pass"] is True
    assert result["negative_pass"] is True
    assert result["relay_isolation_pass"] is True
    assert result["fixed"]["response_exact"] is True
    assert result["fixed"]["provider_request_count"] == 0
    assert result["fixed"]["gateway_call_count"] == 1
    assert result["fixed"]["fixture_provider_call_count"] == 1
    assert result["fixed"]["transport_send_count"] is None
    assert result["fixed"]["identity_preserved"] is True
    assert result["fixed"]["execution_fingerprint"] == execution_fingerprint(IDENTITY, STAGE_ID)
    assert result["fixed"]["receipt_identity_fingerprint_match"] is True
    assert result["fixed"]["receipt_recovered"] is True
    assert result["fixed"]["conflict_rejected"] is True
    assert result["fixed"]["state_fingerprint_unchanged_by_receipt"] is True
    assert result["fixed"]["receipt_metadata_only"] is True

    expected_errors = {
        "MODEL_NOT_ALLOWED": "MODEL_NOT_ALLOWED",
        "MODEL_CAPABILITY_MISMATCH": "MODEL_CAPABILITY_MISMATCH",
        "MODEL_CREDENTIAL_GATE_FAILED": "MODEL_CREDENTIAL_GATE_FAILED",
        "MODEL_AVAILABILITY_GATE_FAILED": "MODEL_AVAILABILITY_GATE_FAILED",
        "MODEL_QUOTA_GATE_FAILED": "MODEL_QUOTA_GATE_FAILED",
        "AUTO_ROUTER_PENDING": "MODEL_SELECTION_PENDING_ROUTER",
        "CANDIDATE_GROUP_FAILED": "MODEL_SELECTION_FAILED",
    }
    for name, error_code in expected_errors.items():
        case = result["negative"][name]
        assert case["error_code"] == error_code
        assert case["provider_request_count"] == 0
        assert case["gateway_calls"] == 0

    assert result["negative"]["RELAY_ISOLATION"] == {
        "relay_calls": 1,
        "anthropic_calls": 0,
        "provider_selected": "relay",
    }


def test_validation_writes_manifest_and_blocks_live_provider_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EMBRIX_LIVE_PROVIDER_FLAG", raising=False)
    current_package = Path(__file__).resolve().parents[2] / "dist" / "Embrix-current"
    before = None
    if current_package.is_dir():
        target = next((item for item in current_package.rglob("*") if item.is_file()), None)
        if target is not None:
            before = hashlib.sha256(target.read_bytes()).hexdigest()

    report = run_validation(evidence_dir=tmp_path / "evidence")
    evidence = tmp_path / "evidence"
    manifest = json.loads((evidence / "validation_manifest.json").read_text(encoding="utf-8"))
    text = (evidence / "validation_report.txt").read_text(encoding="utf-8")

    assert manifest["version"] == "1"
    assert manifest["runtime_wiring"] == "RUNTIME_WIRED_FIXED_MODE"
    assert manifest["project_id"] == PROJECT_ID
    assert manifest["stage_id"] == STAGE_ID.value
    assert manifest["selection_mode"] == "FIXED_MODEL"
    assert manifest["tests"] == {"focused": True, "security": True}
    assert manifest["real_provider"] == {"enabled": False, "reason": "LIVE_FLAG_UNSET"}
    assert report["real_provider_e2e"] == "BLOCKED"
    assert report["package_build"]["status"] == "NOT_PERFORMED"
    assert report["package_build"]["current_package_modified"] is False
    assert report["release_verdict"] == "BLOCKED"
    assert report["secret_free_evidence"] == "PASS"
    assert "REAL_PROVIDER_E2E=BLOCKED" in text
    assert "CURRENT_PACKAGE_MODIFIED=FALSE" in text
    assert "PROMOTION=NOT_PERFORMED" in text
    assert EXPECTED_RESPONSE not in (evidence / "validation_report.json").read_text(encoding="utf-8")

    if before is not None and target is not None:
        assert hashlib.sha256(target.read_bytes()).hexdigest() == before


def test_live_flag_remains_fail_closed_without_verified_production_credential(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EMBRIX_LIVE_PROVIDER_FLAG", "true")
    report = run_validation(evidence_dir=tmp_path / "evidence")

    assert report["real_provider_e2e"] == "BLOCKED"
    assert report["real_provider_reason"] == "LIVE_BRANCH_REQUIRES_VERIFIED_PRODUCTION_CREDENTIAL"
    assert report["offline"]["negative_pass"] is True
    assert report["package_build"]["current_package_modified"] is False


def test_missing_execution_snapshot_rejects_before_gateway_or_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _build_harness(tmp_path, tmp_path / "missing-snapshot.sqlite3")
    monkeypatch.setattr(harness.selection, "execution_snapshot", None)
    gateway_calls: list[str] = []

    def forbidden_gateway(*_args: object, **_kwargs: object) -> None:
        gateway_calls.append("called")
        raise AssertionError("missing snapshot must reject before gateway dispatch")

    monkeypatch.setattr(type(harness.gateway), "generate_bound", forbidden_gateway)
    monkeypatch.setattr(type(harness.gateway), "_generate_bound_with_metadata", forbidden_gateway)
    request = ModelInvocationRequest.from_model_request(
        _model_request(request_id="missing-execution-snapshot"),
        stage_id=STAGE_ID, task_type="FIRMWARE",
    )
    result = harness.invocation.invoke(request)

    assert result.status is ModelInvocationStatus.REJECTED
    assert result.error_code == "MODEL_EXECUTION_SNAPSHOT_UNAVAILABLE"
    assert result.provider_request_count == 0
    assert gateway_calls == []
    assert harness.provider.calls == 0
    assert harness.store.get_attempt_for_project(request.project_id, request.request_id) is None
