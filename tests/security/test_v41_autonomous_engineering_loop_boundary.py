from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot"
PACKAGES = (
    "failure_analysis",
    "repair_proposal",
    "verification_loop",
    "autonomous_engineering_loop",
)
FORBIDDEN_IMPORT_PARTS = (
    "embedded_copilot.agents",
    "embedded_copilot.api",
    "embedded_copilot.autonomous_loop",
    "embedded_copilot.engineering_memory",
    "embedded_copilot.engineering_knowledge",
    "embedded_copilot.knowledge_governance",
    "embedded_copilot.workspace_runtime",
    "embedded_copilot.execution.adapters",
    "embedded_copilot.execution.service",
    "langgraph",
    "openai",
    "deepseek",
    "qwen",
    "subprocess",
    "socket",
    "serial",
)
FORBIDDEN_CALLS = {
    "apply_change",
    "approve",
    "approve_and_execute",
    "approve_and_save",
    "execute",
    "promote",
    "system",
}


def test_v41_services_have_no_agent_runtime_or_direct_execution_dependencies() -> None:
    for package in PACKAGES:
        for path in (ROOT / package).glob("**/*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name.casefold() for alias in node.names]
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names.append(node.module.casefold())
                    assert not any(
                        name.startswith(forbidden)
                        for name in names
                        for forbidden in FORBIDDEN_IMPORT_PARTS
                    ), path
                if isinstance(node, ast.Call):
                    called = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else ""
                    )
                    assert called.casefold() not in FORBIDDEN_CALLS, path


def test_v41_contracts_do_not_retain_raw_patch_or_log_fields() -> None:
    source = "\n".join(
        (ROOT / package / "contracts.py").read_text(encoding="utf-8")
        for package in PACKAGES
    ).casefold()
    for field in ("raw_log", "raw_payload", "patch_content", "prompt_history"):
        assert field not in source


def test_flash_is_only_reached_from_an_explicit_request_guard() -> None:
    source = (ROOT / "autonomous_engineering_loop" / "service.py").read_text(
        encoding="utf-8"
    )
    assert "checked_bundle.flash_request is not None" in source
    assert source.count("self._execution.flash(") == 1
