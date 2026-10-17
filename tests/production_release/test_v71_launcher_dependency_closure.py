from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PIL import Image

from scripts import build_v71_windows_package as builder
from scripts import v71_launcher_dependency_closure as closure
from scripts.v71_launcher_dependency_closure import (
    KNOWN_HIGH_RISK_MODULES,
    LAUNCHER_DEPENDENCY,
    dependency_closure_report,
    reachable_project_modules,
)

ROOT = Path(__file__).resolve().parents[2]


def test_v71_uses_the_top_level_v70_runtime_module_identity() -> None:
    source = (ROOT / "scripts" / "v71_desktop_launcher.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == LAUNCHER_DEPENDENCY
        and any(alias.name == "run" for alias in node.names)
    ]
    assert len(imports) == 1
    assert (ROOT / "scripts" / "__init__.py").is_file()


def test_builder_declares_launcher_identity_without_collecting_scripts_tree() -> None:
    assert LAUNCHER_DEPENDENCY in builder.LAUNCHER_HIDDEN_IMPORTS
    assert "scripts.v70_desktop_launcher" not in builder.PYINSTALLER_HIDDEN_IMPORTS
    assert ROOT / "scripts" in builder.PYINSTALLER_PATHEX
    assert builder.LAUNCHER_ENTRYPOINT.name == "v71_desktop_launcher.py"

    command = builder._pyinstaller_command(
        dist=ROOT / "dist" / "test-package",
        work=ROOT / "build" / "test-pyinstaller",
    )
    assert command.count("--paths") == 2
    assert command[command.index("--paths") + 1] == str(ROOT / "src")
    assert command[command.index("--specpath") + 1] == str(ROOT / "build" / "test-pyinstaller")
    assert command[-1] == str(builder.LAUNCHER_ENTRYPOINT)
    assert command[command.index("--name") + 1] == "Embrix"
    assert command[command.index("--icon") + 1].endswith("embrix-app-icon-white.ico")
    assert f"{ROOT / 'frontend' / 'public' / 'brand' / 'embrix-app-icon-white.ico'};brand" in command


