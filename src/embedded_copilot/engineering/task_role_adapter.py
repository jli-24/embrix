from __future__ import annotations

import copy
import hashlib
import re

from embedded_copilot.agents.base import BaseAgent
from embedded_copilot.agents.types import AgentResult, AgentStatus, AgentTask
from embedded_copilot.engineering_context import (
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringPlanStep,
    EngineeringStepResult,
    EngineeringTaskRequest,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.engineering_validation import (
    ValidationArtifactReference,
    ValidationConflict,
    ValidationRejected,
    ValidationRequest,
    ValidationService,
    ValidationSeverity,
    ValidationUnavailable,
)

_TOKEN = re.compile(r"[^A-Z0-9_]+")


def _reason(*values: object) -> str:
    parts = tuple(
        _TOKEN.sub("_", str(value).upper()).strip("_")
        for value in values
        if value not in (None, "")
    )
    return "_".join(part for part in parts if part)[:128] or "ROLE_UNAVAILABLE"


def _trace(step: EngineeringPlanStep, suffix: str) -> str:
    digest = hashlib.sha256(
        f"{step.task_id}:{step.step_id}:{suffix}".encode()
    ).hexdigest()[:32]
    return f"trace:{step.stage.value.lower()}:{digest}"


def _result(
    step: EngineeringPlanStep,
    *,
    status: EngineeringTaskStatus,
    failure_class: EngineeringFailureClass,
    reason_code: str,
    summary: str,
    evidence_references: tuple[str, ...] = (),
    trace_suffix: str,
) -> EngineeringStepResult:
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
        evidence_references=evidence_references,
        trace_reference=_trace(step, trace_suffix),
    )


def _authoritative_evidence(
    context: EngineeringContextSnapshot | None,
) -> tuple[EngineeringContextEvidence, ...]:
    if context is None:
        return ()
    evidence = (
        *context.graph_evidence,
        *context.knowledge_evidence,
        *context.multimodal_evidence,
    )
    return tuple(
        item
        for item in evidence
        if item.trust_level is EvidenceTrustLevel.VERIFIED_FACT
        and item.evidence_status is EvidenceVerificationStatus.VERIFIED
        and item.verification_status
        in {ContextVerificationStatus.VERIFIED, ContextVerificationStatus.APPROVED}
    )


