from __future__ import annotations

import copy
import hashlib

from embedded_copilot.engineering_memory.foundation import (
    EvidenceMemoryPayload,
    EvidenceResultReference,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
)
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringStepResult,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)

from .contracts import (
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    validate_request,
    validate_result,
)


class SimulationMemoryRecorder:
    """Writes historical Candidate memory only after validation evidence exists."""

    __slots__ = ("_memory",)

    def __init__(self, memory: EngineeringMemoryService) -> None:
        if type(memory) is not EngineeringMemoryService:
            raise TypeError("engineering memory service is invalid")
        self._memory = memory

    def record(
        self,
        request: SimulationRequest,
        result: SimulationResult,
        engineering_result: EngineeringStepResult,
    ) -> FoundationEngineeringMemoryRecord | None:
        checked_request = validate_request(request)
        checked_result = validate_result(result)
        if type(engineering_result) is not EngineeringStepResult:
            raise TypeError("engineering validation result is invalid")
        checked_engineering = EngineeringStepResult.model_validate(
            copy.deepcopy(engineering_result.model_dump(mode="python"))
        )
        if (
            checked_result.request_fingerprint != checked_request.fingerprint
            or checked_result.simulation_id != checked_request.simulation_id
            or checked_result.tenant_id != checked_request.tenant_id
            or checked_result.project_id != checked_request.project_id
            or checked_engineering.tenant_id != checked_request.tenant_id
            or checked_engineering.project_id != checked_request.project_id
            or checked_engineering.stage is not EngineeringTaskStage.VALIDATION
        ):
            raise ValueError("simulation memory identity binding mismatch")
        if checked_result.status is SimulationStatus.PASS:
            if (
                checked_engineering.status is not EngineeringTaskStatus.COMPLETED
                or checked_engineering.failure_class is not EngineeringFailureClass.NONE
            ):
                raise ValueError("passing simulation validation result is inconsistent")
            historical_status = "SIMULATION_PASS"
        elif checked_result.failure_class is SimulationFailureClass.ENGINEERING:
            if (
                checked_engineering.status is not EngineeringTaskStatus.FAILED
                or checked_engineering.failure_class
                is not EngineeringFailureClass.ENGINEERING
            ):
                raise ValueError(
                    "engineering simulation validation result is inconsistent"
                )
            historical_status = "SIMULATION_ENGINEERING_FAILURE"
        else:
            # Infrastructure, cancellation and non-terminal states are operation
            # observations, not engineering history candidates.
            return None
        required_evidence = {
            checked_result.fingerprint,
            f"simulation:{checked_result.simulation_id}",
        }
        if not required_evidence.issubset(checked_engineering.evidence_references):
            raise ValueError("simulation validation evidence binding is missing")
        if checked_result.finished_at is None:
            raise ValueError("terminal simulation result timestamp is unavailable")
        digest = hashlib.sha256(
            f"{checked_result.fingerprint}:{checked_engineering.fingerprint}".encode()
        ).hexdigest()[:48]
        source_reference = f"simulation:{checked_result.simulation_id}"
        artifact_reference = f"artifact:{checked_request.firmware_artifact.artifact_id}"
        scenario_reference = f"scenario:{checked_request.scenario}"
        record = FoundationEngineeringMemoryRecord.create(
            memory_id=f"simulation-evidence-{digest}",
            tenant_id=checked_request.tenant_id,
            project_id=checked_request.project_id,
            memory_type=FoundationMemoryType.EVIDENCE,
            payload=EvidenceMemoryPayload(
                validation_result=EvidenceResultReference(
                    status=historical_status,
                    source_reference=source_reference,
                    fingerprint=checked_result.fingerprint,
                    artifact_reference=artifact_reference,
                )
            ),
            created_at=checked_result.finished_at,
            source_references=(
                source_reference,
                artifact_reference,
                scenario_reference,
                f"validation:{checked_engineering.step_id}",
            ),
        )
        return self._memory.save_memory(record)


__all__ = ("SimulationMemoryRecorder",)
