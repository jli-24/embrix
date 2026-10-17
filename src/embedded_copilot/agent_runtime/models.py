from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.debug_analysis import DebugAnalysisSnapshot, DebugInputSnapshot
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.engineering_eda import EDAAnalysisRequest, EDAAnalysisResult
from embedded_copilot.engineering_optimization import (
    OptimizationProposal as V46OptimizationProposal,
)
from embedded_copilot.engineering_optimization import (
    OptimizationRequest as V46OptimizationRequest,
)
from embedded_copilot.engineering_optimization_loop import (
    OptimizationProposal as LegacyOptimizationProposal,
)
from embedded_copilot.engineering_optimization_loop import (
    OptimizationRequest as LegacyOptimizationRequest,
)
from embedded_copilot.engineering_pcb import PCBAnalysis, PCBAnalysisRequest, PCBContext
from embedded_copilot.engineering_repair import RepairRequest
from embedded_copilot.failure_analysis import FailureAnalysis
from embedded_copilot.firmware_intelligence import (
    FirmwareContext,
    FirmwareProposal,
    InterfaceContract,
)
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

from .contracts import (
    AgentRuntimeContract,
    FingerprintedContract,
    deterministic_id,
    fingerprint,
    identifier,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        material = value.model_dump(mode="python")
    elif type(value) is dict:
        material = value
    else:
        raise ValueError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(material))


class CapabilityType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    VISION = "VISION"
    PCB_ANALYSIS = "PCB_ANALYSIS"
    FIRMWARE_ANALYSIS = "FIRMWARE_ANALYSIS"
    DEBUG_ANALYSIS = "DEBUG_ANALYSIS"
    REPAIR_ANALYSIS = "REPAIR_ANALYSIS"
    OPTIMIZATION_ANALYSIS = "OPTIMIZATION_ANALYSIS"
    EDA_ANALYSIS = "EDA_ANALYSIS"
    HARDWARE_TEST = "HARDWARE_TEST"
    HARDWARE_HIL_TEST = "HARDWARE_HIL_TEST"


class CapabilityAsyncMode(StrEnum):
    NATIVE_ASYNC = "NATIVE_ASYNC"
    THREAD_BRIDGE = "THREAD_BRIDGE"


class CapabilityResultStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class CapabilityAuditEventType(StrEnum):
    CAPABILITY_COMPLETED = "CAPABILITY_COMPLETED"
    CAPABILITY_REJECTED = "CAPABILITY_REJECTED"
    CAPABILITY_FAILED = "CAPABILITY_FAILED"
    CAPABILITY_CANCELLED = "CAPABILITY_CANCELLED"


class CapabilityErrorCode(StrEnum):
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    PAYLOAD_ROUTE_MISMATCH = "PAYLOAD_ROUTE_MISMATCH"
    POLICY_DENIED = "POLICY_DENIED"
    BINDING_UNAVAILABLE = "BINDING_UNAVAILABLE"
    BINDING_REJECTED = "BINDING_REJECTED"
    BINDING_FAILED = "BINDING_FAILED"
    APPLICATION_REJECTED = "APPLICATION_REJECTED"
    APPLICATION_FAILED = "APPLICATION_FAILED"
    CANCELLED = "CANCELLED"


class KnowledgeInput(FingerprintedContract):
    capability: Literal[CapabilityType.KNOWLEDGE] = CapabilityType.KNOWLEDGE


class VisionInput(FingerprintedContract):
    capability: Literal[CapabilityType.VISION] = CapabilityType.VISION
    artifact: EngineeringArtifact

    @field_validator("artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> EngineeringArtifact:
        return _exact(value, EngineeringArtifact, field="artifact")


class PCBInput(FingerprintedContract):
    capability: Literal[CapabilityType.PCB_ANALYSIS] = CapabilityType.PCB_ANALYSIS
    analysis_request: PCBAnalysisRequest
    context: PCBContext

    @field_validator("analysis_request", mode="before")
    @classmethod
    def _request(cls, value: object) -> PCBAnalysisRequest:
        return _exact(value, PCBAnalysisRequest, field="analysis_request")

    @field_validator("context", mode="before")
    @classmethod
    def _context(cls, value: object) -> PCBContext:
        return _exact(value, PCBContext, field="context")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.analysis_request.project_id != self.context.project_id
            or self.analysis_request.context_fingerprint != self.context.fingerprint
        ):
            raise ValueError("PCB input binding mismatch")
        return self