class ExistingEngineeringRoleAdapter:
    """Adapts existing specialists and ValidationService to one private role port."""

    __slots__ = ("_debug", "_firmware", "_hardware", "_validation")

    def __init__(
        self,
        *,
        hardware_agent: BaseAgent,
        firmware_agent: BaseAgent,
        debug_agent: BaseAgent,
        validation_service: ValidationService,
    ) -> None:
        if not isinstance(hardware_agent, BaseAgent):
            raise TypeError("hardware agent is invalid")
        if not isinstance(firmware_agent, BaseAgent):
            raise TypeError("firmware agent is invalid")
        if not isinstance(debug_agent, BaseAgent):
            raise TypeError("debug agent is invalid")
        if not isinstance(validation_service, ValidationService):
            raise TypeError("validation service is invalid")
        self._hardware = hardware_agent
        self._firmware = firmware_agent
        self._debug = debug_agent
        self._validation = validation_service

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
        if (
            checked_step.task_id != checked_request.task_id
            or checked_step.tenant_id != checked_request.tenant_id
            or checked_step.project_id != checked_request.project_id
        ):
            raise ValueError("role request binding mismatch")
        if checked_step.stage is EngineeringTaskStage.REQUIREMENT:
            return _result(
                checked_step,
                status=EngineeringTaskStatus.COMPLETED,
                failure_class=EngineeringFailureClass.NONE,
                reason_code="REQUIREMENT_PROPOSED",
                summary="request retained as a non-authoritative requirement proposal",
                evidence_references=(),
                trace_suffix="requirement-proposed",
            )
        if checked_step.stage is EngineeringTaskStage.VALIDATION:
            return self._validate(
                checked_step,
                checked_request,
                engineering_context,
                prior_results,
            )
        agent = {
            EngineeringTaskStage.HARDWARE: self._hardware,
            EngineeringTaskStage.FIRMWARE: self._firmware,
            EngineeringTaskStage.DEBUG: self._debug,
        }.get(checked_step.stage)
        if agent is None:
            raise ValueError("role stage is not executable")
        return self._run_agent(
            agent,
            checked_step,
            checked_request,
            engineering_context,
            memory_context,
            prior_results,
        )

    @staticmethod
    def _run_agent(
        agent: BaseAgent,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        context: EngineeringContextSnapshot | None,
        memory: tuple[EngineeringContextEvidence, ...],
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> EngineeringStepResult:
        metadata = {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "constraints": request.constraints,
            "engineering_context_fingerprint": (
                None if context is None else context.fingerprint
            ),
            "engineering_memory_context": tuple(
                {
                    "source_reference": item.source_reference,
                    "source_fingerprint": item.source_fingerprint,
                    "summary": item.summary,
                    "trust_level": item.trust_level.value,
                    "evidence_status": item.evidence_status.value,
                }
                for item in memory
            ),
            "prior_result_references": tuple(
                result.trace_reference for result in prior_results
            ),
        }
        requirement = request.objective
        if step.stage is EngineeringTaskStage.DEBUG and prior_results:
            requirement = f"{request.objective}; validation evidence: {prior_results[-1].summary}"
        task = AgentTask(
            task_id=step.step_id,
            task_type=step.stage.value.lower(),
            requirement=requirement,
            metadata=metadata,
        )
        try:
            raw = agent.run(copy.deepcopy(task))
            if type(raw) is not AgentResult:
                raise TypeError("specialist result is invalid")
            result = AgentResult.model_validate(
                copy.deepcopy(raw.model_dump(mode="python"))
            )
        except Exception as error:  # noqa: BLE001 - injected Agent boundary
            return _result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code=_reason(step.stage, type(error).__name__),
                summary=f"{step.stage.value.lower()} specialist boundary unavailable",
                trace_suffix=type(error).__name__,
            )
        if result.agent_name != agent.name:
            raise ValueError("specialist identity mismatch")
        if result.status is AgentStatus.SUCCESS:
            output_fingerprint = "sha256:" + hashlib.sha256(
                result.model_dump_json().encode("utf-8")
            ).hexdigest()
            return _result(
                step,
                status=EngineeringTaskStatus.COMPLETED,
                failure_class=EngineeringFailureClass.NONE,
                reason_code=f"{step.stage.value}_COMPLETED",
                summary=f"{step.stage.value.lower()} specialist completed an unverified engineering proposal",
                evidence_references=(output_fingerprint,),
                trace_suffix=output_fingerprint,
            )
        stage = result.metadata.get("stage")
        error_type = result.metadata.get("error_type")
        failure_class = (
            EngineeringFailureClass.INFRASTRUCTURE
            if any(
                token in str(error_type).casefold()
                for token in ("knowledge", "timeout", "unavailable", "transport")
            )
            else EngineeringFailureClass.ENGINEERING
        )
        return _result(
            step,
            status=EngineeringTaskStatus.FAILED,
            failure_class=failure_class,
            reason_code=_reason(step.stage, stage, error_type),
            summary=f"{step.stage.value.lower()} specialist returned a safe typed failure",
            trace_suffix=_reason(stage, error_type),
        )

    def _validate(
        self,
        step: EngineeringPlanStep,
        request: EngineeringTaskRequest,
        context: EngineeringContextSnapshot | None,
        prior_results: tuple[EngineeringStepResult, ...],
    ) -> EngineeringStepResult:
        authoritative = _authoritative_evidence(context)
        if context is None or not authoritative:
            return _result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code="VALIDATION_AUTHORITY_UNAVAILABLE",
                summary="validation requires authoritative non-memory evidence",
                trace_suffix="authority-unavailable",
            )
        source = prior_results[-1].fingerprint if prior_results else step.fingerprint
        suffix = source.removeprefix("sha256:")[:32]
        knowledge_refs = tuple(
            sorted(f"knowledge://context/{item.evidence_id}" for item in authoritative)
        )
        validation_request = ValidationRequest.create(
            request_id=f"validation-{suffix}",
            project_id=request.project_id,
            artifact_reference=ValidationArtifactReference.create(
                artifact_id=f"engineering-output-{suffix}",
                project_id=request.project_id,
                artifact_type="engineering_output",
                source_reference=f"artifact://engineering-task/{suffix}",
            ),
            context_snapshot=context,
            knowledge_refs=knowledge_refs,
            validation_scope=("engineering_output",),
        )
        try:
            report = self._validation.validate(validation_request)
        except ValidationRejected:
            return _result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INPUT,
                reason_code="VALIDATION_REJECTED",
                summary="validation request was rejected by the existing service boundary",
                trace_suffix="validation-rejected",
            )
        except (ValidationUnavailable, ValidationConflict) as error:
            return _result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.INFRASTRUCTURE,
                reason_code=_reason("VALIDATION", type(error).__name__),
                summary="validation service boundary unavailable",
                trace_suffix=type(error).__name__,
            )
        blocking = tuple(
            finding
            for finding in report.findings
            if finding.severity in {
                ValidationSeverity.ERROR,
                ValidationSeverity.CRITICAL,
            }
        )
        references = (
            f"validation:{report.report_id}",
            *(f"finding:{finding.finding_id}" for finding in report.findings),
        )
        if blocking:
            return _result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.ENGINEERING,
                reason_code="VALIDATION_FINDINGS",
                summary=report.summary,
                evidence_references=references,
                trace_suffix=report.fingerprint,
            )
        return _result(
            step,
            status=EngineeringTaskStatus.COMPLETED,
            failure_class=EngineeringFailureClass.NONE,
            reason_code="VALIDATION_PASS",
            summary=report.summary,
            evidence_references=references,
            trace_suffix=report.fingerprint,
        )


__all__ = ("ExistingEngineeringRoleAdapter",)
