from __future__ import annotations

import hashlib
from copy import deepcopy

from .common import validate_copy
from .contracts import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterfaceType,
    EngineeringPlan,
    EngineeringWorkflow,
    EngineeringWorkflowArtifact,
    EngineeringWorkflowArtifactType,
    EngineeringWorkflowItemStatus,
    EngineeringWorkflowPhase,
    EngineeringWorkflowPhaseType,
    EngineeringWorkflowStatus,
    EngineeringWorkflowTask,
    EngineeringWorkflowTaskType,
    EngineeringWorkflowVerification,
    PlanPhaseStatus,
    PlanProvenance,
    PlanStatus,
    PlanTaskStatus,
    PlanTaskType,
)

_WORKFLOW_VERSION = "1.0"
_PHASE_TYPES = {
    "phase-requirement-confirmation": EngineeringWorkflowPhaseType.REQUIREMENT_CONFIRMATION,
    "phase-system-architecture": EngineeringWorkflowPhaseType.SYSTEM_DESIGN,
    "phase-hardware-design": EngineeringWorkflowPhaseType.HARDWARE_DESIGN,
    "phase-pcb-design": EngineeringWorkflowPhaseType.PCB_DESIGN,
    "phase-firmware-development": EngineeringWorkflowPhaseType.FIRMWARE_DEVELOPMENT,
    "phase-simulation": EngineeringWorkflowPhaseType.SIMULATION,
    "phase-verification": EngineeringWorkflowPhaseType.VERIFICATION,
}
_ARTIFACT_TYPES = {
    "DOCUMENT": EngineeringWorkflowArtifactType.DOCUMENT,
    "SCHEMATIC": EngineeringWorkflowArtifactType.SCHEMATIC,
    "PCB": EngineeringWorkflowArtifactType.PCB,
    "SOURCE_CODE": EngineeringWorkflowArtifactType.FIRMWARE,
    "CONFIGURATION": EngineeringWorkflowArtifactType.CONFIGURATION,
    "SIMULATION": EngineeringWorkflowArtifactType.SIMULATION_RESULT,
    "REPORT": EngineeringWorkflowArtifactType.TEST_REPORT,
}
_STATUS_MAP = {
    PlanPhaseStatus.PENDING: EngineeringWorkflowItemStatus.PENDING,
    PlanPhaseStatus.IN_PROGRESS: EngineeringWorkflowItemStatus.IN_PROGRESS,
    PlanPhaseStatus.COMPLETED: EngineeringWorkflowItemStatus.COMPLETED,
    PlanPhaseStatus.BLOCKED: EngineeringWorkflowItemStatus.BLOCKED,
    PlanPhaseStatus.CANCELLED: EngineeringWorkflowItemStatus.CANCELLED,
    PlanTaskStatus.PENDING: EngineeringWorkflowItemStatus.PENDING,
    PlanTaskStatus.IN_PROGRESS: EngineeringWorkflowItemStatus.IN_PROGRESS,
    PlanTaskStatus.COMPLETED: EngineeringWorkflowItemStatus.COMPLETED,
    PlanTaskStatus.BLOCKED: EngineeringWorkflowItemStatus.BLOCKED,
    PlanTaskStatus.CANCELLED: EngineeringWorkflowItemStatus.CANCELLED,
}
_TASK_TYPES = {
    PlanTaskType.ANALYSIS: EngineeringWorkflowTaskType.ANALYSIS,
    PlanTaskType.DESIGN: EngineeringWorkflowTaskType.DESIGN,
    PlanTaskType.IMPLEMENTATION: EngineeringWorkflowTaskType.IMPLEMENTATION,
    PlanTaskType.REVIEW: EngineeringWorkflowTaskType.REVIEW,
    PlanTaskType.SIMULATION: EngineeringWorkflowTaskType.TEST,
    PlanTaskType.VERIFICATION: EngineeringWorkflowTaskType.TEST,
}
_TRANSITIONS = {
    EngineeringWorkflowStatus.DRAFT: frozenset(
        {EngineeringWorkflowStatus.PLANNED, EngineeringWorkflowStatus.CANCELLED}
    ),
    EngineeringWorkflowStatus.PLANNED: frozenset(
        {
            EngineeringWorkflowStatus.IN_PROGRESS,
            EngineeringWorkflowStatus.BLOCKED,
            EngineeringWorkflowStatus.CANCELLED,
        }
    ),
    EngineeringWorkflowStatus.IN_PROGRESS: frozenset(
        {
            EngineeringWorkflowStatus.COMPLETED,
            EngineeringWorkflowStatus.BLOCKED,
            EngineeringWorkflowStatus.CANCELLED,
        }
    ),
    EngineeringWorkflowStatus.BLOCKED: frozenset(
        {EngineeringWorkflowStatus.IN_PROGRESS, EngineeringWorkflowStatus.CANCELLED}
    ),
    EngineeringWorkflowStatus.COMPLETED: frozenset(),
    EngineeringWorkflowStatus.CANCELLED: frozenset(),
}


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _status(value: PlanPhaseStatus | PlanTaskStatus) -> EngineeringWorkflowItemStatus:
    return _STATUS_MAP[value]