class FirmwareInput(FingerprintedContract):
    capability: Literal[CapabilityType.FIRMWARE_ANALYSIS] = (
        CapabilityType.FIRMWARE_ANALYSIS
    )
    requirement: EngineeringRequirement
    hardware_context: FirmwareContext
    interfaces: tuple[InterfaceContract, ...]
    evidence_refs: tuple[str, ...]
    context_snapshot: EngineeringContextSnapshot | None = None

    @field_validator("requirement", mode="before")
    @classmethod
    def _requirement(cls, value: object) -> EngineeringRequirement:
        return _exact(value, EngineeringRequirement, field="requirement")

    @field_validator("hardware_context", mode="before")
    @classmethod
    def _hardware(cls, value: object) -> FirmwareContext:
        return _exact(value, FirmwareContext, field="hardware_context")

    @field_validator("interfaces", mode="before")
    @classmethod
    def _interfaces(cls, value: object) -> tuple[InterfaceContract, ...]:
        items = tuple(
            _exact(item, InterfaceContract, field="interface")
            for item in tuple_only(value, field="interfaces")
        )
        fingerprints = tuple(item.fingerprint for item in items)
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("firmware interfaces must be unique")
        return items

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="evidence_ref")
            for item in tuple_only(value, field="evidence_refs")
        )
        if not items or len(items) != len(set(items)):
            raise ValueError("firmware evidence is invalid")
        return items

    @field_validator("context_snapshot", mode="before")
    @classmethod
    def _snapshot(cls, value: object) -> EngineeringContextSnapshot | None:
        return (
            None
            if value is None
            else _exact(value, EngineeringContextSnapshot, field="context_snapshot")
        )

    @model_validator(mode="after")
    def _binding(self):
        project = self.requirement.project_id
        if self.hardware_context.project_id != project or (
            self.context_snapshot is not None
            and self.context_snapshot.project_id != project
        ):
            raise ValueError("firmware input binding mismatch")
        return self


class DebugInput(FingerprintedContract):
    capability: Literal[CapabilityType.DEBUG_ANALYSIS] = CapabilityType.DEBUG_ANALYSIS
    snapshot: DebugInputSnapshot

    @field_validator("snapshot", mode="before")
    @classmethod
    def _snapshot(cls, value: object) -> DebugInputSnapshot:
        return _exact(value, DebugInputSnapshot, field="snapshot")


class RepairInput(FingerprintedContract):
    capability: Literal[CapabilityType.REPAIR_ANALYSIS] = CapabilityType.REPAIR_ANALYSIS
    request: RepairRequest

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> RepairRequest:
        return _exact(value, RepairRequest, field="request")


class OptimizationMode(StrEnum):
    CREATE_PROPOSAL = "CREATE_PROPOSAL"
    READ_PROPOSAL = "READ_PROPOSAL"


class OptimizationInput(FingerprintedContract):
    capability: Literal[CapabilityType.OPTIMIZATION_ANALYSIS] = (
        CapabilityType.OPTIMIZATION_ANALYSIS
    )
    request: LegacyOptimizationRequest | None = None
    v46_request: V46OptimizationRequest | None = None
    proposal_id: str | None = None
    mode: OptimizationMode = OptimizationMode.CREATE_PROPOSAL

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> LegacyOptimizationRequest | None:
        return (
            None
            if value is None
            else _exact(value, LegacyOptimizationRequest, field="request")
        )

    @field_validator("v46_request", mode="before")
    @classmethod
    def _v46_request(cls, value: object) -> V46OptimizationRequest | None:
        return (
            None
            if value is None
            else _exact(value, V46OptimizationRequest, field="v46_request")
        )

    @field_validator("proposal_id", mode="before")
    @classmethod
    def _proposal_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="proposal_id")

    @model_validator(mode="after")
    def _mode_binding(self):
        mode = self.mode
        if mode is OptimizationMode.CREATE_PROPOSAL and (
            (self.request is None) == (self.v46_request is None)
            or self.proposal_id is not None
        ):
            raise ValueError("optimization create input mismatch")
        if mode is OptimizationMode.READ_PROPOSAL and (
            self.request is not None
            or self.v46_request is not None
            or self.proposal_id is None
        ):
            raise ValueError("optimization read input mismatch")
        return self


