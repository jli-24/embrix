from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_context import EngineeringContextRequest
from embedded_copilot.engineering_memory.foundation import (
    FoundationMemoryQuery,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    SQLiteEngineeringMemoryRepository,
)
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringStepResult,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.simulation_runtime.contracts import (
    SimulationFailureClass,
    SimulationStatus,
)
from embedded_copilot.simulation_runtime.memory import SimulationMemoryRecorder

from .test_role_adapter import simulation_request, simulation_result

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def validation_result(simulation) -> EngineeringStepResult:
    return EngineeringStepResult.create(
        step_id="validation-1",
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        stage=EngineeringTaskStage.VALIDATION,
        status=(
            EngineeringTaskStatus.COMPLETED
            if simulation.status is SimulationStatus.PASS
            else EngineeringTaskStatus.FAILED
        ),
        failure_class=(
            EngineeringFailureClass.NONE
            if simulation.status is SimulationStatus.PASS
            else EngineeringFailureClass.ENGINEERING
        ),
        reason_code=(
            "SIMULATION_PASS"
            if simulation.status is SimulationStatus.PASS
            else simulation.reason_code
        ),
        summary="simulation projected through existing validation evidence",
        evidence_references=(
            f"simulation:{simulation.simulation_id}",
            simulation.fingerprint,
            *simulation.artifact_references,
        ),
        trace_reference="trace:simulation:validation-1",
    )


def test_simulation_evidence_is_persisted_only_after_validation_projection(
    tmp_path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    service = EngineeringMemoryService(SQLiteEngineeringMemoryRepository(database))
    simulation = simulation_result(
        SimulationStatus.PASS,
        SimulationFailureClass.NONE,
        "SIMULATION_ASSERTIONS_PASSED",
    )
    engineering = validation_result(simulation)

    record = SimulationMemoryRecorder(service).record(
        simulation_request(), simulation, engineering
    )

    assert record is not None
    assert record.memory_type is FoundationMemoryType.EVIDENCE
    assert record.status == "CANDIDATE"
    assert record.payload.validation_result.status == "SIMULATION_PASS"
    assert record.payload.validation_result.fingerprint == simulation.fingerprint
    assert "scenario:uart-boot" in record.source_references

    recovered = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(database)
    ).search_memory(
        FoundationMemoryQuery(
            tenant_id="tenant-1",
            project_id="project-1",
            memory_type=FoundationMemoryType.EVIDENCE,
        )
    )[0]
    assert recovered == record

    context = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(database)
    ).build_context(
        EngineeringContextRequest(
            tenant_id="tenant-1",
            project_id="project-1",
            task="simulation evidence",
        )
    )[0]
    assert context.trust_level.value == "CANDIDATE"
    assert context.evidence_status.value == "CANDIDATE"
    assert context.verification_status.value == "REQUIRES_REVIEW"


def test_cancellation_is_not_persisted_as_engineering_memory(tmp_path) -> None:
    service = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    )
    cancellation = simulation_result(
        SimulationStatus.CANCELLED,
        SimulationFailureClass.CANCELLED,
        "SIMULATION_CANCELLED",
    )
    compatibility = EngineeringStepResult.create(
        step_id="validation-1",
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        stage=EngineeringTaskStage.VALIDATION,
        status=EngineeringTaskStatus.FAILED,
        failure_class=EngineeringFailureClass.INFRASTRUCTURE,
        reason_code="SIMULATION_CANCELLED",
        summary="simulation cancelled",
        evidence_references=(
            "simulation:simulation-1",
            cancellation.fingerprint,
        ),
        trace_reference="trace:simulation:cancelled",
    )

    record = SimulationMemoryRecorder(service).record(
        simulation_request(), cancellation, compatibility
    )

    assert record is None
    assert (
        service.search_memory(
            FoundationMemoryQuery(tenant_id="tenant-1", project_id="project-1")
        )
        == ()
    )


def test_memory_recording_rejects_result_not_projected_as_validation_evidence(
    tmp_path,
) -> None:
    service = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    )
    simulation = simulation_result(
        SimulationStatus.PASS,
        SimulationFailureClass.NONE,
        "SIMULATION_ASSERTIONS_PASSED",
    )
    engineering = validation_result(simulation)
    material = engineering.model_dump(mode="python")
    material.pop("fingerprint")
    material["evidence_references"] = ("validation:static-only",)
    unbound = EngineeringStepResult.create(**material)

    try:
        SimulationMemoryRecorder(service).record(
            simulation_request(), simulation, unbound
        )
    except ValueError as error:
        assert "validation evidence binding" in str(error)
    else:
        raise AssertionError("unprojected simulation result was persisted")
