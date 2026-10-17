from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def test_production_index_assets_are_served_under_canonical_desktop_prefix(tmp_path: Path) -> None:
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    html = (dist / "index.html").read_text(encoding="utf-8")
    references = re.findall(r"(?:src|href)=\"([^\"]+)\"", html)
    assets = [reference for reference in references if reference.endswith((".js", ".css"))]

    assert assets
    assert all(reference.startswith("/desktop/") for reference in assets)
    assert all(not reference.startswith("/assets/") for reference in assets)

    runtime = ProductionReleaseRuntime.create(
        Settings(
            _env_file=None,
            environment="production",
            production_state_root=tmp_path / "state",
            project_root=tmp_path / "projects",
            desktop_static_assets=dist,
        ),
        credentials=InMemoryCredentialProvider(),
    )
    with TestClient(create_app(settings=runtime.settings, production_release_runtime=runtime)) as client:
        page = client.get("/desktop/")
        assert page.status_code == 200
        for reference in assets:
            response = client.get(reference)
            assert response.status_code == 200, reference
