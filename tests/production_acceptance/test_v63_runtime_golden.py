from __future__ import annotations

import pytest

from scripts.v63_runtime_golden import (
    acceptance_environment_available,
    run_golden_cases,
)


pytestmark = pytest.mark.skipif(
    not acceptance_environment_available(),
    reason="v6.3 Synthetic Acceptance Project and state root are explicit external Golden inputs",
)


def test_v63_golden_executor_uses_formal_evidence_and_production_lifecycle() -> None:
    report = run_golden_cases()
    assert [item.case_id for item in report.cases] == [
        "1-memory-candidate",
        "2-human-approval",
        "3-rejection",
        "4-stale-memory",
        "5-governed-knowledge",
        "6-isolation",
        "7-restart-persistence",
        "8-no-automatic-promotion",
        "9-review-replay",
        "10-tombstone",
        "11-evolution-proposal",
        "12-zero-evidence",
    ]
    assert report.passed
    assert "formal Engineering Evidence validation" in report.runtime_evidence


def test_v63_golden_executor_never_claims_physical_evolution_apply() -> None:
    report = run_golden_cases()
    assert report.passed
    assert any("physical Apply remains unavailable" in item.details for item in report.cases)
