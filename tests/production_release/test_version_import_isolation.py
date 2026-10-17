from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_root_version_import_does_not_compose_production_runtime(tmp_path: Path) -> None:
    script = """
import json
import sys
import embedded_copilot
print(json.dumps({
    "versions": [embedded_copilot.__version__, embedded_copilot.PRODUCT_VERSION],
    "production_modules": [name for name in sys.modules if name.startswith((
        "embedded_copilot.production_release", "embedded_copilot.agents",
        "embedded_copilot.api", "embedded_copilot.supervisor",
    ))],
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["versions"][0] == payload["versions"][1]
    assert isinstance(payload["versions"][0], str) and payload["versions"][0]
    assert payload["production_modules"] == []
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_existing_release_exports_preserve_version_and_public_payload() -> None:
    from embedded_copilot import PRODUCT_VERSION, __version__
    from embedded_copilot.production_release import PRODUCT_VERSION as package_version
    from embedded_copilot.production_release.release import (
        PRODUCT_NAME,
        RELEASE_STATUS,
        product_version_payload,
    )

    assert __version__ == PRODUCT_VERSION == package_version
    assert product_version_payload() == {
        "product_name": PRODUCT_NAME,
        "product_version": __version__,
        "release_status": RELEASE_STATUS,
    }


@pytest.mark.parametrize("audit_name", ["v70", "v71"])
def test_static_audit_reads_single_version_source_without_claiming_package_acceptance(
    tmp_path: Path, audit_name: str,
) -> None:
    source = tmp_path / "src" / "embedded_copilot"
    source.mkdir(parents=True)
    (source / "_version.py").write_text('PRODUCT_VERSION = "0.0.123"\n', encoding="utf-8")
    release = source / "production_release"
    release.mkdir()
    (release / "release.py").write_text(
        "from embedded_copilot._version import PRODUCT_VERSION\n", encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text('version = "0.0.123"\n', encoding="utf-8")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    for name in ("package.json", "package-lock.json"):
        (frontend / name).write_text('{"version": "0.0.123"}', encoding="utf-8")
    windows = tmp_path / "packaging" / "windows"
    windows.mkdir(parents=True)
    for name in ("EmbeddedCopilot.iss", "EmbeddedCopilot-v7.1.iss"):
        (windows / name).write_text('#define ProductVersion "0.0.123"\n', encoding="utf-8")
    audit = importlib.import_module(f"scripts.{audit_name}_final_packaging_audit")
    report = audit.run_audit(root=tmp_path)
    assert report["version_consistency"]["sources"]["python_package"] == "0.0.123"
    assert report["version_consistency"]["status"] == "FAIL"
    assert report["release_evidence"]["production_package"] == "UNVERIFIED"
    assert report["release_evidence"]["overall"] == "PARTIAL VERIFIED"

    version_source = source / "_version.py"
    version_source.write_text(
        'raise AssertionError("VERSION_SOURCE_MUST_NOT_EXECUTE")\nPRODUCT_VERSION = "0.0.123"\n',
        encoding="utf-8",
    )
    static_report = audit.run_audit(root=tmp_path)
    assert static_report["version_consistency"]["sources"]["python_package"] == "0.0.123"
    version_source.write_text(
        'raise AssertionError("VERSION_SOURCE_MUST_NOT_EXECUTE")\nPRODUCT_VERSION = object()\n',
        encoding="utf-8",
    )
    rejected = audit.run_audit(root=tmp_path)
    assert rejected["version_consistency"]["sources"]["python_package"] is None
    assert rejected["version_consistency"]["status"] == "FAIL"
    assert rejected["release_evidence"]["production_package"] == "UNVERIFIED"
