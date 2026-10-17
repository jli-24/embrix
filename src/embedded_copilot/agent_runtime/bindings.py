from __future__ import annotations

import asyncio
import copy
from typing import Protocol, runtime_checkable

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.debug_analysis import DebugAnalysisSnapshot, DebugInputSnapshot
from embedded_copilot.engineering_eda import EDAAnalysisRequest, EDAAnalysisResult
from embedded_copilot.engineering_optimization import (
    EngineeringOptimizationService,
    OptimizationRisk,
)
from embedded_copilot.engineering_optimization import (
    OptimizationProposal as V46OptimizationProposal,
)
from embedded_copilot.engineering_optimization_loop import (
    OptimizationProposal as LegacyOptimizationProposal,
)
from embedded_copilot.engineering_optimization_loop import (
    OptimizationRequest as LegacyOptimizationRequest,
)
from embedded_copilot.engineering_pcb import PCBAnalysis, PCBAnalysisRequest, PCBContext
from embedded_copilot.engineering_repair import RepairAnalysisPort
from embedded_copilot.firmware_intelligence import FirmwareProposal
from embedded_copilot.hardware_hil_runtime import (
    HardwareObservation as HILHardwareObservation,
)
from embedded_copilot.hardware_hil_runtime import (
    HardwareTestProposal,
    HardwareTestProposalRequest,
)
from embedded_copilot.hardware_runtime import (
    HardwareObservation,
    HardwareObservationRequest,
)
from embedded_copilot.multimodal_engineering import (
    EngineeringArtifact,
    EngineeringObservation,
)

from .exceptions import CapabilityBindingRejected, CapabilityBindingUnavailable
from .models import (
    AgentCapabilityRequest,
    CapabilityAsyncMode,
    CapabilityBindingReference,
    CapabilityType,
    DebugInput,
    DebugOutput,
    EDAAnalysisInput,
    EDAAnalysisOutput,
    FirmwareInput,
    FirmwareOutput,
    HardwareHILTestInput,
    HardwareHILTestMode,
    HardwareHILTestOutput,
    HardwareTestInput,
    HardwareTestOutput,
    KnowledgeInput,
    KnowledgeOutput,
    OptimizationInput,
    OptimizationMode,
    OptimizationOutput,
    PCBInput,
    PCBOutput,
    RepairInput,
    RepairOutput,
    VisionInput,
    VisionOutput,
)


@runtime_checkable
class _KnowledgeRuntimePort(Protocol):
    def answer(self, request: ApplicationQuestionRequest) -> ApplicationRunResult: ...

    async def aanswer(
        self, request: ApplicationQuestionRequest
    ) -> ApplicationRunResult: ...


@runtime_checkable
class _VisionServicePort(Protocol):
    def analyze(self, artifact: EngineeringArtifact) -> EngineeringObservation: ...

    async def aanalyze(
        self, artifact: EngineeringArtifact
    ) -> EngineeringObservation: ...


@runtime_checkable
class _PCBServicePort(Protocol):
    def analyze(
        self, request: PCBAnalysisRequest, context: PCBContext
    ) -> PCBAnalysis: ...


@runtime_checkable
class _FirmwareServicePort(Protocol):
    def plan(
        self,
        requirement,
        hardware_context,
        interfaces,
        evidence_refs=(),
        *,
        context_snapshot=None,
    ) -> FirmwareProposal: ...


@runtime_checkable
class _DebugServicePort(Protocol):
    def analyze(self, value: DebugInputSnapshot) -> DebugAnalysisSnapshot: ...


@runtime_checkable
class _OptimizationServicePort(Protocol):
    def generate(
        self, request: LegacyOptimizationRequest
    ) -> tuple[LegacyOptimizationProposal, ...]: ...


@runtime_checkable
class _OptimizationProposalReaderPort(Protocol):
    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> V46OptimizationProposal | None: ...


@runtime_checkable
class _EDAServicePort(Protocol):
    def analyze(self, request: EDAAnalysisRequest) -> EDAAnalysisResult: ...


@runtime_checkable
class _HardwareObservationServicePort(Protocol):
    def observe(self, request: HardwareObservationRequest) -> HardwareObservation: ...


