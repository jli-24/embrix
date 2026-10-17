from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    EngineeringWorkflow,
    ExpectedArtifact,
    FirmwareModuleType,
    FirmwareSpecification,
    HardwareDesignSpecification,
    ImplementationArtifactType,
    ImplementationIntent,
    ImplementationSpecification,
    ImplementationStatus,
    ImplementationTask,
    ImplementationTaskType,
    PlanProvenance,
    SimulationScenario,
    SimulationSpecification,
    VerificationCase,
    VerificationSpecification,
    _implementation_revision,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _provenance(
    project_id: str,
    source_type: str,
    source_id: str,
    source_fingerprint: str,
) -> PlanProvenance:
    return PlanProvenance.create(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
    )


def _deduplicate_provenance(values: tuple[PlanProvenance, ...]) -> tuple[PlanProvenance, ...]:
    result: list[PlanProvenance] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in values:
        key = (item.project_id, item.source_type, item.source_id, item.source_fingerprint)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return tuple(result)


def _source_matches(
    provenance: tuple[PlanProvenance, ...],
    source_type: str,
    source_id: str,
    source_fingerprint: str,
) -> bool:
    matches = tuple(
        item for item in provenance
        if item.source_type == source_type and item.source_id == source_id
    )
    return bool(matches) and all(item.source_fingerprint == source_fingerprint for item in matches)


def _verify_provenance(
    hardware: HardwareDesignSpecification,
    firmware: FirmwareSpecification,
    simulation: SimulationSpecification,
    verification: VerificationSpecification,
    workflow: EngineeringWorkflow,
) -> None:
    all_provenance = (
        *hardware.provenance,
        *firmware.provenance,
        *simulation.provenance,
        *verification.provenance,
        *workflow.provenance,
    )
    expected = (
        ("HARDWARE_DESIGN_SPECIFICATION", hardware.hardware_spec_id, hardware.fingerprint),
        ("FIRMWARE_SPECIFICATION", firmware.firmware_spec_id, firmware.fingerprint),
        ("SIMULATION_SPECIFICATION", simulation.simulation_spec_id, simulation.fingerprint),
        ("ENGINEERING_WORKFLOW", workflow.workflow_id, workflow.fingerprint),
    )
    for source_type, source_id, source_fingerprint in expected:
        if not _source_matches(all_provenance, source_type, source_id, source_fingerprint):
            raise ValueError("implementation source provenance mismatch")
    architecture_sources = tuple(
        item for item in all_provenance if item.source_type == "ARCHITECTURE_DESIGN"
    )
    workflow_architecture = tuple(
        item for item in workflow.provenance if item.source_type == "ARCHITECTURE_DESIGN"
    )
    if len(workflow_architecture) != 1 or not architecture_sources or any(
        item.source_id != workflow_architecture[0].source_id
        or item.source_fingerprint != workflow_architecture[0].source_fingerprint
        for item in architecture_sources
    ):
        raise ValueError("implementation architecture provenance mismatch")


