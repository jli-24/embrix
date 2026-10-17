from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    EngineeringWorkflow,
    EngineeringWorkflowPhaseType,
    FirmwareModuleType,
    FirmwareSpecification,
    HardwareDesignSpecification,
    HardwareInterfaceType,
    PlanProvenance,
    SimulationEvidenceType,
    SimulationScenarioType,
    SimulationSpecification,
    VerificationAcceptanceMapping,
    VerificationCase,
    VerificationCaseType,
    VerificationEvidenceRequirement,
    VerificationEvidenceType,
    VerificationIntent,
    VerificationSpecification,
    VerificationStatus,
    _verification_revision,
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


def _interface_case_type(interface_type: HardwareInterfaceType) -> VerificationCaseType:
    if interface_type is HardwareInterfaceType.CONTROL:
        return VerificationCaseType.CONTROL
    if interface_type in {HardwareInterfaceType.COMMUNICATION, HardwareInterfaceType.BUS}:
        return VerificationCaseType.COMMUNICATION
    return VerificationCaseType.INTERFACE


def _scenario_case_type(scenario_type: SimulationScenarioType) -> VerificationCaseType | None:
    mapping = {
        SimulationScenarioType.FUNCTIONAL_TEST: VerificationCaseType.FUNCTIONAL,
        SimulationScenarioType.INTERFACE_TEST: VerificationCaseType.INTERFACE,
        SimulationScenarioType.CONTROL_TEST: VerificationCaseType.CONTROL,
        SimulationScenarioType.COMMUNICATION_TEST: VerificationCaseType.COMMUNICATION,
        SimulationScenarioType.PERFORMANCE_TEST: VerificationCaseType.PERFORMANCE,
    }
    return mapping.get(scenario_type)


def _intent(cases: tuple[VerificationCase, ...]) -> VerificationIntent | None:
    case_types = {case.case_type for case in cases}
    if VerificationCaseType.CONTROL in case_types:
        return VerificationIntent.CONTROL_VALIDATION
    if case_types & {VerificationCaseType.INTERFACE, VerificationCaseType.COMMUNICATION}:
        return VerificationIntent.INTERFACE_VALIDATION
    if VerificationCaseType.PERFORMANCE in case_types:
        return VerificationIntent.PERFORMANCE_VALIDATION
    if VerificationCaseType.FUNCTIONAL in case_types:
        return VerificationIntent.FUNCTIONAL_VALIDATION
    if VerificationCaseType.DESIGN_REVIEW in case_types:
        return VerificationIntent.COMPLIANCE_VALIDATION
    return None


def _case_intent(case_type: VerificationCaseType) -> VerificationIntent:
    mapping = {
        VerificationCaseType.FUNCTIONAL: VerificationIntent.FUNCTIONAL_VALIDATION,
        VerificationCaseType.INTERFACE: VerificationIntent.INTERFACE_VALIDATION,
        VerificationCaseType.CONTROL: VerificationIntent.CONTROL_VALIDATION,
        VerificationCaseType.COMMUNICATION: VerificationIntent.INTERFACE_VALIDATION,
        VerificationCaseType.PERFORMANCE: VerificationIntent.PERFORMANCE_VALIDATION,
        VerificationCaseType.DESIGN_REVIEW: VerificationIntent.COMPLIANCE_VALIDATION,
    }
    return mapping[case_type]


def _evidence_type(value: SimulationEvidenceType) -> VerificationEvidenceType:
    mapping = {
        SimulationEvidenceType.LOG: VerificationEvidenceType.LOG,
        SimulationEvidenceType.TRACE: VerificationEvidenceType.TRACE,
        SimulationEvidenceType.WAVEFORM: VerificationEvidenceType.WAVEFORM,
        SimulationEvidenceType.MEASUREMENT: VerificationEvidenceType.MEASUREMENT,
        SimulationEvidenceType.REPORT: VerificationEvidenceType.SIMULATION_REPORT,
    }
    return mapping.get(value, VerificationEvidenceType.UNKNOWN)


def _workflow_verifications(workflow: EngineeringWorkflow) -> dict[str, object]:
    return {
        verification.verification_id: verification
        for phase in workflow.phases
        for verification in phase.verification_refs
    }


def _case_method(verification_refs: tuple[str, ...], by_id: dict[str, object]) -> str:
    methods = tuple(
        getattr(by_id.get(reference), "method", "")
        for reference in verification_refs
        if by_id.get(reference) is not None
    )
    return methods[0] if methods else "UNKNOWN"


