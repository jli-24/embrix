from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot"
TARGETS = (
    ROOT / "model_runtime" / "contracts.py",
    ROOT / "model_runtime" / "models.py",
    ROOT / "model_runtime" / "service.py",
    ROOT / "model_runtime" / "factory.py",
    ROOT / "model_runtime" / "exceptions.py",
    ROOT / "model_runtime" / "adapters",
    ROOT / "workspace_projection",
    ROOT / "toolchain",
    ROOT / "component_recommendation",
)

FORBIDDEN_MODULES = {
    "os",
    "pathlib",
    "subprocess",
    "socket",
    "httpx",
    "requests",
    "sqlite3",
}


def _files() -> tuple[Path, ...]:
    values: list[Path] = []
    for target in TARGETS:
        values.extend((target,) if target.is_file() else target.rglob("*.py"))
    return tuple(values)


def test_v18_core_does_not_import_external_mutation_or_provider_runtime() -> None:
    from tests.security.boundary_audit import Sources, v18_violations

    index = Sources(ROOT.parent)
    modules = []
    for path in _files():
        parts = path.relative_to(ROOT.parent).with_suffix("").parts
        modules.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    assert v18_violations(index, modules) == []
