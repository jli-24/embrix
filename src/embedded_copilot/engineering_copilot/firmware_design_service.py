from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    EngineeringWorkflow,
    EngineeringWorkflowPhaseType,
    FirmwareInterfaceRole,
    FirmwareModule,
    FirmwareModuleType,
    FirmwareSpecification,
    FirmwareTask,
    FirmwareTaskStatus,
    FirmwareTaskType,
    FirmwareVerificationRequirement,
    HardwareDesignSpecification,
    HardwareFirmwareBinding,
    PlanProvenance,
    _firmware_revision,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _module_name(domain_name: str, module_type: FirmwareModuleType) -> str:
    if module_type is FirmwareModuleType.APPLICATION:
        return "Application"
    if module_type is FirmwareModuleType.DRIVER:
        return "Sensor Acquisition"
    if module_type is FirmwareModuleType.CONTROL:
        return "Motor Control" if "motor" in domain_name.casefold() else "Actuator Control"
    return "Communication"


def _module_type(domain_type: str) -> FirmwareModuleType | None:
    return {
        "CONTROLLER": FirmwareModuleType.APPLICATION,
        "SENSOR": FirmwareModuleType.DRIVER,
        "ACTUATOR": FirmwareModuleType.CONTROL,
        "COMMUNICATION": FirmwareModuleType.COMMUNICATION,
    }.get(domain_type)


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