def _provenance(project_id: str, source_type: str, source_id: str, source_fingerprint: str) -> PlanProvenance:
    return PlanProvenance.create(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
    )


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _conversation_id(plan: EngineeringPlan, architecture: ArchitectureDesign) -> str:
    plan_sources = tuple(
        item for item in plan.provenance if item.source_type == "REQUIREMENT_CONTEXT"
    )
    architecture_sources = tuple(
        item for item in architecture.provenance if item.source_type == "REQUIREMENT_CONTEXT"
    )
    plan_ids = {item.source_id for item in plan_sources}
    architecture_ids = {item.source_id for item in architecture_sources}
    if len(plan_ids) != 1 or len(architecture_ids) != 1 or plan_ids != architecture_ids:
        raise ValueError("workflow conversation provenance is missing or conflicting")
    conversation_id = next(iter(plan_ids))
    if any(item.project_id != plan.project_id for item in (*plan_sources, *architecture_sources)):
        raise ValueError("workflow conversation provenance project mismatch")
    return conversation_id


def _phase_inputs(
    phase_type: EngineeringWorkflowPhaseType,
    plan: EngineeringPlan,
    architecture: ArchitectureDesign,
) -> tuple[str, ...]:
    hardware_interfaces = tuple(
        item.interface_id
        for item in architecture.interfaces
        if item.interface_type is ArchitectureInterfaceType.HARDWARE
    )
    software_interfaces = tuple(
        item.interface_id
        for item in architecture.interfaces
        if item.interface_type is ArchitectureInterfaceType.SOFTWARE
    )
    flow_ids = tuple(item.flow_id for item in (*architecture.data_flow, *architecture.control_flow))
    if phase_type is EngineeringWorkflowPhaseType.REQUIREMENT_CONFIRMATION:
        return (plan.requirement_id,)
    if phase_type is EngineeringWorkflowPhaseType.SYSTEM_DESIGN:
        return _unique(
            tuple(item.component_id for item in architecture.system_components)
            + tuple(item.interface_id for item in architecture.interfaces)
            + tuple(item.decision_id for item in architecture.decisions)
        )
    if phase_type in {
        EngineeringWorkflowPhaseType.HARDWARE_DESIGN,
        EngineeringWorkflowPhaseType.PCB_DESIGN,
    }:
        return _unique(tuple(architecture.boundary.hardware_component_ids) + hardware_interfaces)
    if phase_type is EngineeringWorkflowPhaseType.FIRMWARE_DEVELOPMENT:
        return _unique(
            tuple(architecture.boundary.software_component_ids)
            + software_interfaces
            + flow_ids
        )
    if phase_type is EngineeringWorkflowPhaseType.SIMULATION:
        return flow_ids
    return tuple(item.verification_id for item in architecture.verification)


def _phase_supported(
    phase_type: EngineeringWorkflowPhaseType,
    phase,
    architecture: ArchitectureDesign,
) -> bool:
    if not (phase.tasks or phase.outputs or phase.verification):
        return False
    if phase_type in {
        EngineeringWorkflowPhaseType.HARDWARE_DESIGN,
        EngineeringWorkflowPhaseType.PCB_DESIGN,
    }:
        return bool(architecture.boundary.hardware_component_ids)
    if phase_type is EngineeringWorkflowPhaseType.FIRMWARE_DEVELOPMENT:
        return bool(architecture.boundary.software_component_ids)
    if phase_type is EngineeringWorkflowPhaseType.VERIFICATION:
        return bool(architecture.verification)
    return True


def _phase_provenance(
    plan_phase,
    plan: EngineeringPlan,
    architecture: ArchitectureDesign,
) -> tuple[PlanProvenance, ...]:
    entries = [
        _provenance(plan.project_id, "ENGINEERING_PLAN_PHASE", plan_phase.phase_id, plan_phase.fingerprint),
        _provenance(
            architecture.project_id,
            "ARCHITECTURE_DESIGN",
            architecture.architecture_id,
            architecture.fingerprint,
        ),
    ]
    return tuple(entries)