def _case_refs(
    source_id: str,
    cases: tuple[VerificationCase, ...],
    mappings: tuple,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched = tuple(
        case for case in cases
        if case.target == source_id or source_id in case.related_design_refs
    )
    case_ids = tuple(case.case_id for case in matched)
    acceptance = _unique(
        tuple(
            ref
            for mapping in mappings
            if mapping.case_id in case_ids
            for ref in mapping.acceptance_refs
        )
    )
    return case_ids, acceptance


def _module_intent(module_type: FirmwareModuleType) -> ImplementationIntent:
    if module_type is FirmwareModuleType.CONTROL:
        return ImplementationIntent.CONTROL_IMPLEMENTATION
    if module_type in {FirmwareModuleType.DRIVER, FirmwareModuleType.HAL}:
        return ImplementationIntent.DRIVER_DEVELOPMENT
    if module_type is FirmwareModuleType.COMMUNICATION:
        return ImplementationIntent.INTERFACE_ADAPTATION
    return ImplementationIntent.MODULE_IMPLEMENTATION


def _interface_description(source: str, target: str, direction: str, constraints: tuple[str, ...]) -> str:
    return " | ".join((*constraints, f"source={source}", f"target={target}", f"direction={direction}"))


def _scenario_description(scenario: SimulationScenario) -> str:
    values = (*scenario.inputs, *scenario.expected_outputs)
    return " | ".join(values) if values else scenario.title


def generate_implementation_spec(
    hardware_design_spec: HardwareDesignSpecification,
    firmware_spec: FirmwareSpecification,
    simulation_spec: SimulationSpecification,
    verification_spec: VerificationSpecification,
    engineering_workflow: EngineeringWorkflow,
) -> ImplementationSpecification:
    """Project approved planning contracts into a downstream implementation plan."""

    expected_types = (
        (hardware_design_spec, HardwareDesignSpecification, "HardwareDesignSpecification"),
        (firmware_spec, FirmwareSpecification, "FirmwareSpecification"),
        (simulation_spec, SimulationSpecification, "SimulationSpecification"),
        (verification_spec, VerificationSpecification, "VerificationSpecification"),
        (engineering_workflow, EngineeringWorkflow, "EngineeringWorkflow"),
    )
    for value, expected_type, label in expected_types:
        if type(value) is not expected_type:
            raise TypeError(f"{label} input is required")

    hardware = validate_copy(hardware_design_spec, HardwareDesignSpecification)
    firmware = validate_copy(firmware_spec, FirmwareSpecification)
    simulation = validate_copy(simulation_spec, SimulationSpecification)
    verification = validate_copy(verification_spec, VerificationSpecification)
    workflow = validate_copy(engineering_workflow, EngineeringWorkflow)

    if len({hardware.project_id, firmware.project_id, simulation.project_id, verification.project_id, workflow.project_id}) != 1:
        raise ValueError("implementation source project mismatch")
    if len({hardware.architecture_id, firmware.architecture_id, workflow.architecture_id}) != 1:
        raise ValueError("implementation architecture binding mismatch")
    if hardware.workflow_id != workflow.workflow_id or firmware.workflow_id != workflow.workflow_id:
        raise ValueError("implementation workflow binding mismatch")
    if firmware.hardware_design_id != hardware.hardware_spec_id:
        raise ValueError("implementation firmware hardware binding mismatch")
    if simulation.hardware_design_id != hardware.hardware_spec_id or simulation.firmware_spec_id != firmware.firmware_spec_id:
        raise ValueError("implementation simulation source binding mismatch")
    if simulation.workflow_id != workflow.workflow_id:
        raise ValueError("implementation simulation workflow binding mismatch")
    if verification.hardware_design_id != hardware.hardware_spec_id or verification.firmware_spec_id != firmware.firmware_spec_id:
        raise ValueError("implementation verification source binding mismatch")
    if verification.simulation_spec_id != simulation.simulation_spec_id or verification.workflow_id != workflow.workflow_id:
        raise ValueError("implementation verification workflow binding mismatch")
    if len({firmware.conversation_id, simulation.conversation_id, verification.conversation_id, workflow.conversation_id}) != 1:
        raise ValueError("implementation conversation binding mismatch")
    if firmware.hardware_design_fingerprint != hardware.fingerprint:
        raise ValueError("implementation hardware fingerprint mismatch")
    if (
        simulation.hardware_design_fingerprint != hardware.fingerprint
        or simulation.firmware_spec_fingerprint != firmware.fingerprint
        or verification.hardware_design_fingerprint != hardware.fingerprint
        or verification.firmware_spec_fingerprint != firmware.fingerprint
        or verification.simulation_spec_fingerprint != simulation.fingerprint
        or firmware.workflow_fingerprint != workflow.fingerprint
        or simulation.workflow_fingerprint != workflow.fingerprint
        or verification.workflow_fingerprint != workflow.fingerprint
    ):
        raise ValueError("implementation source fingerprint mismatch")
    _verify_provenance(hardware, firmware, simulation, verification, workflow)

    design_revision = _implementation_revision(
        hardware.hardware_spec_id,
        hardware.fingerprint,
        firmware.firmware_spec_id,
        firmware.fingerprint,
        simulation.simulation_spec_id,
        simulation.fingerprint,
        verification.verification_id,
        verification.fingerprint,
        workflow.workflow_id,
        workflow.fingerprint,
    )
    implementation_id = _id(
        "implementation",
        hardware.hardware_spec_id,
        firmware.firmware_spec_id,
        simulation.simulation_spec_id,
        verification.verification_id,
        workflow.workflow_id,
        design_revision,
    )
    cases = verification.cases
    mappings = verification.acceptance_mapping
    tasks: list[ImplementationTask] = []
    artifacts: list[ExpectedArtifact] = []
    module_task_ids: dict[str, str] = {}

    for module in firmware.modules:
        task_id = _id("implementation-task", implementation_id, "firmware-module", module.module_id)
        module_task_ids[module.module_id] = task_id

    def add_task(
        *,
        task_id: str,
        title: str,
        task_type: ImplementationTaskType,
        intent: ImplementationIntent,
        description: str,
        source_refs: tuple[str, ...],
        dependencies: tuple[str, ...],
        verification_refs: tuple[str, ...],
        acceptance_refs: tuple[str, ...],
        artifact_type: ImplementationArtifactType,
        artifact_name: str,
        artifact_description: str,
    ) -> None:
        artifact_id = _id("expected-artifact", implementation_id, task_id)
        artifacts.append(
            ExpectedArtifact.create(
                artifact_id=artifact_id,
                implementation_id=implementation_id,
                artifact_type=artifact_type,
                name=artifact_name,
                description=artifact_description,
                source_refs=source_refs,
                verification_refs=verification_refs,
            )
        )
        tasks.append(
            ImplementationTask.create(
                task_id=task_id,
                implementation_id=implementation_id,
                title=title,
                task_type=task_type,
                implementation_intent=intent,
                description=description,
                source_refs=source_refs,
                dependencies=dependencies,
                expected_artifacts=(artifact_id,),
                verification_refs=verification_refs,
                acceptance_refs=acceptance_refs,
                status=ImplementationStatus.PLANNED,
            )
        )

    for module in firmware.modules:
        verification_refs, acceptance_refs = _case_refs(module.module_id, cases, mappings)
        source_refs = _unique((module.module_id, *module.source_refs))
        add_task(
            task_id=module_task_ids[module.module_id],
            title=f"Implement {module.name}",
            task_type=ImplementationTaskType.FIRMWARE_MODULE,
            intent=_module_intent(module.module_type),
            description=module.responsibility,
            source_refs=source_refs,
            dependencies=tuple(
                module_task_ids[dependency]
                for dependency in module.dependencies
                if dependency in module_task_ids
            ),
            verification_refs=verification_refs,
            acceptance_refs=acceptance_refs,
            artifact_type=ImplementationArtifactType.SOURCE_CODE,
            artifact_name=f"{module.name} Firmware Module",
            artifact_description=module.responsibility,
        )

    for interface in hardware.interfaces:
        verification_refs, acceptance_refs = _case_refs(interface.interface_id, cases, mappings)
        source_refs = _unique((interface.interface_id, *interface.signal_refs, *interface.evidence_refs))
        description = _interface_description(interface.source, interface.target, interface.direction, interface.constraints)
        add_task(
            task_id=_id("implementation-task", implementation_id, "hardware-interface", interface.interface_id),
            title=f"Implement {interface.interface_id} Interface",
            task_type=ImplementationTaskType.INTERFACE_IMPLEMENTATION,
            intent=ImplementationIntent.INTERFACE_ADAPTATION,
            description=description,
            source_refs=source_refs,
            dependencies=(),
            verification_refs=verification_refs,
            acceptance_refs=acceptance_refs,
            artifact_type=ImplementationArtifactType.CONFIGURATION,
            artifact_name=f"{interface.interface_id} Interface Configuration",
            artifact_description=description,
        )

    # The hardware layer already carries a typed schematic design plan. Project
    # that fact into the existing Implementation contract so schematic
    # production can consume a normal implementation task/artifact instead of
    # introducing a parallel planning model. No electrical facts are created
    # here; the upstream readiness gate remains authoritative.
    if hardware.schematic_plan is not None:
        schematic_plan = hardware.schematic_plan
        schematic_verification_refs = tuple(
            reference for reference in schematic_plan.verification_refs
            if reference in {case.case_id for case in cases}
        )
        add_task(
            task_id=_id("implementation-task", implementation_id, "schematic-production", schematic_plan.schematic_plan_id),
            title="Produce KiCad schematic artifact",
            task_type=ImplementationTaskType.HARDWARE_CONFIGURATION,
            intent=ImplementationIntent.CONFIGURATION_SETUP,
            description="Produce a KiCad schematic artifact from approved structured hardware facts.",
            source_refs=_unique((schematic_plan.schematic_plan_id, hardware.hardware_spec_id, *schematic_plan.verification_refs)),
            dependencies=(),
            verification_refs=schematic_verification_refs,
            acceptance_refs=(),
            artifact_type=ImplementationArtifactType.SCHEMATIC,
            artifact_name="KiCad Schematic Artifact",
            artifact_description="Structured schematic artifact projected from approved hardware facts.",
        )

    for scenario in simulation.scenarios:
        verification_refs, acceptance_refs = _case_refs(scenario.scenario_id, cases, mappings)
        source_refs = _unique((scenario.scenario_id, *scenario.related_models))
        description = _scenario_description(scenario)
        add_task(
            task_id=_id("implementation-task", implementation_id, "simulation-scenario", scenario.scenario_id),
            title=f"Prepare {scenario.title}",
            task_type=ImplementationTaskType.TEST_PREPARATION,
            intent=ImplementationIntent.TEST_PREPARATION,
            description=description,
            source_refs=source_refs,
            dependencies=(),
            verification_refs=verification_refs,
            acceptance_refs=acceptance_refs,
            artifact_type=ImplementationArtifactType.SIMULATION_ASSET,
            artifact_name=f"{scenario.title} Simulation Asset",
            artifact_description=description,
        )

    verification_refs = tuple(case.case_id for case in cases)
    dependencies = _unique(tuple(dependency for task in tasks for dependency in task.dependencies))
    provenance = _deduplicate_provenance(
        (
            *hardware.provenance,
            *firmware.provenance,
            *simulation.provenance,
            *verification.provenance,
            *workflow.provenance,
            _provenance(hardware.project_id, "HARDWARE_DESIGN_SPECIFICATION", hardware.hardware_spec_id, hardware.fingerprint),
            _provenance(firmware.project_id, "FIRMWARE_SPECIFICATION", firmware.firmware_spec_id, firmware.fingerprint),
            _provenance(simulation.project_id, "SIMULATION_SPECIFICATION", simulation.simulation_spec_id, simulation.fingerprint),
            _provenance(verification.project_id, "VERIFICATION_SPECIFICATION", verification.verification_id, verification.fingerprint),
            _provenance(workflow.project_id, "ENGINEERING_WORKFLOW", workflow.workflow_id, workflow.fingerprint),
        )
    )
    return ImplementationSpecification.create(
        implementation_id=implementation_id,
        project_id=hardware.project_id,
        conversation_id=workflow.conversation_id,
        hardware_design_id=hardware.hardware_spec_id,
        firmware_spec_id=firmware.firmware_spec_id,
        simulation_spec_id=simulation.simulation_spec_id,
        verification_id=verification.verification_id,
        workflow_id=workflow.workflow_id,
        hardware_design_fingerprint=hardware.fingerprint,
        firmware_spec_fingerprint=firmware.fingerprint,
        simulation_spec_fingerprint=simulation.fingerprint,
        verification_fingerprint=verification.fingerprint,
        workflow_fingerprint=workflow.fingerprint,
        design_revision=design_revision,
        tasks=tuple(tasks),
        expected_artifacts=tuple(artifacts),
        dependencies=dependencies,
        verification_refs=verification_refs,
        status=ImplementationStatus.PLANNED,
        provenance=provenance,
    )


__all__ = ("generate_implementation_spec",)
