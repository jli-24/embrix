from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.agent_runtime import (
    AgentCapabilityRequest,
    CapabilityAsyncMode,
    CapabilityBindingReference,
    CapabilityBindingUnavailable,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilityType,
    DebugCapabilityBinding,
    DebugInput,
    FirmwareCapabilityBinding,
    FirmwareInput,
    KnowledgeCapabilityBinding,
    KnowledgeInput,
    OptimizationCapabilityBinding,
    OptimizationInput,
    OptimizationMode,
    PCBCapabilityBinding,
    PCBInput,
    RepairCapabilityBinding,
    RepairInput,
    VisionCapabilityBinding,
    VisionInput,
)
from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.debug_analysis import DebugInputSnapshot
from embedded_copilot.debug_analysis.adapters.fake import FakeDebugAnalyzer
from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.engineering_optimization import (
    EngineeringOptimizationService,
    OptimizationRisk,
)
from embedded_copilot.engineering_optimization.adapters import (
    FakeOptimizationAnalysisPort,
    FakeOptimizationAuditPort,
    FakeOptimizationContextProjectionPort,
    FakeOptimizationDecisionPort,
    FakeOptimizationEvidencePort,
    FakeOptimizationMemoryPort,
    FakeOptimizationProposalReader,
    FakeOptimizationReviewPort,
    FixedClock,
)
from embedded_copilot.engineering_pcb import PCBAnalysisRequest
from embedded_copilot.engineering_repair.adapters import FakeRepairAnalysisAdapter
from embedded_copilot.firmware_intelligence import (
    FirmwareContext,
    FirmwarePlanningService,
    InterfaceContract,
)
from embedded_copilot.multimodal_engineering import EngineeringObservation
from tests.engineering_optimization.test_contracts import NOW as V46_NOW
from tests.engineering_optimization.test_contracts import request as v46_request
from tests.engineering_optimization_loop.test_service import (
    proposal as optimization_proposal,
)
from tests.engineering_optimization_loop.test_service import (
    request as optimization_request,
)
from tests.engineering_pcb import conftest as pcb_fixtures
from tests.engineering_pcb.test_service import make_service as make_pcb_service
from tests.engineering_repair import _factories as repair_fixtures
from tests.multimodal_engineering.fixtures import artifact, observation

from .conftest import application_request, knowledge_output


def request_for(
    question: str,
    payload,
    *,
    project_id: str = "project-1",
    requester: str = "engineer-1",
    requested_at=None,
) -> AgentCapabilityRequest:
    values: dict[str, object] = {
        "request_id": f"request-{payload.capability.value.casefold()}",
        "trace_id": f"trace-{payload.capability.value.casefold()}",
        "project_id": project_id,
        "requester": requester,
        "question": question,
        "constraints": (),
        "requested_at": requested_at or application_request().requested_at,
    }
    return AgentCapabilityRequest(
        application_request=ApplicationQuestionRequest(**values),
        payload=payload,
    )


class KnowledgeRuntime:
    def answer(self, request):
        return knowledge_output(request.question).result

    async def aanswer(self, request):
        return self.answer(request)


class VisionService:
    def analyze(self, value):
        return observation(value.artifact_type)

    async def aanalyze(self, value):
        return self.analyze(value)


class OptimizationService:
    def generate(self, request):
        return (optimization_proposal(),)


def v46_service() -> tuple[EngineeringOptimizationService, FakeOptimizationAuditPort]:
    value = v46_request()
    clock = FixedClock(V46_NOW)
    audit = FakeOptimizationAuditPort()
    return (
        EngineeringOptimizationService(
            evidence=FakeOptimizationEvidencePort(value.evidence_refs),
            analysis=FakeOptimizationAnalysisPort(clock),
            reviews=FakeOptimizationReviewPort(),
            decisions=FakeOptimizationDecisionPort(),
            context=FakeOptimizationContextProjectionPort(),
            memory=FakeOptimizationMemoryPort(),
            audit=audit,
            clock=clock,
        ),
        audit,
    )


