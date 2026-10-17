from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.engineering_research import EngineeringResearchSnapshot
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureReviewDecision,
    DesignTruthOrigin,
    DesignSource,
    DesignTruth,
    EngineeringSessionMode,
    EngineeringPlan,
    PlanReviewDecision,
    PlanStatus,
)
from embedded_copilot.engineering_qa import EngineeringAnswer, EngineeringQuestion
from embedded_copilot.model_gateway.contracts import (
    ModelGatewayContract,
    canonical_fingerprint,
    fingerprint,
    identifier,
    safe_text,
    tuple_only,
)

_AUTO = "sha256:" + "0" * 64


class ApplicationRoute(StrEnum):
    GREENFIELD_DISCOVERY = "GREENFIELD_DISCOVERY"
    GREENFIELD_DESIGN = "GREENFIELD_DESIGN"
    BROWNFIELD_ANALYSIS = "BROWNFIELD_ANALYSIS"
    DEBUG_ANALYSIS = "DEBUG_ANALYSIS"
    # Historical values remain readable for persisted v7.0/v7.1 records.
    GREENFIELD = "GREENFIELD"
    BROWNFIELD = "BROWNFIELD"
    KNOWLEDGE = "KNOWLEDGE"
    FIRMWARE = "FIRMWARE"
    DEBUG = "DEBUG"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ApplicationRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ApplicationAuditEventType(StrEnum):
    QUESTION_ANSWERED = "QUESTION_ANSWERED"
    QUESTION_REJECTED = "QUESTION_REJECTED"
    QUESTION_FAILED = "QUESTION_FAILED"


class ApplicationErrorCode(StrEnum):
    UNSUPPORTED_ROUTE = "UNSUPPORTED_ROUTE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    EXTERNAL_EVIDENCE_UNAVAILABLE = "EXTERNAL_EVIDENCE_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EMPTY_PROJECT = "EMPTY_PROJECT"
    NO_ANALYZABLE_SOURCE = "NO_ANALYZABLE_SOURCE"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_NETWORK_UNAVAILABLE = "PROVIDER_NETWORK_UNAVAILABLE"
    PROVIDER_MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
    PROVIDER_CONFIG_INVALID = "PROVIDER_CONFIG_INVALID"
    PROVIDER_CONNECTION_UNVERIFIED = "PROVIDER_CONNECTION_UNVERIFIED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED = "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED"
    EXTERNAL_TRANSMISSION_DENIED = "EXTERNAL_TRANSMISSION_DENIED"
    EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED = "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED"
    EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH = "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH"
    COMPLETED = "COMPLETED"
    ANALYSIS_RUNTIME_FAILED = "ANALYSIS_RUNTIME_FAILED"
    ANSWER_REJECTED = "ANSWER_REJECTED"
    PROVIDER_TRANSPORT_FAILED = "PROVIDER_TRANSPORT_FAILED"
    PROVIDER_PARSE_FAILED = "PROVIDER_PARSE_FAILED"
    ANSWER_SCHEMA_INVALID = "ANSWER_SCHEMA_INVALID"
    ANSWER_CONTRACT_MISMATCH = "ANSWER_CONTRACT_MISMATCH"
    ANSWER_EMPTY = "ANSWER_EMPTY"
    ANSWER_REJECTED_UNSAFE = "ANSWER_REJECTED_UNSAFE"
    ANSWER_REJECTED_EVIDENCE_MISMATCH = "ANSWER_REJECTED_EVIDENCE_MISMATCH"
    APPLICATION_UNAVAILABLE = "APPLICATION_UNAVAILABLE"


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


def _fp(value: object, *, field: str) -> str:
    return fingerprint(value, field=field)


def _optional_fp(value: object, *, field: str) -> str | None:
    return None if value is None else _fp(value, field=field)