@runtime_checkable
class _HILProposalPort(Protocol):
    def create_test_proposal(
        self, request: HardwareTestProposalRequest
    ) -> HardwareTestProposal: ...


@runtime_checkable
class _HILObservationReaderPort(Protocol):
    def get(self, observation_id: str) -> HILHardwareObservation | None: ...


def _request(value: object) -> AgentCapabilityRequest:
    if type(value) is not AgentCapabilityRequest:
        raise CapabilityBindingRejected()
    try:
        return AgentCapabilityRequest.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )
    except Exception as error:
        raise CapabilityBindingRejected() from error


def _service_error(error: Exception) -> Exception:
    if (
        isinstance(error, (TypeError, ValueError))
        or "rejected" in type(error).__name__.casefold()
    ):
        return CapabilityBindingRejected()
    return CapabilityBindingUnavailable()


class _BindingBase:
    __slots__ = ("_reference",)

    def __init__(
        self,
        capability: CapabilityType,
        binding_id: str,
        async_mode: CapabilityAsyncMode,
    ) -> None:
        self._reference = CapabilityBindingReference(
            capability=capability,
            binding_id=binding_id,
            binding_version="v4.3.2",
            async_mode=async_mode,
        )

    @property
    def reference(self) -> CapabilityBindingReference:
        return CapabilityBindingReference.model_validate(
            copy.deepcopy(self._reference.model_dump(mode="python"))
        )


class KnowledgeCapabilityBinding(_BindingBase):
    __slots__ = ("_runtime",)

    def __init__(self, runtime: _KnowledgeRuntimePort) -> None:
        if not isinstance(runtime, _KnowledgeRuntimePort):
            raise TypeError("knowledge runtime is invalid")
        super().__init__(
            CapabilityType.KNOWLEDGE,
            "knowledge-runtime",
            CapabilityAsyncMode.NATIVE_ASYNC,
        )
        self._runtime = runtime

    @staticmethod
    def _output(raw: object) -> KnowledgeOutput:
        try:
            result = ApplicationRunResult.model_validate(
                copy.deepcopy(raw.model_dump(mode="python"))
            )
        except Exception as error:
            raise CapabilityBindingUnavailable() from error
        if result.status is ApplicationRunStatus.REJECTED:
            raise CapabilityBindingRejected()
        if result.status is not ApplicationRunStatus.COMPLETED:
            raise CapabilityBindingUnavailable()
        try:
            return KnowledgeOutput(result=result)
        except Exception as error:
            raise CapabilityBindingRejected() from error

    @staticmethod
    def _bind_request(
        request: AgentCapabilityRequest, output: KnowledgeOutput
    ) -> KnowledgeOutput:
        expected = request.application_request
        result = output.result
        if (
            result.request_id != expected.request_id
            or result.trace_id != expected.trace_id
            or result.project_id != expected.project_id
            or result.question_fingerprint != expected.fingerprint
        ):
            raise CapabilityBindingRejected()
        return output

    def invoke(self, request: AgentCapabilityRequest) -> KnowledgeOutput:
        checked = _request(request)
        if type(checked.payload) is not KnowledgeInput:
            raise CapabilityBindingRejected()
        try:
            return self._bind_request(
                checked,
                self._output(
                    self._runtime.answer(copy.deepcopy(checked.application_request))
                ),
            )
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error

    async def ainvoke(self, request: AgentCapabilityRequest) -> KnowledgeOutput:
        checked = _request(request)
        if type(checked.payload) is not KnowledgeInput:
            raise CapabilityBindingRejected()
        try:
            raw = await self._runtime.aanswer(
                copy.deepcopy(checked.application_request)
            )
            return self._bind_request(checked, self._output(raw))
        except asyncio.CancelledError:
            raise
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error