class EDAAnalysisInput(FingerprintedContract):
    capability: Literal[CapabilityType.EDA_ANALYSIS] = CapabilityType.EDA_ANALYSIS
    request: EDAAnalysisRequest

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> EDAAnalysisRequest:
        return _exact(value, EDAAnalysisRequest, field="EDA analysis request")


class HardwareTestInput(FingerprintedContract):
    capability: Literal[CapabilityType.HARDWARE_TEST] = CapabilityType.HARDWARE_TEST
    request: HardwareObservationRequest

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> HardwareObservationRequest:
        return _exact(
            value, HardwareObservationRequest, field="hardware observation request"
        )


class HardwareHILTestMode(StrEnum):
    PROPOSE = "PROPOSE"
    READ_OBSERVATION = "READ_OBSERVATION"


class HardwareHILTestInput(FingerprintedContract):
    capability: Literal[CapabilityType.HARDWARE_HIL_TEST] = (
        CapabilityType.HARDWARE_HIL_TEST
    )
    mode: HardwareHILTestMode
    proposal_request: HardwareTestProposalRequest | None = None
    observation_id: str | None = None

    @field_validator("proposal_request", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> HardwareTestProposalRequest | None:
        return (
            None
            if value is None
            else _exact(
                value, HardwareTestProposalRequest, field="HIL proposal request"
            )
        )

    @field_validator("observation_id", mode="before")
    @classmethod
    def _observation_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="observation_id")

    @model_validator(mode="after")
    def _mode_binding(self):
        if (self.mode is HardwareHILTestMode.PROPOSE) != (
            self.proposal_request is not None
        ):
            raise ValueError("HIL proposal mode mismatch")
        if (self.mode is HardwareHILTestMode.READ_OBSERVATION) != (
            self.observation_id is not None
        ):
            raise ValueError("HIL observation mode mismatch")
        return self


CapabilityInput = Annotated[
    KnowledgeInput
    | VisionInput
    | PCBInput
    | FirmwareInput
    | DebugInput
    | RepairInput
    | OptimizationInput
    | EDAAnalysisInput
    | HardwareTestInput
    | HardwareHILTestInput,
    Field(discriminator="capability"),
]


