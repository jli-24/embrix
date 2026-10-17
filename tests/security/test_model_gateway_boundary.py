from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/model_gateway")


def _trees():
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_model_gateway_has_no_agent_runtime_sdk_or_execution_dependencies() -> None:
    from tests.security.boundary_audit import Sources, gateway_dependency_violations

    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = index.modules_under("embedded_copilot.model_gateway")
    assert gateway_dependency_violations(index, modules) == []


def test_model_gateway_has_no_mutation_or_execution_calls() -> None:
    from tests.security.boundary_audit import Sources, gateway_mutation_violations

    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = index.modules_under("embedded_copilot.model_gateway")
    assert gateway_mutation_violations(index, modules) == []
