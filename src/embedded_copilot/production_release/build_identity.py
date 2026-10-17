"""Immutable identity embedded in a desktop package at build time."""

from __future__ import annotations

import hashlib
import json
import platform as host_platform
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IDENTITY_FILENAME = "build-identity.json"
_ROOT_FILES = ("pyproject.toml", "requirements.txt", "requirements-dev.txt")
_INPUT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".svg", ".ico", ".png", ".json", ".iss"}
_EXCLUDED_PARTS = {"dist", "build", "node_modules", "__pycache__", ".venv", ".git", "coverage", ".pytest_cache"}


def _source_files(root: Path) -> list[Path]:
    files = [root / name for name in _ROOT_FILES if (root / name).is_file()]
    for directory in ("src", "scripts", "frontend/src", "frontend/public", "packaging/windows"):
        base = root / directory
        if base.is_dir():
            files.extend(path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in _INPUT_SUFFIXES and not any(part in _EXCLUDED_PARTS or part.startswith(".env") for part in path.relative_to(root).parts))
    files.extend(path for path in (root / "frontend/package.json", root / "frontend/package-lock.json", root / "frontend/vite.config.ts", root / "frontend/tsconfig.json") if path.is_file())
    built_frontend = root / "frontend" / "dist"
    if built_frontend.is_dir():
        files.extend(path for path in built_frontend.rglob("*") if path.is_file() and path.name != IDENTITY_FILENAME)
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def source_fingerprint(root: Path) -> str:
    """Hash every selected source file with its relative path and content."""
    digest = hashlib.sha256()
    for path in _source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _git_value(root: Path, *args: str) -> str | None:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def create_build_identity(
    root: Path,
    *,
    product_version: str,
    channel: str = "development",
    mode: str = "development",
) -> dict[str, Any]:
    """Create a fresh identity; source equality does not imply build equality."""
    status = _git_value(root, "status", "--porcelain", "--untracked-files=all", "--", "src", "scripts", "frontend", "packaging", *_ROOT_FILES)
    return {
        "schema_version": 1,
        "product_name": "Embrix",
        "product_version": product_version,
        "build_id": uuid.uuid4().hex,
        "commit": _git_value(root, "rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
        "source_fingerprint": source_fingerprint(root),
        "build_time": datetime.now(UTC).isoformat(),
        "mode": mode,
        "channel": channel,
        "platform": "windows",
        "architecture": host_platform.machine().lower(),
    }


def read_build_identity(base_path: Path | None = None) -> dict[str, Any] | None:
    """Read only embedded package metadata; never inspect a development checkout."""
    base = base_path if base_path is not None else getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    base_directory = Path(base)
    path = base_directory / IDENTITY_FILENAME
    if not path.is_file():
        path = base_directory / "_internal" / IDENTITY_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = ("schema_version", "product_name", "product_version", "build_id", "commit", "dirty", "source_fingerprint", "build_time", "mode", "channel", "platform", "architecture")
    if not isinstance(value, dict) or value.get("schema_version") != 1 or any(field not in value for field in required):
        return None
    return value


__all__ = ("IDENTITY_FILENAME", "create_build_identity", "read_build_identity", "source_fingerprint")