def generate_engineering_workflow(
    architecture_design: ArchitectureDesign,
    engineering_plan: EngineeringPlan,
) -> EngineeringWorkflow:
    """Project two approved design contracts into deterministic process state."""

    if type(architecture_design) is not ArchitectureDesign:
        raise TypeError("architecture_design must be ArchitectureDesign")
    if type(engineering_plan) is not EngineeringPlan:
        raise TypeError("engineering_plan must be EngineeringPlan")
    architecture = validate_copy(architecture_design, ArchitectureDesign)
    plan = validate_copy(engineering_plan, EngineeringPlan)
    if plan.status is not PlanStatus.APPROVED:
        raise ValueError("workflow requires approved engineering plan")
    if architecture.status is not ArchitectureDesignStatus.APPROVED:
        raise ValueError("workflow requires approved architecture design")
    if plan.project_id != architecture.project_id:
        raise ValueError("workflow source project mismatch")
    if architecture.plan_id != plan.plan_id or architecture.plan_fingerprint != plan.fingerprint:
        raise ValueError("workflow plan binding mismatch")
    conversation_id = _conversation_id(plan, architecture)

    workflow_id = _id(
        "workflow",
        _WORKFLOW_VERSION,
        plan.plan_id,
        plan.fingerprint,
        architecture.architecture_id,
        architecture.fingerprint,
        conversation_id,
    )
    recognized = {
        phase.phase_id: phase
        for phase in plan.phases
        if phase.phase_id in _PHASE_TYPES
    }
    for phase in recognized.values():
        if any(dependency not in {item.phase_id for item in plan.phases} for dependency in phase.dependencies):
            raise ValueError("workflow recognized phase depends on unknown phase")

    selected = {
        phase_id: phase
        for phase_id, phase in recognized.items()
        if _phase_supported(_PHASE_TYPES[phase_id], phase, architecture)
    }
    if not selected:
        raise ValueError("workflow has no safely mappable phases")
    if any(
        dependency not in selected
        for phase in selected.values()
        for dependency in phase.dependencies
    ):
        raise ValueError("workflow phase dependency cannot be preserved")

    phase_id_by_source = {
        phase_id: _id("workflow-phase", workflow_id, phase_id, phase.fingerprint)
        for phase_id, phase in selected.items()
    }
    architecture_verification_by_strategy = {}
    strategies = plan.verification_strategy or tuple(
        item for phase in plan.phases for item in phase.verification
    )
    if len(strategies) != len(architecture.verification):
        raise ValueError("workflow verification source count mismatch")
    for strategy, verification in zip(strategies, architecture.verification, strict=True):
        architecture_verification_by_strategy[strategy.strategy_id] = verification

    phase_records: list[EngineeringWorkflowPhase] = []
    for order, (source_phase_id, source_phase) in enumerate(
        ((phase_id, selected[phase_id]) for phase_id in recognized if phase_id in selected),
        start=1,
    ):
        phase_type = _PHASE_TYPES[source_phase_id]
        workflow_phase_id = phase_id_by_source[source_phase_id]
        source_task_ids = {task.task_id for task in source_phase.tasks}
        source_to_workflow_task = {
            task.task_id: _id("workflow-task", workflow_id, task.task_id, task.fingerprint)
            for task in source_phase.tasks
        }
        base_tasks = {
            task.task_id: {
                "task_id": source_to_workflow_task[task.task_id],
                "phase_id": workflow_phase_id,
                "title": task.title,
                "description": source_phase.description,
                "task_type": _TASK_TYPES[task.type],
                "status": _status(task.status),
                "dependencies": tuple(
                    source_to_workflow_task[item]
                    for item in task.dependencies
                    if item in source_task_ids
                ),
                "input_refs": _phase_inputs(phase_type, plan, architecture),
            }
            for task in source_phase.tasks
        }
        owner_task_id = source_to_workflow_task[source_phase.tasks[-1].task_id] if source_phase.tasks else None
        artifacts: list[EngineeringWorkflowArtifact] = []
        artifact_ids: list[str] = []
        if owner_task_id is not None:
            for output in source_phase.outputs:
                artifact_type = _ARTIFACT_TYPES.get(output.output_type.upper())
                if artifact_type is None:
                    continue
                artifact = EngineeringWorkflowArtifact.create(
                    artifact_id=_id("workflow-artifact", workflow_id, output.output_id, output.fingerprint),
                    task_id=owner_task_id,
                    artifact_type=artifact_type,
                    name=output.name,
                    status=EngineeringWorkflowItemStatus.PENDING,
                    source_refs=(output.output_id,),
                )
                artifacts.append(artifact)
                artifact_ids.append(artifact.artifact_id)

        verification_records: list[EngineeringWorkflowVerification] = []
        verification_ids: list[str] = []
        strategy_for_phase = tuple(source_phase.verification)
        if owner_task_id is not None:
            for strategy in strategy_for_phase:
                source_verification = architecture_verification_by_strategy.get(strategy.strategy_id)
                if source_verification is None:
                    continue
                verification = EngineeringWorkflowVerification.create(
                    verification_id=_id(
                        "workflow-verification",
                        workflow_id,
                        source_verification.verification_id,
                        source_verification.fingerprint,
                    ),
                    task_id=owner_task_id,
                    method=source_verification.method,
                    acceptance_refs=source_verification.acceptance_criterion_ids,
                    evidence_refs=source_verification.evidence_required,
                    status=EngineeringWorkflowItemStatus.PENDING,
                )
                verification_records.append(verification)
                verification_ids.append(verification.verification_id)

        tasks: list[EngineeringWorkflowTask] = []
        for source_task in source_phase.tasks:
            values = base_tasks[source_task.task_id]
            tasks.append(
                EngineeringWorkflowTask.create(
                    **values,
                    output_refs=tuple(artifact_ids if source_task.task_id == source_phase.tasks[-1].task_id else ()),
                    expected_outputs=tuple(artifact_ids if source_task.task_id == source_phase.tasks[-1].task_id else ()),
                    verification_refs=tuple(verification_ids if source_task.task_id == source_phase.tasks[-1].task_id else ()),
                    verification_requirements=tuple(verification_ids if source_task.task_id == source_phase.tasks[-1].task_id else ()),
                )
            )
        phase_records.append(
            EngineeringWorkflowPhase.create(
                phase_id=workflow_phase_id,
                workflow_id=workflow_id,
                phase_type=phase_type,
                name=source_phase.name,
                description=source_phase.description,
                order=order,
                status=_status(source_phase.status),
                tasks=tuple(tasks),
                outputs=tuple(artifacts),
                verification_refs=tuple(verification_records),
                dependencies=tuple(phase_id_by_source[item] for item in source_phase.dependencies),
            )
        )

    current_phase_id = next(
        (
            phase.phase_id
            for phase in phase_records
            if phase.status
            not in {
                EngineeringWorkflowItemStatus.COMPLETED,
                EngineeringWorkflowItemStatus.CANCELLED,
            }
        ),
        None,
    )
    provenance: list[PlanProvenance] = [*plan.provenance, *architecture.provenance]
    provenance.extend(
        (
            _provenance(plan.project_id, "ENGINEERING_PLAN", plan.plan_id, plan.fingerprint),
            _provenance(
                architecture.project_id,
                "ARCHITECTURE_DESIGN",
                architecture.architecture_id,
                architecture.fingerprint,
            ),
        )
    )
    deduplicated_provenance = tuple(
        {
            (item.project_id, item.source_type, item.source_id, item.source_fingerprint): item
            for item in provenance
        }.values()
    )
    return EngineeringWorkflow.create(
        workflow_id=workflow_id,
        project_id=plan.project_id,
        conversation_id=conversation_id,
        plan_id=plan.plan_id,
        architecture_id=architecture.architecture_id,
        title=architecture.title,
        status=EngineeringWorkflowStatus.PLANNED,
        phases=tuple(phase_records),
        current_phase_id=current_phase_id,
        workflow_version=_WORKFLOW_VERSION,
        provenance=deduplicated_provenance,
    )


