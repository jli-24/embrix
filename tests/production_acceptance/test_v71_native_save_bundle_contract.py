from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_SOURCE = ROOT / "frontend" / "src" / "copilot" / "settings" / "SettingsPanel.tsx"
STYLES_SOURCE = ROOT / "frontend" / "src" / "copilot" / "styles.css"


def _assets(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*") if path.is_file() and path.suffix in {".css", ".js"}))


def test_native_save_action_is_labeled_and_painted_in_source_contract() -> None:
    settings = SETTINGS_SOURCE.read_text(encoding="utf-8")
    styles = STYLES_SOURCE.read_text(encoding="utf-8")
    primary_rule = re.search(r"\.ide-primary-button\s*\{([^}]*)\}", styles)
    scoped_primary_rule = re.search(r"\.ide-settings__actions\s+\.ide-primary-button\s*\{([^}]*)\}", styles)

    assert 'className="ide-primary-button"' in settings
    assert "保存配置" in settings
    assert "aria-busy={savingProvider}" in settings
    assert 'aria-label={savingProvider ? "保存中…" : "保存配置"}' in settings
    assert primary_rule is not None
    assert "color:" in primary_rule.group(1)
    assert scoped_primary_rule is not None
    assert "background:" in scoped_primary_rule.group(1)
    assert "color:" in scoped_primary_rule.group(1)
    assert "visibility: visible" in styles


def test_built_frontend_keeps_native_save_contract() -> None:
    assets = _assets(ROOT / "frontend" / "dist")
    if not assets:
        pytest.skip("frontend/dist is not present; run npm run build for bundle evidence")
    bundle = "\n".join(path.read_text(encoding="utf-8") for path in assets)

    assert "保存配置" in bundle
    assert ".ide-primary-button" in bundle
    assert "visibility:visible" in bundle or "visibility: visible" in bundle


def test_staged_pyinstaller_package_keeps_native_save_contract() -> None:
    exact_staging_root = ROOT / "dist" / "_staging"
    staging_roots = [exact_staging_root] if exact_staging_root.is_dir() else []
    staging_roots.extend(sorted(
        (path for path in (ROOT / "dist").glob("_staging_v71_provider_switching_*") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ))
    if not staging_roots:
        pytest.skip("fresh staged PyInstaller package is not present")
    package_assets = _assets(staging_roots[0] / "Embrix" / "_internal" / "frontend-dist")
    if not package_assets:
        pytest.skip("fresh staged PyInstaller package is not present")
    bundle = "\n".join(path.read_text(encoding="utf-8") for path in package_assets)

    assert "保存配置" in bundle
    assert ".ide-primary-button" in bundle
    assert "visibility:visible" in bundle or "visibility: visible" in bundle
