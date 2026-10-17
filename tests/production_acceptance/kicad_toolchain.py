from __future__ import annotations

from pathlib import Path

from embedded_copilot.execution_worker import (
    discover_kicad_cli,
    discover_kicad_symbol_root,
)


# This is the existing trusted installation recorded by the G3-V/G4 evidence.
# It is test-environment configuration, not a product fallback or a second
# KiCad setting.
LEGACY_TRUSTED_KICAD_ROOT = Path(r"D:\ruanjian\32\kicad")


def discover_test_kicad_cli() -> Path | None:
    return discover_kicad_cli(known_install_roots=(LEGACY_TRUSTED_KICAD_ROOT,))


def discover_test_symbol_root(executable: Path) -> Path | None:
    return discover_kicad_symbol_root(executable)


__all__ = (
    "LEGACY_TRUSTED_KICAD_ROOT",
    "discover_test_kicad_cli",
    "discover_test_symbol_root",
)
