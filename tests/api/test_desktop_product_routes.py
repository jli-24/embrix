from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.services.config import Settings


class _ChatService:
    async def chat(self, request):
        raise AssertionError("legacy chat should not be called")


class _DesktopProductService:
    def open_session(self, request, *, requester: str):
        return {
            "product_session_id": request.product_session_id,
            "requester": requester,
        }


def test_desktop_product_session_requires_requester_and_uses_injected_runtime() -> None:
    app = create_app(
        service=_ChatService(),
        settings=Settings(_env_file=None),
        desktop_product_runtime=_DesktopProductService(),
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with TestClient(app) as client:
        missing = client.post(
            "/api/v1/desktop/sessions",
            json={
                "product_session_id": "session1",
                "project_id": "project1",
                "expires_at": expires,
            },
        )
        opened = client.post(
            "/api/v1/desktop/sessions",
            headers={"X-Desktop-Requester": "engineer1"},
            json={
                "product_session_id": "session1",
                "project_id": "project1",
                "expires_at": expires,
            },
        )
    assert missing.status_code == 422
    assert opened.status_code == 200
    assert opened.json() == {"product_session_id": "session1", "requester": "engineer1"}
