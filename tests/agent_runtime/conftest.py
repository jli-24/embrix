from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.agent_runtime import (
    AgentCapabilityRequest,
    CapabilityPolicyGrant,
    CapabilityRouter,
    CapabilityType,
    KnowledgeInput,
    KnowledgeOutput,
)
from embedded_copilot.agent_runtime.adapters import (
    FakeCapabilityAuditSink,
    FakeCapabilityBinding,
    FakeCapabilityPolicy,
    FakeCapabilityRegistry,
    FixedCapabilityClock,
)
from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRuntime,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FakeApplicationSupervisor,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)

NOW = datetime(2026, 8, 10, 16, 0, tzinfo=UTC)
PROJECT = "project-1"
REQUESTER = "engineer-1"


def application_request(
    question: str = "What is GPIO12 used for?",
    *,
    project_id: str = PROJECT,
    requester: str = REQUESTER,
    requested_at: datetime = NOW,
) -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="capability-request-1",
        trace_id="capability-trace-1",
        project_id=project_id,
        requester=requester,
        question=question,
        constraints=(),
        requested_at=requested_at,
    )


def context_snapshot(
    question: str = "What is GPIO12 used for?",
) -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="knowledge-gpio12",
        project_id=PROJECT,
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="GPIO12",
        summary="GPIO12 is described by approved project evidence.",
        fact_key="gpio12.role",
        fact_value="project-defined",
        source_reference="knowledge:gpio12",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.9,
        verification_status="APPROVED",
    )
    return EngineeringContextSnapshot.create(
        project_id=PROJECT,
        task_description=question,
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(evidence,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.9,
        provenance=(evidence.source(),),
        verification_status="APPROVED",
    )


def knowledge_output(question: str = "What is GPIO12 used for?") -> KnowledgeOutput:
    request = application_request(question)
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(
                    request, route=ApplicationRoute.KNOWLEDGE
                )
            ),
            context_provider=FakeApplicationContext(context_snapshot(question)),
            model_gateway=FakeApplicationModelGateway(
                content="GPIO12 is described by approved project evidence."
            ),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )
    return KnowledgeOutput(result=runtime.answer(request))


def knowledge_request(
    question: str = "What is GPIO12 used for?",
) -> AgentCapabilityRequest:
    return AgentCapabilityRequest(
        application_request=application_request(question),
        payload=KnowledgeInput(),
    )


def knowledge_router(
    *,
    audit: FakeCapabilityAuditSink | None = None,
    binding: FakeCapabilityBinding | None = None,
    policy: FakeCapabilityPolicy | None = None,
    clock_time: datetime | None = None,
) -> tuple[CapabilityRouter, FakeCapabilityBinding, FakeCapabilityAuditSink]:
    selected_binding = binding or FakeCapabilityBinding(
        capability=CapabilityType.KNOWLEDGE,
        output=knowledge_output(),
    )
    selected_audit = audit or FakeCapabilityAuditSink()
    selected_policy = policy or FakeCapabilityPolicy(
        (
            CapabilityPolicyGrant(
                project_id=PROJECT,
                requester=REQUESTER,
                capabilities=(CapabilityType.KNOWLEDGE,),
            ),
        )
    )
    router = CapabilityRouter(
        policy=selected_policy,
        registry=FakeCapabilityRegistry((selected_binding,)),
        audit_sink=selected_audit,
        clock=FixedCapabilityClock(clock_time or (NOW + timedelta(seconds=2))),
    )
    return router, selected_binding, selected_audit
