from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "acceptance" / "v7.1-canonical-golden-20260824"


def _json_lines(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_reconciled_execution_uses_observed_success_artifacts() -> None:
    result = json.loads((ACCEPTANCE / "reconciled-golden-result.json").read_text())
    alignment = json.loads((ACCEPTANCE / "final-module-alignment.json").read_text())
    provider_request = _json_lines(
        ACCEPTANCE / "evidence" / "provider-request-boundary-telemetry.log"
    )
    provider_response = _json_lines(
        ACCEPTANCE / "evidence" / "provider-response-boundary-telemetry.log"
    )
    application_evidence = _json_lines(
        ACCEPTANCE / "evidence" / "application-evidence-boundary-telemetry.log"
    )

    exe = ROOT / "dist" / "EmbeddedCopilot-7.1.0" / "EmbeddedCopilot" / "EmbeddedCopilot.exe"
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    assert result["execution_status"] == "PASS"
    assert result["golden_execution_verified"] is True
    assert result["provider_golden_rerun_required"] is False
    assert result["status"] == "UNVERIFIED"
    assert result["Build"]["module_alignment"] is True  # type: ignore[index]
    assert alignment["module_alignment"] is True
    assert alignment["exe_sha256"].lower() == digest
    assert len(provider_request) == 1
    assert provider_request[0]["telemetry_status"] == "PASS"
    assert sum(row.get("response_stage") == "R0_RAW_RESPONSE_RECEIVED" for row in provider_response) == 1
    assert all(row["telemetry_status"] == "PASS" for row in provider_response)
    assert all(row["telemetry_status"] == "PASS" for row in application_evidence)
    assert {row["evidence_stage"] for row in application_evidence} == {
        "E0_EVIDENCE_BUILD_ENTER",
        "E1_REQUIREMENT_CONTEXT_BIND",
        "E2_PROJECT_CONTEXT_BIND",
        "E3_EVIDENCE_COLLECTION",
        "E4_EVIDENCE_VALIDATE",
        "E5_APPLICATION_ANSWER_COMMIT",
    }
    assert result["Gateway"]["gateway_invocation_count"] == 1  # type: ignore[index]
    assert result["Gateway"]["provider_invocation_count"] == 1  # type: ignore[index]
    assert result["Response"]["response_received"] is True  # type: ignore[index]
    assert result["Response"]["answer_text_length"] == 2374  # type: ignore[index]
    assert all(result["Evidence"][f"E{index}"] == "PASS" for index in range(6))  # type: ignore[index]
    assert result["Database"]["database_changed"] is False  # type: ignore[index]


def test_release_closure_uses_latest_restart_recovery_pass_without_rewriting_history() -> None:
    result = json.loads((ACCEPTANCE / "golden-result.json").read_text())
    historical = json.loads((ACCEPTANCE / "reconciled-golden-result.json").read_text())

    assert result["status"] == "PASS"
    assert result["restart_recovery_verified"] is True
    assert result["release_gate_status"] == "PASS"
    assert result["v7_1_release_ready"] is True
    assert result["v7_2_allowed_to_start"] is True
    assert historical["status"] == "UNVERIFIED"
    assert historical["restart_recovery"]["status"] == "UNVERIFIED"
