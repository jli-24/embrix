from __future__ import annotations

import json
from pathlib import Path

import pytest

from embedded_copilot.production_release.configuration import load_production_settings
from embedded_copilot.production_release.paths import ProductionStateLayout


def _write_config(layout: ProductionStateLayout, root: Path, allow: tuple[Path, ...]) -> None:
    layout.config.mkdir(parents=True, exist_ok=True)
    layout.production_config.write_text(
        json.dumps({"project_root": str(root), "project_location_allow_list": [str(item) for item in allow]}),
        encoding="utf-8",
    )


def test_production_settings_ignore_ambient_acceptance_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    _write_config(layout, root, (root,))
    monkeypatch.setenv("EMBEDDED_COPILOT_PROJECT_ROOT", r"C:\acceptance_projects\golden")
    monkeypatch.setenv("EMBEDDED_COPILOT_PROJECT_LOCATION_ALLOW_LIST", r"C:\acceptance_projects")

    settings = load_production_settings(assets=tmp_path, layout=layout)

    assert settings.project_root == root
    assert settings.project_location_allow_list == (root,)


def test_production_settings_can_start_with_only_native_picker_roots(tmp_path: Path) -> None:
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    layout.create()
    settings = load_production_settings(assets=tmp_path, layout=layout)
    assert settings.project_root is None
    assert settings.project_location_allow_list == ()


@pytest.mark.parametrize("root_value", [r"C:\acceptance_projects", r"C:\acceptance_state\v7.0", r"C:\acceptance_projects\v7.0-golden"])
def test_production_settings_quarantines_known_acceptance_roots(tmp_path: Path, root_value: str) -> None:
    root = Path(root_value)
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    _write_config(layout, root, (root,))
    settings = load_production_settings(assets=tmp_path, layout=layout)
    assert settings.project_root is None
    assert settings.project_location_allow_list == ()


def test_production_settings_quarantines_explicit_native_picker_fixture(tmp_path: Path) -> None:
    fixture = Path(r"C:\tmp\embedded-copilot-native-picker-root")
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    _write_config(layout, fixture, (fixture,))

    settings = load_production_settings(assets=tmp_path, layout=layout)

    assert settings.project_root is None
    assert settings.project_location_allow_list == ()


def test_production_settings_loads_the_provider_projection_written_by_production_store(
    tmp_path: Path,
) -> None:
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    layout.create()
    layout.production_config.write_text(
        json.dumps(
            {
                "chat_provider": "deepseek",
                "chat_connection_name": "DeepSeek",
                "chat_model": "deepseek-chat",
                "chat_last_test_status": "PROVIDER_CONNECTION_UNVERIFIED",
                "chat_tested_at": None,
                "chat_tested_configuration_fingerprint": None,
                "chat_credential_fingerprint": "hmac-sha256:" + "a" * 64,
                "chat_base_url": "https://api.deepseek.com/v1",
                "chat_endpoint_id": "endpoint-deepseek",
                "chat_credential_ref": "credential:deepseek",
                "chat_model_profile_id": "model-deepseek-deepseek-chat",
                "chat_verified_model": None,
                "chat_available_models": [],
                "chat_model_available": None,
                "chat_response_model": None,
                "chat_response_id": None,
                "chat_latency_ms": None,
                "chat_http_status": None,
                "chat_protocol_adapter": None,
                "chat_verification_id": None,
                "chat_verification_status": None,
                "chat_verification_persisted": None,
                "chat_verification_persistence_error": None,
                "chat_provider_profiles": {},
                "project_root": None,
                "project_location_allow_list": [],
            }
        ),
        encoding="utf-8",
    )

    settings = load_production_settings(assets=tmp_path, layout=layout)

    assert settings.environment == "production"
    assert settings.project_root is None
    assert settings.project_location_allow_list == ()


def test_production_settings_can_bind_an_existing_catalog_database(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    database = tmp_path / "canonical-golden-project.sqlite3"
    metadata = tmp_path / "projects-metadata"
    layout = ProductionStateLayout.for_current_user(tmp_path / "state")
    layout.production_config.parent.mkdir(parents=True, exist_ok=True)
    layout.production_config.write_text(
        json.dumps(
            {
                "project_root": str(root),
                "project_location_allow_list": [str(root)],
                "desktop_state_database": str(database),
                "desktop_project_metadata_directory": str(metadata),
            }
        ),
        encoding="utf-8",
    )

    settings = load_production_settings(assets=tmp_path, layout=layout)

    assert settings.desktop_state_database == database
    assert settings.desktop_project_metadata_directory == metadata
    assert settings.production_state_root is None
