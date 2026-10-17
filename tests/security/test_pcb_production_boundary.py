from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pcb_service_and_serializer_have_no_workspace_or_process_side_effects() -> None:
    source = "\n".join(
        (ROOT / "src" / "embedded_copilot" / "pcb_production" / name).read_text(encoding="utf-8")
        for name in ("contracts.py", "service.py", "serializer.py")
    )
    assert "write_text(" not in source
    assert "write_bytes(" not in source
    assert "subprocess" not in source
    assert "WorkspaceApplyService" not in source


def test_pcb_worker_extensions_do_not_add_an_agent_or_second_worker() -> None:
    source = (ROOT / "src" / "embedded_copilot" / "execution_worker" / "pcb.py").read_text(encoding="utf-8")
    assert "class ExecutionWorker" not in source
    assert "class PCBAgent" not in source
    assert "shell=True" not in source
