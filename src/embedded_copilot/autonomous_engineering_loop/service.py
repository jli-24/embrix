from __future__ import annotations

import asyncio
import copy
import hashlib

from embedded_copilot.engineering_context import EngineeringContextRequest
from embedded_copilot.execution import (
    BuildResult,
    DebugObservation,
    ExecutionStatus,
    FlashResult,
)
from embedded_copilot.failure_analysis import FailureAnalysisService, FailureObservation
from embedded_copilot.repair_proposal import RepairProposalService, RepairTargetType
from embedded_copilot.verification_loop import (
    VerificationRequest,
    VerificationService,
    VerificationStatus,
)

from .contracts import (
    ApprovedExecutionBundle,
    AutonomousEngineeringLoopSnapshot,
    LoopExecutionResult,
    LoopPolicy,
    LoopPreparation,
    LoopState,
)
from .exceptions import LoopRejected
from .ports import ExecutionRetryPort, LoopStatePort


class AutonomousEngineeringLoopService:
    __slots__ = (
        "_analysis",
        "_execution",
        "_proposal",
        "_state",
        "_verification",
    )

    def __init__(
        self,
        *,
        state_port: LoopStatePort,
        failure_analysis: FailureAnalysisService,
        repair_proposals: RepairProposalService,
        verification: VerificationService,
        execution: ExecutionRetryPort,
    ) -> None:
        if not isinstance(state_port, LoopStatePort):
            raise TypeError("loop state port is invalid")
        if not isinstance(execution, ExecutionRetryPort):
            raise TypeError("execution retry port is invalid")
        self._state = state_port
        self._analysis = failure_analysis
        self._proposal = repair_proposals
        self._verification = verification
        self._execution = execution

    def get_snapshot(self, loop_id: str) -> AutonomousEngineeringLoopSnapshot:
        return self._get(loop_id)

    def start(
        self,
        observation: FailureObservation,
        context_request: EngineeringContextRequest,
        *,
        loop_id: str,
        target_type: RepairTargetType,
        affected_component: str,
        governance_reference: str,
        policy: LoopPolicy | None = None,
        model_policy=None,
    ) -> LoopPreparation:
        checked_observation = FailureObservation.model_validate(
            copy.deepcopy(observation.model_dump(mode="python"))
        )
        checked_request = EngineeringContextRequest.model_validate(
            copy.deepcopy(context_request.model_dump(mode="python"))
        )
        loop_policy = (
            LoopPolicy.create(max_retry=3)
            if policy is None
            else LoopPolicy.model_validate(
                copy.deepcopy(policy.model_dump(mode="python"))
            )
        )
        analysis = self._analysis.analyze(
            checked_observation, checked_request, model_policy=model_policy
        )
        proposal = self._proposal.propose(
            analysis, target_type=target_type, affected_component=affected_component
        )
        handoff = self._proposal.handoff(
            proposal, governance_reference=governance_reference
        )
        snapshot = AutonomousEngineeringLoopSnapshot.create(
            loop_id=loop_id,
            project_id=checked_observation.project_id,
            target_id=checked_observation.target_id,
            state=LoopState.WAITING_APPROVAL,
            attempt=0,
            retry_count=0,
            policy=loop_policy,
            failure_id=analysis.failure_id,
            observation_fingerprint=checked_observation.fingerprint,
            analysis_fingerprint=analysis.fingerprint,
            proposal_fingerprint=proposal.fingerprint,
            handoff_fingerprint=handoff.fingerprint,
            context_fingerprint=analysis.context_fingerprint,
        )
        saved = self._state.save(snapshot)
        return LoopPreparation.create(
            snapshot=saved,
            observation=checked_observation,
            analysis=analysis,
            proposal=proposal,
            handoff=handoff,
        )

    def execute_approved(self, bundle: ApprovedExecutionBundle) -> LoopExecutionResult:
        checked_bundle = ApprovedExecutionBundle.model_validate(
            copy.deepcopy(bundle.model_dump(mode="python"))
        )
        snapshot = self._get(checked_bundle.loop_id)
        if snapshot.state is not LoopState.WAITING_APPROVAL:
            raise LoopRejected()
        if (
            snapshot.project_id != checked_bundle.project_id
            or snapshot.target_id != checked_bundle.target_id
            or snapshot.proposal_fingerprint != checked_bundle.proposal_fingerprint
            or snapshot.handoff_fingerprint != checked_bundle.handoff.fingerprint
        ):
            raise LoopRejected()
        executing = self._save(
            snapshot.model_copy(update={"state": LoopState.EXECUTING})
        )
        try:
            build = self._execution.build(checked_bundle.build_request)
            if type(build) is not BuildResult:
                raise LoopRejected()
            build = BuildResult.model_validate(
                copy.deepcopy(build.model_dump(mode="python"))
            )
            if build.request_id != checked_bundle.build_request.request.request_id:
                raise LoopRejected()
            flash = None
            if (
                checked_bundle.flash_request is not None
                and build.status is ExecutionStatus.SUCCEEDED
            ):
                if (
                    build.artifact is None
                    or build.artifact.firmware_fingerprint
                    != checked_bundle.flash_request.artifact.firmware_fingerprint
                ):
                    raise LoopRejected()
                flash = self._execution.flash(checked_bundle.flash_request)
                if type(flash) is not FlashResult:
                    raise LoopRejected()
                flash = FlashResult.model_validate(
                    copy.deepcopy(flash.model_dump(mode="python"))
                )
            debug = None
            if checked_bundle.debug_request is not None:
                debug = self._execution.collect_logs(checked_bundle.debug_request)
                if type(debug) is not DebugObservation:
                    raise LoopRejected()
                debug = DebugObservation.model_validate(
                    copy.deepcopy(debug.model_dump(mode="python"))
                )
        except asyncio.CancelledError:
            self._save(
                executing.model_copy(update={"state": LoopState.WAITING_APPROVAL})
            )
            raise
        except LoopRejected:
            self._save(executing.model_copy(update={"state": LoopState.ESCALATED}))
            raise
        except Exception as error:
            self._save(executing.model_copy(update={"state": LoopState.ESCALATED}))
            raise LoopRejected() from error
        verifying = self._save(
            executing.model_copy(update={"state": LoopState.VERIFYING})
        )
        try:
            verification = self._verification.verify(
                VerificationRequest.create(
                    verification_id="verification-"
                    + hashlib.sha256(snapshot.loop_id.encode()).hexdigest()[:24],
                    project_id=snapshot.project_id,
                    target_id=snapshot.target_id,
                    repair_proposal_fingerprint=snapshot.proposal_fingerprint,
                    prior_failure_fingerprint=snapshot.observation_fingerprint,
                    build_request_id=checked_bundle.build_request.request.request_id,
                    flash_request_id=checked_bundle.flash_request.request.request_id
                    if checked_bundle.flash_request
                    else None,
                    debug_request_id=checked_bundle.debug_request.request.request_id
                    if checked_bundle.debug_request
                    else None,
                    build_result=build,
                    flash_result=flash,
                    debug_observation=debug,
                ),
                attempt=snapshot.attempt,
            )
        except asyncio.CancelledError:
            self._save(
                verifying.model_copy(update={"state": LoopState.WAITING_APPROVAL})
            )
            raise
        except Exception as error:
            self._save(verifying.model_copy(update={"state": LoopState.ESCALATED}))
            raise LoopRejected() from error
        if verification.status is VerificationStatus.PASS:
            state = LoopState.SUCCESS
            retry_count = snapshot.retry_count
        elif verification.status is VerificationStatus.FAIL:
            retry_count = snapshot.retry_count + 1
            state = (
                LoopState.ESCALATED
                if retry_count >= snapshot.policy.max_retry
                else LoopState.FAILED
            )
        else:
            retry_count = snapshot.retry_count
            state = LoopState.ESCALATED
        final = self._save(
            verifying.model_copy(
                update={
                    "state": state,
                    "retry_count": retry_count,
                    "last_verification_fingerprint": verification.fingerprint,
                }
            )
        )
        return LoopExecutionResult.create(
            snapshot=final,
            verification=verification,
            build_result=build,
            flash_result=flash,
            debug_observation=debug,
        )

    def retry(
        self,
        observation: FailureObservation,
        context_request: EngineeringContextRequest,
        *,
        loop_id: str,
        target_type: RepairTargetType,
        affected_component: str,
        governance_reference: str,
        model_policy=None,
    ) -> LoopPreparation:
        snapshot = self._get(loop_id)
        if (
            snapshot.state is not LoopState.FAILED
            or snapshot.retry_count >= snapshot.policy.max_retry
        ):
            raise LoopRejected()
        preparation = self.start(
            observation,
            context_request,
            loop_id=loop_id,
            target_type=target_type,
            affected_component=affected_component,
            governance_reference=governance_reference,
            policy=snapshot.policy,
            model_policy=model_policy,
        )
        updated = preparation.snapshot.model_copy(
            update={
                "attempt": snapshot.attempt + 1,
                "retry_count": snapshot.retry_count,
            }
        )
        saved = self._save(updated)
        material = preparation.model_dump(mode="python")
        material["snapshot"] = saved
        material.pop("fingerprint", None)
        return LoopPreparation.create(**material)

    def _get(self, loop_id: str) -> AutonomousEngineeringLoopSnapshot:
        value = self._state.get(loop_id)
        if value is None:
            raise LoopRejected()
        return AutonomousEngineeringLoopSnapshot.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )

    def _save(
        self, snapshot: AutonomousEngineeringLoopSnapshot
    ) -> AutonomousEngineeringLoopSnapshot:
        material = snapshot.model_dump(mode="python")
        material.pop("fingerprint", None)
        checked = AutonomousEngineeringLoopSnapshot.create(**material)
        saved = self._state.save(checked)
        return AutonomousEngineeringLoopSnapshot.model_validate(
            copy.deepcopy(saved.model_dump(mode="python"))
        )


__all__ = ("AutonomousEngineeringLoopService",)
