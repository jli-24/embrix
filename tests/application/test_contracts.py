from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.application import (
    ApplicationAuditEvent,
    ApplicationAuditEventType,
    ApplicationErrorCode,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRunStatus,
)

from .fixtures import NOW, request


def test_request_is_strict_frozen_normalized_and_fingerprinted() -> None:
    value = request(question="  Can e\u0301vidence be used?  ")
    assert value.question == "Can évidence be used?"
    assert value.fingerprint.startswith("sha256:")
    with pytest.raises(ValidationError):
        request(constraints=[])
    with pytest.raises(ValidationError):
        ApplicationQuestionRequest(
            **request().model_dump(mode="python"), unexpected="forbidden"
        )
    with pytest.raises(ValidationError):
        request(requested_at="2026-08-10T12:00:00Z")
    with pytest.raises(ValidationError):
        request(question="api_key=private")


def test_request_rejects_fingerprint_tampering_and_copies_constraints() -> None:
    original = request(constraints=("Use verified evidence",))
    material = original.model_dump(mode="python")
    material["question"] = "Different question"
    with pytest.raises(ValidationError):
        ApplicationQuestionRequest.model_validate(material)
    dumped = original.model_dump(mode="python")
    dumped["constraints"] = ("changed",)
    assert original.constraints == ("Use verified evidence",)


def test_audit_event_requires_outcome_specific_bindings() -> None:
    common = {
        "event_id": ApplicationAuditEvent.event_id_for(
            request().fingerprint, ApplicationAuditEventType.QUESTION_ANSWERED
        ),
        "event_type": ApplicationAuditEventType.QUESTION_ANSWERED,
        "request_id": "request-1",
        "trace_id": "trace-1",
        "project_id": "project-1",
        "requester": "engineer-1",
        "route": ApplicationRoute.KNOWLEDGE,
        "status": ApplicationRunStatus.COMPLETED,
        "question_fingerprint": request().fingerprint,
        "context_fingerprint": "sha256:" + "b" * 64,
        "answer_fingerprint": "sha256:" + "c" * 64,
        "model_info_fingerprint": "sha256:" + "d" * 64,
        "evidence_refs": ("memory-1",),
        "error_code": None,
        "timestamp": NOW + timedelta(seconds=1),
    }
    event = ApplicationAuditEvent(**common)
    assert event.status is ApplicationRunStatus.COMPLETED
    with pytest.raises(ValidationError):
        ApplicationAuditEvent(**{**common, "answer_fingerprint": None})
    with pytest.raises(ValidationError):
        ApplicationAuditEvent(
            **{
                **common,
                "event_type": ApplicationAuditEventType.QUESTION_FAILED,
                "status": ApplicationRunStatus.FAILED,
                "answer_fingerprint": None,
                "model_info_fingerprint": None,
                "error_code": None,
            }
        )


def test_result_rejects_an_audit_reference_from_another_question() -> None:
    from embedded_copilot.application import (
        ApplicationAuditReference,
        ApplicationRunResult,
    )

    event_type = ApplicationAuditEventType.QUESTION_REJECTED
    with pytest.raises(ValidationError):
        ApplicationRunResult(
            request_id="request-1",
            trace_id="trace-1",
            project_id="project-1",
            route=ApplicationRoute.UNKNOWN,
            status=ApplicationRunStatus.REJECTED,
            question_fingerprint=request().fingerprint,
            context_fingerprint=None,
            answer=None,
            error_code=ApplicationErrorCode.CLARIFICATION_REQUIRED,
            audit=ApplicationAuditReference(
                event_id=ApplicationAuditEvent.event_id_for(
                    "sha256:" + "f" * 64, event_type
                ),
                event_type=event_type,
                fingerprint="sha256:" + "e" * 64,
            ),
            completed_at=NOW,
        )