def test_current_package_and_app_ico_use_the_embrix_visible_identity() -> None:
    assert builder.DEFAULT_DIST.parent == ROOT / "dist" / "_staging"
    assert builder.DEFAULT_WORK.parent == ROOT / "build" / "_staging"
    installer = (ROOT / "packaging" / "windows" / "EmbeddedCopilot-v7.1.iss").read_text(encoding="utf-8")
    assert '#define LauncherExe "EmbrixLauncher.exe"' in installer
    assert "dist\\Embrix-current\\Embrix\\*" in installer
    assert 'Filename: "{app}\\{#LauncherExe}"' in installer
    icon = Image.open(ROOT / "frontend" / "public" / "brand" / "embrix-app-icon-white.ico")
    assert set(icon.info.get("sizes", ())) >= {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
    assert icon.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


def test_lifecycle_executables_use_the_same_app_icon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> None:
        commands.append(command)
        app = tmp_path / "dist" / "Embrix"
        app.mkdir(parents=True, exist_ok=True)
        (app / f"{command[command.index('--name') + 1]}.exe").write_bytes(b"test")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    builder._build_lifecycle_executables(dist=tmp_path / "dist", work=tmp_path / "work")

    assert len(commands) == 2
    assert all(command[command.index("--icon") + 1].endswith("embrix-app-icon-white.ico") for command in commands)


def test_reachable_closure_includes_known_dynamic_risk_points() -> None:
    reachable, dynamic = reachable_project_modules(ROOT)
    assert "v71_desktop_launcher" in reachable
    assert "v70_desktop_launcher" in reachable
    assert set(KNOWN_HIGH_RISK_MODULES) - {"embedded_copilot.api.main"} <= set(dynamic)
    assert set(KNOWN_HIGH_RISK_MODULES).issubset(set(reachable))


def test_reachable_closure_resolves_constant_dynamic_import_expressions() -> None:
    reachable, dynamic = reachable_project_modules(ROOT)
    assert "embedded_copilot.engineering_completion.contracts" in dynamic
    assert "embedded_copilot.engineering_completion.models" in dynamic
    assert "embedded_copilot.engineering_completion.contracts" in reachable
    assert "embedded_copilot.engineering_completion.models" in reachable


def test_package_relative_imports_resolve_inside_the_package() -> None:
    package_tree = ast.parse("from .factory import create\n")
    module_tree = ast.parse("from .factory import create\n")

    package_static, _, _ = closure._imports(
        package_tree,
        "embedded_copilot.model_runtime.integration",
        current_is_package=True,
    )
    module_static, _, _ = closure._imports(
        module_tree,
        "embedded_copilot.model_runtime.integration.runtime",
    )

    assert "embedded_copilot.model_runtime.integration.factory" in package_static
    assert "embedded_copilot.model_runtime.factory" not in package_static
    assert "embedded_copilot.model_runtime.integration.factory" in module_static


def test_source_hash_compile_does_not_inherit_checker_future_flags() -> None:
    module = "embedded_copilot.application"
    source_path = closure._source_path(ROOT, module)
    assert source_path is not None
    source = source_path.read_text(encoding="utf-8")
    expected = closure.canonical_code_hash(
        compile(source, f"<{module}>", "exec", dont_inherit=True, optimize=0)
    )

    assert closure._source_code_hashes(ROOT, (module,))[module] == expected


def test_unknown_dynamic_import_is_reported_and_fails_closed(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "__init__.py").write_text("", encoding="utf-8")
    (scripts_dir / "v71_desktop_launcher.py").write_text(
        "from v70_desktop_launcher import run\n", encoding="utf-8"
    )
    (scripts_dir / "v70_desktop_launcher.py").write_text(
        "import importlib\nmodule_name = input()\nimportlib.import_module(module_name)\n",
        encoding="utf-8",
    )

    reachable, dynamic, unknown = closure._reachable_project_modules(tmp_path)
    report = dependency_closure_report(tmp_path, tmp_path / "missing.exe")

    assert set(("v71_desktop_launcher", "v70_desktop_launcher")) == set(reachable)
    assert dynamic == ()
    assert unknown == 1
    assert report["unknown_dynamic_import_expressions"] == 1
    assert report["launcher_dependency_closure"] is False


def test_duplicate_launcher_identity_is_reported_fail_closed(monkeypatch, tmp_path: Path) -> None:
    class FakePyz:
        toc = {"v70_desktop_launcher", "scripts.v70_desktop_launcher"}

        def extract(self, module: str) -> object:
            return compile("", f"<{module}>", "exec")

    class FakeArchive:
        toc = {"v71_desktop_launcher", "v70_desktop_launcher"}

        def open_embedded_archive(self, name: str) -> FakePyz:
            assert name == "PYZ.pyz"
            return FakePyz()

        def extract(self, module: str) -> bytes:
            return __import__("marshal").dumps(compile("", f"<{module}>", "exec"))

    exe = tmp_path / "EmbeddedCopilot.exe"
    exe.write_bytes(b"placeholder")
    monkeypatch.setattr(closure, "_archive", lambda root, executable: FakeArchive())
    monkeypatch.setattr(closure, "_source_code_hashes", lambda root, modules: {})

    report = dependency_closure_report(ROOT, exe)

    assert "v70_desktop_launcher" in report["duplicate_launcher_modules"]
    assert "scripts.v70_desktop_launcher" in report["duplicate_launcher_modules"]
    assert report["launcher_dependency_closure"] is False


def test_builder_passes_custom_package_to_canonical_audit(monkeypatch, tmp_path: Path) -> None:
    package = tmp_path / "custom" / "EmbeddedCopilot.exe"
    package.parent.mkdir()
    package.write_bytes(b"custom-package")
    captured: dict[str, Path] = {}

    monkeypatch.setattr(builder, "_build_package", lambda **kwargs: package)
    monkeypatch.setattr(builder, "read_build_identity", lambda path: {"source_fingerprint": "test-fingerprint"})
    monkeypatch.setattr(builder, "source_fingerprint", lambda root: "test-fingerprint")
    monkeypatch.setattr(
        builder,
        "dependency_closure_report",
        lambda root, executable: {"launcher_dependency_closure": True},
    )

    def fake_audit(root: Path, *, package: Path | None = None) -> dict[str, object]:
        assert package is not None
        captured["package"] = package
        return {
            "version_consistency": {"status": "PASS"},
            "frontend_production_assets": {"status": "PASS"},
            "prompt_builder_alignment": {"prompt_builder_match": True},
            "canonical_module_alignment": {"module_alignment": True},
            "approved_component_resource": {
                "status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT",
            },
            "default_component_source": {"status": "UNAVAILABLE", "reason": "BLOCKED_RIGHTS"},
            "release_evidence": {
                "release_ready": False, "s05_release_gate": "BLOCKED_RIGHTS_AND_REMAINING_GATES",
            },
        }

    monkeypatch.setattr(builder, "run_audit", fake_audit)

    assert builder.run(dist=tmp_path / "custom", work=tmp_path / "work", build_frontend=False) == 0
    assert captured["package"] == package


@pytest.mark.parametrize("case", [
    "missing_quarantine", "unknown_quarantine", "missing_source", "invalid_source",
    "missing_release", "release_ready", "s05_pass", "prompt_mismatch",
    "module_mismatch", "closure_failure", "version_mismatch", "frontend_invalid",
    "missing_version", "missing_frontend", "malformed_report",
])
def test_builder_rejects_invalid_audit_or_other_existing_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str,
) -> None:
    package = tmp_path / "custom" / "EmbeddedCopilot.exe"
    package.parent.mkdir()
    package.write_bytes(b"test-only package")
    monkeypatch.setattr(builder, "_build_package", lambda **kwargs: package)
    monkeypatch.setattr(builder, "read_build_identity", lambda path: {"source_fingerprint": "test-fingerprint"})
    monkeypatch.setattr(builder, "source_fingerprint", lambda root: "test-fingerprint")
    monkeypatch.setattr(
        builder, "dependency_closure_report",
        lambda root, executable: {"launcher_dependency_closure": case != "closure_failure"},
    )
    report: dict[str, object] = {
        "version_consistency": {"status": "FAIL" if case == "version_mismatch" else "PASS"},
        "frontend_production_assets": {"status": "FAIL" if case == "frontend_invalid" else "PASS"},
        "prompt_builder_alignment": {"prompt_builder_match": case != "prompt_mismatch"},
        "canonical_module_alignment": {"module_alignment": case != "module_mismatch"},
        "approved_component_resource": {
            "status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT",
        },
        "default_component_source": {"status": "UNAVAILABLE", "reason": "BLOCKED_RIGHTS"},
        "release_evidence": {
            "release_ready": False, "s05_release_gate": "BLOCKED_RIGHTS_AND_REMAINING_GATES",
        },
    }
    if case == "missing_quarantine":
        del report["approved_component_resource"]
    elif case == "unknown_quarantine":
        report["approved_component_resource"] = {"status": "UNKNOWN"}
    elif case == "missing_source":
        del report["default_component_source"]
    elif case == "invalid_source":
        report["default_component_source"] = {"status": "AVAILABLE"}
    elif case == "missing_release":
        del report["release_evidence"]
    elif case == "release_ready":
        report["release_evidence"] = {"release_ready": True}
    elif case == "s05_pass":
        report["release_evidence"] = {"release_ready": False, "s05_release_gate": "PASS"}
    elif case == "missing_version":
        del report["version_consistency"]
    elif case == "missing_frontend":
        del report["frontend_production_assets"]
    monkeypatch.setattr(
        builder, "run_audit",
        lambda root, *, package=None: None if case == "malformed_report" else report,
    )
    with pytest.raises(RuntimeError):
        builder.run(dist=tmp_path / "custom", work=tmp_path / "work", build_frontend=False)


def test_invalid_previous_package_is_rejected_by_runtime_closure_check() -> None:
    executable = ROOT / "dist" / "Embrix-P0-1G15F-Verification" / "EmbeddedCopilot" / "EmbeddedCopilot.exe"
    if not executable.is_file():
        return
    report = dependency_closure_report(ROOT, executable)
    assert report["build_time_v71_entry_present"] is True
    assert report["build_time_pyz_v70_present"] is True
    assert report["missing_normal_reachable_project_modules"]
    assert report["launcher_dependency_closure"] is False
