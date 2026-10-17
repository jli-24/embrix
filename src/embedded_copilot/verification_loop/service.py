from __future__ import annotations

import copy

from embedded_copilot.execution import ExecutionStatus

from .contracts import VerificationRequest, VerificationResult, VerificationStatus
from .exceptions import VerificationRejected


class VerificationService:
    def verify(
        self, request: VerificationRequest, *, attempt: int = 0
    ) -> VerificationResult:
        try:
            checked = VerificationRequest.model_validate(
                copy.deepcopy(request.model_dump(mode="python"))
            )
        except (TypeError, ValueError) as error:
            raise VerificationRejected() from error
        fingerprints = _fingerprints(checked)
        if checked.prior_failure_fingerprint in fingerprints:
            return VerificationResult.create(
                verification_id=checked.verification_id,
                project_id=checked.project_id,
                target_id=checked.target_id,
                status=VerificationStatus.ESCALATE,
                attempt=attempt,
                compared_fingerprints=fingerprints,
                evidence_refs=(),
                summary="verification produced no new evidence",
            )
        if _definite_failure(checked):
            status = VerificationStatus.FAIL
            summary = "verification observed a definite execution failure"
        elif (
            checked.build_result is None
            and checked.flash_result is None
            and checked.debug_observation is None
        ):
            status = VerificationStatus.ESCALATE
            summary = "verification evidence is insufficient"
        elif (
            checked.debug_observation is not None
            and checked.debug_observation.severity.value == "ERROR"
        ):
            status = VerificationStatus.FAIL
            summary = "verification observed an error-level debug observation"
        else:
            status = VerificationStatus.PASS
            summary = (
                "requested execution observations completed without a failure signal"
            )
        return VerificationResult.create(
            verification_id=checked.verification_id,
            project_id=checked.project_id,
            target_id=checked.target_id,
            status=status,
            attempt=attempt,
            compared_fingerprints=fingerprints,
            evidence_refs=tuple(f"execution-{value[:16]}" for value in fingerprints),
            summary=summary,
        )


def _fingerprints(request: VerificationRequest) -> tuple[str, ...]:
    values = [
        result.fingerprint
        for result in (
            request.build_result,
            request.flash_result,
            request.debug_observation,
        )
        if result is not None
    ]
    return tuple(sorted(set(values)))


def _definite_failure(request: VerificationRequest) -> bool:
    return any(
        result.status
        in {
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.UNAVAILABLE,
            ExecutionStatus.REJECTED,
        }
        for result in (request.build_result, request.flash_result)
        if result is not None
    )


from .functional_service import FunctionalVerificationService

__all__ = ("FunctionalVerificationService", "VerificationService")
