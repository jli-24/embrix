from __future__ import annotations

from scripts.v64_validation_proposal_golden import run_golden


def test_v64_runtime_golden_cases_close_without_manual_state_insertion() -> None:
    report = run_golden()
    assert report["status"] == "GOLDEN VERIFIED", report
    assert len(report["cases"]) == 16
