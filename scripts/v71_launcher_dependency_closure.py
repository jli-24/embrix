"""Verify the reachable launcher dependency closure in a PyInstaller package."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import marshal
import sys
import types
from collections import deque
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.v71_canonical_module_alignment import canonical_code_hash


LAUNCHER_ENTRYPOINT = "v71_desktop_launcher"
LAUNCHER_DEPENDENCY = "v70_desktop_launcher"
KNOWN_HIGH_RISK_MODULES = (
    "embedded_copilot.api.main",
    "embedded_copilot.api.optimization_v26_routes",
    "embedded_copilot.knowledge.manifest",
)
KNOWN_REQUIRED_CLOSURE = (
    LAUNCHER_ENTRYPOINT,
    LAUNCHER_DEPENDENCY,
    *KNOWN_HIGH_RISK_MODULES,
)
_SCRIPT_MODULES = frozenset({LAUNCHER_ENTRYPOINT, LAUNCHER_DEPENDENCY})


def _source_path(root: Path, module: str) -> Path | None:
    if module in _SCRIPT_MODULES:
        path = root / "scripts" / f"{module}.py"
        return path if path.is_file() else None
    module_path = root / "src" / Path(*module.split("."))
    source_path = module_path.with_suffix(".py")
    if source_path.is_file():
        return source_path
    init_path = module_path / "__init__.py"
    return init_path if init_path.is_file() else None


def _absolute_import(
    current_module: str,
    module: str | None,
    level: int,
    *,
    current_is_package: bool = False,
) -> str:
    if level == 0:
        return module or ""
    package = current_module.split(".") if current_is_package else current_module.split(".")[:-1]
    if level > len(package) + 1:
        return ""
    base = package[: len(package) - level + 1]
    return ".".join((*base, *(module or "").split("."))).strip(".")


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        return None if left is None or right is None else left + right
    return None


def _imports(
    tree: ast.AST,
    current_module: str,
    *,
    current_is_package: bool = False,
) -> tuple[set[str], set[str], int]:
    static_imports: set[str] = set()
    dynamic_imports: set[str] = set()
    unknown_dynamic_imports = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            static_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_import(
                current_module,
                node.module,
                node.level,
                current_is_package=current_is_package,
            )
            if base:
                static_imports.add(base)
                static_imports.update(f"{base}.{alias.name}" for alias in node.names)
            elif node.level == 0:
                static_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "import_module":
                if not (
                    isinstance(function.value, ast.Name)
                    and function.value.id == "importlib"
                    and len(node.args) == 1
                ):
                    unknown_dynamic_imports += 1
                    continue
                value = _constant_string(node.args[0])
                if value is None:
                    unknown_dynamic_imports += 1
                else:
                    dynamic_imports.add(value)
    return static_imports, dynamic_imports, unknown_dynamic_imports


def _reachable_project_modules(
    root: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """Return source-reachable local modules and literal dynamic imports."""

    queue: deque[str] = deque((LAUNCHER_ENTRYPOINT,))
    reachable: set[str] = set()
    dynamic: set[str] = set()
    unknown_dynamic_imports = 0
    while queue:
        module = queue.popleft()
        if module in reachable:
            continue
        path = _source_path(root, module)
        if path is None:
            continue
        reachable.add(module)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        static_imports, dynamic_imports, unknown_count = _imports(
            tree,
            module,
            current_is_package=path.name == "__init__.py",
        )
        dynamic.update(dynamic_imports)
        unknown_dynamic_imports += unknown_count
        for candidate in static_imports:
            if _source_path(root, candidate) is not None and candidate not in reachable:
                queue.append(candidate)
        for candidate in dynamic_imports:
            if _source_path(root, candidate) is not None and candidate not in reachable:
                queue.append(candidate)
    return tuple(sorted(reachable)), tuple(sorted(dynamic)), unknown_dynamic_imports


def reachable_project_modules(root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return source-reachable local modules and literal dynamic imports."""

    reachable, dynamic, _ = _reachable_project_modules(root)
    return reachable, dynamic


def _archive(root: Path, executable: Path) -> Any:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        native_packages = root / ".venv-native" / "Lib" / "site-packages"
        sys.path.insert(0, str(native_packages))
        from PyInstaller.archive.readers import CArchiveReader
    return CArchiveReader(str(executable))


