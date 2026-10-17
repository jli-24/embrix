"""Publish a Native-accepted staging app to a local development update source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedded_copilot.production_release.build_identity import read_build_identity
from embedded_copilot.production_release.paths import default_development_update_source


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("JSON object required")
    return value


def publish_update_package(
    staging: Path,
    update_source: Path,
    native_acceptance_record: Path,
    *,
    notes: str,
    data_schema: dict[str, dict[str, int]],
    app_min_version: str | None = None,
    config_schema_version: int = 1,
) -> Path:
    """Publish archive first, then atomically expose its manifest."""
    if staging.parent.name != "_staging":
        raise ValueError("only a staging package may be published")
    app = staging / "Embrix"
    identity = read_build_identity(app / "_internal")
    if identity is None or identity["product_name"] != "Embrix":
        raise ValueError("staging build identity is invalid")
    if not all((app / executable).is_file() for executable in ("Embrix.exe", "EmbrixLauncher.exe", "EmbrixUpdater.exe")):
        raise ValueError("staging package is incomplete")
    frontend_identity = _read_json(app / "_internal" / "frontend-dist" / "build-identity.json")
    if frontend_identity != identity:
        raise ValueError("frontend build identity does not match application")
    acceptance = _read_json(native_acceptance_record)
    if acceptance.get("build_id") != identity["build_id"] or any(acceptance.get(field) != "PASS" for field in ("status", "native_ui_automation", "app_visible", "restart_smoke")):
        raise ValueError("Native acceptance PASS record does not match build")
    if not notes.strip():
        raise ValueError("update notes are required")
    if not data_schema or any(not isinstance(name, str) or not name or not isinstance(bounds, dict) or set(bounds) != {"min_version", "max_version"} or not all(type(bounds[key]) is int and bounds[key] >= 0 for key in bounds) or bounds["min_version"] > bounds["max_version"] for name, bounds in data_schema.items()):
        raise ValueError("data schema compatibility is invalid")
    if type(config_schema_version) is not int or config_schema_version < 1:
        raise ValueError("config schema version is invalid")
    package_name = f"Embrix-{identity['product_version']}-{identity['build_id']}.zip"
    if any(character in package_name for character in ("/", "\\", ":")):
        raise ValueError("build identity is invalid for package filename")
    packages_directory = update_source / "packages"
    packages_directory.mkdir(parents=True, exist_ok=True)
    package = packages_directory / package_name
    if package.exists():
        raise FileExistsError("update package already exists")
    with tempfile.NamedTemporaryFile(dir=packages_directory, prefix=".embrix-package-", suffix=".zip", delete=False) as temporary:
        temporary_package = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_package, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in sorted(app.rglob("*")):
                if path.is_symlink():
                    raise ValueError("staging package contains a symbolic link")
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
        digest = hashlib.sha256()
        with temporary_package.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        size = temporary_package.stat().st_size
        manifest = {
            "schema_version": 1,
            "product_name": "Embrix",
            "channel": identity["channel"],
            "platform": identity["platform"],
            "architecture": identity["architecture"],
            "product_version": identity["product_version"],
            "build_id": identity["build_id"],
            "package": {"name": package_name, "size": size, "sha256": digest.hexdigest()},
            "notes": [notes],
            "compatibility": {
                "app_min_version": app_min_version or identity["product_version"],
                "data_schema": data_schema,
                "config_schema_version": config_schema_version,
            },
        }
        os.replace(temporary_package, package)
        if package.stat().st_size != size:
            raise ValueError("published package integrity failed")
        published_digest = hashlib.sha256()
        with package.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                published_digest.update(chunk)
        if published_digest.hexdigest() != digest.hexdigest():
            raise ValueError("published package integrity failed")
        try:
            with zipfile.ZipFile(package) as archive:
                if archive.testzip() is not None:
                    raise ValueError("published package integrity failed")
        except zipfile.BadZipFile as error:
            raise ValueError("published package integrity failed") from error
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=update_source, prefix=".embrix-manifest-", suffix=".json", delete=False) as stream:
            temporary_manifest = Path(stream.name)
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        try:
            target = update_source / "manifest.json"
            os.replace(temporary_manifest, target)
            return target
        finally:
            temporary_manifest.unlink(missing_ok=True)
    finally:
        temporary_package.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging", type=Path)
    parser.add_argument("update_source", nargs="?", type=Path, default=default_development_update_source())
    parser.add_argument("--native-acceptance-record", type=Path, required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--data-schema-json", type=Path, required=True)
    parser.add_argument("--app-min-version")
    args = parser.parse_args()
    publish_update_package(args.staging, args.update_source, args.native_acceptance_record, notes=args.notes, data_schema=_read_json(args.data_schema_json), app_min_version=args.app_min_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
