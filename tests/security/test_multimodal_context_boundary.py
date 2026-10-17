from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_context")
FILES = (PACKAGE / "models.py", PACKAGE / "projection.py", PACKAGE / "fusion.py")
FORBIDDEN_IMPORTS = {
    "agents",
    "execution",
    "httpx",
    "langgraph",
    "openai",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "workflow",
    "zhipu",
}
FORBIDDEN_CALLS = {
    "delete",
    "execute",
    "flash",
    "invoke",
    "promote",
    "run",
    "system",
    "update",
    "write",
}


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id.casefold()
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.casefold()
    return ""


def test_multimodal_context_fusion_is_a_read_only_projection_boundary() -> None:
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name.casefold() for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = ((node.module or "").casefold(),)
            else:
                continue
            assert not any(
                forbidden in module
                for module in modules
                for forbidden in FORBIDDEN_IMPORTS
            ), path
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                assert _called_name(node) not in FORBIDDEN_CALLS, path


def test_multimodal_fusion_contracts_do_not_store_raw_assets_or_provider_data() -> None:
    tree = ast.parse((PACKAGE / "models.py").read_text(encoding="utf-8"))
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name in {"MultimodalContextEvidence", "MultimodalContextFusionRequest"}
    }
    fields = {
        node.target.id
        for model in classes.values()
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for forbidden in (
        "api_key",
        "credential",
        "image_bytes",
        "raw_payload",
        "raw_response",
        "prompt_history",
    ):
        assert forbidden not in fields