def _validate_architecture_binding(
    design: ArchitectureDesign | None,
    review: ArchitectureReviewDecision | None,
    *,
    project_id: str,
    requirement_context: RequirementContext | None,
    engineering_plan: EngineeringPlan | None,
    prefix: str = "application",
) -> None:
    if design is not None:
        if design.project_id != project_id:
            raise ValueError(f"{prefix} architecture project mismatch")
        if engineering_plan is None or (
            design.plan_id != engineering_plan.plan_id
            and design.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }
        ):
            raise ValueError(f"{prefix} architecture plan binding mismatch")
        if requirement_context is not None and design.context_fingerprint != requirement_context.fingerprint:
            if design.status not in {ArchitectureDesignStatus.SUPERSEDED, ArchitectureDesignStatus.INVALIDATED}:
                raise ValueError(f"{prefix} architecture context mismatch")
        if design.plan_fingerprint != engineering_plan.fingerprint and design.status not in {
            ArchitectureDesignStatus.SUPERSEDED,
            ArchitectureDesignStatus.INVALIDATED,
        }:
            raise ValueError(f"{prefix} architecture plan fingerprint mismatch")
        if design.status is ArchitectureDesignStatus.APPROVED and review is None:
            raise ValueError(f"{prefix} approved architecture requires a review decision")
    if review is not None:
        if design is None or engineering_plan is None:
            raise ValueError(f"{prefix} architecture review binding mismatch")
        if review.architecture_id != design.architecture_id or review.plan_id != engineering_plan.plan_id:
            raise ValueError(f"{prefix} architecture review binding mismatch")
        if review.project_id != project_id:
            raise ValueError(f"{prefix} architecture review project mismatch")
        if design.status is not ArchitectureDesignStatus.APPROVED:
            raise ValueError(f"{prefix} architecture review status mismatch")
        if review.status.value != "APPROVED" or review.proposal_fingerprint != design.content_fingerprint:
            raise ValueError(f"{prefix} architecture review fingerprint mismatch")


def _validate_design_truth_binding(
    truth: DesignTruth | None,
    design: ArchitectureDesign | None,
    *,
    project_id: str,
    conversation_id: str | None = None,
    prefix: str,
) -> None:
    if truth is None:
        return
    if truth.project_id != project_id:
        raise ValueError(f"{prefix} design truth project mismatch")
    if (
        conversation_id is not None
        and truth.conversation_id != "legacy-conversation"
        and truth.conversation_id != conversation_id
    ):
        raise ValueError(f"{prefix} design truth conversation mismatch")
    if truth.origin is DesignTruthOrigin.REVERSE:
        if truth.architecture_id is not None:
            raise ValueError(f"{prefix} reverse design truth architecture mismatch")
        return
    if design is None or design.status is not ArchitectureDesignStatus.APPROVED:
        raise ValueError(f"{prefix} design truth requires approved architecture")
    if truth.architecture_id != design.architecture_id:
        raise ValueError(f"{prefix} design truth architecture mismatch")
    architecture_sources = tuple(
        binding
        for binding in truth.evidence_bindings
        if binding.entity_id == truth.truth_id
        and binding.source is DesignSource.ARCHITECTURE
        and binding.source_id == design.architecture_id
    )
    if not architecture_sources or architecture_sources[0].source_fingerprint != design.fingerprint:
        raise ValueError(f"{prefix} design truth source fingerprint mismatch")


