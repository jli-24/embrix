from pathlib import Path

from scripts.v62_golden import run_golden_cases


def test_v62_golden_cases_are_offline_and_explicitly_optional() -> None:
    results = run_golden_cases(Path(__file__).resolve().parents[2])
    assert [item.case_id for item in results] == [f"{index}-" + name for index, name in enumerate(("firmware", "debug-cross-evidence", "hardfault", "hardware-firmware-conflict", "requirement", "pcb", "multimodal", "insufficient-evidence"), start=1)]
    assert all(item.status in {"PASS", "UNAVAILABLE"} for item in results)
    assert results[6].status == "UNAVAILABLE"
    assert results[3].status == "PASS"
    assert "0x69" in results[3].details
    assert "0x68" in results[3].details
    assert "both Evidence are bound" in results[3].details
