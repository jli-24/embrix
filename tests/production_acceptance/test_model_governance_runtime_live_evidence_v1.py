from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import scripts.model_governance_runtime_live_evidence_v1 as live_module
from scripts.model_governance_runtime_live_evidence_v1 import (
    _execute_production_live_in_lifespan,
    execution_fingerprint,
    run_live_evidence,
)


def test_live_provider_runtime_execution(tmp_path: Path):
    """Verify the real branch when enabled, and fail closed otherwise."""

    report = run_live_evidence(
        evidence_dir=tmp_path / "live-evidence", run_regression=False
    )
    live = report["live"]
    gate = report["release_gate"]

    if live["status"] == "PASS":
        assert os.getenv("EMBRIX_LIVE_PROVIDER_FLAG", "").strip().casefold() == "true"
        assert live["provider_request_count"] == 1
        assert live["http_status"] is not None and 200 <= live["http_status"] < 300
        assert live["receipt_created"] is True
        assert live["identity_match"] is True
        assert live["restart_recovery"] is True
        assert gate["live_e2e"] == "PASS"
    else:
        assert live["status"] in {"BLOCKED", "FAILED"}
        assert live["real_provider_verified"] is False
        if live["status"] == "BLOCKED":
            assert live["provider_request_count"] == 0
            assert live["receipt_created"] is False
        assert gate["live_e2e"] == live["status"]
        assert gate["release_ready"] is False


def test_live_flag_does_not_override_missing_http_or_identity_evidence(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("EMBRIX_LIVE_PROVIDER_FLAG", "true")
    report = run_live_evidence(
        evidence_dir=tmp_path / "live-evidence", run_regression=False
    )
    live = report["live"]
    manifest = json.loads(
        (tmp_path / "live-evidence" / "validation_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert live["status"] == "BLOCKED"
    assert live["real_provider_verified"] is False
    assert live["provider_request_count"] == 0
    assert manifest["real_provider"] is False
    assert manifest["identity_match"] is False
    assert report["secret_scan"]["status"] == "PASS"


def test_live_flag_unset_skips_production_provider_call(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EMBRIX_LIVE_PROVIDER_FLAG", raising=False)

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("provider composition must not be reached")

    monkeypatch.setattr(live_module, "_execute_production_live", unexpected_call)
    report = run_live_evidence(
        evidence_dir=tmp_path / "live-evidence", run_regression=False
    )

    assert report["live"]["reason"] == "LIVE_FLAG_UNSET"
    assert report["live"]["provider_request_count"] == 0
    assert report["live"]["live_e2e"] == "BLOCKED"
    assert report["live"]["live_flag_status"] == "BLOCKED"
    assert report["live"]["project_context_status"] == "NOT_EVALUATED"
    assert report["live"]["http_status_evidence_status"] == "NOT_EVALUATED"
    assert report["live"]["fixed_model_execution"] == "NOT_EVALUATED"
    assert report["live"]["receipt_validation"] == "NOT_EVALUATED"


def test_controlled_manifest_contains_golden_run_contract(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EMBRIX_LIVE_PROVIDER_FLAG", raising=False)
    evidence = tmp_path / "live-evidence"
    report = run_live_evidence(evidence_dir=evidence, run_regression=False)

    manifest = json.loads(
        (evidence / "validation_manifest.json").read_text(encoding="utf-8")
    )
    required = {
        "schema_version",
        "validation_run_id",
        "timestamp",
        "live_flag_status",
        "project_context_source",
        "project_id",
        "stage_id",
        "selection_mode",
        "policy_source",
        "provider_id",
        "endpoint_id",
        "credential_ref",
        "credential_revision",
        "model_profile_id",
        "model_id",
        "capability_gate",
        "availability_gate",
        "credential_gate",
        "endpoint_health_gate",
        "quota_gate",
        "provider_request_count",
        "provider_request_id",
        "provider_request_id_status",
        "http_status",
        "http_status_source",
        "response_exact_match",
        "receipt_id",
        "receipt_persisted",
        "receipt_restart_readable",
        "identity_match",
        "execution_fingerprint",
        "execution_fingerprint_match",
        "secret_free",
        "real_provider_e2e",
        "status",
        "blockers",
        "failures",
    }

    assert required <= manifest.keys()
    assert report["validation_run_id"].startswith(
        "model-governance-controlled-live-golden-v1_"
    )
    assert manifest["live_flag_status"] == "BLOCKED"
    assert manifest["provider_request_count"] == 0
    assert manifest["real_provider_e2e"] == "BLOCKED"
    assert manifest["response_exact_match"] == "NOT_EVALUATED"
    assert manifest["failures"] == []


def test_invalid_project_context_stops_before_provider_access():
    class EmptyProjectCatalog:
        def projects(self, _tenant_id):
            return ()

        def get_project(self, _project_id, *, tenant_id):
            raise AssertionError(f"unexpected project lookup: {tenant_id}")

        def knowledge_snapshot_root(self, _project_id, *, tenant_id):
            raise AssertionError(f"unexpected snapshot lookup: {tenant_id}")

    class ReceiptStore:
        def list_receipts(self):
            return ()

    class UnexpectedProviderService:
        def available_credential_refs(self):
            raise AssertionError("provider credentials must not be read")

    application = SimpleNamespace(
        state=SimpleNamespace(
            provider_configuration_service=UnexpectedProviderService(),
            model_gateway_management_service=SimpleNamespace(
                receipt_store=ReceiptStore(),
                stage_model_policy=lambda *_args: None,
            ),
            project_catalog_runtime=EmptyProjectCatalog(),
        )
    )

    live = _execute_production_live_in_lifespan(
        application, validation_run_id="invalid-project-test"
    )

    assert live["reason"] == "NO_VALID_PROJECT_CONTEXT"
    assert live["provider_request_count"] == 0
    assert live["valid_project_context"] is False


def test_live_evidence_manifest_is_secret_free_and_architecture_guarded(tmp_path: Path):
    report = run_live_evidence(
        evidence_dir=tmp_path / "live-evidence", run_regression=False
    )
    evidence = tmp_path / "live-evidence"
    manifest_text = (evidence / "validation_manifest.json").read_text(encoding="utf-8")
    report_text = (evidence / "validation_report.json").read_text(encoding="utf-8")

    assert report["architecture_guard"]["status"] == "PASS"
    assert report["secret_scan"]["status"] == "PASS"
    assert "sk-" not in manifest_text.casefold()
    assert "authorization" not in manifest_text.casefold()
    assert "bearer" not in manifest_text.casefold()
    assert '"secret":' not in manifest_text.casefold()
    assert '"token":' not in manifest_text.casefold()
    assert '"authorization":' not in manifest_text.casefold()
    assert "EMBRIX_RUNTIME_WIRING_OK" not in report_text


def test_execution_fingerprint_is_bound_to_full_identity_and_stage():
    identity = {
        "provider_id": "relay",
        "endpoint_id": "endpoint-relay",
        "credential_ref": "credential:relay",
        "model_profile_id": "model-relay-firmware",
        "model_id": "firmware-model",
    }

    first = execution_fingerprint(identity)
    same = execution_fingerprint(dict(identity))
    changed_stage = execution_fingerprint(identity, "DEBUGGING")
    changed_provider = execution_fingerprint({**identity, "provider_id": "anthropic"})

    assert first == same
    assert first.startswith("sha256:")
    assert first != changed_stage
    assert first != changed_provider
