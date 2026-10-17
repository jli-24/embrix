from __future__ import annotations

import ast
from pathlib import Path

from embedded_copilot.engineering_pcb import EngineeringPCBService, ports

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "embedded_copilot" / "engineering_pcb"


def production_files():
    return tuple(
        path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts
    )


def test_no_agent_workflow_llm_execution_or_hardware_imports():
    forbidden = (
        "agents",
        "langgraph",
        "langchain",
        "openai",
        "execution",
        "verification_loop",
        "engineering_repair",
        "subprocess",
        "requests",
        "httpx",
        "socket",
        "serial",
        "sqlite3",
    )
    for path in production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(
            any(part in name.casefold() for part in forbidden) for name in imports
        ), path


def test_no_filesystem_shell_network_eda_or_mutation_calls():
    forbidden_calls = {
        "open",
        "write",
        "write_text",
        "write_bytes",
        "unlink",
        "remove",
        "rmdir",
        "mkdir",
        "rename",
        "replace",
        "system",
        "popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "urlopen",
        "request",
        "connect",
        "commit",
        "checkout",
        "reset",
    }
    for path in production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id.casefold())
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr.casefold())
        assert calls.isdisjoint(forbidden_calls), (
            path,
            calls.intersection(forbidden_calls),
        )


def test_no_pcb_agent_is_defined():
    for path in production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        assert not any(name.endswith("Agent") for name in classes), path


def test_service_public_api_has_no_execution_or_decision_method():
    assert {
        name for name in dir(EngineeringPCBService) if not name.startswith("_")
    } == {"analyze", "build_context", "prepare_governance", "record_repair_handoff"}


def test_port_surface_is_narrow_and_has_no_mutation_api():
    port_names = {name for name in vars(ports) if name.endswith("Port")}
    assert port_names == {
        "PCBAnalysisPort",
        "PCBArtifactSourcePort",
        "PCBDatasheetSourcePort",
        "PCBEngineeringContextSourcePort",
        "PCBGraphPort",
        "PCBOptimizationPort",
        "PCBParserPort",
        "PCBRepairPort",
        "PCBValidationPort",
    }
    for name in port_names:
        methods = {
            method
            for method in vars(getattr(ports, name))
            if not method.startswith("_")
        }
        assert methods.isdisjoint(
            {"delete", "update", "overwrite", "apply", "verify", "execute"}
        )