class AgentCapabilityRequest(FingerprintedContract):
    application_request: ApplicationQuestionRequest
    payload: CapabilityInput

    @field_validator("application_request", mode="before")
    @classmethod
    def _application(cls, value: object) -> ApplicationQuestionRequest:
        return _exact(value, ApplicationQuestionRequest, field="application_request")

    @model_validator(mode="after")
    def _binding(self):
        app = self.application_request
        payload = self.payload
        project = app.project_id
        if isinstance(payload, VisionInput):
            if (
                payload.artifact.project_id != project
                or payload.artifact.captured_at > app.requested_at
            ):
                raise ValueError("vision request binding mismatch")
        elif isinstance(payload, PCBInput):
            if (
                payload.context.project_id != project
                or payload.analysis_request.requester != app.requester
                or payload.analysis_request.requested_at != app.requested_at
            ):
                raise ValueError("PCB request binding mismatch")
        elif isinstance(payload, FirmwareInput):
            if (
                payload.requirement.project_id != project
                or payload.requirement.description != app.question
            ):
                raise ValueError("firmware request binding mismatch")
            if (
                payload.context_snapshot is not None
                and payload.context_snapshot.task_description != app.question
            ):
                raise ValueError("firmware context task mismatch")
        elif isinstance(payload, DebugInput):
            if payload.snapshot.project_id != project:
                raise ValueError("debug request binding mismatch")
        elif isinstance(payload, RepairInput):
            if (
                payload.request.project_id != project
                or payload.request.requester != app.requester
                or payload.request.requested_at != app.requested_at
            ):
                raise ValueError("repair request binding mismatch")
        elif isinstance(payload, OptimizationInput):
            optimization_project = (
                None if payload.request is None else payload.request.project_id
            )
            if payload.v46_request is not None:
                optimization_project = payload.v46_request.project_id
                if payload.v46_request.requester != app.requester:
                    raise ValueError("optimization requester binding mismatch")
            if optimization_project is not None and optimization_project != project:
                raise ValueError("optimization request binding mismatch")
        elif isinstance(payload, EDAAnalysisInput) and (
            payload.request.project_id != project
            or payload.request.requester != app.requester
            or payload.request.requested_at != app.requested_at
        ):
            raise ValueError("EDA request binding mismatch")
        elif isinstance(payload, HardwareTestInput) and (
            payload.request.project_id != project
            or payload.request.requester != app.requester
            or payload.request.requested_at != app.requested_at
        ):
            raise ValueError("hardware request binding mismatch")
        elif (
            isinstance(payload, HardwareHILTestInput)
            and payload.proposal_request is not None
            and (
                payload.proposal_request.project_id != project
                or payload.proposal_request.created_by != app.requester
            )
        ):
            raise ValueError("HIL request binding mismatch")
        return self


class CapabilityRouteDecision(FingerprintedContract):
    request_id: str
    request_fingerprint: str
    candidates: tuple[CapabilityType, ...]
    selected_capability: CapabilityType | None
    matched_rule_ids: tuple[str, ...]
    reason_code: str

    @field_validator("request_id", "reason_code", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def _request_fp(cls, value: object) -> str:
        return fingerprint(value, field="request_fingerprint")

    @field_validator("candidates", mode="before")
    @classmethod
    def _candidates(cls, value: object) -> tuple[CapabilityType, ...]:
        items = tuple_only(value, field="candidates")
        if len(items) != len(set(items)):
            raise ValueError("route candidates must be unique")
        return items

    @field_validator("matched_rule_ids", mode="before")
    @classmethod
    def _rules(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="matched_rule_id")
            for item in tuple_only(value, field="matched_rule_ids")
        )
        if len(items) != len(set(items)):
            raise ValueError("matched rules must be unique")
        return items

    @model_validator(mode="after")
    def _state(self):
        if (self.selected_capability is None) != (not self.candidates):
            raise ValueError("route decision state mismatch")
        if (
            self.selected_capability is not None
            and self.selected_capability not in self.candidates
        ):
            raise ValueError("selected capability is not a candidate")
        return self


