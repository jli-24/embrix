from __future__ import annotations

import copy
import hashlib
import re

from embedded_copilot.engineering_context import (
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextProviderPort,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
)

from .exceptions import EngineeringTaskRejected, EngineeringTaskUnavailable
from .models import (
    EngineeringFailureClass,
    EngineeringPlan,
    EngineeringPlanStep,
    EngineeringStepResult,
    EngineeringTaskRequest,
    EngineeringTaskResult,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from .planner import EngineeringTaskPlanner
from .ports import EngineeringRoleExecutionPort

_REASON_TOKEN = re.compile(r"[^A-Z0-9_]+")


def _copy_model(value: object, model_type):
    return model_type.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _exception_reason(error: Exception) -> str:
    name = _REASON_TOKEN.sub("_", type(error).__name__.upper()).strip("_")
    return f"ROLE_PORT_{name or 'UNAVAILABLE'}"


class EngineeringTaskOrchestrator:
    """Coordinates one explicit, deterministic, bounded engineering task."""

    __slots__ = ("_context", "_memory", "_planner", "_roles")

    def __init__(
        self,
        *,
        planner: EngineeringTaskPlanner,
        role_executor: EngineeringRoleExecutionPort,
        context_provider: EngineeringContextProviderPort,
        memory_service: EngineeringMemoryService,
    ) -> None:
        if type(planner) is not EngineeringTaskPlanner:
            raise TypeError("engineering task planner is invalid")
        if not isinstance(role_executor, EngineeringRoleExecutionPort):
            raise TypeError("engineering role executor is invalid")
        if not isinstance(context_provider, EngineeringContextProviderPort):
            raise TypeError("engineering context provider is invalid")
        if not isinstance(memory_service, EngineeringMemoryService):
            raise TypeError("engineering memory service is invalid")
        self._planner = planner
        self._roles = role_executor
        self._context = context_provider
        self._memory = memory_service

    def run(self, request: EngineeringTaskRequest) -> EngineeringTaskResult:
        checked = self._request(request)
        try:
            plan = self._planner.plan(copy.deepcopy(checked))
            plan = _copy_model(plan, EngineeringPlan)
        except Exception as error:
            raise EngineeringTaskRejected() from error
        context_request = EngineeringContextRequest(
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            task=checked.objective,
            constraints=checked.constraints,
        )
        context = self._load_context(context_request, checked)
        memory = self._load_memory(context_request, checked)

        results: list[EngineeringStepResult] = []
        for stage in (
            EngineeringTaskStage.REQUIREMENT,
            EngineeringTaskStage.HARDWARE,
            EngineeringTaskStage.FIRMWARE,
        ):
            step = self._step(plan, stage, 0)
            result = self._execute(step, checked, context, memory, tuple(results))
            results.append(result)
            if result.status is not EngineeringTaskStatus.COMPLETED:
                return self._result(
                    checked,
                    plan,
                    results,
                    memory,
                    status=self._task_status(result),
                    validation_summary="validation not reached",
                    debug_rounds_used=0,
                )

        validation = self._execute(
            self._step(plan, EngineeringTaskStage.VALIDATION, 0),
            checked,
            context,
            memory,
            tuple(results),
        )
        results.append(validation)
        if validation.status is EngineeringTaskStatus.COMPLETED:
            return self._complete(checked, plan, results, memory, validation.summary, 0)
        if (
            validation.status is not EngineeringTaskStatus.FAILED
            or validation.failure_class is not EngineeringFailureClass.ENGINEERING
        ):
            return self._result(
                checked,
                plan,
                results,
                memory,
                status=self._task_status(validation),
                validation_summary=validation.summary,
                debug_rounds_used=0,
            )

        debug_rounds_used = 0
        for round_index in range(1, plan.max_debug_rounds + 1):
            debug = self._execute(
                self._step(plan, EngineeringTaskStage.DEBUG, round_index),
                checked,
                context,
                memory,
                tuple(results),
            )
            results.append(debug)
            debug_rounds_used += 1
            if debug.status is not EngineeringTaskStatus.COMPLETED:
                return self._result(
                    checked,
                    plan,
                    results,
                    memory,
                    status=self._task_status(debug),
                    validation_summary=validation.summary,
                    debug_rounds_used=debug_rounds_used,
                )
            validation = self._execute(
                self._step(plan, EngineeringTaskStage.VALIDATION, round_index),
                checked,
                context,
                memory,
                tuple(results),
            )
            results.append(validation)
            if validation.status is EngineeringTaskStatus.COMPLETED:
                return self._complete(
                    checked,
                    plan,
                    results,
                    memory,
                    validation.summary,
                    debug_rounds_used,
                )
            if (
                validation.status is not EngineeringTaskStatus.FAILED
                or validation.failure_class
                is not EngineeringFailureClass.ENGINEERING
            ):
                return self._result(
                    checked,
                    plan,
                    results,
                    memory,
                    status=self._task_status(validation),
                    validation_summary=validation.summary,
                    debug_rounds_used=debug_rounds_used,
                )

        return self._result(
            checked,
            plan,
            results,
            memory,
            status=EngineeringTaskStatus.FAILED,
            validation_summary=validation.summary,
            debug_rounds_used=debug_rounds_used,
        )

    @staticmethod
    def _request(value: object) -> EngineeringTaskRequest:
        if type(value) is not EngineeringTaskRequest:
            raise EngineeringTaskRejected()
        try:
            return _copy_model(value, EngineeringTaskRequest)
        except Exception as error:
            raise EngineeringTaskRejected() from error

    def _load_context(
        self,
        request: EngineeringContextRequest,
        task: EngineeringTaskRequest,
    ) -> EngineeringContextSnapshot | None:
        try:
            value = self._context.get_context(copy.deepcopy(request))
            if value is None:
                return None
            if type(value) is not EngineeringContextSnapshot:
                raise TypeError("engineering context result is invalid")
            checked = _copy_model(value, EngineeringContextSnapshot)
        except (TypeError, ValueError) as error:
            raise EngineeringTaskRejected() from error
        except Exception as error:
            raise EngineeringTaskUnavailable() from error
        if checked.tenant_id != task.tenant_id or checked.project_id != task.project_id:
            raise EngineeringTaskRejected()
        return checked

    def _load_memory(
        self,
        request: EngineeringContextRequest,
        task: EngineeringTaskRequest,
    ) -> tuple[EngineeringContextEvidence, ...]:
        try:
            values = self._memory.build_context(copy.deepcopy(request))
            if type(values) is not tuple:
                raise TypeError("engineering memory context is invalid")
            checked = tuple(_copy_model(item, EngineeringContextEvidence) for item in values)
        except (TypeError, ValueError) as error:
            raise EngineeringTaskRejected() from error
        except Exception as error:
            raise EngineeringTaskUnavailable() from error
        if any(
            item.tenant_id != task.tenant_id
            or item.project_id != task.project_id
            or item.trust_level is not EvidenceTrustLevel.CANDIDATE
            or item.evidence_status is not EvidenceVerificationStatus.CANDIDATE
            or item.verification_status
            is not ContextVerificationStatus.REQUIRES_REVIEW
            for item in checked
        ):
            raise EngineeringTaskRejected()
        return checked

    def _execute(
        self,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        context: EngineeringContextSnapshot | None,
        memory: tuple[EngineeringContextEvidence, ...],
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> EngineeringStepResult:
        try:
            value = self._roles.execute_step(
                copy.deepcopy(step),
                copy.deepcopy(request),
                copy.deepcopy(context),
                copy.deepcopy(memory),
                copy.deepcopy(prior_results),
            )
            if type(value) is not EngineeringStepResult:
                raise TypeError("engineering role result is invalid")
            result = _copy_model(value, EngineeringStepResult)
        except (TypeError, ValueError) as error:
            raise EngineeringTaskRejected() from error
        except Exception as error:  # noqa: BLE001 - injected role boundary
            result = EngineeringStepResult.create(
                step_id=step.step_id,
                task_id=step.task_id,
                tenant_id=step.tenant_id,
                project_id=step.project_id,
                stage=step.stage,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code=_exception_reason(error),
                summary=f"{step.stage.value.lower()} role boundary unavailable",
                evidence_references=(),
                trace_reference=f"trace:{request.task_id}:{step.step_id}",
            )
        if (
            result.step_id != step.step_id
            or result.task_id != request.task_id
            or result.tenant_id != request.tenant_id
            or result.project_id != request.project_id
            or result.stage is not step.stage
        ):
            raise EngineeringTaskRejected()
        return result

    @staticmethod
    def _step(
        plan: EngineeringPlan,
        stage: EngineeringTaskStage,
        round_index: int,
    ) -> EngineeringPlanStep:
        matches = tuple(
            step
            for step in plan.steps
            if step.stage is stage and step.round_index == round_index
        )
        if len(matches) != 1:
            raise EngineeringTaskRejected()
        return matches[0]

    @staticmethod
    def _task_status(result: EngineeringStepResult) -> EngineeringTaskStatus:
        return (
            EngineeringTaskStatus.AWAITING_APPROVAL
            if result.status is EngineeringTaskStatus.AWAITING_APPROVAL
            else EngineeringTaskStatus.FAILED
        )

    def _complete(
        self,
        request: EngineeringTaskRequest,
        plan: EngineeringPlan,
        results: list[EngineeringStepResult],
        memory: tuple[EngineeringContextEvidence, ...],
        validation_summary: str,
        debug_rounds_used: int,
    ) -> EngineeringTaskResult:
        step = self._step(plan, EngineeringTaskStage.COMPLETE, 0)
        results.append(
            EngineeringStepResult.create(
                step_id=step.step_id,
                task_id=step.task_id,
                tenant_id=step.tenant_id,
                project_id=step.project_id,
                stage=step.stage,
                status=EngineeringTaskStatus.COMPLETED,
                failure_class=EngineeringFailureClass.NONE,
                reason_code="TASK_COMPLETED",
                summary="bounded engineering task completed",
                evidence_references=(results[-1].trace_reference,),
                trace_reference=f"trace:{request.task_id}:complete",
            )
        )
        return self._result(
            request,
            plan,
            results,
            memory,
            status=EngineeringTaskStatus.COMPLETED,
            validation_summary=validation_summary,
            debug_rounds_used=debug_rounds_used,
        )

    @staticmethod
    def _result(
        request: EngineeringTaskRequest,
        plan: EngineeringPlan,
        results: list[EngineeringStepResult],
        memory: tuple[EngineeringContextEvidence, ...],
        *,
        status: EngineeringTaskStatus,
        validation_summary: str,
        debug_rounds_used: int,
    ) -> EngineeringTaskResult:
        digest = hashlib.sha256(request.task_id.encode("utf-8")).hexdigest()[:24]
        return EngineeringTaskResult.create(
            result_id=f"engineering-result-{digest}",
            task_id=request.task_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            plan_fingerprint=plan.fingerprint,
            step_results=tuple(results),
            validation_summary=validation_summary,
            memory_references=tuple(
                sorted({item.source_reference for item in memory})
            ),
            status=status,
            debug_rounds_used=debug_rounds_used,
        )


__all__ = ("EngineeringTaskOrchestrator",)
