from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot"
PACKAGES = (
    "engineering_copilot",
    "hardware_intelligence",
    "pcb_intelligence",
    "firmware_intelligence",
    "execution",
)
FORBIDDEN_IMPORTS = (
    "embedded_copilot.agents",
    "embedded_copilot.model_runtime",
    "embedded_copilot.services.canonical_runtime",
    "embedded_copilot.services.legacy_runtime",
    "langgraph",
    "engineering_memory",
    "engineering_knowledge",
    "knowledge_governance",
)
FORBIDDEN_CALLS = {
    "promote",
    "approve_and_save",
    "write",
    "delete",
    "execute",
    "invoke",
    "run",
    "system",
    "popen",
}


def test_v40_packages_have_no_forbidden_dependencies_or_calls() -> None:
    for package in PACKAGES:
        for path in (ROOT / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names.append(node.module)
                    assert not any(
                        part in name.casefold()
                        for name in names
                        for part in FORBIDDEN_IMPORTS
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


def test_handoff_contract_does_not_store_raw_content() -> None:
    source = (ROOT / "engineering_copilot" / "contracts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handoff = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProposalReviewHandoff"
    )
    fields = {
        node.target.id
        for node in handoff.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert fields.isdisjoint({"content", "prompt", "context", "model_response"})
