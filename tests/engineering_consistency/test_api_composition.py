from __future__ import annotations

from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings


def test_consistency_route_is_in_existing_desktop_composition():
    app = create_app(settings=Settings(runtime_mode="offline"))
    path = "/api/v1/copilot/projects/{project_id}/hardware-firmware-consistency"
    assert path in app.openapi()["paths"]
    assert {"get", "post"} <= set(app.openapi()["paths"][path])