def transition_workflow_status(
    workflow: EngineeringWorkflow,
    target_status: EngineeringWorkflowStatus,
) -> EngineeringWorkflow:
    if type(workflow) is not EngineeringWorkflow:
        raise TypeError("workflow must be EngineeringWorkflow")
    if not isinstance(target_status, EngineeringWorkflowStatus):
        raise TypeError("target_status must be EngineeringWorkflowStatus")
    checked = validate_copy(workflow, EngineeringWorkflow)
    if target_status not in _TRANSITIONS[checked.status]:
        raise ValueError("invalid workflow status transition")
    if target_status is EngineeringWorkflowStatus.COMPLETED and any(
        phase.status is not EngineeringWorkflowItemStatus.COMPLETED for phase in checked.phases
    ):
        raise ValueError("workflow cannot be completed before all phases are completed")
    current_phase_id = (
        None
        if target_status in {EngineeringWorkflowStatus.COMPLETED, EngineeringWorkflowStatus.CANCELLED}
        else next(
            (
                phase.phase_id
                for phase in checked.phases
                if phase.status
                not in {
                    EngineeringWorkflowItemStatus.COMPLETED,
                    EngineeringWorkflowItemStatus.CANCELLED,
                }
            ),
            None,
        )
    )
    updated = checked.model_copy(
        update={
            "status": target_status,
            "current_phase_id": current_phase_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    return EngineeringWorkflow.model_validate(deepcopy(updated.model_dump(mode="python")), strict=False)


__all__ = ("generate_engineering_workflow", "transition_workflow_status")
