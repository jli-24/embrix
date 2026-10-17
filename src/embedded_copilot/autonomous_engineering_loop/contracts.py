from __future__ import annotations

import copy
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot import ProposalReviewHandoff
from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
)
from embedded_copilot.execution import (
    BuildRequest,
    BuildResult,
    DebugObservation,
    DebugRequest,
    FlashRequest,
    FlashResult,
)
from embedded_copilot.failure_analysis import FailureAnalysis, FailureObservation
from embedded_copilot.repair_proposal import RepairProposal
from embedded_copilot.verification_loop import VerificationResult


class LoopState(StrEnum):
    INIT = "INIT"
    ANALYZING = "ANALYZING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class LoopContract(Fingerprinted):
    pass


class LoopPolicy(LoopContract):
    max_retry: int = 3

    @field_validator("max_retry", mode="before")
    @classmethod
    def _max_retry(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 3:
            raise ValueError("max_retry is invalid")
        return value

    @classmethod
    def create(cls, **values: object) -> LoopPolicy:
        return finalize(cls, values)


class AutonomousEngineeringLoopSnapshot(LoopContract):
    loop_id: str
    project_id: str
    target_id: str
    state: LoopState
    attempt: int
    retry_count: int
    policy: LoopPolicy
    failure_id: str
    observation_fingerprint: str
    analysis_fingerprint: str
    proposal_fingerprint: str
    handoff_fingerprint: str
    context_fingerprint: str
    last_verification_fingerprint: str | None = None

    @field_validator("loop_id", "project_id", "target_id", "failure_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator(
        "observation_fingerprint",
        "analysis_fingerprint",
        "proposal_fingerprint",
        "handoff_fingerprint",
        "context_fingerprint",
        "last_verification_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str | None:
        return None if value is None else fp(value, info.field_name)

    @field_validator("attempt", "retry_count", mode="before")
    @classmethod
    def _counts(cls, value: object, info) -> int:
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError(f"{info.field_name} is invalid")
        return value

    @field_validator("policy", mode="before")
    @classmethod
    def _policy(cls, value: object) -> LoopPolicy:
        return LoopPolicy.model_validate(copy.deepcopy(value))

    @model_validator(mode="after")
    def _state_bound(self) -> AutonomousEngineeringLoopSnapshot:
        if self.retry_count > self.policy.max_retry:
            raise ValueError("retry count exceeds policy")
        if (
            self.state is LoopState.SUCCESS
            and self.last_verification_fingerprint is None
        ):
            raise ValueError("successful loop requires verification")
        return self

    @classmethod
    def create(cls, **values: object) -> AutonomousEngineeringLoopSnapshot:
        return finalize(cls, values)


class ApprovedExecutionBundle(LoopContract):
    loop_id: str
    project_id: str
    target_id: str
    proposal_fingerprint: str
    handoff: ProposalReviewHandoff
    build_request: BuildRequest
    flash_request: FlashRequest | None = None
    debug_request: DebugRequest | None = None

    @field_validator("loop_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fp(cls, value: object) -> str:
        return fp(value, "proposal_fingerprint")

    @field_validator("handoff", mode="before")
    @classmethod
    def _handoff(cls, value: object) -> ProposalReviewHandoff:
        return ProposalReviewHandoff.model_validate(copy.deepcopy(value))

    @field_validator("build_request", mode="before")
    @classmethod
    def _build(cls, value: object) -> BuildRequest:
        return BuildRequest.model_validate(copy.deepcopy(value))

    @field_validator("flash_request", mode="before")
    @classmethod
    def _flash(cls, value: object) -> FlashRequest | None:
        return (
            None if value is None else FlashRequest.model_validate(copy.deepcopy(value))
        )

    @field_validator("debug_request", mode="before")
    @classmethod
    def _debug(cls, value: object) -> DebugRequest | None:
        return (
            None if value is None else DebugRequest.model_validate(copy.deepcopy(value))
        )

    @model_validator(mode="after")
    def _bound(self) -> ApprovedExecutionBundle:
        if (
            self.handoff.project_id != self.project_id
            or self.handoff.proposal_fingerprint != self.proposal_fingerprint
        ):
            raise ValueError("handoff binding mismatch")
        requests = (
            self.build_request.request,
            self.flash_request.request if self.flash_request else None,
            self.debug_request.request if self.debug_request else None,
        )
        for request in requests:
            if request is not None and (
                request.project_id != self.project_id
                or request.target_id != self.target_id
                or request.proposal_fingerprint != self.proposal_fingerprint
            ):
                raise ValueError("execution request binding mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> ApprovedExecutionBundle:
        return finalize(cls, values)


class LoopPreparation(LoopContract):
    snapshot: AutonomousEngineeringLoopSnapshot
    observation: FailureObservation
    analysis: FailureAnalysis
    proposal: RepairProposal
    handoff: ProposalReviewHandoff

    @classmethod
    def create(cls, **values: object) -> LoopPreparation:
        return finalize(cls, values)


class LoopExecutionResult(LoopContract):
    snapshot: AutonomousEngineeringLoopSnapshot
    verification: VerificationResult
    build_result: BuildResult
    flash_result: FlashResult | None = None
    debug_observation: DebugObservation | None = None

    @classmethod
    def create(cls, **values: object) -> LoopExecutionResult:
        return finalize(cls, values)


__all__ = (
    "ApprovedExecutionBundle",
    "AutonomousEngineeringLoopSnapshot",
    "LoopContract",
    "LoopExecutionResult",
    "LoopPolicy",
    "LoopPreparation",
    "LoopState",
)
