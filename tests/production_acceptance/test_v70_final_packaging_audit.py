from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_final_packaging_audit_is_machine_readable_and_keeps_gate_partial() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "v71_final_packaging_audit.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["product_version"] == "7.1.0"
    assert report["version_consistency"]["status"] == "PASS"
    assert report["frontend_production_assets"]["status"] == "PASS"
    assert report["release_evidence"]["production_runtime_code"] == "CODE/RUNTIME VERIFIED"
    assert report["release_evidence"]["restore_failure_atomicity"] == "CODE/RUNTIME VERIFIED"
    assert report["release_evidence"]["production_package"].startswith("PASS")
    assert report["release_evidence"]["installer"] == "UNVERIFIED"
    assert report["release_evidence"]["overall"] == "PARTIAL VERIFIED"


def test_v71_canonical_package_uses_source_greenfield_prompt_builder() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "v71_final_packaging_audit.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    prompt = report["prompt_builder_alignment"]

    assert prompt["source_prompt_length"] > 437
    assert prompt["packaged_prompt_length"] == prompt["source_prompt_length"]
    assert prompt["packaged_prompt_builder_hash"] == prompt["source_prompt_builder_hash"]
    assert prompt["prompt_builder_match"] is True
    assert prompt["requirement_generation_instruction"] is True
    assert prompt["design_generation_instruction"] is True
    assert prompt["plan_generation_instruction"] is True
    assert prompt["engineering_analysis_instruction"] is True
    assert report["release_evidence"]["new_exe_sha256"]


def test_final_packaging_documents_keep_tool_and_evidence_statuses_distinct() -> None:
    audit = (ROOT / "docs/audits/v7.0-final-packaging-audit.md").read_text(encoding="utf-8")
    package = (ROOT / "docs/verification/v7.0-package-golden.md").read_text(encoding="utf-8")
    installer = (ROOT / "docs/verification/v7.0-installer-golden.md").read_text(encoding="utf-8")
    assert "PyInstaller | UNAVAILABLE" in audit
    assert "Production Package and Installer remain **UNVERIFIED**" in audit
    assert "Status: **UNVERIFIED**" in package
    assert "Status: **UNVERIFIED**" in installer
