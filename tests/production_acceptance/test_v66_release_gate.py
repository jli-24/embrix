from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_v66_release_docs_do_not_claim_hardware_golden_without_evidence() -> None:
    status = (ROOT / "docs/verification/v6.6-controlled-execution-status.md").read_text(encoding="utf-8")
    closure = (ROOT / "docs/verification/v6.6-final-runtime-golden-closure.md").read_text(encoding="utf-8")
    assert "Overall v6.6                  = PARTIAL VERIFIED" in status
    assert "Hardware Golden               = DEFERRED" in status
    assert "当前状态：`PARTIAL VERIFIED`" in closure
    assert "禁止写入 `GOLDEN VERIFIED`" in closure


def test_v66_release_gate_is_explicitly_non_hardware_by_default() -> None:
    script = (ROOT / "scripts/v66_controlled_execution_golden.py").read_text(encoding="utf-8")
    assert '"hardware_evidence": False' in script
    assert '"overall": "PARTIAL VERIFIED"' in script
