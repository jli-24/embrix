from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.project_catalog_routes import router


class DecisionCatalog:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def decide_requirement_suggestion(self, project_id, conversation_id, suggestion_id, action, **kwargs):
        self.calls.append(
            {
                "project_id": project_id,
                "conversation_id": conversation_id,
                "suggestion_id": suggestion_id,
                "action": action,
                **kwargs,
            }
        )
        return {"status": "persisted", "suggestion_id": suggestion_id}


def _app(catalog: DecisionCatalog) -> FastAPI:
    application = FastAPI()
    application.state.project_catalog_runtime = catalog

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-g0r"
        return await call_next(request)

    application.include_router(router)
    return application


def test_requirement_suggestion_decision_route_is_explicit_and_typed() -> None:
    catalog = DecisionCatalog()
    with TestClient(_app(catalog)) as client:
        response = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/requirement-suggestions/suggestion-1/decision",
            headers={
                "X-Copilot-Requester": "engineer-1",
                "X-Environment-Tenant": "tenant-1",
            },
            json={"action": "MODIFY", "modified_requirement": "只需要充电，不需要数据"},
        )
        invalid = client.post(
            "/api/v1/copilot/projects/project-1/conversations/conversation-1/requirement-suggestions/suggestion-1/decision",
            headers={
                "X-Copilot-Requester": "engineer-1",
                "X-Environment-Tenant": "tenant-1",
            },
            json={"action": "PENDING"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "persisted", "suggestion_id": "suggestion-1"}
    assert catalog.calls[0]["action"].value == "MODIFY"
    assert catalog.calls[0]["modified_requirement"] == "只需要充电，不需要数据"
    assert invalid.status_code == 422
