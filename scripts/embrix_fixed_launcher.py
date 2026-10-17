"""Stable Embrix entry that resolves the last verified current application."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        root = executable.parent.parent.parent if executable.parent.name == "Embrix" else executable.parent
    else:
        root = Path(__file__).resolve().parents[1] / "dist"
    from embedded_copilot.production_release.desktop_update import UpdateRejected
    from embedded_copilot.production_release.update_switch import recover_interrupted

    try:
        recover_interrupted(root)
    except (UpdateRejected, OSError):
        # A damaged transaction cannot establish which package is verified.
        # Keep both application slots untouched for manual recovery.
        return 2
    application = root / "Embrix-current" / "Embrix" / "Embrix.exe"
    if not application.is_file():
        return 1
    subprocess.Popen([str(application)], cwd=application.parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
