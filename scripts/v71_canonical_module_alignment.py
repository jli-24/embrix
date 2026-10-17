"""Verify source, packaged PYZ, and live process-image module alignment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import types
from pathlib import Path
from typing import Mapping


TARGET_MODULES = (
    "embedded_copilot.application.bindings",
    "embedded_copilot.application.models",
    "embedded_copilot.application.runtime",
    "embedded_copilot.desktop_copilot_chat.models",
    "embedded_copilot.desktop_copilot_chat.runtime",
    "embedded_copilot.engineering_qa.prompt",
    "embedded_copilot.engineering_qa.service",
    "embedded_copilot.production_release.provider",
    "embedded_copilot.runtime_diagnostics",
)


def _constant(value: object) -> object:
    if isinstance(value, types.CodeType):
        return {"code": _code_payload(value)}
    if value is None or isinstance(value, (bool, int, str)):
        return {type(value).__name__: value}
    if isinstance(value, float):
        rendered = (
            "nan"
            if math.isnan(value)
            else "inf"
            if math.isinf(value) and value > 0
            else "-inf"
            if math.isinf(value)
            else value.hex()
        )
        return {"float": rendered}
    if isinstance(value, complex):
        return {"complex": (value.real.hex(), value.imag.hex())}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [_constant(item) for item in value]}
    if isinstance(value, frozenset):
        items = [_constant(item) for item in value]
        return {
            "frozenset": sorted(
                items,
                key=lambda item: json.dumps(
                    item, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                ),
            )
        }
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _code_payload(code: types.CodeType) -> dict[str, object]:
    """Return executable semantics without machine paths or line-table noise."""

    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "code": code.co_code.hex(),
        "consts": [_constant(item) for item in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
        "name": code.co_name,
        "qualname": code.co_qualname,
    }


def canonical_code_hash(code: types.CodeType) -> str:
    payload = json.dumps(
        _code_payload(code),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def manifest_hash(modules: Mapping[str, types.CodeType]) -> str:
    manifest = {
        name: canonical_code_hash(code)
        for name, code in sorted(modules.items())
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_modules(root: Path) -> dict[str, types.CodeType]:
    modules: dict[str, types.CodeType] = {}
    for module in TARGET_MODULES:
        path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        source = path.read_text(encoding="utf-8")
        modules[module] = compile(source, f"<{module}>", "exec", optimize=0)
    return modules


def _archive_reader(root: Path):
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        packages = root / ".venv-native" / "Lib" / "site-packages"
        if not packages.is_dir():
            raise
        sys.path.insert(0, str(packages))
        from PyInstaller.archive.readers import CArchiveReader
    return CArchiveReader


def _pyz_modules(root: Path, executable: Path) -> dict[str, types.CodeType]:
    archive = _archive_reader(root)(str(executable))
    pyz = archive.open_embedded_archive("PYZ.pyz")
    modules: dict[str, types.CodeType] = {}
    for module in TARGET_MODULES:
        code = pyz.extract(module)
        if not isinstance(code, types.CodeType):
            raise ValueError(f"packaged module unavailable: {module}")
        modules[module] = code
    return modules


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def alignment_report(
    root: Path,
    executable: Path,
    *,
    runtime_executable: Path | None = None,
) -> dict[str, object]:
    checked_root = root.resolve()
    checked_executable = executable.resolve()
    live_executable = (runtime_executable or executable).resolve()
    source_hash = manifest_hash(_source_modules(checked_root))
    pyz_hash = manifest_hash(_pyz_modules(checked_root, checked_executable))
    runtime_hash = manifest_hash(_pyz_modules(checked_root, live_executable))
    exe_hash = _sha256(checked_executable)
    runtime_exe_hash = _sha256(live_executable)
    aligned = (
        source_hash == pyz_hash == runtime_hash
        and exe_hash == runtime_exe_hash
    )
    return {
        "target_modules": list(TARGET_MODULES),
        "source_hash": source_hash,
        "pyz_hash": pyz_hash,
        "runtime_hash": runtime_hash,
        "exe_sha256": exe_hash,
        "runtime_exe_sha256": runtime_exe_hash,
        "module_alignment": aligned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--runtime-exe", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = alignment_report(
            args.root,
            args.exe,
            runtime_executable=args.runtime_exe,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report["module_alignment"] else 2
    except (ImportError, OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"module_alignment": False, "error_type": type(error).__name__},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