def generate_firmware_spec(
    hardware_design_spec: HardwareDesignSpecification,
    engineering_workflow: EngineeringWorkflow,
) -> FirmwareSpecification:
    """Project approved hardware planning state into firmware planning state."""

    if type(hardware_design_spec) is not HardwareDesignSpecification:
        raise TypeError("hardware_design_spec must be HardwareDesignSpecification")
    if type(engineering_workflow) is not EngineeringWorkflow:
        raise TypeError("engineering_workflow must be EngineeringWorkflow")
    hardware = validate_copy(hardware_design_spec, HardwareDesignSpecification)
    workflow = validate_copy(engineering_workflow, EngineeringWorkflow)
    if hardware.project_id != workflow.project_id:
        raise ValueError("firmware design source project mismatch")
    if hardware.architecture_id != workflow.architecture_id:
        raise ValueError("firmware design architecture binding mismatch")
    if hardware.workflow_id != workflow.workflow_id:
        raise ValueError("firmware design workflow binding mismatch")
    if hardware.workflow_fingerprint != workflow.fingerprint:
        raise ValueError("firmware design workflow fingerprint mismatch")
    architecture_sources = tuple(
        item for item in workflow.provenance
        if item.source_type == "ARCHITECTURE_DESIGN"
        and item.source_id == hardware.architecture_id
    )
    if (
        len(architecture_sources) != 1
        or not any(
            item.source_type == "ARCHITECTURE_DESIGN"
            and item.source_id == hardware.architecture_id
            and item.source_fingerprint == architecture_sources[0].source_fingerprint
            for item in hardware.provenance
        )
    ):
        raise ValueError("firmware design architecture provenance mismatch")

    spec_id = _id(
        "firmware-spec",
        hardware.hardware_spec_id,
        hardware.fingerprint,
        workflow.workflow_id,
        workflow.fingerprint,
        workflow.conversation_id,
    )
    firmware_domains = tuple(
        domain
        for domain in hardware.hardware_domains
        if _module_type(domain.type.value) is not None
    )
    module_id_by_domain = {
        domain.domain_id: _id("firmware-module", spec_id, domain.domain_id)
        for domain in firmware_domains
    }
    module_type_by_domain = {
        domain.domain_id: _module_type(domain.type.value)
        for domain in firmware_domains
    }

    bindings_by_module: dict[str, list[str]] = {module_id: [] for module_id in module_id_by_domain.values()}
    bindings: list[HardwareFirmwareBinding] = []
    seen_bindings: set[tuple[str, str]] = set()
    for interface in hardware.interfaces:
        for domain_id in (interface.source, interface.target):
            module_id = module_id_by_domain.get(domain_id)
            if module_id is None or (interface.interface_id, module_id) in seen_bindings:
                continue
            seen_bindings.add((interface.interface_id, module_id))
            binding = HardwareFirmwareBinding.create(
                binding_id=_id("firmware-binding", spec_id, interface.interface_id, module_id),
                hardware_interface_id=interface.interface_id,
                firmware_module_id=module_id,
                interface_role=FirmwareInterfaceRole.UNKNOWN,
                direction=interface.direction,
                signal_refs=interface.signal_refs,
                evidence_refs=interface.evidence_refs,
            )
            bindings.append(binding)
            bindings_by_module[module_id].append(binding.binding_id)

    firmware_phases = tuple(
        phase for phase in workflow.phases
        if phase.phase_type is EngineeringWorkflowPhaseType.FIRMWARE_DEVELOPMENT
    )
    verifications_by_module: dict[str, list[str]] = {module_id: [] for module_id in module_id_by_domain.values()}
    verification_requirements: list[FirmwareVerificationRequirement] = []
    for module_id in module_id_by_domain.values():
        for phase in firmware_phases:
            for source in phase.verification_refs:
                verification = FirmwareVerificationRequirement.create(
                    verification_id=_id("firmware-verification", spec_id, module_id, source.verification_id),
                    target_module=module_id,
                    method=source.method,
                    acceptance_refs=source.acceptance_refs,
                    evidence_requirements=source.evidence_refs,
                    simulation_refs=(),
                )
                verification_requirements.append(verification)
                verifications_by_module[module_id].append(verification.verification_id)

    modules: list[FirmwareModule] = []
    tasks: list[FirmwareTask] = []
    for domain in firmware_domains:
        module_id = module_id_by_domain[domain.domain_id]
        module_type = module_type_by_domain[domain.domain_id]
        if module_type is None:
            continue
        responsibility = "; ".join(domain.requirements) or domain.name
        module = FirmwareModule.create(
            module_id=module_id,
            firmware_spec_id=spec_id,
            name=_module_name(domain.name, module_type),
            module_type=module_type,
            responsibility=responsibility,
            dependencies=(),
            hardware_bindings=tuple(bindings_by_module[module_id]),
            verification_refs=tuple(verifications_by_module[module_id]),
            source_refs=_unique((domain.domain_id, *hardware.source_refs)),
        )
        modules.append(module)
        tasks.append(
            FirmwareTask.create(
                task_id=_id("firmware-task", spec_id, module_id),
                firmware_spec_id=spec_id,
                module_id=module_id,
                title=f"Design {module.name} module",
                task_type=FirmwareTaskType.DESIGN,
                dependencies=(),
                expected_outputs=(module.name,),
                verification_refs=tuple(verifications_by_module[module_id]),
                status=FirmwareTaskStatus.PLANNED,
            )
        )

    phase_refs = tuple(
        ref
        for phase in firmware_phases
        for ref in (
            phase.phase_id,
            *(task.task_id for task in phase.tasks),
            *(verification.verification_id for verification in phase.verification_refs),
        )
    )
    provenance = _deduplicate_provenance(
        (
            *hardware.provenance,
            *workflow.provenance,
            _provenance(
                hardware.project_id,
                "HARDWARE_DESIGN_SPECIFICATION",
                hardware.hardware_spec_id,
                hardware.fingerprint,
            ),
            _provenance(
                workflow.project_id,
                "ENGINEERING_WORKFLOW",
                workflow.workflow_id,
                workflow.fingerprint,
            ),
        )
    )
    return FirmwareSpecification.create(
        firmware_spec_id=spec_id,
        project_id=hardware.project_id,
        conversation_id=workflow.conversation_id,
        hardware_design_id=hardware.hardware_spec_id,
        architecture_id=hardware.architecture_id,
        workflow_id=workflow.workflow_id,
        hardware_design_fingerprint=hardware.fingerprint,
        workflow_fingerprint=workflow.fingerprint,
        title=f"{hardware.title} Firmware Design Planning",
        requirements=_unique(
            (*hardware.system_requirements, *(item for domain in hardware.hardware_domains for item in domain.requirements))
        ),
        runtime_constraints=hardware.constraints,
        software_interfaces=tuple(interface.interface_id for interface in hardware.interfaces),
        modules=tuple(modules),
        bindings=tuple(bindings),
        tasks=tuple(tasks),
        verification_requirements=tuple(verification_requirements),
        source_refs=_unique((*hardware.source_refs, *(domain.domain_id for domain in hardware.hardware_domains), *(interface.interface_id for interface in hardware.interfaces), *phase_refs)),
        provenance=provenance,
        design_revision=_firmware_revision(
            hardware.hardware_spec_id,
            hardware.fingerprint,
            workflow.workflow_id,
            workflow.fingerprint,
        ),
    )


__all__ = ("generate_firmware_spec",)
