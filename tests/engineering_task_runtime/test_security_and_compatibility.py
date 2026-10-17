from __future__ import annotations

import ast
from pathlib import Path

import embedded_copilot
from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_qa import EngineeringQuestion
from embedded_copilot.model_gateway import ModelRequest

PACKAGE = Path("src/embedded_copilot/engineering_task_runtime")


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def test_runtime_has_no_direct_repository_provider_sqlite_or_execution_bypass() -> None:
    forbidden_roots = {
        "sqlite3",
        "subprocess",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "socket",
    }
    forbidden_prefixes = (
        "embedded_copilot.engineering_memory.foundation_repository",
        "embedded_copilot.engineering_memory.foundation_sqlite",
        "embedded_copilot.model_gateway",
        "embedded_copilot.model_provider",
        "embedded_copilot.execution_worker",
        "embedded_copilot.execution.adapters",
    )
    for path in PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for module in _imports(tree):
            assert module.split(".", 1)[0] not in forbidden_roots, (path, module)
            assert not module.startswith(forbidden_prefixes), (path, module)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "open", "__import__"}


def test_existing_agents_do_not_access_foundation_repository_or_sqlite() -> None:
    for relative in (
        "hardware/agent.py",
        "firmware/agent.py",
        "debug/agent.py",
    ):
        source = Path("src/embedded_copilot", relative).read_text(encoding="utf-8")
        imports = _imports(ast.parse(source))
        assert not any("engineering_memory" in module for module in imports)
        assert "sqlite3" not in imports
        assert "subprocess" not in imports


def test_no_parallel_generic_agent_framework_is_introduced() -> None:
    forbidden = {
        "BaseAgentV2",
        "AgentManager",
        "AgentRegistryV2",
        "AgentBus",
        "AgentKernel",
    }
    found: set[str] = set()
    for path in Path("src/embedded_copilot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name in forbidden
        )
    assert found == set()


def test_public_contract_fields_keep_legacy_prefix_and_add_gateway_metadata() -> None:
    assert tuple(EngineeringQuestion.model_fields) == (
        "question_id",
        "project_id",
        "question",
        "constraints",
        "fingerprint",
    )
    assert tuple(ApplicationQuestionRequest.model_fields) == (
        "request_id",
        "trace_id",
        "project_id",
        "tenant_id",
        "requester",
        "question",
        "constraints",
        "external_search",
        "requested_at",
        "fingerprint",
    )
    assert tuple(ModelRequest.model_fields) == (
        "request_id",
        "task",
        "engineering_context",
        "system_instruction",
        "model_policy",
        "constraints",
        "prompt",
        "system_prompt",
        "context",
        "temperature",
        "max_tokens",
        "metadata",
        "project_id",
        "conversation_id",
        "endpoint_id",
        "credential_ref",
        "task_type",
        "messages",
        "attachments",
        "images",
        "required_capabilities",
        "preferred_model",
        "preferred_provider",
        "routing_strategy",
        "tool_definitions",
        "structured_output_schema",
        "fingerprint",
    )
    assert not hasattr(embedded_copilot, "EngineeringTaskOrchestrator")
    assert not hasattr(embedded_copilot, "EngineeringTaskPlanner")


def test_default_application_and_engineering_qa_do_not_import_opt_in_runtime() -> None:
    for path in (
        Path("src/embedded_copilot/application/desktop_composition.py"),
        Path("src/embedded_copilot/engineering_qa/service.py"),
        Path("src/embedded_copilot/agents/workflow.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "engineering_task_runtime" not in source