class VisionCapabilityBinding(_BindingBase):
    __slots__ = ("_service",)

    def __init__(self, service: _VisionServicePort) -> None:
        if not isinstance(service, _VisionServicePort):
            raise TypeError("vision service is invalid")
        super().__init__(
            CapabilityType.VISION, "vision-service", CapabilityAsyncMode.NATIVE_ASYNC
        )
        self._service = service

    def _output(self, request: AgentCapabilityRequest, raw: object) -> VisionOutput:
        try:
            output = VisionOutput(observation=raw)
        except Exception as error:
            raise CapabilityBindingUnavailable() from error
        if output.observation.artifact_reference != request.payload.artifact:
            raise CapabilityBindingRejected()
        return output

    def invoke(self, request: AgentCapabilityRequest) -> VisionOutput:
        checked = _request(request)
        if type(checked.payload) is not VisionInput:
            raise CapabilityBindingRejected()
        try:
            return self._output(
                checked, self._service.analyze(copy.deepcopy(checked.payload.artifact))
            )
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error

    async def ainvoke(self, request: AgentCapabilityRequest) -> VisionOutput:
        checked = _request(request)
        if type(checked.payload) is not VisionInput:
            raise CapabilityBindingRejected()
        try:
            raw = await self._service.aanalyze(copy.deepcopy(checked.payload.artifact))
            return self._output(checked, raw)
        except asyncio.CancelledError:
            raise
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error


class _ThreadBinding(_BindingBase):
    async def ainvoke(self, request: AgentCapabilityRequest):
        return await asyncio.to_thread(self.invoke, request)


