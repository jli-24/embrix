from __future__ import annotations

from pathlib import Path

from scripts.v70_production_release_golden import run_golden_cases


def test_v70_release_matrix_is_fixed_and_never_inherits_v67_golden(tmp_path: Path) -> None:
    report = run_golden_cases(projects_root=tmp_path / "projects", state_root=tmp_path / "state")
    assert report.product_version == "7.0.0"
    assert report.status == "PARTIAL VERIFIED"
    assert len(report.cases) == 24
    assert [item.case_id for item in report.cases] == list(range(1, 25))
    assert all(item.status == "UNVERIFIED" for item in report.cases)
    assert report.clean_machine_acceptance == "UNVERIFIED"
    assert report.browser_automation == "UNAVAILABLE / NOT PRESENT"
    assert report.hardware_golden == "DEFERRED"