class CapabilityPolicyGrant(FingerprintedContract):
    project_id: str
    requester: str
    capabilities: tuple[CapabilityType, ...]

    @field_validator("project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[CapabilityType, ...]:
        items = tuple_only(value, field="capabilities")
        if not items or len(items) != len(set(items)):
            raise ValueError("policy capabilities are invalid")
        return tuple(sorted(items, key=lambda item: item.value))


class CapabilityPolicyDecision(FingerprintedContract):
    request_fingerprint: str
    route_fingerprint: str
    capability: CapabilityType | None
    allowed: bool
    grant_fingerprint: str | None
    reason_code: str

    @field_validator("request_fingerprint", "route_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("grant_fingerprint", mode="before")
    @classmethod
    def _grant_fp(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="grant_fingerprint")

    @field_validator("reason_code", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return identifier(value, field="reason_code")

    @model_validator(mode="after")
    def _state(self):
        if self.allowed and (self.capability is None or self.grant_fingerprint is None):
            raise ValueError("allowed policy decision is incomplete")
        if (
            not self.allowed
            and self.grant_fingerprint is not None
            and self.capability is None
        ):
            raise ValueError("denied policy decision is invalid")
        return self


class CapabilityBindingReference(FingerprintedContract):
    capability: CapabilityType
    binding_id: str
    binding_version: str
    async_mode: CapabilityAsyncMode

    @field_validator("binding_id", "binding_version", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)


class KnowledgeOutput(FingerprintedContract):
    capability: Literal[CapabilityType.KNOWLEDGE] = CapabilityType.KNOWLEDGE
    result: ApplicationRunResult

    @field_validator("result", mode="before")
    @classmethod
    def _result(cls, value: object) -> ApplicationRunResult:
        return _exact(value, ApplicationRunResult, field="knowledge_result")

    @model_validator(mode="after")
    def _state(self):
        if (
            self.result.status is not ApplicationRunStatus.COMPLETED
            or self.result.route is not ApplicationRoute.KNOWLEDGE
        ):
            raise ValueError("knowledge result is not completed")
        return self


class VisionOutput(FingerprintedContract):
    capability: Literal[CapabilityType.VISION] = CapabilityType.VISION
    observation: EngineeringObservation

    @field_validator("observation", mode="before")
    @classmethod
    def _observation(cls, value: object) -> EngineeringObservation:
        return _exact(value, EngineeringObservation, field="observation")


class PCBOutput(FingerprintedContract):
    capability: Literal[CapabilityType.PCB_ANALYSIS] = CapabilityType.PCB_ANALYSIS
    analysis: PCBAnalysis

    @field_validator("analysis", mode="before")
    @classmethod
    def _analysis(cls, value: object) -> PCBAnalysis:
        return _exact(value, PCBAnalysis, field="PCB analysis")


class FirmwareOutput(FingerprintedContract):
    capability: Literal[CapabilityType.FIRMWARE_ANALYSIS] = (
        CapabilityType.FIRMWARE_ANALYSIS
    )
    proposal: FirmwareProposal

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> FirmwareProposal:
        return _exact(value, FirmwareProposal, field="firmware proposal")


class DebugOutput(FingerprintedContract):
    capability: Literal[CapabilityType.DEBUG_ANALYSIS] = CapabilityType.DEBUG_ANALYSIS
    analysis: DebugAnalysisSnapshot

    @field_validator("analysis", mode="before")
    @classmethod
    def _analysis(cls, value: object) -> DebugAnalysisSnapshot:
        return _exact(value, DebugAnalysisSnapshot, field="debug analysis")


class RepairOutput(FingerprintedContract):
    capability: Literal[CapabilityType.REPAIR_ANALYSIS] = CapabilityType.REPAIR_ANALYSIS
    analysis: FailureAnalysis

    @field_validator("analysis", mode="before")
    @classmethod
    def _analysis(cls, value: object) -> FailureAnalysis:
        return _exact(value, FailureAnalysis, field="failure analysis")


class OptimizationOutput(FingerprintedContract):
    capability: Literal[CapabilityType.OPTIMIZATION_ANALYSIS] = (
        CapabilityType.OPTIMIZATION_ANALYSIS
    )
    mode: OptimizationMode = OptimizationMode.CREATE_PROPOSAL
    proposals: tuple[LegacyOptimizationProposal, ...] = ()
    proposal: V46OptimizationProposal | None = None

    @field_validator("proposals", mode="before")
    @classmethod
    def _proposals(cls, value: object) -> tuple[LegacyOptimizationProposal, ...]:
        items = tuple(
            _exact(item, LegacyOptimizationProposal, field="optimization proposal")
            for item in tuple_only(value, field="proposals")
        )
        ids = tuple(item.proposal_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("optimization proposal identities must be unique")
        return items

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> V46OptimizationProposal | None:
        return (
            None
            if value is None
            else _exact(
                value, V46OptimizationProposal, field="v4.6 optimization proposal"
            )
        )

    @model_validator(mode="after")
    def _mode_binding(self):
        if (self.proposal is None) != bool(self.proposals):
            raise ValueError("optimization output mismatch")
        return self


class EDAAnalysisOutput(FingerprintedContract):
    capability: Literal[CapabilityType.EDA_ANALYSIS] = CapabilityType.EDA_ANALYSIS
    result: EDAAnalysisResult

    @field_validator("result", mode="before")
    @classmethod
    def _result(cls, value: object) -> EDAAnalysisResult:
        return _exact(value, EDAAnalysisResult, field="EDA analysis result")


class HardwareTestOutput(FingerprintedContract):
    capability: Literal[CapabilityType.HARDWARE_TEST] = CapabilityType.HARDWARE_TEST
    observation: HardwareObservation

    @field_validator("observation", mode="before")
    @classmethod
    def _observation(cls, value: object) -> HardwareObservation:
        return _exact(value, HardwareObservation, field="hardware observation")


class HardwareHILTestOutput(FingerprintedContract):
    capability: Literal[CapabilityType.HARDWARE_HIL_TEST] = (
        CapabilityType.HARDWARE_HIL_TEST
    )
    mode: HardwareHILTestMode
    proposal: HardwareTestProposal | None = None
    observation: HILHardwareObservation | None = None

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> HardwareTestProposal | None:
        return (
            None
            if value is None
            else _exact(value, HardwareTestProposal, field="HIL proposal")
        )

    @field_validator("observation", mode="before")
    @classmethod
    def _observation(cls, value: object) -> HILHardwareObservation | None:
        return (
            None
            if value is None
            else _exact(value, HILHardwareObservation, field="HIL observation")
        )

    @model_validator(mode="after")
    def _mode_binding(self):
        if (self.mode is HardwareHILTestMode.PROPOSE) != (self.proposal is not None):
            raise ValueError("HIL proposal output mismatch")
        if (self.mode is HardwareHILTestMode.READ_OBSERVATION) != (
            self.observation is not None
        ):
            raise ValueError("HIL observation output mismatch")
        return self


CapabilityOutput = Annotated[
    KnowledgeOutput
    | VisionOutput
    | PCBOutput
    | FirmwareOutput
    | DebugOutput
    | RepairOutput
    | OptimizationOutput
    | EDAAnalysisOutput
    | HardwareTestOutput
    | HardwareHILTestOutput,
    Field(discriminator="capability"),
]

_OUTPUT_ADAPTER = TypeAdapter(CapabilityOutput)


def validate_capability_output(value: object) -> CapabilityOutput:
    if not isinstance(
        value,
        (
            KnowledgeOutput,
            VisionOutput,
            PCBOutput,
            FirmwareOutput,
            DebugOutput,
            RepairOutput,
            OptimizationOutput,
            EDAAnalysisOutput,
            HardwareTestOutput,
            HardwareHILTestOutput,
        ),
    ):
        raise TypeError("capability output is invalid")
    return _OUTPUT_ADAPTER.validate_python(
        copy.deepcopy(value.model_dump(mode="python"))
    )


class CapabilityAuditEvent(FingerprintedContract):
    event_id: str
    event_type: CapabilityAuditEventType
    request_id: str
    trace_id: str
    project_id: str
    requester: str
    request_fingerprint: str
    payload_fingerprint: str
    route_fingerprint: str
    policy_decision_fingerprint: str | None
    binding_reference: CapabilityBindingReference | None
    selected_capability: CapabilityType | None
    output_fingerprint: str | None
    error_code: CapabilityErrorCode | None
    timestamp: datetime

    @field_validator(
        "event_id", "request_id", "trace_id", "project_id", "requester", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "request_fingerprint", "payload_fingerprint", "route_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("policy_decision_fingerprint", "output_fingerprint", mode="before")
    @classmethod
    def _optional_fps(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _state(self):
        if self.event_id != self.event_id_for(
            self.request_fingerprint, self.route_fingerprint, self.event_type
        ):
            raise ValueError("capability audit event id mismatch")
        if self.event_type is CapabilityAuditEventType.CAPABILITY_COMPLETED:
            if (
                self.output_fingerprint is None
                or self.error_code is not None
                or self.binding_reference is None
                or self.selected_capability is None
            ):
                raise ValueError("completed audit event is invalid")
        elif (
            self.event_type is not CapabilityAuditEventType.CAPABILITY_CANCELLED
            and self.output_fingerprint is not None
        ):
            raise ValueError("non-completed audit output is invalid")
        if (
            self.event_type is not CapabilityAuditEventType.CAPABILITY_COMPLETED
            and self.error_code is None
        ):
            raise ValueError("terminal audit error code is required")
        if (
            self.binding_reference is not None
            and self.binding_reference.capability is not self.selected_capability
        ):
            raise ValueError("capability audit binding mismatch")
        if (
            self.event_type is CapabilityAuditEventType.CAPABILITY_CANCELLED
            and self.binding_reference is None
        ):
            raise ValueError("cancelled audit binding is required")
        return self

    @staticmethod
    def event_id_for(
        request_fingerprint: str,
        route_fingerprint: str,
        event_type: CapabilityAuditEventType,
    ) -> str:
        return deterministic_id(
            "capability-event",
            fingerprint(request_fingerprint),
            fingerprint(route_fingerprint),
            event_type.value,
        )


class CapabilityAuditReference(AgentRuntimeContract):
    event_id: str
    event_type: CapabilityAuditEventType
    fingerprint: str

    @field_validator("event_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="event_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def from_event(cls, event: CapabilityAuditEvent) -> CapabilityAuditReference:
        checked = CapabilityAuditEvent.model_validate(
            copy.deepcopy(event.model_dump(mode="python"))
        )
        return cls(
            event_id=checked.event_id,
            event_type=checked.event_type,
            fingerprint=checked.fingerprint,
        )


class AgentCapabilityResult(FingerprintedContract):
    request_id: str
    request_fingerprint: str
    route: CapabilityRouteDecision
    policy: CapabilityPolicyDecision | None
    binding_reference: CapabilityBindingReference | None
    status: CapabilityResultStatus
    output: CapabilityOutput | None
    error_code: CapabilityErrorCode | None
    audit: CapabilityAuditReference
    completed_at: datetime

    @field_validator("request_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="request_id")

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="request_fingerprint")

    @field_validator("completed_at", mode="before")
    @classmethod
    def _completed(cls, value: object) -> datetime:
        return utc_datetime(value, field="completed_at")

    @model_validator(mode="after")
    def _state(self):
        if (
            self.route.request_id != self.request_id
            or self.route.request_fingerprint != self.request_fingerprint
        ):
            raise ValueError("capability result request binding mismatch")
        if self.status is CapabilityResultStatus.COMPLETED:
            if (
                self.output is None
                or self.error_code is not None
                or self.binding_reference is None
            ):
                raise ValueError("completed capability result is invalid")
            if self.output.capability is not self.route.selected_capability:
                raise ValueError("capability output route mismatch")
        elif self.output is not None or self.error_code is None:
            raise ValueError("non-completed capability result is invalid")
        if self.policy is not None and (
            self.policy.request_fingerprint != self.request_fingerprint
            or self.policy.route_fingerprint != self.route.fingerprint
            or self.policy.capability is not self.route.selected_capability
        ):
            raise ValueError("capability result policy binding mismatch")
        if self.status is CapabilityResultStatus.COMPLETED and (
            self.policy is None or not self.policy.allowed
        ):
            raise ValueError("completed capability result requires authorization")
        if self.binding_reference is not None and (
            self.binding_reference.capability is not self.route.selected_capability
        ):
            raise ValueError("capability result binding mismatch")
        expected_event = {
            CapabilityResultStatus.COMPLETED: CapabilityAuditEventType.CAPABILITY_COMPLETED,
            CapabilityResultStatus.REJECTED: CapabilityAuditEventType.CAPABILITY_REJECTED,
            CapabilityResultStatus.FAILED: CapabilityAuditEventType.CAPABILITY_FAILED,
        }[self.status]
        if self.audit.event_type is not expected_event:
            raise ValueError("capability result audit mismatch")
        if self.audit.event_id != CapabilityAuditEvent.event_id_for(
            self.request_fingerprint, self.route.fingerprint, expected_event
        ):
            raise ValueError("capability result audit identity mismatch")
        return self


__all__ = tuple(
    name
    for name in globals()
    if name.startswith(("Agent", "Capability"))
    or name.endswith(("Input", "Output"))
    or name == "validate_capability_output"
)
