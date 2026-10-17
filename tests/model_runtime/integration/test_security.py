from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[3]
SOURCE = ROOT / "src" / "embedded_copilot" / "model_runtime" / "integration"


def _trees() -> tuple[tuple[Path, ast.AST], ...]:
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in SOURCE.rglob("*.py")
    )


def test_integration_import_boundary_excludes_execution_and_mutation_layers() -> None:
    forbidden = {
        "agents",
        "workflow",
        "execution",
        "repair",
        "hardware",
        "subprocess",
        "socket",
        "requests",
        "git",
        "database",
        "sqlalchemy",
    }
    for path, tree in _trees():
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
        assert not any(
            part in forbidden for name in imports for part in name.casefold().split(".")
        ), path


def test_integration_has_no_filesystem_shell_or_unsafe_http_switches() -> None:
    forbidden_calls = {
        "open",
        "exec",
        "eval",
        "system",
        "popen",
        "run",
        "remove",
        "unlink",
        "write_text",
        "write_bytes",
    }
    for path, tree in _trees():
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        calls.update(
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        )
        assert calls.isdisjoint(forbidden_calls), path
        text = path.read_text(encoding="utf-8")
        assert "trust_env=True" not in text
        assert "follow_redirects=True" not in text
        assert "verify=False" not in text


def test_credentials_are_not_exposed_by_binding_or_error_text() -> None:
    from embedded_copilot.model_runtime.integration import (
        ProductionModelSettings,
        create_production_model_binding,
    )

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "production-secret",
        }
    )
    binding = create_production_model_binding(settings)
    assert "production-secret" not in repr(settings)
    assert "production-secret" not in repr(binding)
    assert not hasattr(binding, "api_key")
