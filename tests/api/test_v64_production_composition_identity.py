from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api import main as api_main
from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings
from embedded_copilot.engineering_validation_proposal.adapters.sqlite import (
    SQLiteV64Store,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.paths import versioned_database_path
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def test_production_composition_binds_v64_store_to_v64_database(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            project_root=project_root,
            desktop_static_assets=assets,
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(
        settings=runtime.settings,
        production_release_runtime=runtime,
    )

    with TestClient(app) as client:
        service = app.state.engineering_validation_proposal_service
        assert service is not None
        store = service._store
        assert isinstance(store, SQLiteV64Store)
        assert Path(store.database) == versioned_database_path(
            runtime.settings.desktop_state_database,
            64,
        )
        assert Path(store.database) != versioned_database_path(
            runtime.settings.desktop_state_database,
            63,
        )
        nonce = runtime.sessions.issue_bootstrap(
            requester="desktop-user",
            tenant_id="desktop-tenant",
        )
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        assert client.get(
            "/api/v1/copilot/capabilities",
            headers={
                "X-Copilot-Requester": "desktop-user",
                "X-Environment-Tenant": "desktop-tenant",
            },
        ).status_code == 200


def test_production_composition_does_not_construct_legacy_model_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Production composition has one model invocation authority."""

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            project_root=project_root,
            desktop_static_assets=assets,
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )

    def fail_legacy_factory(*args, **kwargs):
        raise AssertionError("production must not construct model_runtime")

    monkeypatch.setattr(api_main, "create_model_runtime", fail_legacy_factory)
    original_chat_composition = api_main.build_project_first_chat_runtime
    captured_chat_kwargs: dict[str, object] = {}

    def capture_chat_composition(*args, **kwargs):
        captured_chat_kwargs.update(kwargs)
        return original_chat_composition(*args, **kwargs)

    monkeypatch.setattr(
        api_main,
        "build_project_first_chat_runtime",
        capture_chat_composition,
    )
    app = create_app(
        settings=runtime.settings,
        production_release_runtime=runtime,
    )

    with TestClient(app):
        assert app.state.model_invocation_service is not None
        # Status remains a read-only unavailable projection when no live
        # provider is configured; it cannot issue a legacy provider health
        # request.
        assert app.state.model_status_port.__class__.__name__ == "UnavailableStatusPort"
    assert captured_chat_kwargs["provider_binding"] is None
    assert captured_chat_kwargs["provider_policy"] is None
