from __future__ import annotations

from pathlib import Path

from scripts.v67_desktop_product_golden import run_golden_cases


def test_v67_golden_runner_exercises_canonical_projection_without_fake_completion(tmp_path: Path) -> None:
    report = run_golden_cases(projects_root=tmp_path / "projects", state_root=tmp_path / "state", prepare_inputs=True)
    assert len(report.cases) == 20
    assert report.browser_automation == "UNAVAILABLE / NOT PRESENT"
    assert report.security_counters["external_provider_invocations"] == 0
    assert report.security_counters["hardware_invocations"] == 0
    assert report.status == "GOLDEN VERIFIED"
    assert {item.case_id for item in report.cases} == set(range(1, 21))
    assert all(item.status == "PASS" for item in report.cases)
    assert any(item.case_id == 3 and item.status == "PASS" for item in report.cases)


def test_v67_runner_does_not_claim_golden_when_external_acceptance_paths_are_missing(tmp_path: Path) -> None:
    report = run_golden_cases(projects_root=tmp_path / "missing-projects", state_root=tmp_path / "missing-state")
    assert report.status == "PARTIAL VERIFIED"
    assert len(report.cases) == 20
    assert all(item.status == "UNAVAILABLE" for item in report.cases)
