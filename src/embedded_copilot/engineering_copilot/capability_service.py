from __future__ import annotations

import hashlib

from .common import validate_copy
from .contracts import (
    CapabilityApprovalRequirement,
    CapabilityApprovalStatus,
    CapabilityCategory,
    CapabilityEvaluation,
    CapabilityRiskLevel,
    CapabilityStatus,
    EngineeringCapability,
    ExecutionIntent,
    ExecutionIntentStatus,
    ExecutionIntentType,
    ImplementationCapabilityBinding,
    ImplementationIntent,
    ImplementationSpecification,
    ImplementationTask,
    ImplementationTaskType,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


_CAPABILITY_CATALOG = {
    "ARTIFACT_CREATION": (
        "Artifact Creation",
        CapabilityCategory.ARTIFACT,
        "Declare a future artifact creation operation.",
        CapabilityRiskLevel.LOW,
        False,
        CapabilityStatus.PLANNED,
    ),
    "FIRMWARE_CODE_GENERATION": (
        "Firmware Code Generation",
        CapabilityCategory.FIRMWARE,
        "Declare future firmware source generation.",
        CapabilityRiskLevel.HIGH,
        True,
        CapabilityStatus.PLANNED,
    ),
    "PROJECT_BUILD": (
        "Project Build",
        CapabilityCategory.BUILD,
        "Request a controlled real firmware build through the canonical Execution Worker; availability still depends on trusted target, configuration and toolchain preflight.",
        CapabilityRiskLevel.HIGH,
        True,
        CapabilityStatus.RESTRICTED,
    ),
    "SIMULATION_EXECUTION": (
        "Simulation Execution",
        CapabilityCategory.SIMULATION,
        "Declare a future simulation operation.",
        CapabilityRiskLevel.HIGH,
        True,
        CapabilityStatus.PLANNED,
    ),
    "VERIFICATION_EXECUTION": (
        "Verification Execution",
        CapabilityCategory.VERIFICATION,
        "Declare a future verification operation.",
        CapabilityRiskLevel.HIGH,
        True,
        CapabilityStatus.PLANNED,
    ),
    "FLASH_DEVICE": (
        "Flash Device",
        CapabilityCategory.FIRMWARE,
        "Declare a restricted future device flash operation.",
        CapabilityRiskLevel.CRITICAL,
        True,
        CapabilityStatus.RESTRICTED,
    ),
    "HARDWARE_MODIFICATION": (
        "Hardware Modification",
        CapabilityCategory.HARDWARE,
        "Declare a restricted future hardware modification.",
        CapabilityRiskLevel.CRITICAL,
        True,
        CapabilityStatus.RESTRICTED,
    ),
}


_TASK_MAPPING = {
    (ImplementationTaskType.FIRMWARE_MODULE, ImplementationIntent.MODULE_IMPLEMENTATION): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.FIRMWARE_MODULE, ImplementationIntent.DRIVER_DEVELOPMENT): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.FIRMWARE_MODULE, ImplementationIntent.CONTROL_IMPLEMENTATION): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.FIRMWARE_MODULE, ImplementationIntent.INTERFACE_ADAPTATION): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.DRIVER_IMPLEMENTATION, ImplementationIntent.DRIVER_DEVELOPMENT): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.CONTROL_ALGORITHM, ImplementationIntent.CONTROL_IMPLEMENTATION): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.COMMUNICATION, ImplementationIntent.INTERFACE_ADAPTATION): (
        ExecutionIntentType.GENERATE_SOURCE,
        "FIRMWARE_CODE_GENERATION",
    ),
    (ImplementationTaskType.INTERFACE_IMPLEMENTATION, ImplementationIntent.INTERFACE_ADAPTATION): (
        ExecutionIntentType.CREATE_ARTIFACT,
        "ARTIFACT_CREATION",
    ),
    (ImplementationTaskType.HARDWARE_CONFIGURATION, ImplementationIntent.CONFIGURATION_SETUP): (
        ExecutionIntentType.CREATE_ARTIFACT,
        "ARTIFACT_CREATION",
    ),
    (ImplementationTaskType.CONFIGURATION, ImplementationIntent.CONFIGURATION_SETUP): (
        ExecutionIntentType.CREATE_ARTIFACT,
        "ARTIFACT_CREATION",
    ),
    (ImplementationTaskType.TEST_PREPARATION, ImplementationIntent.TEST_PREPARATION): (
        ExecutionIntentType.CREATE_ARTIFACT,
        "ARTIFACT_CREATION",
    ),
    (ImplementationTaskType.DOCUMENTATION, ImplementationIntent.DOCUMENTATION): (
        ExecutionIntentType.CREATE_ARTIFACT,
        "ARTIFACT_CREATION",
    ),
}