def test_registry_is_constructor_only_and_resolves_static_binding() -> None:
    binding = KnowledgeCapabilityBinding(KnowledgeRuntime())
    registry = CapabilityRegistry((binding,))
    assert registry.resolve(CapabilityType.KNOWLEDGE) is binding
    assert registry.resolve(CapabilityType.VISION) is None
    for forbidden in ("register", "update", "remove", "delete"):
        assert not hasattr(registry, forbidden)


def test_registry_rejects_duplicate_capability() -> None:
    with pytest.raises(CapabilityRegistryError):
        CapabilityRegistry(
            (
                KnowledgeCapabilityBinding(KnowledgeRuntime()),
                KnowledgeCapabilityBinding(KnowledgeRuntime()),
            )
        )


def test_registry_rejects_duplicate_binding_identity() -> None:
    class IdentityBinding:
        def __init__(self, capability):
            self._reference = CapabilityBindingReference(
                capability=capability,
                binding_id="same-binding",
                binding_version="v4.3.2",
                async_mode=CapabilityAsyncMode.THREAD_BRIDGE,
            )

        @property
        def reference(self):
            return self._reference

        def invoke(self, request):
            raise AssertionError

        async def ainvoke(self, request):
            raise AssertionError

    with pytest.raises(CapabilityRegistryError):
        CapabilityRegistry(
            (
                IdentityBinding(CapabilityType.KNOWLEDGE),
                IdentityBinding(CapabilityType.VISION),
            )
        )


def test_knowledge_binding_calls_existing_application_runtime() -> None:
    binding = KnowledgeCapabilityBinding(KnowledgeRuntime())
    request = AgentCapabilityRequest(
        application_request=application_request("What is GPIO12 used for?"),
        payload=KnowledgeInput(),
    )
    assert binding.invoke(request).result.status.value == "COMPLETED"
    assert asyncio.run(binding.ainvoke(request)).capability is CapabilityType.KNOWLEDGE


def test_vision_binding_calls_existing_multimodal_service_contract() -> None:
    binding = VisionCapabilityBinding(VisionService())
    request = request_for(
        "Analyze this PCB image",
        VisionInput(artifact=artifact()),
        requested_at=artifact().captured_at,
    )
    output = binding.invoke(request)
    assert output.observation == observation()
    assert asyncio.run(binding.ainvoke(request)).capability is CapabilityType.VISION


def test_pcb_binding_only_calls_analysis_service() -> None:
    artifacts = pcb_fixtures.artifacts.__wrapped__()
    parsed = pcb_fixtures.parsed_artifacts.__wrapped__(artifacts)
    snapshot = pcb_fixtures.snapshot.__wrapped__()
    context_request = pcb_fixtures.context_request.__wrapped__(artifacts)
    service = make_pcb_service(artifacts, parsed, snapshot)
    context = service.build_context(context_request)
    analysis_request = PCBAnalysisRequest.create(
        analysis_id="router-pcb-analysis",
        project_id=pcb_fixtures.PROJECT,
        context_fingerprint=context.fingerprint,
        rule_scope=("POWER_NETS",),
        requester="engineer",
        requested_at=pcb_fixtures.NOW,
    )
    request = request_for(
        "Inspect PCB netlist",
        PCBInput(analysis_request=analysis_request, context=context),
        project_id=pcb_fixtures.PROJECT,
        requester="engineer",
        requested_at=pcb_fixtures.NOW,
    )
    output = PCBCapabilityBinding(service).invoke(request)
    assert output.analysis.context_fingerprint == context.fingerprint


def test_firmware_binding_calls_existing_planning_service() -> None:
    question = "Analyze STM32 HAL firmware driver"
    requirement = EngineeringRequirement.create(
        requirement_id="firmware-requirement",
        project_id="project-1",
        description=question,
    )
    context = FirmwareContext.create(
        project_id="project-1",
        platform="STM32F4",
        sdk_hal="STM32 HAL",
        rtos="FreeRTOS",
        clock_assumptions=("84MHz",),
    )
    interface = InterfaceContract.create(
        name="SPI", direction="bidirectional", signal="SPI1"
    )
    request = request_for(
        question,
        FirmwareInput(
            requirement=requirement,
            hardware_context=context,
            interfaces=(interface,),
            evidence_refs=("datasheet-1",),
        ),
    )
    output = FirmwareCapabilityBinding(FirmwarePlanningService()).invoke(request)
    assert output.proposal.project_id == "project-1"


