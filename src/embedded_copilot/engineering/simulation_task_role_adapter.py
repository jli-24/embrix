from __future__ import annotations

import copy
import hashlib
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import (
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringPlanStep,
    EngineeringStepResult,
    EngineeringTaskRequest,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.engineering_task_runtime.ports import EngineeringRoleExecutionPort
from embedded_copilot.simulation_runtime.contracts import (
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    validate_request,
    validate_result,
)


@runtime_checkable
class SimulationRequestFactoryPort(Protocol):
    def create(
        self,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> SimulationRequest: ...


@runtime_checkable
class SimulationEvaluationPort(Protocol):
    def evaluate(self, request: SimulationRequest) -> SimulationResult: ...


def _copy_result(value: object) -> EngineeringStepResult:
    if type(value) is not EngineeringStepResult:
        raise TypeError("engineering role result is invalid")
    return EngineeringStepResult.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _trace(step: EngineeringPlanStep, result: SimulationResult) -> str:
    digest = hashlib.sha256(
        f"{step.step_id}:{result.fingerprint}".encode()
    ).hexdigest()[:40]
    return f"trace:simulation:{digest}"


class SimulationTaskRoleAdapter:
    """Decorates the existing validation role without adding a workflow stage."""

    __slots__ = ("_delegate", "_factory", "_simulation")

    def __init__(
        self,
        *,
        delegate: EngineeringRoleExecutionPort,
        request_factory: SimulationRequestFactoryPort,
        simulation: SimulationEvaluationPort,
    ) -> None:
        if not isinstance(delegate, EngineeringRoleExecutionPort):
            raise TypeError("engineering role delegate is invalid")
        if not isinstance(request_factory, SimulationRequestFactoryPort):
            raise TypeError("simulation request factory is invalid")
        if not isinstance(simulation, SimulationEvaluationPort):
            raise TypeError("simulation evaluation port is invalid")
        self._delegate = delegate
        self._factory = request_factory
        self._simulation = simulation

    def execute_step(
        self,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        engineering_context: EngineeringContextSnapshot | None,
        memory_context: tuple[EngineeringContextEvidence, ...],
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> EngineeringStepResult:
        checked_step = EngineeringPlanStep.model_validate(
            copy.deepcopy(step.model_dump(mode="python"))
        )
        checked_request = EngineeringTaskRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        base = _copy_result(
            self._delegate.execute_step(
                copy.deepcopy(checked_step),
                copy.deepcopy(checked_request),
                copy.deepcopy(engineering_context),
                copy.deepcopy(memory_context),
                copy.deepcopy(prior_results),
            )
        )
        if checked_step.stage is not EngineeringTaskStage.VALIDATION:
            return base
        if base.status is not EngineeringTaskStatus.COMPLETED:
            return base
        try:
            simulation_request = validate_request(
                self._factory.create(
                    copy.deepcopy(checked_step),
                    copy.deepcopy(checked_request),
                    copy.deepcopy(prior_results),
                )
            )
            if (
                simulation_request.tenant_id != checked_request.tenant_id
                or simulation_request.project_id != checked_request.project_id
            ):
                raise ValueError("simulation request task binding mismatch")
            result = validate_result(
                self._simulation.evaluate(copy.deepcopy(simulation_request))
            )
            if (
                result.simulation_id != simulation_request.simulation_id
                or result.tenant_id != checked_request.tenant_id
                or result.project_id != checked_request.project_id
                or result.request_fingerprint != simulation_request.fingerprint
            ):
                raise ValueError("simulation result task binding mismatch")
        except Exception:  # noqa: BLE001 - injected private runtime boundary
            return self._step_result(
                checked_step,
                base,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code="SIMULATION_BOUNDARY_UNAVAILABLE",
                summary="simulation validation boundary failed closed",
                result=None,
            )
        if result.status is SimulationStatus.PASS:
            return self._step_result(
                checked_step,
                base,
                status=EngineeringTaskStatus.COMPLETED,
                failure_class=EngineeringFailureClass.NONE,
                reason_code="SIMULATION_PASS",
                summary="existing validation and bounded simulation assertions passed",
                result=result,
            )
        if result.status is SimulationStatus.PENDING:
            if result.reason_code == "SIMULATION_WAIT_APPROVAL":
                return self._step_result(
                    checked_step,
                    base,
                    status=EngineeringTaskStatus.AWAITING_APPROVAL,
                    failure_class=EngineeringFailureClass.APPROVAL,
                    reason_code=result.reason_code,
                    summary="simulation test operation awaits existing approval",
                    result=result,
                )
            return self._step_result(
                checked_step,
                base,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code=result.reason_code,
                summary="simulation operation has not reached a terminal state",
                result=result,
            )
        if result.status is SimulationStatus.RUNNING:
            return self._step_result(
                checked_step,
                base,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code="SIMULATION_RUNNING",
                summary="simulation operation is still running",
                result=result,
            )
        if result.status is SimulationStatus.CANCELLED:
            return self._step_result(
                checked_step,
                base,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code="SIMULATION_CANCELLED",
                summary="simulation cancellation retained through compatibility projection",
                result=result,
            )
        failure = (
            EngineeringFailureClass.ENGINEERING
            if result.failure_class is SimulationFailureClass.ENGINEERING
            else EngineeringFailureClass.INFRASTRUCTURE
        )
        return self._step_result(
            checked_step,
            base,
            status=EngineeringTaskStatus.FAILED,
            failure_class=failure,
            reason_code=result.reason_code,
            summary=(
                "simulation produced a bounded engineering validation failure"
                if failure is EngineeringFailureClass.ENGINEERING
                else "simulation infrastructure failed closed"
            ),
            result=result,
        )

    @staticmethod
    def _step_result(
        step: EngineeringPlanStep,
        base: EngineeringStepResult,
        *,
        status: EngineeringTaskStatus,
        failure_class: EngineeringFailureClass,
        reason_code: str,
        summary: str,
        result: SimulationResult | None,
    ) -> EngineeringStepResult:
        simulation_references: tuple[str, ...] = ()
        trace = base.trace_reference
        if result is not None:
            simulation_references = tuple(
                dict.fromkeys(
                    (
                        f"simulation:{result.simulation_id}",
                        result.fingerprint,
                        *result.artifact_references,
                        *result.telemetry_references,
                        *(item.evidence_reference for item in result.assertion_results),
                    )
                )
            )
            trace = _trace(step, result)
        return EngineeringStepResult.create(
            step_id=step.step_id,
            task_id=step.task_id,
            tenant_id=step.tenant_id,
            project_id=step.project_id,
            stage=step.stage,
            status=status,
            failure_class=failure_class,
            reason_code=reason_code,
            summary=summary,
            evidence_references=tuple(
                dict.fromkeys((*base.evidence_references, *simulation_references))
            ),
            trace_reference=trace,
        )


__all__ = (
    "SimulationEvaluationPort",
    "SimulationRequestFactoryPort",
    "SimulationTaskRoleAdapter",
)
