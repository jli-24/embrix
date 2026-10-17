from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.copilot_environment import (
    CollaborationView,
    CopilotRuntimeHealth,
    EngineeringAuditView,
    EngineeringEnvironmentRuntime,
    EngineeringMemoryView,
    EngineeringTeamContext,
    EnvironmentDeploymentProfile,
    EnvironmentEvent,
    EnvironmentEventType,
    EnvironmentProjectReference,
    EnvironmentRejected,
    FakeAuditQueryAdapter,
    FakeDeploymentProfileAdapter,
    FakeEnvironmentEventStream,
    FakeEnvironmentProjectAdapter,
    FakeMemoryViewAdapter,
    FakeRuntimeHealthAdapter,
    FakeTeamAdapter,
    MemoryViewStatus,
    ProjectEnvironmentStatus,
    ProjectView,
    RuntimeComponent,
    RuntimeHealthStatus,
    TenantEnvironment,
    TenantEnvironmentStatus,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def tenant(tenant_id: str = "tenant-a") -> TenantEnvironment:
    return TenantEnvironment(
        tenant_id=tenant_id,
        environment_id=f"environment-{tenant_id}",
        created_at=NOW,
        status=TenantEnvironmentStatus.READY,
    )


def project(tenant_id: str = "tenant-a", project_id: str = "project-a") -> ProjectView:
    environment = tenant(tenant_id)
    return ProjectView(
        environment=environment,
        project=EnvironmentProjectReference(
            tenant_id=tenant_id,
            project_id=project_id,
            project_name_reference=f"project-name-{project_id}",
            workspace_reference=f"workspace-{project_id}",
            status=ProjectEnvironmentStatus.ACTIVE,
        ),
    )


def collaboration(tenant_id: str = "tenant-a") -> CollaborationView:
    return CollaborationView(
        environment=tenant(tenant_id),
        team=EngineeringTeamContext(
            tenant_id=tenant_id,
            team_id=f"team-{tenant_id}",
            members_reference=("member-a", "member-b"),
            project_scope=("project-a",),
        ),
        active_reviewer_references=("reviewer-a",),
        pending_review_references=("review-a",),
        recent_activity_references=("activity-a",),
    )


def health(tenant_id: str = "tenant-a") -> CopilotRuntimeHealth:
    return CopilotRuntimeHealth(
        tenant_id=tenant_id,
        component=RuntimeComponent.APPLICATION,
        status=RuntimeHealthStatus.HEALTHY,
        observed_at=NOW,
    )


def audit(
    tenant_id: str = "tenant-a", project_id: str = "project-a"
) -> EngineeringAuditView:
    return EngineeringAuditView(
        tenant_id=tenant_id,
        project_id=project_id,
        event_type="PROJECT_VIEWED",
        object_reference="workspace-project-a",
        status="AVAILABLE",
        timestamp=NOW,
    )


def memory(
    tenant_id: str = "tenant-a", project_id: str = "project-a"
) -> EngineeringMemoryView:
    return EngineeringMemoryView(
        tenant_id=tenant_id,
        project_id=project_id,
        memory_reference="memory-a",
        status=MemoryViewStatus.REVIEW_REQUIRED,
    )


def deployment(tenant_id: str = "tenant-a") -> EnvironmentDeploymentProfile:
    return EnvironmentDeploymentProfile(
        tenant_id=tenant_id,
        deployment_id=f"deployment-{tenant_id}",
        runtime_type="LOCAL",
        version_reference="v5.0",
        status=TenantEnvironmentStatus.READY,
    )


def event(
    tenant_id: str = "tenant-a", reference: str = "project-a"
) -> EnvironmentEvent:
    return EnvironmentEvent(
        tenant_id=tenant_id,
        event_type=EnvironmentEventType.PROJECT_CHANGED,
        reference=reference,
        status="AVAILABLE",
        timestamp=NOW,
    )


def runtime() -> EngineeringEnvironmentRuntime:
    return EngineeringEnvironmentRuntime(
        projects=FakeEnvironmentProjectAdapter(
            (project(), project("tenant-b", "project-b"))
        ),
        teams=FakeTeamAdapter((collaboration(), collaboration("tenant-b"))),
        health=FakeRuntimeHealthAdapter((health(),)),
        audit=FakeAuditQueryAdapter((audit(),)),
        memory=FakeMemoryViewAdapter((memory(),)),
        deployment=FakeDeploymentProfileAdapter((deployment(),)),
        events=FakeEnvironmentEventStream((event(),)),
    )


def test_contracts_are_immutable_and_fingerprint_bound() -> None:
    value = project()
    assert (
        value.fingerprint
        == ProjectView.model_validate(value.model_dump(mode="python")).fingerprint
    )
    with pytest.raises(ValidationError):
        EnvironmentProjectReference(
            tenant_id="tenant-a",
            project_id="project-a",
            project_name_reference="api_key",
            workspace_reference="workspace-a",
            status=ProjectEnvironmentStatus.ACTIVE,
        )


def test_runtime_rejects_cross_tenant_project_and_returns_deep_copies() -> None:
    service = runtime()
    listed = service.list_projects("tenant-a")
    repeated = service.list_projects("tenant-a")
    assert listed == (project(),)
    assert listed[0] is not repeated[0]
    with pytest.raises(EnvironmentRejected):
        service.project("tenant-a", "project-b")


def test_runtime_filters_audit_and_memory_without_exposing_content() -> None:
    service = runtime()
    assert service.audit_views("tenant-a", project_id="project-a") == (audit(),)
    assert service.memory_views("tenant-a", project_id="project-a") == (memory(),)
    assert "prompt" not in EngineeringAuditView.model_fields
    assert "summary" not in EngineeringMemoryView.model_fields


def test_fake_event_stream_is_ordered_idempotent_and_resyncs() -> None:
    stream = FakeEnvironmentEventStream(())
    first = stream.publish(event(reference="project-a"))
    duplicate = stream.publish(event(reference="project-a"))
    second = stream.publish(event(reference="project-b"))
    assert first == duplicate
    assert stream.read_after("tenant-a", first.fingerprint) == (second,)
    assert stream.read_after("tenant-a", "sha256:" + "0" * 64) is None


def test_runtime_health_and_team_are_view_only() -> None:
    service = runtime()
    assert service.team("tenant-a") == collaboration()
    assert service.runtime_health("tenant-a") == (health(),)
    assert service.deployment_profile("tenant-a") == deployment()
    assert not hasattr(service, "approve")
    assert not hasattr(service, "promote")