class ApplicationQuestionRequest(ModelGatewayContract):
    request_id: str
    trace_id: str
    project_id: str
    tenant_id: str = "default-tenant"
    requester: str
    question: str
    constraints: tuple[str, ...] = ()
    external_search: bool = False
    requested_at: datetime
    fingerprint: str = _AUTO

    @field_validator("request_id", "trace_id", "project_id", "tenant_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> str:
        return safe_text(value, field="question", maximum=4096, allow_newlines=True)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise ValueError("constraints must be a tuple")
        return tuple(
            safe_text(item, field="constraint", maximum=256)
            for item in tuple_only(value, field="constraints")
        )

    @field_validator("external_search", mode="before")
    @classmethod
    def _external_search(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("external_search is invalid")
        return value

    @field_validator("requested_at", mode="before")
    @classmethod
    def _requested_at(cls, value: object) -> datetime:
        return _utc(value, field="requested_at")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, field="fingerprint")

    @model_validator(mode="after")
    def _verify(self) -> ApplicationQuestionRequest:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("application question fingerprint mismatch")
        return self


class ApplicationRoutingContext(ModelGatewayContract):
    engineering_session_mode: EngineeringSessionMode = EngineeringSessionMode.UNDETERMINED
    project_creation_mode: str | None = None
    project_type: str | None = None
    project_status: str | None = None
    conversation_id: str | None = None
    previous_route: str | None = None
    previous_analysis_state: str | None = None
    previous_intent: str | None = None
    available_artifact_count: int = 0
    requirement_context: RequirementContext | None = None
    research_snapshot: EngineeringResearchSnapshot | None = None
    engineering_plan: EngineeringPlan | None = None
    engineering_plan_review: PlanReviewDecision | None = None
    architecture_design: ArchitectureDesign | None = None
    architecture_review: ArchitectureReviewDecision | None = None
    design_truth: DesignTruth | None = None

    @field_validator(
        "project_creation_mode",
        "project_type",
        "project_status",
        "conversation_id",
        "previous_route",
        "previous_analysis_state",
        "previous_intent",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="routing context")

    @field_validator("available_artifact_count", mode="before")
    @classmethod
    def _artifact_count(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("available_artifact_count is invalid")
        return value

    @field_validator("engineering_session_mode", mode="before")
    @classmethod
    def _session_mode(cls, value: object) -> EngineeringSessionMode:
        return value if isinstance(value, EngineeringSessionMode) else EngineeringSessionMode(value)

    @field_validator("engineering_plan", mode="before")
    @classmethod
    def _engineering_plan(cls, value: object) -> EngineeringPlan | None:
        return None if value is None else EngineeringPlan.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("research_snapshot", mode="before")
    @classmethod
    def _research_snapshot(cls, value: object) -> EngineeringResearchSnapshot | None:
        return None if value is None else EngineeringResearchSnapshot.model_validate(
            copy.deepcopy(value), strict=False
        )

    @field_validator("engineering_plan_review", mode="before")
    @classmethod
    def _engineering_plan_review(cls, value: object) -> PlanReviewDecision | None:
        return None if value is None else PlanReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_design", mode="before")
    @classmethod
    def _architecture_design(cls, value: object) -> ArchitectureDesign | None:
        return None if value is None else ArchitectureDesign.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_review", mode="before")
    @classmethod
    def _architecture_review(cls, value: object) -> ArchitectureReviewDecision | None:
        return None if value is None else ArchitectureReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("design_truth", mode="before")
    @classmethod
    def _design_truth(cls, value: object) -> DesignTruth | None:
        return None if value is None else DesignTruth.model_validate(copy.deepcopy(value), strict=False)

    @model_validator(mode="after")
    def _verify_bindings(self) -> "ApplicationRoutingContext":
        if self.research_snapshot is not None:
            if self.research_snapshot.project_id != (
                self.requirement_context.project_id
                if self.requirement_context is not None
                else self.research_snapshot.project_id
            ):
                raise ValueError("routing research project mismatch")
            if self.conversation_id is not None and self.research_snapshot.conversation_id != self.conversation_id:
                raise ValueError("routing research conversation mismatch")
        if self.requirement_context is not None and self.engineering_plan is not None:
            if self.requirement_context.project_id != self.engineering_plan.project_id:
                raise ValueError("routing requirement and plan project mismatch")
            if self.engineering_plan.status is not PlanStatus.SUPERSEDED and self.engineering_plan.context_fingerprint != self.requirement_context.fingerprint:
                raise ValueError("routing engineering plan context mismatch")
        project_id = (
            self.requirement_context.project_id
            if self.requirement_context is not None
            else self.engineering_plan.project_id
            if self.engineering_plan is not None
            else None
        )
        if self.architecture_design is not None and project_id is None:
            raise ValueError("routing architecture requires requirement or plan context")
        if project_id is not None:
            _validate_architecture_binding(
                self.architecture_design,
                self.architecture_review,
                project_id=project_id,
                requirement_context=self.requirement_context,
                engineering_plan=self.engineering_plan,
                prefix="routing",
            )
            _validate_design_truth_binding(
                self.design_truth,
                self.architecture_design,
                project_id=project_id,
                conversation_id=self.conversation_id,
                prefix="routing",
            )
        elif self.architecture_review is not None:
            raise ValueError("routing architecture review requires context")
        elif self.design_truth is not None:
            raise ValueError("routing design truth requires context")
        return self


class ApplicationRouteDecision(ModelGatewayContract):
    request_id: str
    request_fingerprint: str
    route: ApplicationRoute
    reason_code: str
    fingerprint: str = _AUTO

    @field_validator("request_id", "reason_code", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value: object, info) -> str:
        return _fp(value, field=info.field_name)

    @model_validator(mode="after")
    def _verify(self) -> ApplicationRouteDecision:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("application route fingerprint mismatch")
        return self

    @classmethod
    def for_request(
        cls, request: ApplicationQuestionRequest, *, route: ApplicationRoute
    ) -> ApplicationRouteDecision:
        checked = ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        return cls(
            request_id=checked.request_id,
            request_fingerprint=checked.fingerprint,
            route=route,
            reason_code={
                ApplicationRoute.GREENFIELD_DISCOVERY: "greenfield_discovery",
                ApplicationRoute.GREENFIELD_DESIGN: "greenfield_design",
                ApplicationRoute.BROWNFIELD_ANALYSIS: "brownfield_analysis",
                ApplicationRoute.DEBUG_ANALYSIS: "debug_analysis",
                ApplicationRoute.GREENFIELD: "greenfield_request",
                ApplicationRoute.BROWNFIELD: "brownfield_request",
                ApplicationRoute.KNOWLEDGE: "knowledge_request",
                ApplicationRoute.FIRMWARE: "firmware_request",
                ApplicationRoute.DEBUG: "debug_request",
                ApplicationRoute.UNKNOWN: "clarification_required",
                ApplicationRoute.CLARIFICATION_REQUIRED: "clarification_required",
            }[route],
        )


class ApplicationAnswerEvidence(ModelGatewayContract):
    question: EngineeringQuestion
    context: EngineeringContextSnapshot
    answer: EngineeringAnswer
    requirement_context: RequirementContext | None = None
    engineering_plan: EngineeringPlan | None = None
    engineering_plan_review: PlanReviewDecision | None = None
    architecture_design: ArchitectureDesign | None = None
    architecture_review: ArchitectureReviewDecision | None = None
    design_truth: DesignTruth | None = None
    fingerprint: str = _AUTO

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> EngineeringQuestion:
        return EngineeringQuestion.model_validate(copy.deepcopy(value))

    @field_validator("context", mode="before")
    @classmethod
    def _context(cls, value: object) -> EngineeringContextSnapshot:
        return EngineeringContextSnapshot.model_validate(copy.deepcopy(value))

    @field_validator("answer", mode="before")
    @classmethod
    def _answer(cls, value: object) -> EngineeringAnswer:
        return EngineeringAnswer.model_validate(copy.deepcopy(value))

    @field_validator("requirement_context", mode="before")
    @classmethod
    def _requirements(cls, value: object) -> RequirementContext | None:
        return None if value is None else RequirementContext.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_plan", mode="before")
    @classmethod
    def _engineering_plan(cls, value: object) -> EngineeringPlan | None:
        return None if value is None else EngineeringPlan.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_plan_review", mode="before")
    @classmethod
    def _engineering_plan_review(cls, value: object) -> PlanReviewDecision | None:
        return None if value is None else PlanReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_design", mode="before")
    @classmethod
    def _architecture_design(cls, value: object) -> ArchitectureDesign | None:
        return None if value is None else ArchitectureDesign.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_review", mode="before")
    @classmethod
    def _architecture_review(cls, value: object) -> ArchitectureReviewDecision | None:
        return None if value is None else ArchitectureReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("design_truth", mode="before")
    @classmethod
    def _design_truth(cls, value: object) -> DesignTruth | None:
        return None if value is None else DesignTruth.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, field="fingerprint")

    @model_validator(mode="after")
    def _verify(self) -> ApplicationAnswerEvidence:
        if self.requirement_context is not None and self.requirement_context.project_id != self.question.project_id:
            raise ValueError("application requirement context project mismatch")
        if self.engineering_plan is not None:
            if self.engineering_plan.project_id != self.question.project_id:
                raise ValueError("application engineering plan project mismatch")
            if (
                self.requirement_context is not None
                and self.engineering_plan.status is not PlanStatus.SUPERSEDED
                and self.engineering_plan.context_fingerprint != self.requirement_context.fingerprint
            ):
                raise ValueError("application engineering plan context mismatch")
            if self.engineering_plan.status is PlanStatus.APPROVED and self.engineering_plan_review is None:
                raise ValueError("approved engineering plan requires a review decision")
        if self.engineering_plan_review is not None:
            if self.engineering_plan is None or self.engineering_plan_review.plan_id != self.engineering_plan.plan_id:
                raise ValueError("application engineering plan review binding mismatch")
            if self.engineering_plan_review.project_id != self.question.project_id:
                raise ValueError("application engineering plan review project mismatch")
            if self.engineering_plan.status is not PlanStatus.APPROVED or self.engineering_plan_review.status.value != "APPROVED":
                raise ValueError("application engineering plan review status mismatch")
            if self.engineering_plan_review.proposal_fingerprint != self.engineering_plan.content_fingerprint:
                raise ValueError("application engineering plan review fingerprint mismatch")
        _validate_architecture_binding(
            self.architecture_design,
            self.architecture_review,
            project_id=self.question.project_id,
            requirement_context=self.requirement_context,
            engineering_plan=self.engineering_plan,
        )
        _validate_design_truth_binding(
            self.design_truth,
            self.architecture_design,
            project_id=self.question.project_id,
            prefix="application",
        )
        evidence = (
            *self.context.memory_evidence,
            *self.context.graph_evidence,
            *self.context.knowledge_evidence,
            *self.context.multimodal_evidence,
        )
        expected_refs = tuple(sorted({item.evidence_id for item in evidence}))
        if (
            self.question.project_id != self.context.project_id
            or self.question.question != self.context.task_description
            or self.answer.question_id != self.question.question_id
            or self.answer.evidence_refs != expected_refs
        ):
            raise ValueError("application answer evidence binding mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("application answer evidence fingerprint mismatch")
        return self


class ApplicationAuditEvent(ModelGatewayContract):
    event_id: str
    event_type: ApplicationAuditEventType
    request_id: str
    trace_id: str
    project_id: str
    requester: str
    route: ApplicationRoute
    status: ApplicationRunStatus
    question_fingerprint: str
    context_fingerprint: str | None
    answer_fingerprint: str | None
    model_info_fingerprint: str | None
    evidence_refs: tuple[str, ...]
    error_code: ApplicationErrorCode | None
    timestamp: datetime
    fingerprint: str = _AUTO

    @field_validator("event_id", "request_id", "trace_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _required_fps(cls, value: object, info) -> str:
        return _fp(value, field=info.field_name)

    @field_validator(
        "context_fingerprint",
        "answer_fingerprint",
        "model_info_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_fps(cls, value: object, info) -> str | None:
        return _optional_fp(value, field=info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise ValueError("evidence_refs must be a tuple")
        refs = tuple(
            identifier(item, field="evidence_ref")
            for item in tuple_only(value, field="evidence_refs")
        )
        if len(refs) != len(set(refs)) or refs != tuple(sorted(refs)):
            raise ValueError("audit evidence references are invalid")
        return refs

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return _utc(value, field="timestamp")

    @model_validator(mode="after")
    def _verify(self) -> ApplicationAuditEvent:
        expected_type = {
            ApplicationRunStatus.COMPLETED: ApplicationAuditEventType.QUESTION_ANSWERED,
            ApplicationRunStatus.REJECTED: ApplicationAuditEventType.QUESTION_REJECTED,
            ApplicationRunStatus.FAILED: ApplicationAuditEventType.QUESTION_FAILED,
        }[self.status]
        if self.event_type is not expected_type:
            raise ValueError("application audit event status mismatch")
        if self.event_id != self.event_id_for(self.question_fingerprint, self.event_type):
            raise ValueError("application audit event id mismatch")
        if self.status is ApplicationRunStatus.COMPLETED:
            if (
                self.route not in {
                ApplicationRoute.GREENFIELD,
                    ApplicationRoute.GREENFIELD_DISCOVERY,
                    ApplicationRoute.GREENFIELD_DESIGN,
                    ApplicationRoute.BROWNFIELD,
                    ApplicationRoute.BROWNFIELD_ANALYSIS,
                    ApplicationRoute.KNOWLEDGE,
                    ApplicationRoute.FIRMWARE,
                    ApplicationRoute.DEBUG,
                    ApplicationRoute.DEBUG_ANALYSIS,
                }
                or self.context_fingerprint is None
                or self.answer_fingerprint is None
                or self.model_info_fingerprint is None
                or (
                    not self.evidence_refs
                    and self.route
                    not in {
                        ApplicationRoute.GREENFIELD,
                        ApplicationRoute.GREENFIELD_DISCOVERY,
                        ApplicationRoute.GREENFIELD_DESIGN,
                    }
                )
                or self.error_code is not None
            ):
                raise ValueError("completed audit event is incomplete")
        elif (
            self.answer_fingerprint is not None
            or self.model_info_fingerprint is not None
            or self.evidence_refs
            or self.error_code is None
        ):
            raise ValueError("non-completed audit event binding mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("application audit event fingerprint mismatch")
        return self

    @staticmethod
    def event_id_for(
        question_fingerprint: str, event_type: ApplicationAuditEventType
    ) -> str:
        checked = _fp(question_fingerprint, field="question_fingerprint")
        digest = hashlib.sha256(f"{checked}|{event_type.value}".encode()).hexdigest()
        return "event-" + digest[:48]

    @classmethod
    def create(
        cls,
        *,
        event_type: ApplicationAuditEventType,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        status: ApplicationRunStatus,
        context_fingerprint: str | None,
        answer_fingerprint: str | None,
        model_info_fingerprint: str | None,
        evidence_refs: tuple[str, ...],
        error_code: ApplicationErrorCode | None,
        timestamp: datetime,
    ) -> ApplicationAuditEvent:
        checked = ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        return cls(
            event_id=cls.event_id_for(checked.fingerprint, event_type),
            event_type=event_type,
            request_id=checked.request_id,
            trace_id=checked.trace_id,
            project_id=checked.project_id,
            requester=checked.requester,
            route=route,
            status=status,
            question_fingerprint=checked.fingerprint,
            context_fingerprint=context_fingerprint,
            answer_fingerprint=answer_fingerprint,
            model_info_fingerprint=model_info_fingerprint,
            evidence_refs=tuple(sorted(evidence_refs)),
            error_code=error_code,
            timestamp=timestamp,
        )


class ApplicationAuditReference(ModelGatewayContract):
    event_id: str
    event_type: ApplicationAuditEventType
    fingerprint: str

    @field_validator("event_id", mode="before")
    @classmethod
    def _event_id(cls, value: object) -> str:
        return identifier(value, field="event_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, field="fingerprint")

    @classmethod
    def from_event(cls, event: ApplicationAuditEvent) -> ApplicationAuditReference:
        checked = ApplicationAuditEvent.model_validate(
            copy.deepcopy(event.model_dump(mode="python"))
        )
        return cls(
            event_id=checked.event_id,
            event_type=checked.event_type,
            fingerprint=checked.fingerprint,
        )


class ApplicationRunResult(ModelGatewayContract):
    request_id: str
    trace_id: str
    project_id: str
    route: ApplicationRoute
    status: ApplicationRunStatus
    question_fingerprint: str
    context_fingerprint: str | None
    context: EngineeringContextSnapshot | None = None
    answer: EngineeringAnswer | None
    requirement_context: RequirementContext | None = None
    research_snapshot: EngineeringResearchSnapshot | None = None
    engineering_plan: EngineeringPlan | None = None
    engineering_plan_review: PlanReviewDecision | None = None
    architecture_design: ArchitectureDesign | None = None
    architecture_review: ArchitectureReviewDecision | None = None
    design_truth: DesignTruth | None = None
    error_code: ApplicationErrorCode | None
    gateway_invocation_count: int = 0
    provider_response_present: bool = False
    answer_validation_stage: str | None = None
    answer_rejection_reason: ApplicationErrorCode | None = None
    proposed_requirements_count: int = 0
    open_questions_count: int = 0
    answer_text_present: bool = False
    original_exception_type: str | None = None
    original_exception_hash: str | None = None
    exception_layer: str | None = None
    exception_stage: str | None = None
    traceback_fingerprint: str | None = None
    audit: ApplicationAuditReference
    completed_at: datetime
    fingerprint: str = _AUTO

    @field_validator("request_id", "trace_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _required_fps(cls, value: object, info) -> str:
        return _fp(value, field=info.field_name)

    @field_validator("context_fingerprint", mode="before")
    @classmethod
    def _context_fp(cls, value: object) -> str | None:
        return _optional_fp(value, field="context_fingerprint")

    @field_validator("context", mode="before")
    @classmethod
    def _context(cls, value: object) -> EngineeringContextSnapshot | None:
        return None if value is None else EngineeringContextSnapshot.model_validate(
            copy.deepcopy(value)
        )

    @field_validator("answer", mode="before")
    @classmethod
    def _answer(cls, value: object) -> EngineeringAnswer | None:
        return None if value is None else EngineeringAnswer.model_validate(copy.deepcopy(value))

    @field_validator("requirement_context", mode="before")
    @classmethod
    def _requirements(cls, value: object) -> RequirementContext | None:
        return None if value is None else RequirementContext.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("research_snapshot", mode="before")
    @classmethod
    def _research_snapshot(cls, value: object) -> EngineeringResearchSnapshot | None:
        return None if value is None else EngineeringResearchSnapshot.model_validate(
            copy.deepcopy(value), strict=False
        )

    @field_validator("engineering_plan", mode="before")
    @classmethod
    def _engineering_plan(cls, value: object) -> EngineeringPlan | None:
        return None if value is None else EngineeringPlan.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("engineering_plan_review", mode="before")
    @classmethod
    def _engineering_plan_review(cls, value: object) -> PlanReviewDecision | None:
        return None if value is None else PlanReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_design", mode="before")
    @classmethod
    def _architecture_design(cls, value: object) -> ArchitectureDesign | None:
        return None if value is None else ArchitectureDesign.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("architecture_review", mode="before")
    @classmethod
    def _architecture_review(cls, value: object) -> ArchitectureReviewDecision | None:
        return None if value is None else ArchitectureReviewDecision.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("design_truth", mode="before")
    @classmethod
    def _design_truth(cls, value: object) -> DesignTruth | None:
        return None if value is None else DesignTruth.model_validate(copy.deepcopy(value), strict=False)

    @field_validator(
        "gateway_invocation_count",
        "proposed_requirements_count",
        "open_questions_count",
        mode="before",
    )
    @classmethod
    def _diagnostic_count(cls, value: object, info) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{info.field_name} is invalid")
        return value

    @field_validator("provider_response_present", "answer_text_present", mode="before")
    @classmethod
    def _diagnostic_bool(cls, value: object, info) -> bool:
        if type(value) is not bool:
            raise TypeError(f"{info.field_name} is invalid")
        return value

    @field_validator(
        "original_exception_type",
        "original_exception_hash",
        "exception_layer",
        "exception_stage",
        "traceback_fingerprint",
        mode="before",
    )
    @classmethod
    def _exception_diagnostic_text(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if value == "-":
            return value
        return safe_text(value, field=info.field_name, maximum=256)

    @field_validator("answer_validation_stage", mode="before")
    @classmethod
    def _diagnostic_stage(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="answer_validation_stage")

    @field_validator("audit", mode="before")
    @classmethod
    def _audit(cls, value: object) -> ApplicationAuditReference:
        return ApplicationAuditReference.model_validate(copy.deepcopy(value))

    @field_validator("completed_at", mode="before")
    @classmethod
    def _completed_at(cls, value: object) -> datetime:
        return _utc(value, field="completed_at")

    @model_validator(mode="after")
    def _verify(self) -> ApplicationRunResult:
        if self.requirement_context is not None and self.requirement_context.project_id != self.project_id:
            raise ValueError("application requirement context project mismatch")
        if self.research_snapshot is not None and self.research_snapshot.project_id != self.project_id:
            raise ValueError("application research project mismatch")
        if self.engineering_plan is not None:
            if self.engineering_plan.project_id != self.project_id:
                raise ValueError("application engineering plan project mismatch")
            if (
                self.requirement_context is not None
                and self.engineering_plan.status is not PlanStatus.SUPERSEDED
                and self.engineering_plan.context_fingerprint != self.requirement_context.fingerprint
            ):
                raise ValueError("application engineering plan context mismatch")
        if self.engineering_plan_review is not None:
            if self.engineering_plan is None or self.engineering_plan_review.plan_id != self.engineering_plan.plan_id:
                raise ValueError("application engineering plan review binding mismatch")
            if self.engineering_plan_review.project_id != self.project_id:
                raise ValueError("application engineering plan review project mismatch")
            if self.engineering_plan.status is not PlanStatus.APPROVED or self.engineering_plan_review.status.value != "APPROVED":
                raise ValueError("application engineering plan review status mismatch")
            if self.engineering_plan_review.proposal_fingerprint != self.engineering_plan.content_fingerprint:
                raise ValueError("application engineering plan review fingerprint mismatch")
        _validate_architecture_binding(
            self.architecture_design,
            self.architecture_review,
            project_id=self.project_id,
            requirement_context=self.requirement_context,
            engineering_plan=self.engineering_plan,
        )
        _validate_design_truth_binding(
            self.design_truth,
            self.architecture_design,
            project_id=self.project_id,
            prefix="application",
        )
        expected_event = {
            ApplicationRunStatus.COMPLETED: ApplicationAuditEventType.QUESTION_ANSWERED,
            ApplicationRunStatus.REJECTED: ApplicationAuditEventType.QUESTION_REJECTED,
            ApplicationRunStatus.FAILED: ApplicationAuditEventType.QUESTION_FAILED,
        }[self.status]
        expected_event_id = ApplicationAuditEvent.event_id_for(
            self.question_fingerprint, expected_event
        )
        if (
            self.audit.event_type is not expected_event
            or self.audit.event_id != expected_event_id
        ):
            raise ValueError("application result audit binding mismatch")
        if self.status is ApplicationRunStatus.COMPLETED:
            if (
                self.route not in {
                ApplicationRoute.GREENFIELD,
                    ApplicationRoute.GREENFIELD_DISCOVERY,
                    ApplicationRoute.GREENFIELD_DESIGN,
                    ApplicationRoute.BROWNFIELD,
                    ApplicationRoute.BROWNFIELD_ANALYSIS,
                    ApplicationRoute.KNOWLEDGE,
                    ApplicationRoute.FIRMWARE,
                    ApplicationRoute.DEBUG,
                    ApplicationRoute.DEBUG_ANALYSIS,
                }
                or self.context_fingerprint is None
                or self.answer is None
                or self.error_code is not None
            ):
                raise ValueError("completed application result is incomplete")
        elif self.answer is not None or self.error_code is None:
            raise ValueError("non-completed application result is invalid")
        expected = canonical_fingerprint(
            self,
            exclude={
                "fingerprint",
                "gateway_invocation_count",
                "provider_response_present",
                "answer_validation_stage",
                "answer_rejection_reason",
                "proposed_requirements_count",
                "open_questions_count",
                "answer_text_present",
                "original_exception_type",
                "original_exception_hash",
                "exception_layer",
                "exception_stage",
                "traceback_fingerprint",
            },
        )
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("application result fingerprint mismatch")
        return self

    @classmethod
    def create(
        cls,
        *,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        status: ApplicationRunStatus,
        context_fingerprint: str | None,
        context: EngineeringContextSnapshot | None = None,
        answer: EngineeringAnswer | None,
        error_code: ApplicationErrorCode | None,
        audit_event: ApplicationAuditEvent,
        requirement_context: RequirementContext | None = None,
        research_snapshot: EngineeringResearchSnapshot | None = None,
        engineering_plan: EngineeringPlan | None = None,
        engineering_plan_review: PlanReviewDecision | None = None,
        architecture_design: ArchitectureDesign | None = None,
        architecture_review: ArchitectureReviewDecision | None = None,
        design_truth: DesignTruth | None = None,
        gateway_invocation_count: int = 0,
        provider_response_present: bool = False,
        answer_validation_stage: str | None = None,
        answer_rejection_reason: ApplicationErrorCode | None = None,
        proposed_requirements_count: int = 0,
        open_questions_count: int = 0,
        answer_text_present: bool = False,
        original_exception_type: str | None = None,
        original_exception_hash: str | None = None,
        exception_layer: str | None = None,
        exception_stage: str | None = None,
        traceback_fingerprint: str | None = None,
    ) -> ApplicationRunResult:
        checked_request = ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        checked_event = ApplicationAuditEvent.model_validate(
            copy.deepcopy(audit_event.model_dump(mode="python"))
        )
        return cls(
            request_id=checked_request.request_id,
            trace_id=checked_request.trace_id,
            project_id=checked_request.project_id,
            route=route,
            status=status,
            question_fingerprint=checked_request.fingerprint,
            context_fingerprint=context_fingerprint,
            context=context,
            answer=answer,
            requirement_context=requirement_context,
            research_snapshot=research_snapshot,
            engineering_plan=engineering_plan,
            engineering_plan_review=engineering_plan_review,
            architecture_design=architecture_design,
            architecture_review=architecture_review,
            design_truth=design_truth,
            error_code=error_code,
            gateway_invocation_count=gateway_invocation_count,
            provider_response_present=provider_response_present,
            answer_validation_stage=answer_validation_stage,
            answer_rejection_reason=answer_rejection_reason,
            proposed_requirements_count=proposed_requirements_count,
            open_questions_count=open_questions_count,
            answer_text_present=answer_text_present,
            original_exception_type=original_exception_type,
            original_exception_hash=original_exception_hash,
            exception_layer=exception_layer,
            exception_stage=exception_stage,
            traceback_fingerprint=traceback_fingerprint,
            audit=ApplicationAuditReference.from_event(checked_event),
            completed_at=checked_event.timestamp,
        )


__all__ = (
    "ApplicationAnswerEvidence",
    "ApplicationAuditEvent",
    "ApplicationAuditEventType",
    "ApplicationAuditReference",
    "ApplicationErrorCode",
    "ApplicationQuestionRequest",
    "ApplicationRoutingContext",
    "ApplicationRoute",
    "ApplicationRouteDecision",
    "ApplicationRunResult",
    "ApplicationRunStatus",
)
