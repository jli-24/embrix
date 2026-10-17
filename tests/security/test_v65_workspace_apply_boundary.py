from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path("src/embedded_copilot/engineering_workspace_apply")


def test_v65_public_contract_has_no_shell_or_device_fields() -> None:
    forbidden = {"command", "shell", "script", "cwd", "working_directory", "device_handle", "provider_payload", "raw_source", "raw_log", "secret", "credential"}
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id.casefold() not in forbidden, (path, node.target.id)


def test_v65_apply_package_is_not_reachable_from_runtime_agents() -> None:
    agents = Path("src/embedded_copilot/agents")
    for path in agents.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "engineering_workspace_apply" not in source
        assert "WorkspaceApplyService" not in source


def test_v65_recovery_receipt_contains_metadata_not_source() -> None:
    source = Path("src/embedded_copilot/engineering_workspace_apply/models.py").read_text(encoding="utf-8")
    assert "recovery_object_reference" in source
    assert "checksum" in source
    assert "after_content" not in source.split("class RecoveryReceipt", 1)[1].split("class ApplyReceipt", 1)[0]
