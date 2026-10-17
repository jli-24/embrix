"""Build the v7.1.0 Windows onedir package after the frontend build."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from embedded_copilot.production_release.build_identity import (
    create_build_identity,
    read_build_identity,
    source_fingerprint,
)
from embedded_copilot.production_release.release import PRODUCT_VERSION
from scripts.v71_final_packaging_audit import run_audit
from scripts.v71_launcher_dependency_closure import dependency_closure_report

_BUILD_DIRECTORY_ID = uuid.uuid4().hex
DEFAULT_DIST = ROOT / "dist" / "_staging" / _BUILD_DIRECTORY_ID
DEFAULT_WORK = ROOT / "build" / "_staging" / _BUILD_DIRECTORY_ID
LAUNCHER_ENTRYPOINT = ROOT / "scripts" / "v71_desktop_launcher.py"
PYINSTALLER_PATHEX = (ROOT / "src", ROOT / "scripts")
LAUNCHER_HIDDEN_IMPORTS = ("v70_desktop_launcher",)
PYINSTALLER_HIDDEN_IMPORTS = (
    "clr",
    "pythonnet",
    "embedded_copilot.desktop_copilot_chat",
    "embedded_copilot.application.desktop_composition",
    "embedded_copilot.application.runtime",
    "embedded_copilot.engineering_completion.contracts",
    "embedded_copilot.engineering_completion.models",
    "embedded_copilot.knowledge.manifest",
    "embedded_copilot.api.optimization_v26_routes",
    "embedded_copilot.agents.knowledge",
    "embedded_copilot.engineering_context.adapters.project_engineering",
    "embedded_copilot.firmware_agent_adapter",
    "embedded_copilot.debug_agent_adapter",
    "embedded_copilot.model_gateway",
    "embedded_copilot.engineering_qa.prompt",
    "embedded_copilot.firmware_source_execution",
    "embedded_copilot.pcb_production",
    "embedded_copilot.pcb_production.execution",
    "embedded_copilot.engineering_pcb.final_closure",
    "embedded_copilot.execution_worker.pcb",
    # Real simulation runtime v1 is a production module set.  The desktop
    # composition can opt into it without relying on PyInstaller's dynamic
    # import discovery, while Renode itself remains an explicit user-local
    # installation and is not bundled into this package.
    "embedded_copilot.execution_worker.contracts",
    "embedded_copilot.execution_worker.ports",
    "embedded_copilot.execution_worker.adapters",
    "embedded_copilot.execution_worker.worker",
    "embedded_copilot.execution_worker.store",
    "embedded_copilot.execution_worker.registry",
    "embedded_copilot.simulation_runtime",
    "embedded_copilot.simulation_runtime.adapters",
    "embedded_copilot.simulation_runtime.contracts",
    "embedded_copilot.simulation_runtime.exceptions",
    "embedded_copilot.simulation_runtime.memory",
    "embedded_copilot.simulation_runtime.operation",
    "embedded_copilot.simulation_runtime.ports",
    "embedded_copilot.simulation_runtime.renode",
    "embedded_copilot.simulation_runtime.service",
    "embedded_copilot.simulation_runtime.stores",
    *LAUNCHER_HIDDEN_IMPORTS,
)


def _pyinstaller_command(*, dist: Path, work: Path, identity_file: Path | None = None, frontend_dist: Path | None = None) -> list[str]:
    frontend = ROOT / "frontend"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "Embrix",
        "--icon",
        str(frontend / "public" / "brand" / "embrix-app-icon-white.ico"),
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
    ]
    for path in PYINSTALLER_PATHEX:
        command.extend(("--paths", str(path)))
    command.extend(
        [
            *(("--add-data", f"{identity_file};.") if identity_file is not None else ()),
            "--add-data",
            f"{frontend_dist or frontend / 'dist'};frontend-dist",
            "--add-data",
            f"{frontend / 'public' / 'brand' / 'embrix-app-icon-white.ico'};brand",
            "--collect-all",
            "webview",
        ]
    )
    for module in PYINSTALLER_HIDDEN_IMPORTS:
        command.extend(("--hidden-import", module))
    command.append(str(LAUNCHER_ENTRYPOINT))
    return command


def _build_lifecycle_executables(*, dist: Path, work: Path) -> None:
    """Bundle the fixed launcher and out-of-process switch helper as separate executables."""
    app = dist / "Embrix"
    for name, script in (
        ("EmbrixUpdater", "embrix_update_helper.py"),
        ("EmbrixLauncher", "embrix_fixed_launcher.py"),
    ):
        entrypoint = ROOT / "scripts" / script
        if not entrypoint.is_file():
            raise FileNotFoundError(f"lifecycle entrypoint missing: {script}")
        specific_work = work / name
        subprocess.run(
            [
                sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "--onefile", "--windowed", "--name", name,
                "--icon", str(ROOT / "frontend" / "public" / "brand" / "embrix-app-icon-white.ico"),
                "--paths", str(ROOT / "src"),
                "--distpath", str(app),
                "--workpath", str(specific_work),
                "--specpath", str(specific_work),
                str(entrypoint),
            ],
            cwd=ROOT,
            check=True,
        )
    shutil.copy2(app / "EmbrixLauncher.exe", dist / "EmbrixLauncher.exe")


def _build_package(*, dist: Path, work: Path, build_frontend: bool) -> Path:
    frontend = ROOT / "frontend"
    if dist.resolve() == (ROOT / "dist" / "Embrix-current").resolve():
        raise ValueError("ordinary build must not overwrite current")
    if dist.exists() and any(dist.iterdir()):
        raise FileExistsError("build destination must be empty")
    if build_frontend:
        subprocess.run(["npm.cmd", "run", "build"], cwd=frontend, check=True)
    if not (frontend / "dist").is_dir():
        raise FileNotFoundError("frontend/dist is required")
    identity = create_build_identity(ROOT, product_version=PRODUCT_VERSION)
    with tempfile.TemporaryDirectory(prefix="embrix-build-") as temporary:
        temp = Path(temporary)
        identity_file = temp / "build-identity.json"
        identity_file.write_text(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        staged_frontend = temp / "frontend-dist"
        shutil.copytree(frontend / "dist", staged_frontend)
        shutil.copy2(identity_file, staged_frontend / "build-identity.json")
        subprocess.run(
            _pyinstaller_command(dist=dist, work=work, identity_file=identity_file, frontend_dist=staged_frontend),
            cwd=ROOT,
            check=True,
        )
    _build_lifecycle_executables(dist=dist, work=work)
    return dist / "Embrix" / "Embrix.exe"


def run(*, dist: Path = DEFAULT_DIST, work: Path = DEFAULT_WORK, build_frontend: bool = True) -> int:
    package = _build_package(dist=dist, work=work, build_frontend=build_frontend)
    embedded_identity = read_build_identity(package.parent / "_internal")
    if embedded_identity is None or embedded_identity["source_fingerprint"] != source_fingerprint(ROOT):
        raise RuntimeError("source changed during package build; staging package is not publishable")
    closure = dependency_closure_report(ROOT, package)
    if closure.get("launcher_dependency_closure") is not True:
        raise RuntimeError("packaged launcher dependency closure is incomplete")
    report = run_audit(ROOT, package=package)
    if not isinstance(report, dict):
        raise RuntimeError("canonical packaging audit report is invalid")
    versions = report.get("version_consistency")
    if not isinstance(versions, dict) or versions.get("status") != "PASS":
        raise RuntimeError("package versions are inconsistent")
    frontend = report.get("frontend_production_assets")
    if not isinstance(frontend, dict) or frontend.get("status") != "PASS":
        raise RuntimeError("frontend production assets are unavailable")
    alignment = report.get("prompt_builder_alignment", {})
    if not isinstance(alignment, dict) or alignment.get("prompt_builder_match") is not True:
        raise RuntimeError("canonical PromptBuilder does not match source")
    module_alignment = report.get("canonical_module_alignment", {})
    if not isinstance(module_alignment, dict) or module_alignment.get("module_alignment") is not True:
        raise RuntimeError("canonical runtime modules do not match source")
    component_resource = report.get("approved_component_resource", {})
    if component_resource != {"status": "PASS", "reason": "QUARANTINED_RESOURCE_ABSENT"}:
        raise RuntimeError("quarantined component resource check failed")
    component_source = report.get("default_component_source")
    if component_source != {"status": "UNAVAILABLE", "reason": "BLOCKED_RIGHTS"}:
        raise RuntimeError("default component source status is invalid")
    release_evidence = report.get("release_evidence")
    if (
        not isinstance(release_evidence, dict)
        or release_evidence.get("release_ready") is not False
        or release_evidence.get("s05_release_gate") != "BLOCKED_RIGHTS_AND_REMAINING_GATES"
    ):
        raise RuntimeError("S05 release gate is invalid")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--skip-frontend-build", action="store_true")
    args = parser.parse_args()
    return run(
        dist=args.dist_dir,
        work=args.work_dir,
        build_frontend=not args.skip_frontend_build,
    )


if __name__ == "__main__":
    raise SystemExit(main())
