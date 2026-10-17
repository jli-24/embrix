from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    ArchitectureComponent,
    ArchitectureDecisionType,
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterfaceType,
    EngineeringWorkflow,
    EngineeringWorkflowPhaseType,
    HardwareCandidateStatus,
    HardwareCandidateType,
    HardwareComponentCandidate,
    HardwareDecision,
    HardwareDesignPlanStatus,
    HardwareDesignSpecification,
    HardwareDomain,
    HardwareDomainType,
    HardwareInterface,
    HardwareInterfaceType,
    PCBDesignPlan,
    PlanProvenance,
    SchematicCircuitType,
    SchematicDesignPlan,
    _hardware_revision,
)

_DOMAIN_ALIASES: tuple[tuple[HardwareDomainType, tuple[str, ...]], ...] = (
    (HardwareDomainType.SYSTEM, ("SYSTEM", "ROOT")),
    (HardwareDomainType.CONTROLLER, ("CONTROLLER", "MCU", "MICROCONTROLLER", "STM32", "ESP32")),
    (HardwareDomainType.POWER, ("POWER", "BATTERY", "SUPPLY", "POWER_SYSTEM")),
    (HardwareDomainType.SENSOR, ("SENSOR", "SENSOR_INPUT", "IMU", "CAMERA")),
    (HardwareDomainType.ACTUATOR, ("ACTUATOR", "MOTOR", "DRIVER", "OUTPUT")),
    (HardwareDomainType.COMMUNICATION, ("COMMUNICATION", "NETWORK", "PROTOCOL", "WIRELESS")),
    (HardwareDomainType.MECHANICAL, ("MECHANICAL", "CHASSIS", "STRUCTURE")),
)
_INTERFACE_TYPES: dict[str, HardwareInterfaceType] = {
    "POWER": HardwareInterfaceType.POWER,
    "GPIO": HardwareInterfaceType.GPIO,
    "BUS": HardwareInterfaceType.BUS,
    "I2C": HardwareInterfaceType.BUS,
    "SPI": HardwareInterfaceType.BUS,
    "CONTROL": HardwareInterfaceType.CONTROL,
    "PWM": HardwareInterfaceType.CONTROL,
    "CLOCK": HardwareInterfaceType.CLOCK,
    "DEBUG": HardwareInterfaceType.DEBUG,
    "SWD": HardwareInterfaceType.DEBUG,
    "COMMUNICATION": HardwareInterfaceType.COMMUNICATION,
    "UART": HardwareInterfaceType.COMMUNICATION,
    "CAN": HardwareInterfaceType.COMMUNICATION,
    "MQTT": HardwareInterfaceType.COMMUNICATION,
    "TCP/IP": HardwareInterfaceType.COMMUNICATION,
    "ANALOG": HardwareInterfaceType.ANALOG,
    "DIGITAL": HardwareInterfaceType.DIGITAL,
}


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _domain_type(component: ArchitectureComponent) -> HardwareDomainType:
    for value in (component.category, component.name):
        normalized = value.strip().upper().replace("-", "_").replace("/", "_")
        tokens = {normalized, *(part for part in normalized.split("_") if part)}
        for domain_type, aliases in _DOMAIN_ALIASES:
            if tokens & set(aliases):
                return domain_type
    return HardwareDomainType.OTHER


def _interface_type(protocol: str) -> HardwareInterfaceType:
    normalized = protocol.strip().upper()
    return _INTERFACE_TYPES.get(normalized, HardwareInterfaceType.UNKNOWN)


def _hardware_sources(
    architecture: ArchitectureDesign,
    workflow: EngineeringWorkflow,
) -> tuple[PlanProvenance, ...]:
    values = [
        PlanProvenance.create(
            project_id=architecture.project_id,
            source_type="ARCHITECTURE_DESIGN",
            source_id=architecture.architecture_id,
            source_fingerprint=architecture.fingerprint,
        ),
        PlanProvenance.create(
            project_id=workflow.project_id,
            source_type="ENGINEERING_WORKFLOW",
            source_id=workflow.workflow_id,
            source_fingerprint=workflow.fingerprint,
        ),
        *architecture.provenance,
    ]
    result: list[PlanProvenance] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.source_type, value.source_id, value.source_fingerprint)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _decision_domain(
    subject: str,
    components: tuple[ArchitectureComponent, ...],
    domain_ids: dict[str, str],
) -> str | None:
    normalized = subject.casefold()
    for component in components:
        if component.name.casefold() in normalized or component.category.casefold() in normalized:
            return domain_ids.get(component.component_id)
    return None