class PCBCapabilityBinding(_ThreadBinding):
    __slots__ = ("_service",)

    def __init__(self, service: _PCBServicePort) -> None:
        if not isinstance(service, _PCBServicePort):
            raise TypeError("PCB service is invalid")
        super().__init__(
            CapabilityType.PCB_ANALYSIS,
            "pcb-analysis-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._service = service

    def invoke(self, request: AgentCapabilityRequest) -> PCBOutput:
        checked = _request(request)
        if type(checked.payload) is not PCBInput:
            raise CapabilityBindingRejected()
        try:
            output = PCBOutput(
                analysis=self._service.analyze(
                    copy.deepcopy(checked.payload.analysis_request),
                    copy.deepcopy(checked.payload.context),
                )
            )
        except Exception as error:
            raise _service_error(error) from error
        if (
            output.analysis.project_id != checked.application_request.project_id
            or output.analysis.analysis_id
            != checked.payload.analysis_request.analysis_id
            or output.analysis.context_fingerprint
            != checked.payload.context.fingerprint
        ):
            raise CapabilityBindingRejected()
        return output


class FirmwareCapabilityBinding(_ThreadBinding):
    __slots__ = ("_service",)

    def __init__(self, service: _FirmwareServicePort) -> None:
        if not isinstance(service, _FirmwareServicePort):
            raise TypeError("firmware service is invalid")
        super().__init__(
            CapabilityType.FIRMWARE_ANALYSIS,
            "firmware-planning-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._service = service

    def invoke(self, request: AgentCapabilityRequest) -> FirmwareOutput:
        checked = _request(request)
        if type(checked.payload) is not FirmwareInput:
            raise CapabilityBindingRejected()
        payload = checked.payload
        try:
            output = FirmwareOutput(
                proposal=self._service.plan(
                    copy.deepcopy(payload.requirement),
                    copy.deepcopy(payload.hardware_context),
                    copy.deepcopy(payload.interfaces),
                    copy.deepcopy(payload.evidence_refs),
                    context_snapshot=copy.deepcopy(payload.context_snapshot),
                )
            )
        except Exception as error:
            raise _service_error(error) from error
        if output.proposal.project_id != checked.application_request.project_id:
            raise CapabilityBindingRejected()
        if set(output.proposal.evidence_refs) != set(payload.evidence_refs):
            raise CapabilityBindingRejected()
        return output


class DebugCapabilityBinding(_ThreadBinding):
    __slots__ = ("_service",)

    def __init__(self, service: _DebugServicePort) -> None:
        if not isinstance(service, _DebugServicePort):
            raise TypeError("debug service is invalid")
        super().__init__(
            CapabilityType.DEBUG_ANALYSIS,
            "debug-analysis-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._service = service

    def invoke(self, request: AgentCapabilityRequest) -> DebugOutput:
        checked = _request(request)
        if type(checked.payload) is not DebugInput:
            raise CapabilityBindingRejected()
        try:
            output = DebugOutput(
                analysis=self._service.analyze(copy.deepcopy(checked.payload.snapshot))
            )
        except Exception as error:
            raise _service_error(error) from error
        if (
            output.analysis.project_id != checked.application_request.project_id
            or output.analysis.failure_reference
            != checked.payload.snapshot.failure_reference
        ):
            raise CapabilityBindingRejected()
        return output


class RepairCapabilityBinding(_ThreadBinding):
    __slots__ = ("_port",)

    def __init__(self, port: RepairAnalysisPort) -> None:
        if not isinstance(port, RepairAnalysisPort):
            raise TypeError("repair analysis port is invalid")
        super().__init__(
            CapabilityType.REPAIR_ANALYSIS,
            "repair-analysis-port",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._port = port

    def invoke(self, request: AgentCapabilityRequest) -> RepairOutput:
        checked = _request(request)
        if type(checked.payload) is not RepairInput:
            raise CapabilityBindingRejected()
        try:
            output = RepairOutput(
                analysis=self._port.analyze(copy.deepcopy(checked.payload.request))
            )
        except Exception as error:
            raise _service_error(error) from error
        expected = checked.payload.request.failure_reference
        if (
            output.analysis.project_id != checked.application_request.project_id
            or output.analysis.failure_id != expected.failure_id
            or output.analysis.fingerprint != expected.failure_fingerprint
        ):
            raise CapabilityBindingRejected()
        return output


class OptimizationCapabilityBinding(_ThreadBinding):
    __slots__ = ("_legacy", "_reader", "_v46")

    def __init__(
        self,
        service: _OptimizationServicePort | None = None,
        *,
        v46_service: EngineeringOptimizationService | None = None,
        proposal_reader: _OptimizationProposalReaderPort | None = None,
    ) -> None:
        if service is not None and not isinstance(service, _OptimizationServicePort):
            raise TypeError("legacy optimization service is invalid")
        if v46_service is not None and not isinstance(
            v46_service, EngineeringOptimizationService
        ):
            raise TypeError("v4.6 optimization service is invalid")
        if proposal_reader is not None and not isinstance(
            proposal_reader, _OptimizationProposalReaderPort
        ):
            raise TypeError("optimization proposal reader is invalid")
        super().__init__(
            CapabilityType.OPTIMIZATION_ANALYSIS,
            "optimization-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._legacy, self._v46, self._reader = service, v46_service, proposal_reader

    def invoke(self, request: AgentCapabilityRequest) -> OptimizationOutput:
        checked = _request(request)
        if type(checked.payload) is not OptimizationInput:
            raise CapabilityBindingRejected()
        payload = checked.payload
        try:
            if payload.request is not None:
                if self._legacy is None:
                    raise CapabilityBindingUnavailable()
                output = OptimizationOutput(
                    proposals=self._legacy.generate(copy.deepcopy(payload.request))
                )
                requested_findings, requested_targets = (
                    set(payload.request.finding_refs),
                    set(payload.request.optimization_scope),
                )
                if any(
                    item.project_id != checked.application_request.project_id
                    or not set(item.source_finding_refs).issubset(requested_findings)
                    or item.target not in requested_targets
                    for item in output.proposals
                ):
                    raise CapabilityBindingRejected()
                return output
            if payload.mode is OptimizationMode.CREATE_PROPOSAL:
                if self._v46 is None or payload.v46_request is None:
                    raise CapabilityBindingUnavailable()
                analysis = self._v46.analyze(copy.deepcopy(payload.v46_request))
                finding = analysis.findings[0]
                proposal = self._v46.create_proposal(
                    analysis,
                    finding.finding_id,
                    recommendation="Review the evidence-bound optimization before any change.",
                    risk=OptimizationRisk.LOW,
                    expected_impact="Potential improvement requires human review.",
                )
                if proposal.project_id != checked.application_request.project_id:
                    raise CapabilityBindingRejected()
                return OptimizationOutput(
                    mode=OptimizationMode.CREATE_PROPOSAL, proposal=proposal
                )
            if self._reader is None or payload.proposal_id is None:
                raise CapabilityBindingUnavailable()
            proposal = self._reader.get_proposal(
                checked.application_request.project_id, payload.proposal_id
            )
            if (
                proposal is None
                or proposal.project_id != checked.application_request.project_id
            ):
                raise CapabilityBindingRejected()
            return OptimizationOutput(
                mode=OptimizationMode.READ_PROPOSAL, proposal=proposal
            )
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error


class EDAAnalysisCapabilityBinding(_ThreadBinding):
    __slots__ = ("_service",)

    def __init__(self, service: _EDAServicePort) -> None:
        if not isinstance(service, _EDAServicePort):
            raise TypeError("EDA service is invalid")
        super().__init__(
            CapabilityType.EDA_ANALYSIS,
            "eda-analysis-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._service = service

    def invoke(self, request: AgentCapabilityRequest) -> EDAAnalysisOutput:
        checked = _request(request)
        if type(checked.payload) is not EDAAnalysisInput:
            raise CapabilityBindingRejected()
        try:
            output = EDAAnalysisOutput(
                result=self._service.analyze(copy.deepcopy(checked.payload.request))
            )
        except Exception as error:
            raise _service_error(error) from error
        expected = checked.payload.request
        if (
            output.result.project_id != checked.application_request.project_id
            or output.result.request_fingerprint != expected.fingerprint
            or output.result.pcb_analysis_fingerprint
            != expected.pcb_analysis.fingerprint
        ):
            raise CapabilityBindingRejected()
        return output


class HardwareTestCapabilityBinding(_ThreadBinding):
    __slots__ = ("_service",)

    def __init__(self, service: _HardwareObservationServicePort) -> None:
        if not isinstance(service, _HardwareObservationServicePort):
            raise TypeError("hardware observation service is invalid")
        super().__init__(
            CapabilityType.HARDWARE_TEST,
            "hardware-observation-service",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._service = service

    def invoke(self, request: AgentCapabilityRequest) -> HardwareTestOutput:
        checked = _request(request)
        if type(checked.payload) is not HardwareTestInput:
            raise CapabilityBindingRejected()
        try:
            output = HardwareTestOutput(
                observation=self._service.observe(
                    copy.deepcopy(checked.payload.request)
                )
            )
        except Exception as error:
            raise _service_error(error) from error
        expected = checked.payload.request
        if (
            output.observation.project_id != checked.application_request.project_id
            or output.observation.request_fingerprint != expected.fingerprint
            or output.observation.target != expected.target
        ):
            raise CapabilityBindingRejected()
        return output


class HardwareHILTestCapabilityBinding(_ThreadBinding):
    """Router-facing HIL binding: proposal and persisted observation read only."""

    __slots__ = ("_observations", "_proposals")

    def __init__(
        self, proposals: _HILProposalPort, observations: _HILObservationReaderPort
    ) -> None:
        if not isinstance(proposals, _HILProposalPort) or not isinstance(
            observations, _HILObservationReaderPort
        ):
            raise TypeError("HIL router binding dependency is invalid")
        super().__init__(
            CapabilityType.HARDWARE_HIL_TEST,
            "hardware-hil-proposal-reader",
            CapabilityAsyncMode.THREAD_BRIDGE,
        )
        self._proposals, self._observations = proposals, observations

    def invoke(self, request: AgentCapabilityRequest) -> HardwareHILTestOutput:
        checked = _request(request)
        if type(checked.payload) is not HardwareHILTestInput:
            raise CapabilityBindingRejected()
        payload = checked.payload
        try:
            if payload.mode is HardwareHILTestMode.PROPOSE:
                proposal = self._proposals.create_test_proposal(
                    copy.deepcopy(payload.proposal_request)
                )
                output = HardwareHILTestOutput(mode=payload.mode, proposal=proposal)
                if proposal.project_id != checked.application_request.project_id:
                    raise CapabilityBindingRejected()
            else:
                observation = self._observations.get(payload.observation_id)
                output = HardwareHILTestOutput(
                    mode=payload.mode, observation=observation
                )
                if (
                    observation is None
                    or observation.project_id != checked.application_request.project_id
                ):
                    raise CapabilityBindingRejected()
            return output
        except (CapabilityBindingRejected, CapabilityBindingUnavailable):
            raise
        except Exception as error:
            raise _service_error(error) from error


__all__ = (
    "DebugCapabilityBinding",
    "EDAAnalysisCapabilityBinding",
    "FirmwareCapabilityBinding",
    "HardwareHILTestCapabilityBinding",
    "HardwareTestCapabilityBinding",
    "KnowledgeCapabilityBinding",
    "OptimizationCapabilityBinding",
    "PCBCapabilityBinding",
    "RepairCapabilityBinding",
    "VisionCapabilityBinding",
)
