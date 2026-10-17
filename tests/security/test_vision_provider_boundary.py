from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/model_gateway/vision")
FORBIDDEN_IMPORTS = {
    "agents",
    "chromadb",
    "engineering_memory",
    "engineering_knowledge",
    "execution",
    "httpx",
    "knowledge_governance",
    "langgraph",
    "model_runtime",
    "openai",
    "os",
    "pathlib",
    "qwen",
    "random",
    "requests",
    "socket",
    "subprocess",
    "tempfile",
    "time",
    "urllib",
    "vision_runtime",
    "workflow",
    "zhipu",
}
FORBIDDEN_CALLS = {
    "delete",
    "execute",
    "invoke",
    "promote",
    "run",
    "system",
    "update",
    "write",
    "open",
}


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id.casefold()
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.casefold()
    return ""


def test_vision_provider_contract_layer_has_no_external_or_mutation_dependencies() -> None:
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name.casefold() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = ((node.module or "").casefold(),)
            else:
                continue
            allowed = path == PACKAGE / "adapters" / "glm_vision.py"
            allowed_imports = {"httpx", "os"} if allowed else set()
            forbidden_imports = FORBIDDEN_IMPORTS - allowed_imports
            assert not any(
                forbidden in name for name in names for forbidden in forbidden_imports
            ), path
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert _called_name(node) not in FORBIDDEN_CALLS, path


def test_vision_contracts_do_not_accept_or_store_secrets_or_raw_assets() -> None:
    contracts = ast.parse((PACKAGE / "contracts.py").read_text(encoding="utf-8"))
    fields = {
        node.target.id
        for model in contracts.body
        if isinstance(model, ast.ClassDef)
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for forbidden in (
        "api_key",
        "credential",
        "endpoint",
        "image_bytes",
        "prompt_history",
        "raw_payload",
    ):
        assert forbidden not in fields


def test_fake_adapter_has_no_time_randomness_or_environment_access() -> None:
    adapter = ast.parse((PACKAGE / "adapters" / "fake.py").read_text(encoding="utf-8"))
    forbidden_attributes = {"environ", "getenv", "now", "time", "random"}
    for node in ast.walk(adapter):
        if isinstance(node, ast.Attribute):
            assert node.attr.casefold() not in forbidden_attributes


def test_glm_adapter_is_the_only_http_and_environment_boundary() -> None:
    adapter = ast.parse(
        (PACKAGE / "adapters" / "glm_vision.py").read_text(encoding="utf-8")
    )
    fields = {
        node.target.id
        for model in adapter.body
        if isinstance(model, ast.ClassDef)
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for forbidden in ("raw_payload", "prompt_history", "endpoint", "credential"):
        assert forbidden not in fields