def _hash_manifest(values: dict[str, str]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _entry_code_hash(archive: Any, module: str) -> str | None:
    try:
        value = archive.extract(module)
        code = marshal.loads(value)
    except (KeyError, EOFError, OSError, TypeError, ValueError):
        return None
    return canonical_code_hash(code) if isinstance(code, types.CodeType) else None


def _source_code_hashes(root: Path, modules: tuple[str, ...]) -> dict[str, str]:
    return {
        module: canonical_code_hash(
            compile(
                _source_path(root, module).read_text(encoding="utf-8"),
                f"<{module}>",
                "exec",
                dont_inherit=True,
                optimize=0,
            )
        )
        for module in modules
        if _source_path(root, module) is not None
    }


def dependency_closure_report(root: Path, executable: Path) -> dict[str, object]:
    checked_root = root.resolve()
    checked_executable = executable.resolve()
    reachable, dynamic_imports, unknown_dynamic_imports = _reachable_project_modules(checked_root)
    local_dynamic = tuple(
        module for module in dynamic_imports if _source_path(checked_root, module) is not None
    )
    unresolved_dynamic = tuple(
        module
        for module in dynamic_imports
        if (module.startswith("embedded_copilot") or module.startswith("v"))
        and _source_path(checked_root, module) is None
    )
    source_hashes = _source_code_hashes(checked_root, reachable)

    try:
        display_executable = str(checked_executable.relative_to(checked_root))
    except ValueError:
        display_executable = "<outside-root>"
    report: dict[str, object] = {
        "executable": display_executable,
        "scripts_is_python_package": (checked_root / "scripts" / "__init__.py").is_file(),
        "v71_v70_import_style": "from v70_desktop_launcher import run",
        "expected_runtime_module_name": LAUNCHER_DEPENDENCY,
        "known_required_closure": list(KNOWN_REQUIRED_CLOSURE),
        "normal_reachable_project_modules": list(reachable),
        "dynamic_project_local_imports": list(local_dynamic),
        "unresolved_dynamic_project_local_imports": list(unresolved_dynamic),
        "unknown_dynamic_import_expressions": unknown_dynamic_imports,
        "source_fingerprint": _hash_manifest(source_hashes),
        "runtime_E1_RUNTIME_IMPORT": "NOT_VERIFIED_BY_PYZ_CHECK",
    }

    if not checked_executable.is_file():
        report.update(
            {
                "build_time_v71_entry_present": False,
                "build_time_pyz_v71_present": False,
                "build_time_pyz_v70_present": False,
                "packaged_v71_import": False,
                "packaged_v70_import": False,
                "launcher_dependency_closure": False,
                "error_type": "PACKAGE_MISSING",
            }
        )
        return report

    try:
        archive = _archive(checked_root, checked_executable)
        pyz = archive.open_embedded_archive("PYZ.pyz")
        names = set(pyz.toc)
        packaged_hashes: dict[str, str] = {}
        import_results: dict[str, bool] = {}
        for module in reachable:
            if module == LAUNCHER_ENTRYPOINT:
                entry_hash = _entry_code_hash(archive, module)
                import_results[module] = entry_hash is not None
                if entry_hash is not None:
                    packaged_hashes[module] = entry_hash
                continue
            try:
                code = pyz.extract(module)
            except (KeyError, OSError, TypeError, ValueError):
                code = None
            import_results[module] = isinstance(code, types.CodeType)
            if isinstance(code, types.CodeType):
                packaged_hashes[module] = canonical_code_hash(code)
        missing_reachable = tuple(module for module in reachable if not import_results[module])
        launcher_variants = (
            "v70_desktop_launcher",
            "v71_desktop_launcher",
            "scripts.v70_desktop_launcher",
            "scripts.v71_desktop_launcher",
        )
        archive_names = set(archive.toc)
        launcher_locations = {
            module: {
                "carchive": module in archive_names,
                "pyz": module in names,
            }
            for module in launcher_variants
        }
        duplicate_launchers = tuple(
            module
            for module, locations in launcher_locations.items()
            if sum(bool(value) for value in locations.values()) > 1
            or module.startswith("scripts.") and any(locations.values())
        )
        source_fingerprint = _hash_manifest(source_hashes)
        package_source_fingerprint = _hash_manifest(packaged_hashes)
        report.update(
            {
                "build_time_v71_entry_present": LAUNCHER_ENTRYPOINT in archive.toc,
                "build_time_pyz_v71_present": False,
                "build_time_pyz_v70_present": import_results.get(LAUNCHER_DEPENDENCY, False),
                "packaged_v71_import": LAUNCHER_ENTRYPOINT in archive.toc,
                "packaged_v70_import": import_results.get(LAUNCHER_DEPENDENCY, False),
                "missing_normal_reachable_project_modules": list(missing_reachable),
                "duplicate_launcher_modules": list(duplicate_launchers),
                "launcher_identity_locations": launcher_locations,
                "package_source_fingerprint": package_source_fingerprint,
                "launcher_dependency_closure": bool(
                    report["scripts_is_python_package"]
                    and unknown_dynamic_imports == 0
                    and not unresolved_dynamic
                    and not missing_reachable
                    and not duplicate_launchers
                    and all(import_results.get(module, False) for module in KNOWN_REQUIRED_CLOSURE)
                    and LAUNCHER_ENTRYPOINT in archive.toc
                    and source_fingerprint == package_source_fingerprint
                ),
            }
        )
    except (ImportError, OSError, TypeError, ValueError) as error:
        report.update(
            {
                "build_time_v71_entry_present": False,
                "build_time_pyz_v71_present": False,
                "build_time_pyz_v70_present": False,
                "packaged_v71_import": False,
                "packaged_v70_import": False,
                "launcher_dependency_closure": False,
                "error_type": type(error).__name__,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--exe", type=Path, required=True)
    args = parser.parse_args()
    report = dependency_closure_report(args.root, args.exe)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("launcher_dependency_closure") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
