from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_context.adapters.project_engineering import (
    ProjectEngineeringEvidenceAdapter,
)
from embedded_copilot.engineering_context.models import (
    ContextSourceType,
    EngineeringContextRequest,
)
from embedded_copilot.engineering_context.projection import ReadOnlyProjectionSource
from embedded_copilot.engineering_context.service import EngineeringContextService
from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRunStatus,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    FakeApplicationAuditSink,
    FakeApplicationModelGateway,
    FixedApplicationClock,
    ModelPolicy,
)


def _request() -> EngineeringContextRequest:
    return EngineeringContextRequest(
        project_id="project-qdwq",
        tenant_id="desktop-tenant",
        task="stm32",
        constraints=(),
    )


def test_create_new_greenfield_projects_do_not_require_a_source_root() -> None:
    project = SimpleNamespace(
        creation_mode=SimpleNamespace(value="CREATE_NEW"),
        project_type=SimpleNamespace(value="STM32"),
        name="qdwq",
    )
    adapter = ProjectEngineeringEvidenceAdapter(
        lambda _project_id: Path("C:/missing-greenfield-project"),
        lambda _project_id, _tenant_id: project,
    )

    values = adapter.list_evidence(_request())

    assert {item.evidence_id for item in values} >= {
        "greenfield-project-metadata",
        "greenfield-user-requirement",
    }
    assert all(item.source_type is ContextSourceType.REQUIREMENT for item in values)


def test_attach_existing_projects_still_fail_closed_when_source_root_is_missing() -> None:
    project = SimpleNamespace(
        creation_mode=SimpleNamespace(value="ATTACH_EXISTING"),
        project_type=SimpleNamespace(value="STM32"),
        name="qdwq",
    )
    adapter = ProjectEngineeringEvidenceAdapter(
        lambda _project_id: Path("C:/missing-brownfield-project"),
        lambda _project_id, _tenant_id: project,
    )

    with pytest.raises(ValueError):
        adapter.list_evidence(_request())


def test_greenfield_context_reaches_the_provider_without_source_evidence() -> None:
    project = SimpleNamespace(
        creation_mode=SimpleNamespace(value="CREATE_NEW"),
        project_type=SimpleNamespace(value="STM32"),
        name="qdwq",
    )
    adapter = ProjectEngineeringEvidenceAdapter(
        lambda _project_id: Path("C:/missing-greenfield-project"),
        lambda _project_id, _tenant_id: project,
    )
    provider = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(engineering_port=adapter)
    )
    gateway = FakeApplicationModelGateway(content="开始收集 STM32 需求")
    request = ApplicationQuestionRequest(
        request_id="greenfield-request",
        trace_id="greenfield-trace",
        project_id="project-qdwq",
        tenant_id="desktop-tenant",
        requester="desktop-user",
        question="stm32",
        constraints=(),
        requested_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=provider,
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(datetime(2026, 8, 17, tzinfo=UTC)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    result = runtime.answer(request)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.requirement_context is not None
    assert gateway.call_count == 1
