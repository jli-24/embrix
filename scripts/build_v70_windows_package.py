"""Deterministic Windows package assembly; never invokes user-supplied commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from embedded_copilot.production_release.release import PRODUCT_VERSION

FRONTEND = ROOT / "frontend"
DIST = ROOT / "dist" / f"EmbeddedCopilot-{PRODUCT_VERSION}"


def run() -> int:
    subprocess.run(["npm.cmd", "run", "build"], cwd=FRONTEND, check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--windowed",
            "--name",
            "EmbeddedCopilot",
            "--distpath",
            str(DIST),
            "--workpath",
            str(ROOT / "build" / "v70-pyinstaller"),
            "--paths",
            str(ROOT / "src"),
            "--add-data",
            f"{FRONTEND / 'dist'};frontend-dist",
            "--collect-all",
            "webview",
            "--hidden-import",
            "clr",
            "--hidden-import",
            "pythonnet",
            "--hidden-import",
            "embedded_copilot.engineering_completion.contracts",
            "--hidden-import",
            "embedded_copilot.engineering_completion.models",
            "--hidden-import",
            "embedded_copilot.knowledge.manifest",
            "--hidden-import",
            "embedded_copilot.api.optimization_v26_routes",
            # Keep the canonical Desktop Chat composition explicit: the
            # knowledge agent is loaded through importlib and the remaining
            # runtime boundaries are exercised only after packaged startup.
            "--hidden-import",
            "embedded_copilot.agents.knowledge",
            "--hidden-import",
            "embedded_copilot.application.desktop_composition",
            "--hidden-import",
            "embedded_copilot.desktop_copilot_chat",
            "--hidden-import",
            "embedded_copilot.engineering_context.adapters.project_engineering",
            "--hidden-import",
            "embedded_copilot.firmware_agent_adapter",
            "--hidden-import",
            "embedded_copilot.debug_agent_adapter",
            "--hidden-import",
            "embedded_copilot.model_gateway",
            str(ROOT / "scripts" / "v70_desktop_launcher.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
