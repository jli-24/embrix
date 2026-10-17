"""Copy a Native-accepted external stage into the fixed per-user install root."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from embedded_copilot.production_release.desktop_update import (
    UpdateRejected,
    _package_identity,
)
from embedded_copilot.production_release.paths import default_install_root
from scripts.promote_embrix_initial_package import promote


def _accepted_identity(staging: Path, record: Path) -> dict:
    identity = _package_identity(staging / "Embrix")
    try:
        acceptance = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateRejected("NATIVE_ACCEPTANCE_INVALID") from error
    required = ("status", "native_ui_automation", "app_visible", "restart_smoke")
    if not isinstance(acceptance, dict) or acceptance.get("build_id") != identity["build_id"] or any(acceptance.get(key) != "PASS" for key in required):
        raise UpdateRejected("NATIVE_ACCEPTANCE_INVALID")
    if not (staging / "EmbrixLauncher.exe").is_file() or not (staging / "Embrix" / "EmbrixUpdater.exe").is_file():
        raise UpdateRejected("PACKAGE_INCOMPLETE")
    return identity


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matching_copy(source: Path, target: Path) -> bool:
    source_files = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
    target_files = {path.relative_to(target) for path in target.rglob("*") if path.is_file()}
    return source_files == target_files and all(
        not (source / relative).is_symlink()
        and not (target / relative).is_symlink()
        and (source / relative).stat().st_size == (target / relative).stat().st_size
        and _file_hash(source / relative) == _file_hash(target / relative)
        for relative in source_files
    )


def bootstrap(staging: Path, native_acceptance_record: Path, *, install_root: Path | None = None) -> Path:
    """Stage an external package, then use the existing initial promotion gate."""
    if staging.parent.name != "_staging":
        raise UpdateRejected("STAGING_LOCATION_INVALID")
    root = install_root if install_root is not None else default_install_root()
    if root.is_symlink() or (root / "_staging").is_symlink():
        raise UpdateRejected("PACKAGE_SYMLINK_REJECTED")
    if any(path.exists() or path.is_symlink() for path in (root / "Embrix-current", root / "EmbrixLauncher.exe")):
        raise UpdateRejected("INITIAL_INSTALL_ALREADY_EXISTS")
    if staging.is_symlink() or not staging.is_dir() or any(path.is_symlink() for path in staging.rglob("*")):
        raise UpdateRejected("PACKAGE_SYMLINK_REJECTED")
    identity = _accepted_identity(staging, native_acceptance_record)
    build_id = identity["build_id"]
    if not isinstance(build_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", build_id) or build_id in {".", ".."}:
        raise UpdateRejected("PACKAGE_IDENTITY_MISMATCH")
    target = root / "_staging" / build_id
    if target.exists() or target.is_symlink():
        raise UpdateRejected("STAGING_ALREADY_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, target, symlinks=False)
    if not _matching_copy(staging, target):
        raise UpdateRejected("PACKAGE_COPY_MISMATCH")
    if _accepted_identity(target, native_acceptance_record) != identity:
        raise UpdateRejected("PACKAGE_IDENTITY_MISMATCH")
    return promote(target, root, native_acceptance_record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("--native-acceptance-record", required=True, type=Path)
    arguments = parser.parse_args()
    bootstrap(arguments.staging, arguments.native_acceptance_record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