def _case_acceptance(verification_refs: tuple[str, ...], by_id: dict[str, object]) -> tuple[str, ...]:
    return _unique(
        tuple(
            reference
            for verification_id in verification_refs
            for reference in getattr(by_id.get(verification_id), "acceptance_refs", ())
        )
    )


def generate_verification_spec(
    hardware_design_spec: HardwareDesignSpecification,
    firmware_spec: FirmwareSpecification,
    simulation_spec: SimulationSpecification,
    engineering_workflow: EngineeringWorkflow,
) -> VerificationSpecification:
    """Project validated planning contracts into a downstream verification plan."""

    if type(hardware_design_spec) is not HardwareDesignSpecification:
        raise TypeError("hardware_design_spec must be HardwareDesignSpecification")
    if type(firmware_spec) is not FirmwareSpecification:
        raise TypeError("firmware_spec must be FirmwareSpecification")
    if type(simulation_spec) is not SimulationSpecification:
        raise TypeError("simulation_spec must be SimulationSpecification")
    if type(engineering_workflow) is not EngineeringWorkflow:
        raise TypeError("engineering_workflow must be EngineeringWorkflow")
    hardware = validate_copy(hardware_design_spec, HardwareDesignSpecification)
    firmware = validate_copy(firmware_spec, FirmwareSpecification)
    simulation = validate_copy(simulation_spec, SimulationSpecification)
    workflow = validate_copy(engineering_workflow, EngineeringWorkflow)

    if len({hardware.project_id, firmware.project_id, simulation.project_id, workflow.project_id}) != 1:
        raise ValueError("verification source project mismatch")
    if hardware.architecture_id != firmware.architecture_id or hardware.architecture_id != workflow.architecture_id:
        raise ValueError("verification architecture binding mismatch")
    if hardware.workflow_id != workflow.workflow_id or firmware.workflow_id != workflow.workflow_id:
        raise ValueError("verification workflow binding mismatch")
    if firmware.hardware_design_id != hardware.hardware_spec_id:
        raise ValueError("verification firmware hardware binding mismatch")
    if simulation.hardware_design_id != hardware.hardware_spec_id or simulation.firmware_spec_id != firmware.firmware_spec_id:
        raise ValueError("verification simulation design binding mismatch")
    if simulation.workflow_id != workflow.workflow_id:
        raise ValueError("verification simulation workflow binding mismatch")
    if firmware.conversation_id != workflow.conversation_id or simulation.conversation_id != workflow.conversation_id:
        raise ValueError("verification conversation binding mismatch")
    if hardware.workflow_fingerprint != workflow.fingerprint or firmware.workflow_fingerprint != workflow.fingerprint:
        raise ValueError("verification workflow fingerprint mismatch")
    if firmware.hardware_design_fingerprint != hardware.fingerprint:
        raise ValueError("verification firmware hardware fingerprint mismatch")
    if (
        simulation.hardware_design_fingerprint != hardware.fingerprint
        or simulation.firmware_spec_fingerprint != firmware.fingerprint
        or simulation.workflow_fingerprint != workflow.fingerprint
    ):
        raise ValueError("verification simulation fingerprint mismatch")
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
        raise ValueError("verification architecture provenance mismatch")

    verification_id = _id(
        "verification-spec",
        hardware.hardware_spec_id,
        hardware.fingerprint,
        firmware.firmware_spec_id,
        firmware.fingerprint,
        simulation.simulation_spec_id,
        simulation.fingerprint,
        workflow.workflow_id,
        workflow.fingerprint,
        workflow.conversation_id,
    )
    workflow_by_id = _workflow_verifications(workflow)
    firmware_verifications_by_module: dict[str, tuple] = {}
    for verification in firmware.verification_requirements:
        firmware_verifications_by_module[verification.target_module] = (
            *firmware_verifications_by_module.get(verification.target_module, ()),
            verification,
        )
    binding_module_ids: dict[str, tuple[str, ...]] = {}
    for binding in firmware.bindings:
        binding_module_ids[binding.hardware_interface_id] = _unique(
            (*binding_module_ids.get(binding.hardware_interface_id, ()), binding.firmware_module_id)
        )

    cases: list[VerificationCase] = []
    case_acceptance: dict[str, tuple[str, ...]] = {}
    case_requirements: dict[str, tuple[str, ...]] = {}

    def add_case(
        *,
        origin: str,
        title: str,
        case_type: VerificationCaseType,
        target: str,
        requirement_refs: tuple[str, ...] = (),
        design_refs: tuple[str, ...] = (),
        method: str = "UNKNOWN",
        expected_behavior: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        acceptance_refs: tuple[str, ...] = (),
    ) -> VerificationCase:
        case = VerificationCase.create(
            case_id=_id("verification-case", verification_id, origin, case_type.value),
            verification_id=verification_id,
            title=title,
            case_type=case_type,
            verification_intent=_case_intent(case_type),
            target=target,
            related_requirement_refs=_unique(requirement_refs),
            related_design_refs=_unique(design_refs),
            method=method,
            expected_behavior=_unique(expected_behavior),
            evidence_refs=_unique(evidence_refs),
            status=VerificationStatus.PLANNED,
        )
        cases.append(case)
        case_acceptance[case.case_id] = _unique(acceptance_refs)
        case_requirements[case.case_id] = _unique(requirement_refs)
        return case

    domain_names = {domain.domain_id: domain.name for domain in hardware.hardware_domains}
    for interface in hardware.interfaces:
        if interface.interface_type is HardwareInterfaceType.UNKNOWN:
            continue
        module_ids = binding_module_ids.get(interface.interface_id, ())
        module_verifications = tuple(
            item for module_id in module_ids
            for item in firmware_verifications_by_module.get(module_id, ())
        )
        verification_refs = _unique(tuple(item.verification_id for item in module_verifications))
        acceptance_refs = _unique(tuple(ref for item in module_verifications for ref in item.acceptance_refs))
        source = domain_names.get(interface.source, interface.source)
        target = domain_names.get(interface.target, interface.target)
        add_case(
            origin=interface.interface_id,
            title=f"{source} to {target} Verification",
            case_type=_interface_case_type(interface.interface_type),
            target=interface.interface_id,
            design_refs=(interface.interface_id,),
            method=_case_method(verification_refs, workflow_by_id),
            expected_behavior=interface.constraints,
            evidence_refs=_unique((*interface.evidence_refs, *verification_refs)),
            acceptance_refs=acceptance_refs,
        )

    for module in firmware.modules:
        module_verifications = firmware_verifications_by_module.get(module.module_id, ())
        verification_refs = _unique(tuple(item.verification_id for item in module_verifications))
        acceptance_refs = _unique(tuple(ref for item in module_verifications for ref in item.acceptance_refs))
        method = module_verifications[0].method if module_verifications else "UNKNOWN"
        evidence_refs = _unique(
            (*module.source_refs, *(item.verification_id for item in module_verifications))
        )
        expected_behavior = (module.responsibility,)
        if module.module_type is FirmwareModuleType.CONTROL:
            add_case(
                origin=module.module_id,
                title=f"{module.name} Functional Verification",
                case_type=VerificationCaseType.FUNCTIONAL,
                target=module.module_id,
                design_refs=(module.module_id,),
                method=method,
                expected_behavior=expected_behavior,
                evidence_refs=evidence_refs,
                acceptance_refs=acceptance_refs,
            )
            add_case(
                origin=module.module_id,
                title=f"{module.name} Control Verification",
                case_type=VerificationCaseType.CONTROL,
                target=module.module_id,
                design_refs=(module.module_id,),
                method=method,
                expected_behavior=expected_behavior,
                evidence_refs=evidence_refs,
                acceptance_refs=acceptance_refs,
            )
        elif module.module_type is FirmwareModuleType.COMMUNICATION:
            add_case(
                origin=module.module_id,
                title=f"{module.name} Communication Verification",
                case_type=VerificationCaseType.COMMUNICATION,
                target=module.module_id,
                design_refs=(module.module_id,),
                method=method,
                expected_behavior=expected_behavior,
                evidence_refs=evidence_refs,
                acceptance_refs=acceptance_refs,
            )
        else:
            add_case(
                origin=module.module_id,
                title=f"{module.name} Functional Verification",
                case_type=VerificationCaseType.FUNCTIONAL,
                target=module.module_id,
                design_refs=(module.module_id,),
                method=method,
                expected_behavior=expected_behavior,
                evidence_refs=evidence_refs,
                acceptance_refs=acceptance_refs,
            )

    simulation_evidence_by_scenario: dict[str, tuple] = {}
    for evidence in simulation.evidence_requirements:
        simulation_evidence_by_scenario[evidence.scenario_id] = (
            *simulation_evidence_by_scenario.get(evidence.scenario_id, ()),
            evidence,
        )
    simulation_case_ids: dict[str, str] = {}
    for scenario in simulation.scenarios:
        case_type = _scenario_case_type(scenario.scenario_type)
        if case_type is None:
            continue
        case = add_case(
            origin=scenario.scenario_id,
            title=f"{scenario.title} Verification",
            case_type=case_type,
            target=scenario.scenario_id,
            requirement_refs=scenario.acceptance_refs,
            design_refs=(scenario.scenario_id, *scenario.related_models),
            expected_behavior=scenario.expected_outputs,
            evidence_refs=tuple(item.evidence_id for item in simulation_evidence_by_scenario.get(scenario.scenario_id, ())),
            acceptance_refs=scenario.acceptance_refs,
        )
        simulation_case_ids[scenario.scenario_id] = case.case_id

    for phase in workflow.phases:
        if phase.phase_type is not EngineeringWorkflowPhaseType.VERIFICATION:
            continue
        for verification in phase.verification_refs:
            add_case(
                origin=verification.verification_id,
                title=f"{verification.method} Design Review",
                case_type=VerificationCaseType.DESIGN_REVIEW,
                target=verification.verification_id,
                design_refs=(phase.phase_id, verification.task_id),
                method=verification.method,
                acceptance_refs=verification.acceptance_refs,
            )

    evidence_requirements: list[VerificationEvidenceRequirement] = []
    for scenario_id, case_id in simulation_case_ids.items():
        for source in simulation_evidence_by_scenario.get(scenario_id, ()):
            evidence_requirements.append(
                VerificationEvidenceRequirement.create(
                    evidence_id=_id("verification-evidence", verification_id, source.evidence_id),
                    verification_id=verification_id,
                    case_id=case_id,
                    evidence_type=_evidence_type(source.evidence_type),
                    description=source.description,
                    source_reference=source.evidence_id,
                    acceptance_refs=source.acceptance_refs,
                )
            )
    cases_with_evidence = {item.case_id for item in evidence_requirements}
    for case in cases:
        if case.case_id in cases_with_evidence:
            continue
        evidence_requirements.append(
            VerificationEvidenceRequirement.create(
                evidence_id=_id("verification-evidence", verification_id, case.case_id),
                verification_id=verification_id,
                case_id=case.case_id,
                evidence_type=VerificationEvidenceType.UNKNOWN,
                description=f"Pending evidence for {case.title}",
                source_reference=case.case_id,
                acceptance_refs=case_acceptance[case.case_id],
            )
        )

    acceptance_mapping = tuple(
        VerificationAcceptanceMapping.create(
            mapping_id=_id("verification-mapping", verification_id, case_id, requirement_ref),
            verification_id=verification_id,
            case_id=case_id,
            requirement_ref=requirement_ref,
            acceptance_refs=case_acceptance[case_id],
        )
        for case_id, requirement_refs in case_requirements.items()
        for requirement_ref in requirement_refs
    )
    provenance = _deduplicate_provenance(
        (
            *hardware.provenance,
            *firmware.provenance,
            *simulation.provenance,
            *workflow.provenance,
            _provenance(hardware.project_id, "HARDWARE_DESIGN_SPECIFICATION", hardware.hardware_spec_id, hardware.fingerprint),
            _provenance(firmware.project_id, "FIRMWARE_SPECIFICATION", firmware.firmware_spec_id, firmware.fingerprint),
            _provenance(simulation.project_id, "SIMULATION_SPECIFICATION", simulation.simulation_spec_id, simulation.fingerprint),
            _provenance(workflow.project_id, "ENGINEERING_WORKFLOW", workflow.workflow_id, workflow.fingerprint),
        )
    )
    return VerificationSpecification.create(
        verification_id=verification_id,
        project_id=hardware.project_id,
        conversation_id=workflow.conversation_id,
        hardware_design_id=hardware.hardware_spec_id,
        firmware_spec_id=firmware.firmware_spec_id,
        simulation_spec_id=simulation.simulation_spec_id,
        workflow_id=workflow.workflow_id,
        hardware_design_fingerprint=hardware.fingerprint,
        firmware_spec_fingerprint=firmware.fingerprint,
        simulation_spec_fingerprint=simulation.fingerprint,
        workflow_fingerprint=workflow.fingerprint,
        design_revision=_verification_revision(
            hardware.hardware_spec_id,
            hardware.fingerprint,
            firmware.firmware_spec_id,
            firmware.fingerprint,
            simulation.simulation_spec_id,
            simulation.fingerprint,
            workflow.workflow_id,
            workflow.fingerprint,
        ),
        verification_intent=_intent(tuple(cases)),
        cases=tuple(cases),
        evidence_requirements=tuple(evidence_requirements),
        acceptance_mapping=acceptance_mapping,
        status=VerificationStatus.PLANNED,
        provenance=provenance,
    )


__all__ = ("generate_verification_spec",)
