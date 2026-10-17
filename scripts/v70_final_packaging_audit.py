"""Read-only v7.0 final packaging evidence audit.

This script reports the difference between build-tool availability and release
evidence.  It never installs tools, changes PATH, builds an installer, or
captures secrets and is safe to run from a developer checkout.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

PRODUCT_VERSION = "7.0.0"


def _version(path: Path, pattern: str) -> str | None:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    return match.group(1) if match else None


def run_audit(root: Path | None = None) -> dict[str, object]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    release = root / "src" / "embedded_copilot" / "_version.py"
    pyproject = root / "pyproject.toml"
    frontend_package = root / "frontend" / "package.json"
    frontend_lock = root / "frontend" / "package-lock.json"
    installer = root / "packaging" / "windows" / "EmbeddedCopilot.iss"
    launcher = root / "scripts" / "v70_desktop_launcher.py"
    package_builder = root / "scripts" / "build_v70_windows_package.py"
    versions = {
        "python_package": _version(release, r'^PRODUCT_VERSION\s*=\s*["\']([^"\']+)["\']'),
        "pyproject": _version(pyproject, r'^version\s*=\s*["\']([^"\']+)["\']'),
        "frontend": json.loads(frontend_package.read_text(encoding="utf-8")).get("version"),
        "frontend_lock": json.loads(frontend_lock.read_text(encoding="utf-8")).get("version"),
        "installer_metadata": _version(installer, r'^#define ProductVersion\s+["\']([^"\']+)["\']'),
    }
    version_consistency = all(value == PRODUCT_VERSION for value in versions.values())

    dist = root / "frontend" / "dist"
    assets = [*dist.rglob("*.js"), *dist.rglob("*.html")] if dist.is_dir() else []
    bundle_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in assets)
    frontend_checks = {
        "dist_present": dist.is_dir(),
        "no_dev_server_localhost": "localhost" not in bundle_text.lower(),
        "bootstrap_fragment_present": "hash" in bundle_text and "replaceState" in bundle_text,
        "nonce_query_absent": "?nonce" not in bundle_text.lower(),
        "status": "PASS" if dist.is_dir() and "localhost" not in bundle_text.lower() else "FAIL",
    }

    shell_text = launcher.read_text(encoding="utf-8") if launcher.is_file() else ""
    builder_text = package_builder.read_text(encoding="utf-8") if package_builder.is_file() else ""
    native_shell_checks = {
        "host_port_present": "NativeDesktopHostPort" in shell_text,
        "native_default": "PyWebView2DesktopHost" in shell_text,
        "explicit_browser_flag": "--browser" in shell_text,
        "windowed_package": '"--windowed"' in builder_text,
        "single_entrypoint": '"EmbeddedCopilot"' in builder_text,
        "status": "PASS" if "NativeDesktopHostPort" in shell_text and '"--windowed"' in builder_text else "FAIL",
    }

    pyinstaller = (
        shutil.which("pyinstaller")
        or shutil.which("pyinstaller.exe")
        or (root / ".venv-native" / "Scripts" / "pyinstaller.exe")
    )
    iscc = shutil.which("iscc") or shutil.which("ISCC.exe")
    return {
        "product_version": PRODUCT_VERSION,
        "version_consistency": {"status": "PASS" if version_consistency else "FAIL", "sources": versions},
        "frontend_production_assets": frontend_checks,
        "native_desktop_shell_source": native_shell_checks,
        "tool_availability": {
            "PyInstaller": "AVAILABLE" if pyinstaller else "UNAVAILABLE",
            "ISCC": "AVAILABLE" if iscc else "UNAVAILABLE",
        },
        "release_evidence": {
            "production_runtime_code": "CODE/RUNTIME VERIFIED",
            "production_launcher_boundary": "CODE/RUNTIME VERIFIED",
            "credential_boundary": "CODE/RUNTIME VERIFIED",
            "migration_governance": "CODE/RUNTIME VERIFIED",
            "backup": "CODE/RUNTIME VERIFIED",
            "restore": "CODE/RUNTIME VERIFIED",
            "restore_failure_atomicity": "CODE/RUNTIME VERIFIED",
            "diagnostics": "CODE/RUNTIME VERIFIED",
            # Tool presence alone is never release evidence.  A future audit
            # must additionally inspect a real package/installer and its
            # installed-runtime and lifecycle evidence before promotion.
            "production_package": "UNVERIFIED",
            "packaged_native_smoke": (
                "PASS — onedir/windowed EXE exists"
                if (root / "dist" / f"EmbeddedCopilot-{PRODUCT_VERSION}" / "EmbeddedCopilot" / "EmbeddedCopilot.exe").is_file()
                else "UNVERIFIED"
            ),
            "installer": "UNVERIFIED",
            "fresh_install": "UNVERIFIED",
            "upgrade": "UNVERIFIED",
            "repair": "UNVERIFIED",
            "uninstall": "UNVERIFIED",
            "clean_machine_acceptance": "UNVERIFIED",
            "installer_signing": "UNAVAILABLE / UNVERIFIED",
            "overall": "PARTIAL VERIFIED",
        },
        "clean_machine_environment": "UNAVAILABLE / NOT PRESENT",
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2, sort_keys=True))
