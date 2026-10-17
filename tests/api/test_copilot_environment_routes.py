from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_environment_routes import router
from embedded_copilot.copilot_environment import (
    EnvironmentEvent,
    EnvironmentEventType,
    EnvironmentRejected,
)


class _EnvironmentService:
    def list_projects(self, tenant_id: str):
        return [{"tenant_id": tenant_id, "project_id": "project-a"}]

    def project(self, tenant_id: str, project_id: str):
        if project_id == "project-b":
            raise EnvironmentRejected("project tenant mismatch")
        return {"tenant_id": tenant_id, "project_id": project_id}

    def team(self, tenant_id: str):
        return {"tenant_id": tenant_id, "team_id": "team-a"}

    def runtime_health(self, tenant_id: str):
        return []

    def audit_views(self, tenant_id: str, **kwargs):
        return []

    def memory_views(self, tenant_id: str, **kwargs):
        return []

    def events_after(self, tenant_id: str, *, last_event_fingerprint: str | None):
        if last_event_fingerprint == "stale":
            return None
        return (
            EnvironmentEvent(
                tenant_id=tenant_id,
                event_type=EnvironmentEventType.PROJECT_CHANGED,
                reference="project-a",
                status="AVAILABLE",
                timestamp=datetime(2026, 8, 10, tzinfo=UTC),
            ),
        )


def _app(environment_runtime: object | None):
    app = FastAPI()
    app.state.copilot_environment_runtime = environment_runtime

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "test-trace"
        return await call_next(request)

    app.include_router(router)
    return app


def test_environment_requires_tenant_and_injected_runtime() -> None:
    with TestClient(_app(None)) as client:
        missing_tenant = client.get("/api/v1/environment/projects")
        unavailable = client.get(
            "/api/v1/environment/projects",
            headers={
                "X-Copilot-Requester": "engineer-1",
                "X-Environment-Tenant": "tenant-a",
            },
        )
    assert missing_tenant.status_code == 401
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "ENVIRONMENT_UNAVAILABLE"


def test_environment_get_endpoints_bind_the_tenant_and_reject_cross_tenant() -> None:
    with TestClient(_app(_EnvironmentService())) as client:
        projects = client.get(
            "/api/v1/environment/projects",
            headers={"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-a"},
        )
        team = client.get(
            "/api/v1/environment/team",
            headers={"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-a"},
        )
        rejected = client.get(
            "/api/v1/environment/projects/project-b",
            headers={"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-a"},
        )
    assert projects.status_code == 200
    assert projects.json()[0]["tenant_id"] == "tenant-a"
    assert team.json()["tenant_id"] == "tenant-a"
    assert rejected.status_code == 403
    assert rejected.json()["error"] == "ENVIRONMENT_TENANT_REJECTED"


def test_environment_events_replay_or_request_resync_without_mutating_routes() -> None:
    application = _app(_EnvironmentService())
    with TestClient(application) as client:
        replay = client.get(
            "/api/v1/environment/events",
            headers={"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-a"},
        )
        resync = client.get(
            "/api/v1/environment/events",
            headers={
                "X-Copilot-Requester": "engineer-1",
                "X-Environment-Tenant": "tenant-a",
                "Last-Event-ID": "stale",
            },
        )
        environment_paths = {
            path: set(definition)
            for path, definition in application.openapi()["paths"].items()
            if path.startswith("/api/v1/environment")
        }
    assert replay.status_code == 200
    assert "event: PROJECT_CHANGED" in replay.text
    assert "event: RESYNC_REQUIRED" in resync.text
    assert environment_paths
    assert all(methods == {"get"} for methods in environment_paths.values())
