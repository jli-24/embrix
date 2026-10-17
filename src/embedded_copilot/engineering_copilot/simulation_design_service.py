from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    EngineeringWorkflow,
    EngineeringWorkflowPhaseType,
    FirmwareSpecification,
    HardwareDesignSpecification,
    HardwareInterfaceType,
    PlanProvenance,
    SimulationDesignIntent,
    SimulationEvidenceRequirement,
    SimulationEvidenceType,
    SimulationModel,
    SimulationScenario,
    SimulationScenarioType,
    SimulationSpecification,
    SimulationStatus,
    SimulationTargetType,
    _simulation_revision,
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


def _scenario_type(interface_type: HardwareInterfaceType) -> SimulationScenarioType:
    if interface_type is HardwareInterfaceType.CONTROL:
        return SimulationScenarioType.CONTROL_TEST
    if interface_type in {HardwareInterfaceType.COMMUNICATION, HardwareInterfaceType.BUS}:
        return SimulationScenarioType.COMMUNICATION_TEST
    return SimulationScenarioType.INTERFACE_TEST


def _design_intent(scenarios: tuple[SimulationScenario, ...]) -> SimulationDesignIntent:
    scenario_types = {scenario.scenario_type for scenario in scenarios}
    if SimulationScenarioType.CONTROL_TEST in scenario_types:
        return SimulationDesignIntent.CONTROL_VALIDATION
    if scenario_types & {
        SimulationScenarioType.INTERFACE_TEST,
        SimulationScenarioType.COMMUNICATION_TEST,
    }:
        return SimulationDesignIntent.INTERFACE_VALIDATION
    return SimulationDesignIntent.UNKNOWN


