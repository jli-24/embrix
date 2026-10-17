from __future__ import annotations

import ast
from pathlib import Path

import pytest

from embedded_copilot.virtual_assembly import RootedArtifactReader

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot" / "virtual_assembly"


def test_virtual_assembly_has_no_second_execution_or_simulation_backend() -> None:
    forbidden_modules = {
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "serial",
        "renode",
        "wokwi",
        "ngspice",
    }
    forbidden_calls = {"system", "popen", "run", "Popen", "check_call", "check_output"}
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden_modules for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden_modules
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls


def test_rooted_reader_rejects_workspace_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "board.kicad_pcb").write_bytes(b"board")
    reader = RootedArtifactReader(root)

    assert reader.read("board.kicad_pcb") == b"board"
    with pytest.raises(ValueError):
        reader.read("../outside.kicad_pcb")
    with pytest.raises(ValueError):
        reader.read("C:/outside.kicad_pcb")

