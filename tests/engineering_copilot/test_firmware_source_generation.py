from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    AuthorizationDecisionStatus,
    ExecutionRequestStatus,
    FirmwareSourceGenerationRequest,
    FirmwareSourceFileProposal,
    FirmwareSourceProposal,
    FirmwareSourceProposalDecisionStatus,
    decide_execution_authorization_request,
    decide_firmware_source_proposal,
    evaluate_authorization,
    evaluate_capabilities,
    generate_execution_intent,
    generate_execution_requests,
    generate_firmware_source_proposal,
    generate_firmware_source_request,
    generate_implementation_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_implementation_planning_layer import _with_explicit_sources


def _source_state():
    hardware, firmware, simulation, verification, workflow = _with_explicit_sources()
    implementation = generate_implementation_spec(
        hardware, firmware, simulation, verification, workflow
    )
    evaluation = evaluate_capabilities(implementation)
    intents = tuple(generate_execution_intent(task) for task in implementation.tasks)
    requests = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=implementation.conversation_id,
        requested_by="engineer-1",
    )
    authorization = evaluate_authorization(evaluation, intents, requests)
    index = next(
        index
        for index, request in enumerate(requests)
        if request.capability_id == "FIRMWARE_CODE_GENERATION"
    )
    authorization = decide_execution_authorization_request(
        evaluation,
        intents,
        authorization,
        request_id=requests[index].request_id,
        decision=AuthorizationDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 4, 9, tzinfo=UTC),
    )
    request = authorization.requests[index]
    intent = next(item for item in intents if item.intent_id == request.intent_id)
    task = next(item for item in implementation.tasks if item.task_id in intent.source_task_refs)
    module = next(item for item in firmware.modules if item.module_id in task.source_refs)
    return firmware, implementation, task, module, intent, authorization, request


def test_firmware_source_contracts_are_strict_frozen_and_round_trip() -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=request.request_id,
    )
    proposal = generate_firmware_source_proposal(generation_request, module)

    assert type(proposal.files) is tuple
    assert len(proposal.files) == 2
    assert FirmwareSourceProposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert FirmwareSourceGenerationRequest.model_validate_json(
        generation_request.model_dump_json()
    ) == generation_request
    with pytest.raises((TypeError, ValidationError)):
        proposal.project_id = "project-other"

    payload = proposal.model_dump(mode="python")
    payload["unexpected"] = True
    payload["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="extra|unexpected"):
        FirmwareSourceProposal.model_validate(payload, strict=False)

    tampered = proposal.model_dump(mode="python")
    tampered["files"][0]["content"] += "\nunsafe mutation"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint|content"):
        FirmwareSourceProposal.model_validate(tampered, strict=False)


def test_generation_is_deterministic_safe_and_does_not_invent_platform_code() -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=request.request_id,
    )
    first = generate_firmware_source_proposal(generation_request, module)
    second = generate_firmware_source_proposal(generation_request, module)

    assert first == second
    assert tuple(item.relative_path.rsplit(".", 1)[-1] for item in first.files) == ("h", "c")
    assert all(item.relative_path.startswith("generated/") for item in first.files)
    assert all(item.size_bytes <= 64 * 1024 for item in first.files)
    assert sum(item.size_bytes for item in first.files) <= 128 * 1024
    combined = "\n".join(item.content for item in first.files).casefold()
    for forbidden in ("stm32_hal", "freertos", "pwm", "timer", "tim_", "irq", "dma", "pid", "hal_"):
        assert forbidden not in combined
    assert "todo" in combined
    assert module.responsibility.casefold() in combined


def test_generation_rejects_unapproved_or_cross_bound_requests() -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    pending_requests = tuple(
        item.model_copy(update={"status": ExecutionRequestStatus.REQUESTED, "fingerprint": ZERO})
        if item.request_id == request.request_id
        else item
        for item in authorization.requests
    )
    pending_decisions = tuple(
        item.model_copy(
            update={
                "decision": AuthorizationDecisionStatus.PENDING,
                "approved_by": None,
                "timestamp_reference": None,
                "fingerprint": ZERO,
            }
        )
        if item.request_id == request.request_id
        else item
        for item in authorization.decisions
    )
    pending = authorization.model_copy(
        update={
            "requests": pending_requests,
            "decisions": pending_decisions,
            "fingerprint": ZERO,
        }
    )
    with pytest.raises(ValueError, match="approved|authorized"):
        generate_firmware_source_request(
            firmware,
            implementation,
            task,
            module,
            intent,
            pending,
            authorization_request_id=request.request_id,
        )

    other_task = implementation.tasks[1]
    with pytest.raises(ValueError, match="module|task|binding"):
        generate_firmware_source_request(
            firmware,
            implementation,
            other_task,
            module,
            intent,
            authorization,
            authorization_request_id=request.request_id,
        )


def test_proposal_decision_binds_reviewer_time_and_content() -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=request.request_id,
    )
    proposal = generate_firmware_source_proposal(generation_request, module)
    decided_at = datetime(2026, 9, 4, 10, tzinfo=UTC)
    decision = decide_firmware_source_proposal(
        proposal,
        decision=FirmwareSourceProposalDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=decided_at,
    )
    assert decision.proposal_fingerprint == proposal.fingerprint
    assert decision.reviewer == "engineer-1"
    assert decision.decided_at == decided_at
    assert type(decision).model_validate_json(decision.model_dump_json()) == decision


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../motor.c",
        "/generated/motor.c",
        "C:/generated/motor.c",
        "generated/../motor.c",
        "generated//motor.c",
        "generated/CON.h",
        "generated/motor.cpp",
    ),
)
def test_source_file_contract_rejects_unsafe_or_unsupported_paths(
    unsafe_path: str,
) -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=request.request_id,
    )
    source_file = generate_firmware_source_proposal(generation_request, module).files[0]
    payload = source_file.model_dump(mode="python")
    payload.update(relative_path=unsafe_path, fingerprint=ZERO)
    with pytest.raises(ValidationError, match="relative_path|unsafe"):
        FirmwareSourceFileProposal.model_validate(payload, strict=False)


def test_source_proposal_rejects_case_insensitive_duplicate_paths() -> None:
    firmware, implementation, task, module, intent, authorization, request = _source_state()
    generation_request = generate_firmware_source_request(
        firmware,
        implementation,
        task,
        module,
        intent,
        authorization,
        authorization_request_id=request.request_id,
    )
    proposal = generate_firmware_source_proposal(generation_request, module)
    duplicate = FirmwareSourceFileProposal.create(
        relative_path="generated/"
        + proposal.files[0].relative_path.removeprefix("generated/").upper(),
        content=proposal.files[0].content,
        content_fingerprint=proposal.files[0].content_fingerprint,
        size_bytes=proposal.files[0].size_bytes,
    )
    payload = proposal.model_dump(mode="python")
    payload.update(files=(proposal.files[0], duplicate), fingerprint=ZERO)
    with pytest.raises(ValidationError, match="unique"):
        FirmwareSourceProposal.model_validate(payload, strict=False)