def test_safe_answer_diagnostics_do_not_change_legacy_result_fingerprint() -> None:
    from embedded_copilot.application import ApplicationRunResult

    checked_request = request()
    event = ApplicationAuditEvent.create(
        event_type=ApplicationAuditEventType.QUESTION_FAILED,
        request=checked_request,
        route=ApplicationRoute.GREENFIELD_DISCOVERY,
        status=ApplicationRunStatus.FAILED,
        context_fingerprint=None,
        answer_fingerprint=None,
        model_info_fingerprint=None,
        evidence_refs=(),
        error_code=ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
        timestamp=NOW + timedelta(seconds=1),
    )
    result = ApplicationRunResult.create(
        request=checked_request,
        route=ApplicationRoute.GREENFIELD_DISCOVERY,
        status=ApplicationRunStatus.FAILED,
        context_fingerprint=None,
        answer=None,
        error_code=ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
        audit_event=event,
        gateway_invocation_count=1,
        provider_response_present=True,
        answer_validation_stage="MODEL_RESPONSE_NORMALIZATION",
        answer_rejection_reason=ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
        answer_text_present=True,
    )
    legacy = result.model_dump(
        mode="python",
        exclude={
            "gateway_invocation_count",
            "provider_response_present",
            "answer_validation_stage",
            "answer_rejection_reason",
            "proposed_requirements_count",
            "open_questions_count",
            "answer_text_present",
        },
    )

    restored = ApplicationRunResult.model_validate(legacy)

    assert restored.fingerprint == result.fingerprint
    assert restored.gateway_invocation_count == 0
    assert restored.answer_validation_stage is None


def test_dependency_container_is_frozen() -> None:
    from embedded_copilot.application import (
        ApplicationDependencyContainer,
        ApplicationRouteDecision,
        FakeApplicationAuditSink,
        FakeApplicationContext,
        FakeApplicationModelGateway,
        FakeApplicationSupervisor,
        FixedApplicationClock,
        ModelPolicy,
    )

    container = ApplicationDependencyContainer(
        agent_runtime=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                request(), route=ApplicationRoute.KNOWLEDGE
            )
        ),
        context_provider=FakeApplicationContext(None),
        model_gateway=FakeApplicationModelGateway(),
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW),
        model_policy=ModelPolicy(
            primary_provider="fake", model_name="fake-model"
        ),
    )
    with pytest.raises(FrozenInstanceError):
        container.clock = FixedApplicationClock(NOW)  # type: ignore[misc]


def test_dependency_container_rejects_missing_ports() -> None:
    from embedded_copilot.application import (
        ApplicationDependencyContainer,
        ApplicationDependencyError,
        FakeApplicationAuditSink,
        FakeApplicationContext,
        FakeApplicationModelGateway,
        FixedApplicationClock,
        ModelPolicy,
    )

    with pytest.raises(ApplicationDependencyError):
        ApplicationDependencyContainer(
            agent_runtime=object(),  # type: ignore[arg-type]
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )


@pytest.mark.parametrize(
    "error_code",
    [
        ApplicationErrorCode.UNSUPPORTED_ROUTE,
        ApplicationErrorCode.CLARIFICATION_REQUIRED,
        ApplicationErrorCode.CONTEXT_UNAVAILABLE,
        ApplicationErrorCode.INSUFFICIENT_EVIDENCE,
        ApplicationErrorCode.MODEL_UNAVAILABLE,
        ApplicationErrorCode.ANSWER_REJECTED,
        ApplicationErrorCode.PROVIDER_TRANSPORT_FAILED,
        ApplicationErrorCode.PROVIDER_PARSE_FAILED,
        ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
        ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH,
        ApplicationErrorCode.ANSWER_EMPTY,
        ApplicationErrorCode.ANSWER_REJECTED_UNSAFE,
        ApplicationErrorCode.ANSWER_REJECTED_EVIDENCE_MISMATCH,
        ApplicationErrorCode.APPLICATION_UNAVAILABLE,
    ],
)
def test_error_codes_are_closed(error_code: ApplicationErrorCode) -> None:
    assert type(error_code.value) is str
