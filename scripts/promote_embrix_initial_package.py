"""Install a Native-accepted staging package only when no current app exists."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedded_copilot.production_release.desktop_update import (
    UpdateRejected,
    _atomic_json,
    _package_identity,
    installation_lock,
)


def promote(staging: Path, install_root: Path, acceptance_record: Path) -> Path:
    staging = staging.resolve()
    install_root = install_root.resolve()
    if staging.parent != install_root / "_staging":
        raise UpdateRejected("STAGING_LOCATION_INVALID")
    identity = _package_identity(staging / "Embrix")
    try:
        acceptance = json.loads(acceptance_record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateRejected("NATIVE_ACCEPTANCE_INVALID") from error
    required = ("status", "native_ui_automation", "app_visible", "restart_smoke")
    if acceptance.get("build_id") != identity["build_id"] or any(acceptance.get(key) != "PASS" for key in required):
        raise UpdateRejected("NATIVE_ACCEPTANCE_INVALID")
    launcher = staging / "EmbrixLauncher.exe"
    if not launcher.is_file() or not (staging / "Embrix" / "EmbrixUpdater.exe").is_file():
        raise UpdateRejected("PACKAGE_INCOMPLETE")
    current = install_root / "Embrix-current"
    fixed = install_root / "EmbrixLauncher.exe"
    with installation_lock(install_root):
        if current.exists() or fixed.exists():
            raise UpdateRejected("INITIAL_INSTALL_ALREADY_EXISTS")
        _atomic_json(staging / "verification.json", {
            "schema_version": 1, "build_id": identity["build_id"], "verification": "NATIVE_ACCEPTED"
        })
        staging.rename(current)
        try:
            shutil.copy2(current / "EmbrixLauncher.exe", fixed)
        except OSError:
            current.rename(staging)
            raise
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("install_root", type=Path)
    parser.add_argument("--native-acceptance-record", required=True, type=Path)
    arguments = parser.parse_args()
    promote(arguments.staging, arguments.install_root, arguments.native_acceptance_record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