def generate_simulation_spec(
    hardware_design_spec: HardwareDesignSpecification,
    firmware_spec: FirmwareSpecification,
    engineering_workflow: EngineeringWorkflow,
) -> SimulationSpecification:
    """Project approved hardware and firmware planning state into simulation planning state."""

    if type(hardware_design_spec) is not HardwareDesignSpecification:
        raise TypeError("hardware_design_spec must be HardwareDesignSpecification")
    if type(firmware_spec) is not FirmwareSpecification:
        raise TypeError("firmware_spec must be FirmwareSpecification")
    if type(engineering_workflow) is not EngineeringWorkflow:
        raise TypeError("engineering_workflow must be EngineeringWorkflow")
    hardware = validate_copy(hardware_design_spec, HardwareDesignSpecification)
    firmware = validate_copy(firmware_spec, FirmwareSpecification)
    workflow = validate_copy(engineering_workflow, EngineeringWorkflow)

    if hardware.project_id != firmware.project_id or hardware.project_id != workflow.project_id:
        raise ValueError("simulation design source project mismatch")
    if hardware.architecture_id != workflow.architecture_id or firmware.architecture_id != hardware.architecture_id:
        raise ValueError("simulation design architecture binding mismatch")
    if hardware.workflow_id != workflow.workflow_id or firmware.workflow_id != workflow.workflow_id:
        raise ValueError("simulation design workflow binding mismatch")
    if firmware.hardware_design_id != hardware.hardware_spec_id:
        raise ValueError("simulation design hardware binding mismatch")
    if firmware.conversation_id != workflow.conversation_id:
        raise ValueError("simulation design conversation binding mismatch")
    if hardware.workflow_fingerprint != workflow.fingerprint:
        raise ValueError("simulation design hardware workflow fingerprint mismatch")
    if firmware.hardware_design_fingerprint != hardware.fingerprint:
        raise ValueError("simulation design firmware hardware fingerprint mismatch")
    if firmware.workflow_fingerprint != workflow.fingerprint:
        raise ValueError("simulation design firmware workflow fingerprint mismatch")
    hardware_interface_ids = {interface.interface_id for interface in hardware.interfaces}
    if any(binding.hardware_interface_id not in hardware_interface_ids for binding in firmware.bindings):
        raise ValueError("simulation design firmware interface reference mismatch")

    workflow_architecture_sources = tuple(
        item for item in workflow.provenance
        if item.source_type == "ARCHITECTURE_DESIGN" and item.source_id == hardware.architecture_id
    )
    hardware_architecture_sources = tuple(
        item for item in hardware.provenance
        if item.source_type == "ARCHITECTURE_DESIGN" and item.source_id == hardware.architecture_id
    )
    if (
        len(workflow_architecture_sources) != 1
        or len(hardware_architecture_sources) != 1
        or workflow_architecture_sources[0].source_fingerprint
        != hardware_architecture_sources[0].source_fingerprint
    ):
        raise ValueError("simulation design architecture provenance mismatch")

    spec_id = _id(
        "simulation-spec",
        hardware.hardware_spec_id,
        hardware.fingerprint,
        firmware.firmware_spec_id,
        firmware.fingerprint,
        workflow.workflow_id,
        workflow.fingerprint,
        workflow.conversation_id,
    )
    model_id_by_domain = {
        domain.domain_id: _id("simulation-model", spec_id, "hardware", domain.domain_id)
        for domain in hardware.hardware_domains
        if domain.type.value in {
            "CONTROLLER", "SENSOR", "ACTUATOR", "COMMUNICATION",
        }
    }
    model_id_by_module = {
        module.module_id: _id("simulation-model", spec_id, "firmware", module.module_id)
        for module in firmware.modules
    }
    models = tuple(
        SimulationModel.create(
            model_id=model_id_by_domain[domain.domain_id],
            simulation_spec_id=spec_id,
            name=domain.name,
            model_type=SimulationTargetType(domain.type.value),
            source_reference=domain.domain_id,
            parameters=(),
            interfaces=domain.interfaces,
            evidence_refs=(),
        )
        for domain in hardware.hardware_domains
        if domain.domain_id in model_id_by_domain
    ) + tuple(
        SimulationModel.create(
            model_id=model_id_by_module[module.module_id],
            simulation_spec_id=spec_id,
            name=module.name,
            model_type=SimulationTargetType.FIRMWARE_MODULE,
            source_reference=module.module_id,
            parameters=(),
            interfaces=module.hardware_bindings,
            evidence_refs=(),
        )
        for module in firmware.modules
    )

    bindings_by_interface: dict[str, list[tuple[str, str]]] = {}
    for binding in firmware.bindings:
        bindings_by_interface.setdefault(binding.hardware_interface_id, []).append(
            (binding.firmware_module_id, binding.binding_id)
        )
    verifications_by_module: dict[str, tuple] = {}
    for verification in firmware.verification_requirements:
        verifications_by_module.setdefault(verification.target_module, ())
        verifications_by_module[verification.target_module] = (
            *verifications_by_module[verification.target_module],
            verification,
        )
    domain_names = {domain.domain_id: domain.name for domain in hardware.hardware_domains}

    scenarios: list[SimulationScenario] = []
    evidence_requirements: list[SimulationEvidenceRequirement] = []
    for interface in hardware.interfaces:
        if interface.interface_type is HardwareInterfaceType.UNKNOWN:
            continue
        bindings = bindings_by_interface.get(interface.interface_id, [])
        related_module_ids = _unique(tuple(item[0] for item in bindings))
        related_verifications = tuple(
            verification
            for module_id in related_module_ids
            for verification in verifications_by_module.get(module_id, ())
        )
        if not related_verifications:
            continue
        scenario_type = _scenario_type(interface.interface_type)
        related_models = _unique(
            (
                model_id_by_domain.get(interface.source, ""),
                model_id_by_domain.get(interface.target, ""),
                *(model_id_by_module.get(module_id, "") for module_id in related_module_ids),
            )
        )
        title = (
            f"{domain_names.get(interface.source, 'Source domain')} to "
            f"{domain_names.get(interface.target, 'Target domain')} "
            f"{scenario_type.value.replace('_', ' ').title()}"
        )
        acceptance_refs = _unique(
            tuple(ref for verification in related_verifications for ref in verification.acceptance_refs)
        )
        expected_outputs = _unique(
            (
                *acceptance_refs,
                *(verification.method for verification in related_verifications),
            )
        )
        scenario_id = _id("simulation-scenario", spec_id, interface.interface_id)
        scenario = SimulationScenario.create(
            scenario_id=scenario_id,
            simulation_spec_id=spec_id,
            title=title,
            scenario_type=scenario_type,
            inputs=(interface.interface_id,),
            expected_outputs=expected_outputs,
            related_models=related_models,
            acceptance_refs=acceptance_refs,
        )
        scenarios.append(scenario)
        evidence_requirements.append(
            SimulationEvidenceRequirement.create(
                evidence_id=_id("simulation-evidence", spec_id, scenario_id),
                simulation_spec_id=spec_id,
                scenario_id=scenario_id,
                evidence_type=SimulationEvidenceType.UNKNOWN,
                description=f"Pending evidence for {scenario.title}",
                acceptance_refs=acceptance_refs,
            )
        )

    simulation_phases = tuple(
        phase for phase in workflow.phases
        if phase.phase_type is EngineeringWorkflowPhaseType.SIMULATION
    )
    verification_refs = _unique(
        (
            *(item.verification_id for item in firmware.verification_requirements),
            *(verification.verification_id for phase in simulation_phases for verification in phase.verification_refs),
        )
    )
    source_refs = _unique(
        (
            *hardware.source_refs,
            *firmware.source_refs,
            workflow.workflow_id,
            *(domain.domain_id for domain in hardware.hardware_domains),
            *(interface.interface_id for interface in hardware.interfaces),
            *(module.module_id for module in firmware.modules),
            *(phase.phase_id for phase in simulation_phases),
            *(task.task_id for phase in simulation_phases for task in phase.tasks),
            *verification_refs,
        )
    )
    provenance = _deduplicate_provenance(
        (
            *hardware.provenance,
            *firmware.provenance,
            *workflow.provenance,
            _provenance(
                hardware.project_id,
                "HARDWARE_DESIGN_SPECIFICATION",
                hardware.hardware_spec_id,
                hardware.fingerprint,
            ),
            _provenance(
                firmware.project_id,
                "FIRMWARE_SPECIFICATION",
                firmware.firmware_spec_id,
                firmware.fingerprint,
            ),
            _provenance(
                workflow.project_id,
                "ENGINEERING_WORKFLOW",
                workflow.workflow_id,
                workflow.fingerprint,
            ),
        )
    )
    return SimulationSpecification.create(
        simulation_spec_id=spec_id,
        project_id=hardware.project_id,
        conversation_id=firmware.conversation_id,
        hardware_design_id=hardware.hardware_spec_id,
        firmware_spec_id=firmware.firmware_spec_id,
        workflow_id=workflow.workflow_id,
        hardware_design_fingerprint=hardware.fingerprint,
        firmware_spec_fingerprint=firmware.fingerprint,
        workflow_fingerprint=workflow.fingerprint,
        title=f"{firmware.title} Simulation Design Planning",
        design_intent=_design_intent(tuple(scenarios)),
        status=SimulationStatus.PLANNED,
        targets=tuple(model.model_id for model in models),
        models=models,
        scenarios=tuple(scenarios),
        evidence_requirements=tuple(evidence_requirements),
        verification_refs=verification_refs,
        source_refs=source_refs,
        provenance=provenance,
        design_revision=_simulation_revision(
            hardware.hardware_spec_id,
            hardware.fingerprint,
            firmware.firmware_spec_id,
            firmware.fingerprint,
            workflow.workflow_id,
            workflow.fingerprint,
        ),
    )


__all__ = ("generate_simulation_spec",)