def test_debug_binding_calls_existing_analysis_service() -> None:
    snapshot = DebugInputSnapshot.create(
        project_id="project-1",
        failure_reference="failure:project-1",
        failure_type="BUILD",
        safe_summary="Compiler failure requires analysis.",
        evidence_reference="evidence:project-1",
    )
    request = request_for("error: compiler failure", DebugInput(snapshot=snapshot))
    output = DebugCapabilityBinding(FakeDebugAnalyzer()).invoke(request)
    assert output.analysis.failure_reference == snapshot.failure_reference


def test_repair_binding_only_calls_analysis_port() -> None:
    source = repair_fixtures.analysis()
    repair_request = repair_fixtures.request(source)
    request = request_for(
        "repair this failure",
        RepairInput(request=repair_request),
        requested_at=repair_fixtures.NOW,
    )
    output = RepairCapabilityBinding(FakeRepairAnalysisAdapter((source,))).invoke(
        request
    )
    assert output.analysis == source


def test_optimization_binding_only_returns_proposals() -> None:
    request = request_for(
        "optimize the power design",
        OptimizationInput(request=optimization_request()),
    )
    output = OptimizationCapabilityBinding(OptimizationService()).invoke(request)
    assert output.proposals == (optimization_proposal(),)


def test_v46_optimization_binding_uses_explicit_create_path_without_legacy_fallback() -> (
    None
):
    service, _ = v46_service()
    request = request_for(
        "optimize the power design",
        OptimizationInput(v46_request=v46_request()),
        requested_at=V46_NOW,
    )
    output = OptimizationCapabilityBinding(v46_service=service).invoke(request)
    assert output.mode is OptimizationMode.CREATE_PROPOSAL
    assert output.proposal is not None
    assert output.proposals == ()
    with pytest.raises(CapabilityBindingUnavailable):
        OptimizationCapabilityBinding(OptimizationService()).invoke(request)


def test_v46_read_proposal_is_reader_only_and_has_no_governance_side_effect() -> None:
    service, audit = v46_service()
    analysis = service.analyze(v46_request())
    proposal = service.create_proposal(
        analysis,
        analysis.findings[0].finding_id,
        recommendation="Review the evidence-bound optimization before any change.",
        risk=OptimizationRisk.LOW,
        expected_impact="Potential improvement requires human review.",
    )
    before_events = tuple(audit._items.values())
    request = request_for(
        "optimize the power design",
        OptimizationInput(
            mode=OptimizationMode.READ_PROPOSAL, proposal_id=proposal.proposal_id
        ),
        requested_at=V46_NOW,
    )
    output = OptimizationCapabilityBinding(
        proposal_reader=FakeOptimizationProposalReader((proposal,))
    ).invoke(request)
    assert output.mode is OptimizationMode.READ_PROPOSAL
    assert output.proposal == proposal
    assert tuple(audit._items.values()) == before_events


def test_seven_bindings_have_unique_static_capabilities() -> None:
    source = repair_fixtures.analysis()
    bindings = (
        KnowledgeCapabilityBinding(KnowledgeRuntime()),
        VisionCapabilityBinding(VisionService()),
        DebugCapabilityBinding(FakeDebugAnalyzer()),
        FirmwareCapabilityBinding(FirmwarePlanningService()),
        RepairCapabilityBinding(FakeRepairAnalysisAdapter((source,))),
        OptimizationCapabilityBinding(OptimizationService()),
    )
    assert len({item.reference.capability for item in bindings}) == len(bindings)


def test_vision_service_returning_wrong_artifact_is_rejected() -> None:
    class WrongVision(VisionService):
        def analyze(self, value) -> EngineeringObservation:
            return observation(value.artifact_type).model_copy(
                update={
                    "artifact_reference": artifact(value.artifact_type).model_copy(
                        update={"artifact_id": "wrong"}
                    )
                }
            )

    request = request_for(
        "Analyze this PCB image",
        VisionInput(artifact=artifact()),
        requested_at=artifact().captured_at,
    )
    with pytest.raises(CapabilityBindingUnavailable):
        VisionCapabilityBinding(WrongVision()).invoke(request)
