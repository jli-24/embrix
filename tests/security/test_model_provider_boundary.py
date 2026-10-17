from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/model_gateway")
FORBIDDEN_IMPORTS = {
    "agents",
    "langgraph",
    "model_runtime",
    "workflow",
    "engineering_memory",
    "engineering_knowledge",
    "knowledge_governance",
    "execution",
    "subprocess",
    "socket",
    "requests",
    "urllib",
}
FORBIDDEN_CALLS = {
    "write",
    "update",
    "delete",
    "execute",
    "run",
    "invoke",
    "promote",
    "system",
}


def test_real_provider_adapters_are_http_only_and_service_isolation_is_preserved() -> None:
    from tests.security.boundary_audit import Sources, gateway_dependency_violations

    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = index.modules_under("embedded_copilot.model_gateway")
    assert gateway_dependency_violations(index, modules) == []


def test_provider_boundary_has_no_mutation_or_secret_dto_fields() -> None:
    from tests.security.boundary_audit import Sources, gateway_mutation_violations

    contracts = ast.parse((PACKAGE / "contracts.py").read_text(encoding="utf-8"))
    fields = {
        node.target.id
        for model in contracts.body
        if isinstance(model, ast.ClassDef)
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for forbidden in ("api_key", "credential", "raw_payload", "prompt_history"):
        assert forbidden not in fields
    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = index.modules_under("embedded_copilot.model_gateway")
    assert gateway_mutation_violations(index, modules) == []