def _mapping(task: ImplementationTask) -> tuple[ExecutionIntentType, str]:
    try:
        return _TASK_MAPPING[(task.task_type, task.implementation_intent)]
    except KeyError as error:
        raise ValueError("implementation task capability mapping combination is unsupported") from error


def generate_execution_intent(
    implementation_task: ImplementationTask,
) -> ExecutionIntent:
    """Project one explicit implementation task into a declaration-only intent."""

    if type(implementation_task) is not ImplementationTask:
        raise TypeError("ImplementationTask input is required")
    task = validate_copy(implementation_task, ImplementationTask)
    intent_type, capability_id = _mapping(task)
    return ExecutionIntent.create(
        intent_id=_id(
            "execution-intent",
            task.implementation_id,
            task.task_id,
            task.fingerprint,
            intent_type.value,
            capability_id,
        ),
        intent_type=intent_type,
        source_task_refs=(task.task_id,),
        required_capabilities=(capability_id,),
        status=ExecutionIntentStatus.PLANNED,
    )


def evaluate_capabilities(
    implementation_spec: ImplementationSpecification,
) -> CapabilityEvaluation:
    """Evaluate declared task capabilities without invoking an execution boundary."""

    if type(implementation_spec) is not ImplementationSpecification:
        raise TypeError("ImplementationSpecification input is required")
    spec = validate_copy(implementation_spec, ImplementationSpecification)
    intents = tuple(generate_execution_intent(task) for task in spec.tasks)
    task_by_id = {task.task_id: task for task in spec.tasks}

    bindings = tuple(
        ImplementationCapabilityBinding.create(
            binding_id=_id(
                "capability-binding",
                spec.implementation_id,
                intent.source_task_refs[0],
                task_by_id[intent.source_task_refs[0]].fingerprint,
                intent.required_capabilities[0],
            ),
            implementation_task_id=intent.source_task_refs[0],
            capability_id=intent.required_capabilities[0],
            required=True,
            reason=(
                f"Required by {task_by_id[intent.source_task_refs[0]].task_type.value} / "
                f"{task_by_id[intent.source_task_refs[0]].implementation_intent.value}."
            ),
        )
        for intent in intents
    )

    required_ids = tuple(
        capability_id
        for capability_id in _CAPABILITY_CATALOG
        if any(binding.capability_id == capability_id for binding in bindings)
    )
    capabilities = tuple(
        EngineeringCapability.create(
            capability_id=capability_id,
            name=_CAPABILITY_CATALOG[capability_id][0],
            category=_CAPABILITY_CATALOG[capability_id][1],
            description=_CAPABILITY_CATALOG[capability_id][2],
            risk_level=_CAPABILITY_CATALOG[capability_id][3],
            requires_approval=_CAPABILITY_CATALOG[capability_id][4],
            status=_CAPABILITY_CATALOG[capability_id][5],
            source_refs=tuple(
                dict.fromkeys(
                    binding.implementation_task_id
                    for binding in bindings
                    if binding.capability_id == capability_id
                )
            ),
        )
        for capability_id in required_ids
    )
    capability_by_id = {item.capability_id: item for item in capabilities}
    approval_requirements = tuple(
        CapabilityApprovalRequirement.create(
            approval_id=_id("capability-approval", intent.intent_id, intent.fingerprint),
            intent_id=intent.intent_id,
            required_capabilities=intent.required_capabilities,
            risk_level=capability_by_id[intent.required_capabilities[0]].risk_level,
            approval_status=CapabilityApprovalStatus.PENDING,
        )
        for intent in intents
        if capability_by_id[intent.required_capabilities[0]].requires_approval
    )
    return CapabilityEvaluation.create(
        evaluation_id=_id(
            "capability-evaluation",
            spec.project_id,
            spec.implementation_id,
            spec.fingerprint,
        ),
        project_id=spec.project_id,
        implementation_id=spec.implementation_id,
        implementation_fingerprint=spec.fingerprint,
        capabilities=capabilities,
        bindings=bindings,
        approval_requirements=approval_requirements,
    )


__all__ = ("evaluate_capabilities", "generate_execution_intent")
