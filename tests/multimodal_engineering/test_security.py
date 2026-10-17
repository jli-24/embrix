from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "src" / "embedded_copilot" / "multimodal_engineering"


def _sources():
    return tuple(PACKAGE.rglob("*.py"))


def test_only_glm_adapter_may_import_http_client() -> None:
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        if "httpx" in imports:
            assert path.as_posix().endswith("adapters/glm46.py")


def test_package_has_no_mutation_execution_agent_or_old_vision_boundary() -> None:
    forbidden_import_parts = {
        "subprocess",
        "shutil",
        "execution",
        "engineering_repair",
        "hardware",
        "agents",
        "workflow",
        "langgraph",
        "torch",
        "tensorflow",
    }
    forbidden_calls = {
        "open",
        "write",
        "write_text",
        "write_bytes",
        "copy",
        "copyfile",
        "system",
        "popen",
        "run",
        "flash",
        "build",
        "apply",
        "execute",
    }
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = (node.module or "",)
            else:
                continue
            assert not any(
                part in name.casefold().split(".")
                for name in names
                for part in forbidden_import_parts
            ), path
            assert not any("model_gateway.vision" in name for name in names), path
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            assert name.casefold() not in forbidden_calls, path


def test_public_contract_and_audit_fields_exclude_sensitive_payloads() -> None:
    forbidden = {
        "bytes",
        "image_bytes",
        "pdf_bytes",
        "base64",
        "prompt",
        "provider_payload",
        "raw_response",
        "api_key",
        "credential",
        "media_url",
        "file_path",
        "local_path",
        "hidden_reasoning",
    }
    for filename in ("models.py", "audit.py", "application.py"):
        tree = ast.parse((PACKAGE / filename).read_text(encoding="utf-8"))
        fields = {
            node.target.id
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef)
            for node in cls.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        assert fields.isdisjoint(forbidden)
