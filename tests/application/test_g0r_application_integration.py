from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationRoute,
    ApplicationRunStatus,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.engineering_research import (
    EngineeringResearchService,
    GitHubResearchSource,
    ResearchSourceType,
    UnavailableResearchSource,
)

from .fixtures import NOW, request


class MappingTransport:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = values

    def search(self, query: str) -> object:
        del query
        return self.values


def test_g0r_is_reachable_from_application_and_does_not_promote_reference_truth() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    research = EngineeringResearchService(
        sources=(
            GitHubResearchSource(
                MappingTransport(
                    [
                        {
                            "url": "https://github.com/alice/smartwatch",
                            "title": "ESP32-S3 smartwatch reference",
                            "description": "Smartwatch with IMU, display, battery and USB",
                            "license": "MIT",
                            "artifact_hints": {"firmware": "PRESENT", "bom": "UNKNOWN"},
                            "extracted_facts": [
                                {
                                    "category": "BATTERY_MEASUREMENT",
                                    "value": "battery gauge",
                                    "artifact": "README",
                                }
                            ],
                        }
                    ]
                )
            ),
            UnavailableResearchSource(ResearchSourceType.OSHWHUB, "TEST_UNAVAILABLE"),
            UnavailableResearchSource(ResearchSourceType.VENDOR_REFERENCE, "TEST_UNAVAILABLE"),
        )
    )
    question = request(
        question="做一个 ESP32-S3 智能手表，带 IMU、屏幕、电池充电和 USB",
        tenant_id="desktop-tenant",
    )
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(content="开始收集需求"),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            research_service=research,
        )
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.research_snapshot is not None
    assert result.research_snapshot.evidence_pack.projects[0].license_name == "MIT"
    assert "参考项目调研" in result.answer.content
    assert result.design_truth is None
    assert not any(
        item.category == "BATTERY_LEVEL_MONITORING"
        for item in result.requirement_context.requirements
    )
