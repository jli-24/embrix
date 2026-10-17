from __future__ import annotations

import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

from scripts import build_v71_windows_package as builder
from scripts import v71_final_packaging_audit as audit

RESOURCE_DIR = Path("embedded_copilot/component_recommendation/resources")


def test_wheel_and_windows_declared_data_quarantine_resource_pending_rights() -> None:
    configuration = tomllib.loads((builder.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data = configuration["tool"]["setuptools"].get("package-data", {}).get("embedded_copilot.component_recommendation", [])
    assert "resources/*.json" not in data
    assert "resources/*.txt" not in data
    excluded = configuration["tool"]["setuptools"]["exclude-package-data"]["embedded_copilot.component_recommendation"]
    assert "resources/*" in excluded
    command = builder._pyinstaller_command(dist=builder.ROOT / "dist" / "test", work=builder.ROOT / "build" / "test")
    assert not any(
        value.endswith(";" + RESOURCE_DIR.as_posix())
        for value in command
    )


def test_package_audit_passes_absent_quarantined_resource_but_capability_stays_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "Embrix.exe"
    package.touch()
    assert audit._approved_component_resource_audit(package) == {
        "status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT",
    }
    monkeypatch.setattr(audit, "_prompt_builder_alignment", lambda root, executable: {"prompt_builder_match": False})
    monkeypatch.setattr(audit, "alignment_report", lambda root, executable: {"module_alignment": False})
    report = audit.run_audit(builder.ROOT, package=package)
    assert report["approved_component_resource"] == {
        "status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT",
    }
    assert report["default_component_source"] == {
        "status": "UNAVAILABLE", "reason": "BLOCKED_RIGHTS",
    }
    assert report["release_evidence"]["release_ready"] is False
    assert report["release_evidence"]["s05_release_gate"] == "BLOCKED_RIGHTS_AND_REMAINING_GATES"


def test_package_audit_passes_empty_resource_directories(tmp_path: Path) -> None:
    package = tmp_path / "Embrix.exe"
    package.touch()
    (package.parent / "_internal" / RESOURCE_DIR / "nested").mkdir(parents=True)
    assert audit._approved_component_resource_audit(package)["status"] == "PASS"


def test_package_audit_does_not_pass_without_an_artifact(tmp_path: Path) -> None:
    assert audit._approved_component_resource_audit(tmp_path / "not-built.exe") == {
        "status": "FAIL", "reason": "RESOURCE_AUDIT_FAILED",
    }


@pytest.mark.parametrize("relative", [
    Path("_internal") / RESOURCE_DIR / "approved_components_v1.json",
    Path("_internal") / RESOURCE_DIR / "renamed-resource.bin",
    RESOURCE_DIR / "copied-resource.json",
    Path("_internal/src") / RESOURCE_DIR / "KICAD_LICENSE.txt",
    Path("_internal") / RESOURCE_DIR.parent / "approved_components_v2.json",
])
def test_package_audit_rejects_quarantined_content_at_recognizable_paths(
    tmp_path: Path, relative: Path,
) -> None:
    package = tmp_path / "Embrix.exe"
    package.touch()
    target = package.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"quarantined content")
    assert audit._approved_component_resource_audit(package) == {"status": "FAIL", "reason": "QUARANTINED_RESOURCE_PRESENT"}


def test_package_audit_scan_error_is_not_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "Embrix.exe"
    package.touch()
    target = package.parent / "_internal" / RESOURCE_DIR
    target.mkdir(parents=True)
    original = audit.os.scandir

    def unreadable(path):
        if Path(path) == target:
            raise PermissionError("test-only unreadable resource directory")
        return original(path)

    monkeypatch.setattr(audit.os, "scandir", unreadable)
    assert audit._approved_component_resource_audit(package) == {
        "status": "FAIL", "reason": "RESOURCE_AUDIT_FAILED",
    }


def test_setuptools_build_py_output_excludes_quarantined_component_resources(tmp_path: Path) -> None:
    output = tmp_path / "build-lib"
    subprocess.run(
        [sys.executable, "-c", "from setuptools import setup; setup(script_args=['build_py', '--build-lib', " + repr(str(output)) + "])"],
        cwd=builder.ROOT, check=True, capture_output=True, text=True,
    )
    assert not (output / RESOURCE_DIR / "approved_components_v1.json").exists()
    assert not (output / RESOURCE_DIR / "KICAD_ATTRIBUTION.txt").exists()
    assert not (output / RESOURCE_DIR / "KICAD_CHANGE_NOTICE.txt").exists()
    assert not (output / RESOURCE_DIR / "KICAD_LICENSE.txt").exists()


def test_setuptools_sdist_excludes_quarantined_component_resources(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", "from setuptools import setup; setup(script_args=['sdist', '--dist-dir', " + repr(str(tmp_path)) + "])"],
        cwd=builder.ROOT, check=True, capture_output=True, text=True,
    )
    archives = list(tmp_path.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as archive:
        names = archive.getnames()
    assert not any("component_recommendation/resources/" in name for name in names)
