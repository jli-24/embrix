from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime

from .common import ident, validate_copy
from .contracts import (
    AuthorizationDecisionStatus,
    ExecutionAuthorization,
    ExecutionIntent,
    ExecutionRequestStatus,
    FirmwareModule,
    FirmwareSourceFileProposal,
    FirmwareSourceGenerationRequest,
    FirmwareSourceGenerationState,
    FirmwareSourceGenerationStatus,
    FirmwareSourceProposal,
    FirmwareSourceProposalDecision,
    FirmwareSourceProposalDecisionStatus,
    ImplementationSpecification,
    ImplementationTask,
    FirmwareSpecification,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _slug(name: str, module_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    value = re.sub(r"[^a-z0-9]+", "_", ascii_name).strip("_")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if not value or value in reserved:
        value = "module_" + hashlib.sha256(module_id.encode("utf-8")).hexdigest()[:12]
    return value[:64]


def _comment_text(value: str) -> str:
    """Keep approved descriptive text inside one inert C comment line."""

    return " ".join(value.replace("*/", "* /").split())


def generate_firmware_source_request(
    firmware_spec: FirmwareSpecification,
    implementation_spec: ImplementationSpecification,
    implementation_task: ImplementationTask,
    firmware_module: FirmwareModule,
    execution_intent: ExecutionIntent,
    authorization: ExecutionAuthorization,
    *,
    authorization_request_id: str,
    tenant_id: str = "desktop-tenant",
    source_request_id: str | None = None,
    trace_id: str | None = None,
) -> FirmwareSourceGenerationRequest:
    firmware = validate_copy(firmware_spec, FirmwareSpecification)
    implementation = validate_copy(implementation_spec, ImplementationSpecification)
    task = validate_copy(implementation_task, ImplementationTask)
    module = validate_copy(firmware_module, FirmwareModule)
    intent = validate_copy(execution_intent, ExecutionIntent)
    auth = validate_copy(authorization, ExecutionAuthorization)
    request_id = ident(authorization_request_id, "authorization_request_id")
    tenant_id = ident(tenant_id, "tenant_id")
    source_request_id = (
        None if source_request_id is None else ident(source_request_id, "source_request_id")
    )
    trace_id = None if trace_id is None else ident(trace_id, "trace_id")

    if (
        firmware.project_id != implementation.project_id
        or firmware.conversation_id != implementation.conversation_id
        or implementation.firmware_spec_id != firmware.firmware_spec_id
        or implementation.firmware_spec_fingerprint != firmware.fingerprint
        or task.implementation_id != implementation.implementation_id
        or task not in implementation.tasks
        or module.firmware_spec_id != firmware.firmware_spec_id
        or module not in firmware.modules
    ):
        raise ValueError("firmware source project or specification binding mismatch")
    referenced_modules = tuple(item for item in firmware.modules if item.module_id in task.source_refs)
    if referenced_modules != (module,):
        raise ValueError("implementation task must uniquely bind one firmware module")
    if (
        intent.source_task_refs != (task.task_id,)
        or "FIRMWARE_CODE_GENERATION" not in intent.required_capabilities
        or auth.project_id != implementation.project_id
        or auth.conversation_id != implementation.conversation_id
    ):
        raise ValueError("firmware task intent or authorization binding mismatch")
    matching_requests = tuple(item for item in auth.requests if item.request_id == request_id)
    matching_decisions = tuple(item for item in auth.decisions if item.request_id == request_id)
    if len(matching_requests) != 1 or len(matching_decisions) != 1:
        raise ValueError("authorization request binding mismatch")
    execution_request = matching_requests[0]
    decision = matching_decisions[0]
    if (
        execution_request.intent_id != intent.intent_id
        or execution_request.capability_id != "FIRMWARE_CODE_GENERATION"
        or execution_request.project_id != implementation.project_id
        or execution_request.conversation_id != implementation.conversation_id
        or execution_request.status is not ExecutionRequestStatus.AUTHORIZED
        or decision.decision is not AuthorizationDecisionStatus.APPROVED
        or decision.approved_by is None
        or decision.timestamp_reference is None
    ):
        raise ValueError("firmware source request requires exact approved authorization")
    source_refs = (
        firmware.firmware_spec_id,
        implementation.implementation_id,
        task.task_id,
        module.module_id,
        intent.intent_id,
        execution_request.request_id,
        decision.decision_id,
        auth.authorization_id,
    )
    return FirmwareSourceGenerationRequest.create(
        request_id=_id(
            "firmware-source-request",
            tenant_id,
            implementation.project_id,
            implementation.conversation_id,
            firmware.fingerprint,
            implementation.fingerprint,
            task.fingerprint,
            module.fingerprint,
            intent.fingerprint,
            auth.fingerprint,
            execution_request.fingerprint,
            decision.fingerprint,
        ),
        source_request_id=source_request_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
        project_id=implementation.project_id,
        conversation_id=implementation.conversation_id,
        requester=execution_request.requested_by,
        firmware_spec_id=firmware.firmware_spec_id,
        firmware_spec_fingerprint=firmware.fingerprint,
        implementation_id=implementation.implementation_id,
        implementation_revision=implementation.design_revision,
        implementation_fingerprint=implementation.fingerprint,
        implementation_task_id=task.task_id,
        implementation_task_fingerprint=task.fingerprint,
        firmware_module_id=module.module_id,
        firmware_module_fingerprint=module.fingerprint,
        execution_intent_id=intent.intent_id,
        execution_intent_fingerprint=intent.fingerprint,
        authorization_id=auth.authorization_id,
        authorization_fingerprint=auth.fingerprint,
        authorization_request_id=execution_request.request_id,
        authorization_request_fingerprint=execution_request.fingerprint,
        authorization_decision_id=decision.decision_id,
        authorization_decision_fingerprint=decision.fingerprint,
        source_refs=source_refs,
        verification_refs=tuple(
            ident(item, "verification_ref") for item in task.verification_refs
        ),
    )


def _file(path: str, content: str) -> FirmwareSourceFileProposal:
    encoded = content.encode("utf-8")
    return FirmwareSourceFileProposal.create(
        relative_path=path,
        content=content,
        content_fingerprint="sha256:" + hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )


def generate_firmware_source_proposal(
    generation_request: FirmwareSourceGenerationRequest,
    firmware_module: FirmwareModule,
) -> FirmwareSourceProposal:
    request = validate_copy(generation_request, FirmwareSourceGenerationRequest)
    module = validate_copy(firmware_module, FirmwareModule)
    if (
        module.module_id != request.firmware_module_id
        or module.fingerprint != request.firmware_module_fingerprint
    ):
        raise ValueError("firmware module request binding mismatch")
    slug = _slug(module.name, module.module_id)
    guard = f"EMBRIX_GENERATED_{slug.upper()}_H"
    responsibility = _comment_text(module.responsibility)
    header = (
        f"#ifndef {guard}\n"
        f"#define {guard}\n\n"
        f"/* Responsibility: {responsibility} */\n"
        "/* TODO: Add declarations only after structured interface details are approved. */\n\n"
        f"#endif /* {guard} */\n"
    )
    source = (
        f'#include "{slug}.h"\n\n'
        f"/* Responsibility: {responsibility} */\n"
        "/* TODO: Add implementation only after platform and interface details are approved. */\n"
    )
    files = (
        _file(f"generated/{slug}.h", header),
        _file(f"generated/{slug}.c", source),
    )
    validation = "sha256:" + hashlib.sha256(
        "|".join(item.content_fingerprint for item in files).encode("utf-8")
    ).hexdigest()
    return FirmwareSourceProposal.create(
        proposal_id=_id(
            "firmware-source-proposal",
            request.request_id,
            request.fingerprint,
            module.fingerprint,
            *(item.fingerprint for item in files),
        ),
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        requester=request.requester,
        generation_request_id=request.request_id,
        generation_request_fingerprint=request.fingerprint,
        firmware_module_id=module.module_id,
        module_name=module.name,
        module_responsibility=module.responsibility,
        files=files,
        verification_refs=request.verification_refs,
        validation_fingerprint=validation,
        source_refs=(request.request_id, module.module_id),
    )


def pending_firmware_source_proposal_decision(
    proposal: FirmwareSourceProposal,
) -> FirmwareSourceProposalDecision:
    checked = validate_copy(proposal, FirmwareSourceProposal)
    return FirmwareSourceProposalDecision.create(
        decision_id=_id("firmware-source-decision", checked.proposal_id, checked.fingerprint),
        proposal_id=checked.proposal_id,
        proposal_fingerprint=checked.fingerprint,
        decision=FirmwareSourceProposalDecisionStatus.PENDING,
        reviewer=None,
        decided_at=None,
        reason="Awaiting explicit firmware source generation approval.",
    )


def decide_firmware_source_proposal(
    proposal: FirmwareSourceProposal,
    *,
    decision: FirmwareSourceProposalDecisionStatus,
    reviewer: str,
    decided_at: datetime,
) -> FirmwareSourceProposalDecision:
    checked = validate_copy(proposal, FirmwareSourceProposal)
    if not isinstance(decision, FirmwareSourceProposalDecisionStatus):
        decision = FirmwareSourceProposalDecisionStatus(decision)
    if decision is FirmwareSourceProposalDecisionStatus.PENDING:
        raise ValueError("terminal firmware proposal decision is required")
    reviewer = ident(reviewer, "reviewer")
    if not isinstance(decided_at, datetime) or decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("decided_at must be timezone-aware")
    return FirmwareSourceProposalDecision.create(
        decision_id=_id("firmware-source-decision", checked.proposal_id, checked.fingerprint),
        proposal_id=checked.proposal_id,
        proposal_fingerprint=checked.fingerprint,
        decision=decision,
        reviewer=reviewer,
        decided_at=decided_at.astimezone(UTC),
        reason=(
            "Firmware source proposal explicitly approved."
            if decision is FirmwareSourceProposalDecisionStatus.APPROVED
            else "Firmware source proposal explicitly rejected."
        ),
    )


def create_firmware_source_generation_state(
    generation_request: FirmwareSourceGenerationRequest,
    proposal: FirmwareSourceProposal,
) -> FirmwareSourceGenerationState:
    request = validate_copy(generation_request, FirmwareSourceGenerationRequest)
    checked_proposal = validate_copy(proposal, FirmwareSourceProposal)
    decision = pending_firmware_source_proposal_decision(checked_proposal)
    return FirmwareSourceGenerationState.create(
        generation_id=_id(
            "firmware-source-generation",
            request.request_id,
            checked_proposal.fingerprint,
        ),
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        request=request,
        proposal=checked_proposal,
        decision=decision,
        operation_id=None,
        operation_fingerprint=None,
        status=FirmwareSourceGenerationStatus.AWAITING_APPROVAL,
    )


def is_firmware_source_generation_request(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"[\s，。！？、,.!?]+", "", unicodedata.normalize("NFKC", value)).casefold()
    return normalized in {
        "开始生成这个固件模块",
        "生成这个固件模块",
        "开始生成固件源码",
        "生成固件源码",
        "generatethisfirmwaremodule",
        "generatefirmwaresource",
    }


__all__ = (
    "create_firmware_source_generation_state",
    "decide_firmware_source_proposal",
    "generate_firmware_source_proposal",
    "generate_firmware_source_request",
    "is_firmware_source_generation_request",
    "pending_firmware_source_proposal_decision",
)