def generate_hardware_design_spec(
    architecture_design: ArchitectureDesign,
    engineering_workflow: EngineeringWorkflow,
) -> HardwareDesignSpecification:
    """Project approved architecture and workflow into hardware planning state."""

    if type(architecture_design) is not ArchitectureDesign:
        raise TypeError("architecture_design must be ArchitectureDesign")
    if type(engineering_workflow) is not EngineeringWorkflow:
        raise TypeError("engineering_workflow must be EngineeringWorkflow")
    architecture = validate_copy(architecture_design, ArchitectureDesign)
    workflow = validate_copy(engineering_workflow, EngineeringWorkflow)
    if architecture.status is not ArchitectureDesignStatus.APPROVED:
        raise ValueError("hardware design requires approved architecture design")
    if architecture.project_id != workflow.project_id:
        raise ValueError("hardware design source project mismatch")
    if workflow.architecture_id != architecture.architecture_id:
        raise ValueError("hardware design workflow binding mismatch")
    if architecture.plan_id != workflow.plan_id:
        raise ValueError("hardware design workflow plan binding mismatch")
    workflow_architecture_sources = tuple(
        item for item in workflow.provenance
        if item.source_type == "ARCHITECTURE_DESIGN"
        and item.source_id == architecture.architecture_id
    )
    if (
        len(workflow_architecture_sources) != 1
        or workflow_architecture_sources[0].source_fingerprint != architecture.fingerprint
    ):
        raise ValueError("hardware design workflow architecture provenance mismatch")
    workflow_plan_sources = tuple(
        item for item in workflow.provenance
        if item.source_type == "ENGINEERING_PLAN"
        and item.source_id == architecture.plan_id
    )
    if (
        len(workflow_plan_sources) != 1
        or workflow_plan_sources[0].source_fingerprint != architecture.plan_fingerprint
    ):
        raise ValueError("hardware design workflow plan provenance mismatch")
    hardware_phases = tuple(
        phase for phase in workflow.phases
        if phase.phase_type is EngineeringWorkflowPhaseType.HARDWARE_DESIGN
    )
    if not hardware_phases:
        raise ValueError("hardware design workflow phase is unavailable")

    source_components = tuple(
        component
        for component in architecture.system_components
        if component.level.value == "SYSTEM"
        or component.component_id in set(architecture.boundary.hardware_component_ids)
    )
    spec_id = _id(
        "hardware-spec",
        architecture.project_id,
        architecture.architecture_id,
        architecture.fingerprint,
        workflow.workflow_id,
        workflow.fingerprint,
    )
    domain_ids = {
        component.component_id: _id("hardware-domain", spec_id, component.component_id)
        for component in source_components
    }
    domains_by_component = {component.component_id: domain_ids[component.component_id] for component in source_components}
    hardware_interfaces = tuple(
        interface for interface in architecture.interfaces
        if interface.interface_type is ArchitectureInterfaceType.HARDWARE
        and interface.source_component_id in domains_by_component
        and interface.target_component_id in domains_by_component
    )
    interface_ids = {
        interface.interface_id: _id("hardware-interface", spec_id, interface.interface_id)
        for interface in hardware_interfaces
    }
    domains = tuple(
        HardwareDomain.create(
            domain_id=domain_ids[component.component_id],
            hardware_spec_id=spec_id,
            name=component.name,
            type=_domain_type(component),
            requirements=(component.responsibility,),
            interfaces=tuple(
                interface_ids[item.interface_id]
                for item in hardware_interfaces
                if item.source_component_id == component.component_id
                or item.target_component_id == component.component_id
            ),
            verification_refs=tuple(
                verification.verification_id
                for phase in hardware_phases
                for verification in phase.verification_refs
            ),
        )
        for component in source_components
    )
    interfaces = tuple(
        HardwareInterface.create(
            interface_id=interface_ids[source.interface_id],
            source=domains_by_component[source.source_component_id],
            target=domains_by_component[source.target_component_id],
            interface_type=_interface_type(source.protocol),
            signal_refs=(),
            direction="UNKNOWN",
            constraints=(),
            evidence_refs=source.evidence_binding_ids,
        )
        for source in hardware_interfaces
    )

    hardware_decisions: list[HardwareDecision] = []
    candidates: list[HardwareComponentCandidate] = []
    for source in architecture.decisions:
        if source.decision_type is not ArchitectureDecisionType.HARDWARE_SELECTION:
            continue
        options = _unique((source.selected_option, *source.alternatives))
        hardware_decisions.append(
            HardwareDecision.create(
                decision_id=_id("hardware-decision", spec_id, source.decision_id),
                subject=source.subject,
                options=options,
                selected_option=None,
                rationale=source.rationale,
                status="OPEN",
                evidence_refs=source.evidence_binding_ids,
            )
        )
        domain_id = _decision_domain(source.subject, source_components, domain_ids)
        if domain_id is None:
            continue
        for option in source.alternatives:
            if option == "UNKNOWN_ALTERNATIVE":
                continue
            candidates.append(
                HardwareComponentCandidate.create(
                    candidate_id=_id("hardware-candidate", spec_id, source.decision_id, option),
                    domain_id=domain_id,
                    candidate_type=HardwareCandidateType.UNKNOWN,
                    category=source.decision_type.value,
                    name=option,
                    description=source.rationale,
                    selection_status=HardwareCandidateStatus.PROPOSED,
                    constraints=(),
                    evidence_refs=source.evidence_binding_ids,
                )
            )

    verification_refs = tuple(
        verification.verification_id
        for phase in hardware_phases
        for verification in phase.verification_refs
    )
    verification_requirements = _unique(
        [
            verification.method
            for phase in hardware_phases
            for verification in phase.verification_refs
        ]
    )
    circuits: list[SchematicCircuitType] = []
    circuit_by_domain = {
        HardwareDomainType.POWER: SchematicCircuitType.POWER_CIRCUIT,
        HardwareDomainType.CONTROLLER: SchematicCircuitType.CONTROLLER_CIRCUIT,
        HardwareDomainType.SENSOR: SchematicCircuitType.SENSOR_CIRCUIT,
        HardwareDomainType.ACTUATOR: SchematicCircuitType.ACTUATOR_CIRCUIT,
        HardwareDomainType.COMMUNICATION: SchematicCircuitType.COMMUNICATION_CIRCUIT,
    }
    for domain in domains:
        circuit = circuit_by_domain.get(domain.type)
        if circuit is not None and circuit not in circuits:
            circuits.append(circuit)
    if interfaces:
        circuits.append(SchematicCircuitType.INTERFACE_CIRCUIT)

    schematic_plan = SchematicDesignPlan.create(
        schematic_plan_id=_id("schematic-plan", spec_id),
        hardware_spec_id=spec_id,
        circuits=tuple(circuits),
        status=HardwareDesignPlanStatus.PLANNED,
        constraints=architecture.constraints,
        verification_refs=verification_refs,
    )
    pcb_phases = tuple(
        phase for phase in workflow.phases
        if phase.phase_type is EngineeringWorkflowPhaseType.PCB_DESIGN
    )
    pcb_plan = None
    if pcb_phases:
        pcb_plan = PCBDesignPlan.create(
            pcb_plan_id=_id("pcb-plan", spec_id),
            hardware_spec_id=spec_id,
            status=HardwareDesignPlanStatus.PLANNED,
            constraints=architecture.constraints,
            layout_requirements=("UNKNOWN",),
            verification_refs=tuple(
                verification.verification_id
                for phase in pcb_phases
                for verification in phase.verification_refs
            ),
        )

    source_refs = _unique(
        [
            architecture.architecture_id,
            workflow.workflow_id,
            *(component.component_id for component in source_components),
            *(interface.interface_id for interface in hardware_interfaces),
            *(decision.decision_id for decision in architecture.decisions if decision.decision_type is ArchitectureDecisionType.HARDWARE_SELECTION),
            *(phase.phase_id for phase in hardware_phases),
            *(binding.binding_id for binding in architecture.evidence_bindings),
        ]
    )
    return HardwareDesignSpecification.create(
        hardware_spec_id=spec_id,
        project_id=architecture.project_id,
        architecture_id=architecture.architecture_id,
        workflow_id=workflow.workflow_id,
        architecture_fingerprint=architecture.fingerprint,
        workflow_fingerprint=workflow.fingerprint,
        design_revision=_hardware_revision(
            architecture.architecture_id,
            architecture.fingerprint,
            workflow.workflow_id,
            workflow.fingerprint,
        ),
        title=f"{architecture.title} Hardware Design Planning",
        system_requirements=_unique(
            [component.responsibility for component in source_components]
            + list(architecture.constraints)
        ),
        hardware_domains=domains,
        component_candidates=tuple(candidates),
        interfaces=interfaces,
        decisions=tuple(hardware_decisions),
        constraints=architecture.constraints,
        verification_requirements=verification_requirements,
        source_refs=source_refs,
        provenance=_hardware_sources(architecture, workflow),
        schematic_plan=schematic_plan,
        pcb_plan=pcb_plan,
    )


__all__ = ("generate_hardware_design_spec",)
