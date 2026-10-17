from __future__ import annotations

import os
import shutil
from pathlib import Path


_KICAD_EXECUTABLE_NAMES = ("kicad-cli.exe", "kicad-cli")


def discover_kicad_cli(
    *,
    configured_path: str | None = None,
    known_install_roots: tuple[Path, ...] = (),
) -> Path | None:
    """Resolve the one trusted KiCad CLI authority used by G3, G4 and G5.

    Discovery order is explicit configuration, the process PATH, then caller-
    supplied trusted installation roots.  The last input is intentionally a
    narrow configuration boundary: discovery never scans arbitrary drives or
    the workspace, and it never accepts a symlink or a basename-only path.
    """

    candidates: list[Path] = []
    configured = configured_path
    if configured is None:
        configured = os.environ.get("EMBRIX_KICAD_CLI", "").strip()
    if configured:
        candidates.append(Path(configured))

    for executable_name in _KICAD_EXECUTABLE_NAMES:
        discovered = shutil.which(executable_name)
        if discovered:
            candidates.append(Path(discovered))

    for root in known_install_roots:
        install_root = Path(root)
        candidates.extend(
            install_root / executable_name
            for executable_name in _KICAD_EXECUTABLE_NAMES
        )
        candidates.extend(
            install_root / "bin" / executable_name
            for executable_name in _KICAD_EXECUTABLE_NAMES
        )

    for candidate in candidates:
        resolved = _trusted_executable(candidate)
        if resolved is not None:
            return resolved
    return None


def discover_kicad_library_root(
    executable: str | Path,
    library_name: str,
    *,
    configured_root: str | Path | None = None,
) -> Path | None:
    """Resolve a standard KiCad library adjacent to the trusted executable."""

    if library_name not in {"symbols", "footprints"}:
        raise ValueError("unsupported KiCad library")
    executable_path = _trusted_executable(Path(executable))
    if executable_path is None:
        return None
    candidates: list[Path] = []
    if configured_root:
        candidates.append(Path(configured_root))
    candidates.extend(
        (
            executable_path.parent.parent / "share" / "kicad" / library_name,
            executable_path.parent.parent.parent / "share" / "kicad" / library_name,
        )
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_dir() and not resolved.is_symlink():
            return resolved
    return None


def discover_kicad_symbol_root(
    executable: str | Path,
    *,
    configured_root: str | Path | None = None,
) -> Path | None:
    return discover_kicad_library_root(
        executable,
        "symbols",
        configured_root=configured_root,
    )


def discover_kicad_footprint_root(
    executable: str | Path,
    *,
    configured_root: str | Path | None = None,
) -> Path | None:
    return discover_kicad_library_root(
        executable,
        "footprints",
        configured_root=configured_root,
    )


def _trusted_executable(candidate: Path) -> Path | None:
    if (
        not candidate.is_absolute()
        or candidate.name.casefold() not in _KICAD_EXECUTABLE_NAMES
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        resolved.name.casefold() not in _KICAD_EXECUTABLE_NAMES
        or resolved.is_symlink()
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
    ):
        return None
    return resolved


__all__ = (
    "discover_kicad_cli",
    "discover_kicad_footprint_root",
    "discover_kicad_library_root",
    "discover_kicad_symbol_root",
)
