from __future__ import annotations

import ast
from pathlib import Path


CORE = Path("src/embedded_copilot/engineering_validation_proposal")
CORE_FILES = tuple(sorted(path for path in CORE.glob("*.py") if path.name != "__init__.py"))


def _trees() -> tuple[tuple[Path, ast.AST], ...]:
    return tuple((path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))) for path in CORE_FILES)


def test_canonical_core_has_no_provider_agent_or_execution_dependency() -> None:
    forbidden = {"agents", "agent_runtime", "model_gateway", "workspace", "execution", "subprocess", "socket", "requests", "urllib", "httpx", "openai", "langgraph", "langchain", "os", "pathlib"}
    for path, tree in _trees():
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(item.name.casefold() for item in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module.casefold(),)
            assert not any(token in module for module in modules for token in forbidden), path


def test_canonical_core_has_no_file_or_execution_calls() -> None:
    forbidden_calls = {"open", "write", "write_text", "write_bytes", "unlink", "mkdir", "remove", "run", "execute", "invoke", "flash", "build", "apply", "repair", "deploy", "shell"}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                assert name.casefold() not in forbidden_calls, (path, name)


def test_contract_fields_do_not_accept_raw_commands_or_sensitive_payloads() -> None:
    forbidden_fields = {"command", "shell", "script", "working_directory", "device_handle", "raw_source", "raw_log", "credential", "token", "secret", "provider_payload"}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id.casefold() not in forbidden_fields, (path, node.target.id)
