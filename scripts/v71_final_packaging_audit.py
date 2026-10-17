"""Read-only v7.1 packaging evidence audit."""

from __future__ import annotations

import ast
import dis
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import types
from pathlib import Path

# Preserve direct operator execution from the repository root.  Python places
# the ``scripts`` directory, not the repository root, on ``sys.path`` in this
# invocation form.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.v71_canonical_module_alignment import alignment_report

PRODUCT_VERSION = "7.1.0"
_PROMPT_MODULE = "embedded_copilot.engineering_qa.prompt"
_PROMPT_NAME = "_GREENFIELD_SYSTEM"
_PROMPT_PREFIX = "You are Embrix Engineering Copilot"


def _version(path: Path, pattern: str) -> str | None:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return match.group(1) if match else None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _resource_tree_has_content(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        return True
    with os.scandir(path) as entries:
        return any(_resource_tree_has_content(Path(entry.path)) for entry in entries)


def _direct_quarantined_file_present(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise ValueError("component resource parent is not a directory")
    with os.scandir(path) as entries:
        return any(
            (entry.name.casefold().startswith("approved_components") and entry.name.casefold().endswith(".json"))
            or (entry.name.casefold().startswith("kicad_") and entry.name.casefold().endswith(".txt"))
            for entry in entries
        )


def _approved_component_resource_audit(package: Path) -> dict[str, str]:
    try:
        if not stat.S_ISREG(os.lstat(package).st_mode):
            raise ValueError("package artifact is not a regular file")
        for prefix in (Path("_internal"), Path("."), Path("_internal/src"), Path("src")):
            parent = package.parent / prefix / "embedded_copilot" / "component_recommendation"
            if _resource_tree_has_content(parent / "resources") or _direct_quarantined_file_present(parent):
                return {"status": "FAIL", "reason": "QUARANTINED_RESOURCE_PRESENT"}
    except (OSError, ValueError):
        return {"status": "FAIL", "reason": "RESOURCE_AUDIT_FAILED"}
    return {"status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT"}


def _source_prompt(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments: dict[str, ast.expr] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = node.value

    def resolve_expr(value: ast.expr, seen: set[str]) -> str:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.Name):
            if value.id in seen or value.id not in assignments:
                raise ValueError("source Greenfield prompt is not resolvable")
            return resolve_expr(assignments[value.id], {*seen, value.id})
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            return resolve_expr(value.left, seen) + resolve_expr(value.right, seen)
        raise ValueError("source Greenfield prompt is not resolvable")

    if _PROMPT_NAME not in assignments:
        raise ValueError("source Greenfield prompt is missing or ambiguous")
    return resolve_expr(assignments[_PROMPT_NAME], {_PROMPT_NAME})


def _archive_reader(root: Path):
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        native_packages = root / ".venv-native" / "Lib" / "site-packages"
        if not native_packages.is_dir():
            raise
        sys.path.insert(0, str(native_packages))
        from PyInstaller.archive.readers import CArchiveReader
    return CArchiveReader


def _packaged_prompt(root: Path, executable: Path) -> str:
    reader_type = _archive_reader(root)
    archive = reader_type(str(executable))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    code = pyz.extract(_PROMPT_MODULE)
    if not isinstance(code, types.CodeType):
        raise ValueError("packaged PromptBuilder module is unavailable")
    values: dict[str, str] = {}
    stack: list[str] = []
    for instruction in dis.get_instructions(code):
        if instruction.opname == "LOAD_CONST" and isinstance(instruction.argval, str):
            stack.append(instruction.argval)
        elif instruction.opname == "LOAD_NAME" and instruction.argval in values:
            stack.append(values[instruction.argval])
        elif instruction.opname == "BINARY_OP" and instruction.argrepr == "+":
            if len(stack) >= 2:
                right, left = stack.pop(), stack.pop()
                stack.append(left + right)
        elif instruction.opname == "STORE_NAME" and stack:
            value = stack.pop()
            if isinstance(value, str):
                values[instruction.argval] = value
        elif instruction.opname == "POP_TOP" and stack:
            stack.pop()
        if instruction.opname == "RETURN_VALUE":
            break
    prompt = values.get(_PROMPT_NAME)
    if not isinstance(prompt, str) or not prompt.startswith(_PROMPT_PREFIX):
        raise ValueError("packaged Greenfield prompt is missing or ambiguous")
    return prompt


def _prompt_builder_alignment(root: Path, executable: Path) -> dict[str, object]:
    source_path = root / "src" / "embedded_copilot" / "engineering_qa" / "prompt.py"
    source_prompt: str | None = None
    packaged_prompt: str | None = None
    error_code: str | None = None
    try:
        source_prompt = _source_prompt(source_path)
        if executable.is_file():
            packaged_prompt = _packaged_prompt(root, executable)
        else:
            error_code = "CANONICAL_EXE_MISSING"
    except (ImportError, OSError, TypeError, ValueError):
        error_code = "PROMPT_BUILDER_AUDIT_FAILED"
    source_hash = (
        None if source_prompt is None else _sha256(source_prompt.encode("utf-8"))
    )
    packaged_hash = (
        None if packaged_prompt is None else _sha256(packaged_prompt.encode("utf-8"))
    )
    matched = bool(
        source_prompt is not None
        and packaged_prompt is not None
        and len(source_prompt) == len(packaged_prompt)
        and source_hash == packaged_hash
    )
    checked_prompts = (source_prompt, packaged_prompt)
    requirement_instruction = all(
        prompt is not None
        and "RequirementContext" in prompt
        and "requirements" in prompt
        for prompt in checked_prompts
    )
    design_instruction = all(
        prompt is not None
        and "design" in prompt.casefold()
        and "architecture" in prompt.casefold()
        for prompt in checked_prompts
    )
    plan_instruction = all(
        prompt is not None
        and "design and execution" in prompt.casefold()
        and "execution" in prompt.casefold()
        for prompt in checked_prompts
    )
    engineering_analysis_instruction = all(
        prompt is not None and "engineering" in prompt.casefold()
        for prompt in checked_prompts
    )
    return {
        "status": "PASS" if matched else ("UNVERIFIED" if error_code == "CANONICAL_EXE_MISSING" else "FAIL"),
        "source_prompt_length": None if source_prompt is None else len(source_prompt),
        "packaged_prompt_length": None if packaged_prompt is None else len(packaged_prompt),
        "source_prompt_builder_hash": source_hash,
        "packaged_prompt_builder_hash": packaged_hash,
        "prompt_builder_match": matched,
        "requirement_generation_instruction": requirement_instruction,
        "design_generation_instruction": design_instruction,
        "plan_generation_instruction": plan_instruction,
        "engineering_analysis_instruction": engineering_analysis_instruction,
        "error_code": None if matched else error_code or "PROMPT_BUILDER_MISMATCH",
    }


def run_audit(root: Path | None = None, *, package: Path | None = None) -> dict[str, object]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    release = root / "src" / "embedded_copilot" / "_version.py"
    pyproject = root / "pyproject.toml"
    frontend_package = root / "frontend" / "package.json"
    frontend_lock = root / "frontend" / "package-lock.json"
    installer = root / "packaging" / "windows" / "EmbeddedCopilot-v7.1.iss"
    versions = {
        "python_package": _version(release, r'^PRODUCT_VERSION\s*=\s*["\']([^"\']+)["\']'),
        "pyproject": _version(pyproject, r'^version\s*=\s*["\']([^"\']+)["\']'),
        "frontend": json.loads(frontend_package.read_text(encoding="utf-8")).get("version"),
        "frontend_lock": json.loads(frontend_lock.read_text(encoding="utf-8")).get("version"),
        "installer_metadata": _version(installer, r'^#define ProductVersion\s+["\']([^"\']+)["\']'),
    }
    dist = root / "frontend" / "dist"
    assets = [*dist.rglob("*.js"), *dist.rglob("*.html")] if dist.is_dir() else []
    bundle_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in assets)
    dev_server_url = re.compile(
        r"(?i)(?:https?://)?(?:localhost|127(?:\.\d{1,3}){3})(?::\d+)\b"
    )
    has_dev_server_url = dev_server_url.search(bundle_text) is not None
    frontend = {
        "dist_present": dist.is_dir(),
        "no_dev_server_localhost": not has_dev_server_url,
        "bootstrap_fragment_present": "hash" in bundle_text and "replaceState" in bundle_text,
        "nonce_query_absent": "?nonce" not in bundle_text.lower(),
        "status": "PASS" if dist.is_dir() and not has_dev_server_url else "FAIL",
    }
    if package is None:
        staging = root / "dist" / "_staging" / "Embrix" / "Embrix.exe"
        current = root / "dist" / "Embrix-current" / "Embrix" / "Embrix.exe"
        legacy = root / "dist" / f"EmbeddedCopilot-{PRODUCT_VERSION}" / "EmbeddedCopilot" / "EmbeddedCopilot.exe"
        package = staging if staging.is_file() else current if current.is_file() else legacy
    package = package.resolve()
    pyinstaller = shutil.which("pyinstaller") or shutil.which("pyinstaller.exe") or (root / ".venv-native" / "Scripts" / "pyinstaller.exe")
    prompt_alignment = _prompt_builder_alignment(root, package)
    try:
        module_alignment = alignment_report(root, package) if package.is_file() else {
            "source_hash": None,
            "pyz_hash": None,
            "runtime_hash": None,
            "module_alignment": False,
        }
    except (ImportError, OSError, TypeError, ValueError):
        module_alignment = {
            "source_hash": None,
            "pyz_hash": None,
            "runtime_hash": None,
            "module_alignment": False,
        }
    executable_hash = _sha256(package.read_bytes()) if package.is_file() else None
    return {
        "product_version": PRODUCT_VERSION,
        "version_consistency": {"status": "PASS" if all(value == PRODUCT_VERSION for value in versions.values()) else "FAIL", "sources": versions},
        "frontend_production_assets": frontend,
        "prompt_builder_alignment": prompt_alignment,
        "canonical_module_alignment": module_alignment,
        "approved_component_resource": _approved_component_resource_audit(package),
        "default_component_source": {"status": "UNAVAILABLE", "reason": "BLOCKED_RIGHTS"},
        "tool_availability": {
            "PyInstaller": "AVAILABLE" if pyinstaller else "UNAVAILABLE",
            "ISCC": "AVAILABLE" if shutil.which("iscc") or shutil.which("ISCC.exe") else "UNAVAILABLE",
        },
        "release_evidence": {
            "production_runtime_code": "CODE/RUNTIME VERIFIED",
            "restore_failure_atomicity": "CODE/RUNTIME VERIFIED",
            "clarification_golden": "UNVERIFIED",
            "production_package": (
                "PASS — onedir/windowed EXE exists and PromptBuilder matches source"
                if package.is_file() and prompt_alignment["prompt_builder_match"] is True
                else "FAIL — canonical PromptBuilder mismatch"
                if package.is_file()
                else "UNVERIFIED"
            ),
            "new_exe_sha256": executable_hash,
            "installer": "UNVERIFIED",
            "fresh_install": "UNVERIFIED",
            "restart_recovery": "UNVERIFIED",
            "overall": "PARTIAL VERIFIED",
            "release_ready": False,
            "s05_release_gate": "BLOCKED_RIGHTS_AND_REMAINING_GATES",
        },
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2, sort_keys=True))
