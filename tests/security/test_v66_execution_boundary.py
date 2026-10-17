from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path("src/embedded_copilot")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def test_agents_and_api_have_no_subprocess_or_execution_worker_import() -> None:
    from tests.security.boundary_audit import Sources, agent_api_execution_violations

    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = (
        *index.modules_under("embedded_copilot.agents"),
        *index.modules_under("embedded_copilot.api"),
    )
    assert agent_api_execution_violations(index, modules) == []


def test_worker_is_the_only_new_subprocess_boundary() -> None:
    for path in (ROOT / "execution_worker").rglob("*.py"):
        imports = _imports(path)
        if "subprocess" in imports:
            assert path.name == "adapters.py"


def test_public_execution_contracts_do_not_define_command_or_path_inputs() -> None:
    forbidden = {"command", "shell", "script", "powershell", "bash", "cmd", "cwd", "working_directory", "executable_path", "arbitrary_args", "environment"}
    contracts = ROOT / "execution_worker" / "contracts.py"
    tree = ast.parse(contracts.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        assert not ({name.casefold() for name in names} & forbidden), node
